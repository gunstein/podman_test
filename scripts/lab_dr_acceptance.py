"""Operator-side controller for the disposable Todo DR acceptance lab."""

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import shlex
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence


DEFAULT_STATE = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    / "todo"
    / "lab-dr-acceptance.json"
)
PROFILES = {"reset-check": ("reset_hosts", "clean_preflight")}
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:@/-]+$")
NAME_VALUE = re.compile(r"^[A-Za-z0-9_.-]+$")


class LabError(RuntimeError):
    """An expected, operator-actionable lab orchestration error."""


@dataclass(frozen=True)
class Node:
    role: str
    vmid: int
    proxmox_name: str
    hostname: str
    address: str
    clean_snapshot: str


@dataclass(frozen=True)
class LabConfig:
    path: Path
    proxmox_ssh_target: str
    proxmox_identity_file: Optional[Path]
    guest_user: str
    guest_identity_file: Optional[Path]
    command_timeout_seconds: int
    wait_timeout_seconds: int
    poll_interval_seconds: float
    primary: Node
    standby: Node
    fingerprint: str

    @property
    def nodes(self) -> tuple[Node, Node]:
        return self.primary, self.standby

    @property
    def reset_confirmation(self) -> str:
        return f"{self.primary.vmid}:{self.standby.vmid}"


Runner = Callable[
    [Sequence[str], Optional[str], Optional[int]], subprocess.CompletedProcess
]


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def positive_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise LabError(f"{field} must be a positive integer")
    return value


def positive_number(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise LabError(f"{field} must be a positive number")
    return float(value)


def required_string(
    mapping: dict, key: str, field: str, pattern: re.Pattern = NAME_VALUE
) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value or not pattern.fullmatch(value):
        raise LabError(f"{field} contains an invalid or missing value")
    return value


def optional_path(value: object, field: str) -> Optional[Path]:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise LabError(f"{field} must be a path string")
    path = Path(value).expanduser()
    if not path.is_file():
        raise LabError(f"{field} does not exist: {path}")
    return path.resolve()


def parse_node(role: str, value: object) -> Node:
    if not isinstance(value, dict):
        raise LabError(f"nodes.{role} must be a table")
    address = required_string(value, "address", f"nodes.{role}.address")
    try:
        ipaddress.ip_address(address)
    except ValueError as error:
        raise LabError(f"nodes.{role}.address is not an IP address") from error
    return Node(
        role=role,
        vmid=positive_integer(value.get("vmid"), f"nodes.{role}.vmid"),
        proxmox_name=required_string(
            value, "proxmox_name", f"nodes.{role}.proxmox_name"
        ),
        hostname=required_string(
            value, "hostname", f"nodes.{role}.hostname"
        ),
        address=address,
        clean_snapshot=required_string(
            value, "clean_snapshot", f"nodes.{role}.clean_snapshot"
        ),
    )


def load_config(path: Path) -> LabConfig:
    path = path.expanduser().resolve()
    try:
        raw_bytes = path.read_bytes()
        document = tomllib.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LabError(f"Cannot read lab config {path}: {error}") from error
    if document.get("version") != 1:
        raise LabError("Lab config version must be 1")
    proxmox = document.get("proxmox")
    guest = document.get("guest_ssh")
    nodes = document.get("nodes")
    if not isinstance(proxmox, dict):
        raise LabError("Missing [proxmox] table")
    if not isinstance(guest, dict):
        raise LabError("Missing [guest_ssh] table")
    if not isinstance(nodes, dict):
        raise LabError("Missing [nodes] table")

    primary = parse_node("primary", nodes.get("primary"))
    standby = parse_node("standby", nodes.get("standby"))
    if primary.vmid == standby.vmid:
        raise LabError("Primary and standby VMIDs must differ")
    if primary.address == standby.address:
        raise LabError("Primary and standby addresses must differ")
    if primary.hostname == standby.hostname:
        raise LabError("Primary and standby hostnames must differ")

    proxmox_target = required_string(
        proxmox,
        "ssh_target",
        "proxmox.ssh_target",
        SAFE_VALUE,
    )
    guest_user = required_string(guest, "user", "guest_ssh.user")
    command_timeout = positive_integer(
        proxmox.get("command_timeout_seconds", 600),
        "proxmox.command_timeout_seconds",
    )
    wait_timeout = positive_integer(
        proxmox.get("wait_timeout_seconds", 300),
        "proxmox.wait_timeout_seconds",
    )
    poll_interval = positive_number(
        proxmox.get("poll_interval_seconds", 2),
        "proxmox.poll_interval_seconds",
    )
    return LabConfig(
        path=path,
        proxmox_ssh_target=proxmox_target,
        proxmox_identity_file=optional_path(
            proxmox.get("identity_file"), "proxmox.identity_file"
        ),
        guest_user=guest_user,
        guest_identity_file=optional_path(
            guest.get("identity_file"), "guest_ssh.identity_file"
        ),
        command_timeout_seconds=command_timeout,
        wait_timeout_seconds=wait_timeout,
        poll_interval_seconds=poll_interval,
        primary=primary,
        standby=standby,
        fingerprint=hashlib.sha256(raw_bytes).hexdigest(),
    )


def run_command(
    arguments: Sequence[str],
    input_text: Optional[str] = None,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    print(f"$ {shlex.join(arguments)}", flush=True)
    try:
        result = subprocess.run(
            list(arguments),
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise LabError(
            f"Command timed out after {timeout} seconds: "
            f"{shlex.join(arguments)}"
        ) from error
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(
            result.stderr,
            end="" if result.stderr.endswith("\n") else "\n",
            file=sys.stderr,
        )
    return result


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()

    def load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "created_at": timestamp(), "stages": {}}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LabError(f"Cannot read state {self.path}: {error}") from error
        if state.get("version") != 1 or not isinstance(state.get("stages"), dict):
            raise LabError(f"Unsupported state format: {self.path}")
        return state

    def save(self, state: dict) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)


class ProxmoxAdapter:
    def __init__(self, config: LabConfig, runner: Runner = run_command) -> None:
        self.config = config
        self.runner = runner

    def _ssh_prefix(self) -> list[str]:
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
        ]
        if self.config.proxmox_identity_file:
            command.extend(["-i", str(self.config.proxmox_identity_file)])
        command.append(self.config.proxmox_ssh_target)
        return command

    def qm(self, *arguments: object) -> subprocess.CompletedProcess:
        command = self._ssh_prefix() + ["qm"] + [str(item) for item in arguments]
        result = self.runner(
            command, None, self.config.command_timeout_seconds
        )
        if result.returncode != 0:
            raise LabError(f"Proxmox qm command failed: {shlex.join(command)}")
        return result

    def vm_config(self, node: Node) -> dict[str, str]:
        result = self.qm("config", node.vmid)
        parsed = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                parsed[key.strip()] = value.strip()
        return parsed

    def validate_node(self, node: Node) -> None:
        actual = self.vm_config(node).get("name")
        if actual != node.proxmox_name:
            raise LabError(
                f"VM {node.vmid} is {actual!r}, expected "
                f"{node.proxmox_name!r}"
            )
        snapshots = self.qm("listsnapshot", node.vmid).stdout
        pattern = re.compile(
            rf"(^|\s){re.escape(node.clean_snapshot)}(?=\s|$)",
            re.MULTILINE,
        )
        if not pattern.search(snapshots):
            raise LabError(
                f"VM {node.vmid} has no snapshot {node.clean_snapshot!r}"
            )

    def status(self, node: Node) -> str:
        output = self.qm("status", node.vmid).stdout.strip()
        match = re.fullmatch(r"status:\s+(running|stopped)", output)
        if not match:
            raise LabError(f"Unexpected status for VM {node.vmid}: {output!r}")
        return match.group(1)

    def wait_status(self, node: Node, expected: str) -> None:
        deadline = time.monotonic() + self.config.wait_timeout_seconds
        while self.status(node) != expected:
            if time.monotonic() >= deadline:
                raise LabError(
                    f"VM {node.vmid} did not become {expected} in time"
                )
            time.sleep(self.config.poll_interval_seconds)

    def stop(self, node: Node) -> None:
        if self.status(node) == "running":
            self.qm("stop", node.vmid)
            self.wait_status(node, "stopped")

    def rollback(self, node: Node) -> None:
        self.qm("rollback", node.vmid, node.clean_snapshot)

    def start(self, node: Node) -> None:
        if self.status(node) != "running":
            self.qm("start", node.vmid)
            self.wait_status(node, "running")

    def wait_guest_agent(self, node: Node) -> None:
        deadline = time.monotonic() + self.config.wait_timeout_seconds
        while True:
            try:
                self.qm("guest", "cmd", node.vmid, "ping")
                return
            except LabError:
                if time.monotonic() >= deadline:
                    raise LabError(
                        f"QEMU Guest Agent did not answer for VM {node.vmid}"
                    )
                time.sleep(self.config.poll_interval_seconds)


class GuestAdapter:
    def __init__(self, config: LabConfig, runner: Runner = run_command) -> None:
        self.config = config
        self.runner = runner

    def _ssh_prefix(self, node: Node) -> list[str]:
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=5",
        ]
        if self.config.guest_identity_file:
            command.extend(["-i", str(self.config.guest_identity_file)])
        command.append(f"{self.config.guest_user}@{node.address}")
        return command

    def run_script(
        self, node: Node, script: str
    ) -> subprocess.CompletedProcess:
        command = self._ssh_prefix(node) + ["sh", "-s"]
        result = self.runner(
            command, script, self.config.command_timeout_seconds
        )
        if result.returncode != 0:
            raise LabError(
                f"Guest command failed on {node.hostname}: "
                f"{shlex.join(command)}"
            )
        return result

    def wait_ssh(self, node: Node) -> None:
        deadline = time.monotonic() + self.config.wait_timeout_seconds
        while True:
            result = self.runner(
                self._ssh_prefix(node) + ["true"],
                None,
                10,
            )
            if result.returncode == 0:
                return
            if time.monotonic() >= deadline:
                raise LabError(f"SSH did not become ready on {node.address}")
            time.sleep(self.config.poll_interval_seconds)

    def clean_preflight(self, node: Node) -> str:
        expected = shlex.quote(node.hostname)
        address = shlex.quote(" " + node.address + "/")
        script = rf"""set -eu
test "$(hostname)" = {expected}
ip -brief -4 address | grep -F -- {address}
test "$(getenforce)" = Enforcing
test "$(podman info --format '{{{{.Host.Security.Rootless}}}}')" = true
systemctl is-active sshd firewalld fapolicyd qemu-guest-agent
test "$(loginctl show-user "$USER" -p Linger --value)" = yes
if podman ps -a --format '{{{{.Names}}}}' | grep -Eq '^todo([_-]|$)'; then
    echo "Todo container state exists" >&2
    exit 1
fi
if podman volume ls --format '{{{{.Name}}}}' | grep -Eq '^todo([_-]|$)'; then
    echo "Todo volume state exists" >&2
    exit 1
fi
if podman secret ls --format '{{{{.Name}}}}' | grep -Eq '^todo([_-]|$)'; then
    echo "Todo secret state exists" >&2
    exit 1
fi
if podman network ls --format '{{{{.Name}}}}' | grep -Fxq todo-network; then
    echo "Todo network exists" >&2
    exit 1
fi
test -z "$(find "$HOME/.config/containers/systemd" -type f \( -name 'todo*.container' -o -name 'todo*.kube' -o -name 'todo*.network' -o -name 'todo*.volume' \) -print -quit 2>/dev/null)"
test ! -e "$HOME/.config/todo"
test ! -e /opt/todo/bin/todo_dr.py
test ! -e /opt/todo/bin/todo_dr_run.py
test ! -e /opt/todo/bin/todo_backup.py
printf 'LAB_MACHINE_ID=%s\n' "$(cat /etc/machine-id)"
"""
        result = self.run_script(node, script)
        match = re.search(r"^LAB_MACHINE_ID=([0-9a-f]{32})$", result.stdout, re.MULTILINE)
        if not match:
            raise LabError(f"Missing valid machine ID from {node.hostname}")
        return match.group(1)


class LabAcceptance:
    def __init__(
        self,
        config: LabConfig,
        state_store: StateStore,
        proxmox: Optional[ProxmoxAdapter] = None,
        guests: Optional[GuestAdapter] = None,
    ) -> None:
        self.config = config
        self.state_store = state_store
        self.proxmox = proxmox or ProxmoxAdapter(config)
        self.guests = guests or GuestAdapter(config)

    def _state(self, profile: str) -> dict:
        state = self.state_store.load()
        recorded_config = state.get("config_fingerprint")
        if recorded_config and recorded_config != self.config.fingerprint:
            raise LabError(
                "State belongs to a different lab configuration; use a new "
                "state path or restore the original config"
            )
        recorded_profile = state.get("profile")
        if recorded_profile and recorded_profile != profile:
            raise LabError(
                f"State belongs to profile {recorded_profile!r}, not {profile!r}"
            )
        state["config"] = str(self.config.path)
        state["config_fingerprint"] = self.config.fingerprint
        state["profile"] = profile
        return state

    def _execute(
        self,
        profile: str,
        stage: str,
        action: Callable[[], None],
        destructive: bool = False,
    ) -> None:
        state = self._state(profile)
        stages = state["stages"]
        existing = stages.get(stage, {})
        if existing.get("status") == "completed":
            print(f"Stage {stage} already completed; nothing to do.")
            return
        if existing and destructive:
            raise LabError(
                f"Destructive stage {stage!r} was already started. Inspect "
                "Proxmox and use a new state file for an intentional reset."
            )
        started = timestamp()
        stages[stage] = {"status": "running", "started_at": started}
        self.state_store.save(state)
        try:
            action()
        except Exception as error:
            stages[stage] = {
                "status": "failed",
                "started_at": started,
                "failed_at": timestamp(),
                "error": str(error),
            }
            self.state_store.save(state)
            raise
        stages[stage] = {
            "status": "completed",
            "started_at": started,
            "completed_at": timestamp(),
        }
        self.state_store.save(state)

    def validate_infrastructure(self) -> None:
        for node in self.config.nodes:
            self.proxmox.validate_node(node)
        print("Proxmox VM identities and clean snapshots: OK")

    def reset_hosts(self, profile: str, confirmation: str) -> None:
        state = self._state(profile)
        if state["stages"].get("reset_hosts", {}).get("status") == "completed":
            print("Stage reset_hosts already completed; nothing to do.")
            return
        if confirmation != self.config.reset_confirmation:
            raise LabError(
                "Snapshot reset requires --confirm-reset "
                f"{self.config.reset_confirmation!r}"
            )

        self.validate_infrastructure()

        def action() -> None:
            for node in self.config.nodes:
                self.proxmox.stop(node)
            for node in self.config.nodes:
                self.proxmox.rollback(node)
            for node in self.config.nodes:
                self.proxmox.start(node)
            for node in self.config.nodes:
                self.proxmox.wait_guest_agent(node)
                self.guests.wait_ssh(node)

        self._execute(
            profile,
            "reset_hosts",
            action,
            destructive=True,
        )

    def clean_preflight(self, profile: str) -> None:
        state = self._state(profile)
        if state["stages"].get("reset_hosts", {}).get("status") != "completed":
            raise LabError("Stage 'reset_hosts' must be completed first")

        def action() -> None:
            machine_ids = [
                self.guests.clean_preflight(node) for node in self.config.nodes
            ]
            if len(set(machine_ids)) != len(machine_ids):
                raise LabError("Primary and standby machine IDs must differ")
            print("Clean Oracle Linux baseline: OK")

        self._execute(profile, "clean_preflight", action)

    def run(
        self, profile: str, confirmation: Optional[str]
    ) -> None:
        if profile not in PROFILES:
            raise LabError(f"Unknown profile: {profile}")
        for stage in PROFILES[profile]:
            if stage == "reset_hosts":
                self.reset_hosts(profile, confirmation or "")
            elif stage == "clean_preflight":
                self.clean_preflight(profile)

    def show_plan(self, profile: str) -> None:
        if profile not in PROFILES:
            raise LabError(f"Unknown profile: {profile}")
        print(f"Profile: {profile}")
        print(f"Config: {self.config.path}")
        print(f"State: {self.state_store.path}")
        for position, stage in enumerate(PROFILES[profile], start=1):
            marker = "DESTRUCTIVE" if stage == "reset_hosts" else "read-only"
            print(f"{position}. {stage} [{marker}]")
        print(
            "Required reset confirmation: "
            f"{self.config.reset_confirmation}"
        )

    def report(self, profile: str, as_json: bool = False) -> None:
        state = self._state(profile)
        stages = state["stages"]
        result = {
            "profile": profile,
            "config": str(self.config.path),
            "state": str(self.state_store.path),
            "stages": {
                stage: stages.get(stage, {"status": "pending"})
                for stage in PROFILES[profile]
            },
        }
        statuses = [entry["status"] for entry in result["stages"].values()]
        if all(status == "completed" for status in statuses):
            result["overall"] = "PASS"
        elif any(status == "failed" for status in statuses):
            result["overall"] = "FAIL"
        else:
            result["overall"] = "INCOMPLETE"
        if as_json:
            print(json.dumps(result, indent=2, sort_keys=True))
            return
        print(f"Profile: {profile}")
        for stage, details in result["stages"].items():
            print(f"{stage}: {details['status'].upper()}")
        print(f"Overall: {result['overall']}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Control the disposable two-VM Todo DR acceptance lab."
    )
    result.add_argument("--config", required=True, type=Path)
    result.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = result.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate")
    validate.add_argument("--local-only", action="store_true")

    for name in ("plan", "status"):
        command = commands.add_parser(name)
        command.add_argument(
            "--profile", choices=sorted(PROFILES), default="reset-check"
        )

    run = commands.add_parser("run")
    run.add_argument(
        "--profile", choices=sorted(PROFILES), default="reset-check"
    )
    run.add_argument("--confirm-reset")

    report = commands.add_parser("report")
    report.add_argument(
        "--profile", choices=sorted(PROFILES), default="reset-check"
    )
    report.add_argument("--json", action="store_true")
    return result


def main(arguments: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(arguments)
    try:
        config = load_config(args.config)
        controller = LabAcceptance(config, StateStore(args.state))
        if args.command == "validate":
            print("Local configuration: OK")
            if not args.local_only:
                controller.validate_infrastructure()
        elif args.command == "plan":
            controller.show_plan(args.profile)
        elif args.command in ("status", "report"):
            controller.report(
                args.profile,
                as_json=getattr(args, "json", False),
            )
        elif args.command == "run":
            controller.run(args.profile, args.confirm_reset)
        return 0
    except LabError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
