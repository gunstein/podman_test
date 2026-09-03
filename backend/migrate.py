import argparse
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg

from backend.main import connect

DIRECTORY = Path(__file__).parent / "migrations"
PATTERN = re.compile(r"^(\d+)_(.+)\.(up|down)\.sql$")
# SQLSTATE 57P03 means PostgreSQL is running but not ready for connections.
RETRYABLE_CONNECT_SQLSTATES = {None, "57P03"}
CONNECT_RETRY_INTERVAL_SECONDS = 2.0


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up_path: Path
    down_path: Path


def load_migrations() -> list[Migration]:
    files = {}
    for path in DIRECTORY.iterdir():
        match = PATTERN.match(path.name)
        if not match:
            continue
        version, name, direction = match.groups()
        entry = files.setdefault(int(version), {"name": name})
        if entry["name"] != name or direction in entry:
            raise RuntimeError(f"Conflicting migration version {version}")
        entry[direction] = path

    migrations = []
    for version, entry in sorted(files.items()):
        if "up" not in entry or "down" not in entry:
            raise RuntimeError(f"Migration {version:03d} needs up and down files")
        migrations.append(Migration(version, entry["name"], entry["up"], entry["down"]))
    return migrations


def ensure_table(connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def applied_versions(connection) -> set[int]:
    ensure_table(connection)
    rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    return {row["version"] for row in rows}


def parse_connect_timeout(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError(
            "connect timeout must be a finite, non-negative number"
        )
    return seconds


def connect_with_retry(timeout_seconds: float):
    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise ValueError("connect timeout must be finite and non-negative")
    if timeout_seconds == 0:
        return connect()

    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            return connect()
        except psycopg.OperationalError as error:
            if error.sqlstate not in RETRYABLE_CONNECT_SQLSTATES:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            print("Database is not ready; retrying connection", flush=True)
            time.sleep(min(CONNECT_RETRY_INTERVAL_SECONDS, remaining))


def migrate_up(connect_timeout: float = 0) -> None:
    with connect_with_retry(connect_timeout) as connection:
        applied = applied_versions(connection)
        for migration in load_migrations():
            if migration.version in applied:
                continue
            connection.execute(migration.up_path.read_text())
            connection.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                (migration.version, migration.name),
            )
            print(f"Applied {migration.version:03d}_{migration.name}")


def migrate_down() -> None:
    migrations = {item.version: item for item in load_migrations()}
    with connect() as connection:
        ensure_table(connection)
        row = connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if row is None:
            print("Nothing to roll back")
            return
        migration = migrations.get(row["version"])
        if migration is None:
            raise RuntimeError(f"Missing files for migration {row['version']}")
        connection.execute(migration.down_path.read_text())
        connection.execute(
            "DELETE FROM schema_migrations WHERE version = %s", (migration.version,)
        )
        print(f"Rolled back {migration.version:03d}_{migration.name}")


def show_status() -> None:
    with connect() as connection:
        applied = applied_versions(connection)
        for migration in load_migrations():
            state = "applied" if migration.version in applied else "pending"
            print(f"{migration.version:03d}_{migration.name}: {state}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run database migrations")
    parser.add_argument(
        "--connect-timeout",
        type=parse_connect_timeout,
        default=0,
        help="wait this many seconds for a transient database connection failure",
    )
    parser.add_argument("command", choices=("status", "up", "down"))
    args = parser.parse_args()
    if args.command == "up":
        migrate_up(args.connect_timeout)
    else:
        {"status": show_status, "down": migrate_down}[args.command]()


if __name__ == "__main__":
    main()
