"""Local disaster-recovery checks and PostgreSQL promotion for Todo standby."""

import argparse
import json
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

DEFAULT_CONFIG = Path.home() / ".config" / "todo" / "todo-dr.json"
POSTGRES_DATA = "/var/lib/postgresql/data"


class DrError(RuntimeError):
    """An expected, operator-actionable DR error."""


@dataclass(frozen=True)
class Config:
    primary_name: str
    primary_address: str
    standby_name: str
    rpo_target_seconds: int


@dataclass(frozen=True)
class DatabaseStatus:
    in_recovery: bool
    transaction_read_only: bool
    receive_lsn: str
    replay_lsn: str
    apply_lag_bytes: int


Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess]
Connector = Callable[[str, int, float], bool]


def run_command(
    arguments: Sequence[str], timeout: float = 120
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(arguments),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def tcp_reachable(address: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((address, port), timeout=timeout):
            return True
    except OSError:
        return False


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = Config(
            primary_name=str(raw["primary_name"]),
            primary_address=str(raw["primary_address"]),
            standby_name=str(raw["standby_name"]),
            rpo_target_seconds=int(
                raw.get("rpo_target_seconds", raw.get("rpo_seconds"))
            ),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise DrError(f"Cannot read valid DR configuration from {path}: {error}") from error

    if not all((config.primary_name, config.primary_address, config.standby_name)):
        raise DrError(f"DR configuration contains an empty host identity: {path}")
    if config.rpo_target_seconds <= 0:
        raise DrError("rpo_target_seconds must be greater than zero")
    return config


class TodoDr:
    def __init__(
        self,
        config: Config,
        runner: Runner = run_command,
        connector: Callable[[str, int, float], bool] = tcp_reachable,
    ) -> None:
        self.config = config
        self.runner = runner
        self.connector = connector

    def _run(self, arguments: Sequence[str], description: str) -> str:
        try:
            result = self.runner(arguments, 120)
        except subprocess.TimeoutExpired as error:
            raise DrError(
                f"{description} timed out after 120 seconds"
            ) from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            suffix = f": {detail}" if detail else ""
            raise DrError(f"{description} failed{suffix}")
        return result.stdout.strip()

    def service_status(self) -> str:
        return self._run(
            ["systemctl", "--user", "is-active", "todo-postgres.service"],
            "PostgreSQL systemd status check",
        )

    def container_health(self) -> str:
        return self._run(
            [
                "podman",
                "inspect",
                "--format",
                "{" * 2 + ".State.Health.Status" + "}" * 2,
                "todo-postgres",
            ],
            "PostgreSQL container health check",
        )

    def database_status(self) -> DatabaseStatus:
        sql = (
            "SELECT pg_is_in_recovery(), "
            "current_setting('transaction_read_only'), "
            "COALESCE(pg_last_wal_receive_lsn()::text, ''), "
            "COALESCE(pg_last_wal_replay_lsn()::text, ''), "
            "COALESCE(pg_wal_lsn_diff(pg_last_wal_receive_lsn(), "
            "pg_last_wal_replay_lsn())::bigint, 0);"
        )
        output = self._run(
            [
                "podman", "exec", "todo-postgres", "psql",
                "--username", "todo", "--dbname", "postgres",
                "--tuples-only", "--no-align", "--field-separator=|",
                "--command", sql,
            ],
            "PostgreSQL recovery query",
        )
        fields = output.split("|")
        if len(fields) != 5 or fields[0] not in ("t", "f"):
            raise DrError(f"Unexpected PostgreSQL status output: {output!r}")
        try:
            lag = int(fields[4])
        except ValueError as error:
            raise DrError(f"Invalid apply lag from PostgreSQL: {fields[4]!r}") from error
        return DatabaseStatus(
            in_recovery=fields[0] == "t",
            transaction_read_only=fields[1] == "on",
            receive_lsn=fields[2] or "not available",
            replay_lsn=fields[3] or "not available",
            apply_lag_bytes=lag,
        )

    def primary_reachable(self) -> bool:
        return self.connector(self.config.primary_address, 5432, 2.0)

    def status_lines(self) -> List[str]:
        service = self.service_status()
        health = self.container_health()
        database = self.database_status()
        primary = "reachable" if self.primary_reachable() else "unreachable"
        role = "standby" if database.in_recovery else "promoted primary"
        writable = "no" if database.transaction_read_only else "yes"
        return [
            f"Service: {service}",
            f"Container: {health}",
            f"Database role: {role}",
            f"Writable: {writable}",
            f"Receive LSN: {database.receive_lsn}",
            f"Replay LSN: {database.replay_lsn}",
            f"Local apply lag: {database.apply_lag_bytes} bytes",
            f"Primary endpoint {self.config.primary_address}:5432: {primary}",
            f"Configured RPO target (informational): at most "
            f"{self.config.rpo_target_seconds} seconds",
        ]

    def preflight(self, fencing_confirmation: str) -> DatabaseStatus:
        expected = f"{self.config.primary_name} is fenced"
        if fencing_confirmation != expected:
            raise DrError(f"Fencing confirmation must be exactly: {expected!r}")
        local_hostname = socket.gethostname()
        if local_hostname != self.config.standby_name:
            raise DrError(
                "Run promotion on the configured standby host "
                f"{self.config.standby_name!r}, not {local_hostname!r}"
            )
        if self.service_status() != "active":
            raise DrError("PostgreSQL systemd service is not active")
        if self.container_health() != "healthy":
            raise DrError("PostgreSQL container is not healthy")

        database = self.database_status()
        if not database.in_recovery or not database.transaction_read_only:
            raise DrError("Local PostgreSQL is not a read-only standby")
        if "not available" in (database.receive_lsn, database.replay_lsn):
            raise DrError("Standby receive or replay LSN is unavailable")
        if database.apply_lag_bytes != 0:
            raise DrError(
                f"Standby has unreplayed local WAL: {database.apply_lag_bytes} bytes"
            )
        if self.primary_reachable():
            raise DrError(
                f"Primary PostgreSQL still answers at {self.config.primary_address}:5432; "
                "fencing is not demonstrated"
            )
        return database

    def promote(
        self, fencing_confirmation: str, promotion_confirmation: str
    ) -> DatabaseStatus:
        self.preflight(fencing_confirmation)
        if promotion_confirmation != self.config.standby_name:
            raise DrError(
                "Promotion confirmation must equal the standby hostname: "
                f"{self.config.standby_name!r}"
            )
        self._run(
            [
                "podman", "exec", "todo-postgres", "pg_ctl", "-D",
                POSTGRES_DATA, "promote", "-w", "-t", "60",
            ],
            "PostgreSQL promotion",
        )
        database = self.database_status()
        if database.in_recovery or database.transaction_read_only:
            raise DrError("Promotion command completed, but PostgreSQL is not writable")
        return database


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Inspect and safely promote the local Todo PostgreSQL standby."
    )
    result.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help=f"DR configuration file (default: {DEFAULT_CONFIG})",
    )
    subparsers = result.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Show local standby and primary reachability")
    preflight = subparsers.add_parser(
        "preflight", help="Verify local promotion prerequisites without changing state"
    )
    preflight.add_argument("--confirm-primary-fenced", required=True)
    promote = subparsers.add_parser(
        "promote", help="Promote the local PostgreSQL standby after fencing"
    )
    promote.add_argument("--confirm-primary-fenced", required=True)
    promote.add_argument("--confirm-promotion", required=True)
    return result


def main(arguments: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(arguments)
    try:
        tool = TodoDr(load_config(args.config))
        if args.command == "status":
            print("\n".join(tool.status_lines()))
        elif args.command == "preflight":
            status = tool.preflight(args.confirm_primary_fenced)
            print(
                "Preflight passed: primary is confirmed fenced, its database "
                "endpoint is unreachable, and local apply lag is "
                f"{status.apply_lag_bytes} bytes."
            )
        elif args.command == "promote":
            tool.promote(args.confirm_primary_fenced, args.confirm_promotion)
            print("Promotion completed: local PostgreSQL is writable.")
        return 0
    except DrError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
