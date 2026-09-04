"""Resumable orchestration for the controlled Todo disaster-recovery drill."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence


DEFAULT_STATE = Path.home() / ".config" / "todo" / "todo-dr-run.json"
DEFAULT_DR_TOOL = Path("/opt/todo/bin/todo_dr.py")
STAGES = (
    "promotion",
    "application",
    "rebuild",
    "verification",
)


class RunError(RuntimeError):
    """An expected, operator-actionable orchestration error."""


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def run_command(arguments: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(arguments), check=False, text=True)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Dict[str, object]:
        if not self.path.exists():
            return {"version": 1, "created_at": timestamp(), "stages": {}}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RunError(f"Cannot read DR run state {self.path}: {error}") from error
        if state.get("version") != 1 or not isinstance(state.get("stages"), dict):
            raise RunError(f"Unsupported DR run state format: {self.path}")
        return state

    def save(self, state: Dict[str, object]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)


class DrRun:
    def __init__(
        self,
        project_root: Path,
        inventory: Path,
        state_store: StateStore,
        dr_tool: Path = DEFAULT_DR_TOOL,
        runner: Runner = run_command,
        ask_become_pass: bool = False,
    ) -> None:
        self.project_root = project_root.resolve()
        self.inventory = inventory.resolve()
        self.state_store = state_store
        self.dr_tool = dr_tool
        self.runner = runner
        self.ask_become_pass = ask_become_pass

    def _state(self) -> Dict[str, object]:
        state = self.state_store.load()
        recorded = state.get("inventory")
        if recorded and recorded != str(self.inventory):
            raise RunError(
                f"State belongs to inventory {recorded}, not {self.inventory}"
            )
        state["inventory"] = str(self.inventory)
        return state

    def _run(self, arguments: Sequence[str], description: str) -> None:
        result = self.runner(arguments)
        if result.returncode != 0:
            raise RunError(f"{description} failed with exit code {result.returncode}")

    def _playbook(self, name: str, extra_vars: Optional[dict] = None) -> None:
        arguments = [
            "ansible-playbook",
            "--inventory",
            str(self.inventory),
        ]
        if self.ask_become_pass:
            arguments.append("--ask-become-pass")
        arguments.append(str(self.project_root / "ansible" / name))
        if extra_vars:
            arguments.extend(["--extra-vars", json.dumps(extra_vars, sort_keys=True)])
        self._run(arguments, name)

    def _require_completed(self, state: Dict[str, object], stage: str) -> None:
        stages = state["stages"]
        if stages.get(stage, {}).get("status") != "completed":
            raise RunError(f"Stage {stage!r} must be completed first")

    def _execute_stage(
        self, name: str, action: Callable[[], None], destructive: bool = False
    ) -> None:
        state = self._state()
        stages = state["stages"]
        existing = stages.get(name, {})
        if existing.get("status") == "completed":
            print(f"Stage {name} already completed; nothing to do.")
            return
        if existing and destructive:
            raise RunError(
                f"Destructive stage {name!r} was already started. Inspect the slot, "
                "volume and logs; the runner will not retry it automatically."
            )
        stages[name] = {"status": "running", "started_at": timestamp()}
        self.state_store.save(state)
        try:
            action()
        except Exception as error:
            stages[name] = {
                "status": "failed",
                "started_at": stages[name]["started_at"],
                "failed_at": timestamp(),
                "error": str(error),
            }
            self.state_store.save(state)
            raise
        stages[name] = {
            "status": "completed",
            "started_at": stages[name]["started_at"],
            "completed_at": timestamp(),
        }
        self.state_store.save(state)

    def _destructive_stage_is_pending(self, name: str) -> bool:
        state = self._state()
        existing = state["stages"].get(name, {})
        if existing.get("status") == "completed":
            print(f"Stage {name} already completed; nothing to do.")
            return False
        if existing:
            raise RunError(
                f"Destructive stage {name!r} was already started. Inspect the "
                "result and recover manually; the runner will not retry it."
            )
        return True

    def show_status(self) -> None:
        state = self._state()
        stages = state["stages"]
        print(f"State: {self.state_store.path}")
        print(f"Inventory: {self.inventory}")
        for stage in STAGES:
            print(f"{stage}: {stages.get(stage, {}).get('status', 'pending')}")

    def promote(self, fenced: str, host: str) -> None:
        if not self._destructive_stage_is_pending("promotion"):
            return
        self._run(
            [
                sys.executable,
                str(self.dr_tool),
                "preflight",
                "--confirm-primary-fenced",
                fenced,
            ],
            "local PostgreSQL promotion preflight",
        )

        def action() -> None:
            self._run(
                [
                    sys.executable,
                    str(self.dr_tool),
                    "promote",
                    "--confirm-primary-fenced",
                    fenced,
                    "--confirm-promotion",
                    host,
                ],
                "local PostgreSQL promotion",
            )

        self._execute_stage("promotion", action, destructive=True)

    def deploy_application(self) -> None:
        state = self._state()
        self._require_completed(state, "promotion")
        self._execute_stage(
            "application",
            lambda: self._playbook("deploy-promoted-application.yml"),
        )

    def rebuild_preflight(self, fenced: str, reseed: str) -> None:
        self._playbook(
            "preflight-standby-rebuild.yml",
            {
                "todo_confirm_old_primary_fenced": fenced,
                "todo_confirm_reseed": reseed,
            },
        )

    def rebuild(self, fenced: str, reseed: str) -> None:
        state = self._state()
        self._require_completed(state, "application")
        if not self._destructive_stage_is_pending("rebuild"):
            return
        self.rebuild_preflight(fenced, reseed)
        self._execute_stage(
            "rebuild",
            lambda: self._playbook(
                "rebuild-standby.yml",
                {
                    "todo_confirm_old_primary_fenced": fenced,
                    "todo_confirm_reseed": reseed,
                },
            ),
            destructive=True,
        )

    def verify(self) -> None:
        state = self._state()
        self._require_completed(state, "rebuild")
        self._execute_stage(
            "verification", lambda: self._playbook("cluster-status.yml")
        )


def parser() -> argparse.ArgumentParser:
    project_root = Path(__file__).resolve().parents[1]
    result = argparse.ArgumentParser(
        description="Run the controlled Todo DR drill as resumable stages."
    )
    result.add_argument("--inventory", required=True, type=Path)
    result.add_argument("--state", type=Path, default=DEFAULT_STATE)
    result.add_argument("--project-root", type=Path, default=project_root)
    result.add_argument("--dr-tool", type=Path, default=DEFAULT_DR_TOOL)
    result.add_argument(
        "--ask-become-pass",
        action="store_true",
        help="Allow Ansible to prompt for privilege escalation when a stage installs trusted tools.",
    )
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    promote = commands.add_parser("promote")
    promote.add_argument("--confirm-primary-fenced", required=True)
    promote.add_argument("--confirm-promotion", required=True)
    commands.add_parser("deploy-application")
    for name in ("rebuild-preflight", "rebuild"):
        command = commands.add_parser(name)
        command.add_argument("--confirm-old-primary-fenced", required=True)
        command.add_argument("--confirm-reseed", required=True)
    commands.add_parser("verify")
    return result


def main(arguments: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(arguments)
    try:
        if not args.inventory.is_file():
            raise RunError(f"Inventory does not exist: {args.inventory}")
        run = DrRun(
            args.project_root,
            args.inventory,
            StateStore(args.state),
            args.dr_tool,
            ask_become_pass=args.ask_become_pass,
        )
        if args.command == "status":
            run.show_status()
        elif args.command == "promote":
            run.promote(args.confirm_primary_fenced, args.confirm_promotion)
        elif args.command == "deploy-application":
            run.deploy_application()
        elif args.command == "rebuild-preflight":
            run.rebuild_preflight(
                args.confirm_old_primary_fenced, args.confirm_reseed
            )
        elif args.command == "rebuild":
            run.rebuild(args.confirm_old_primary_fenced, args.confirm_reseed)
        elif args.command == "verify":
            run.verify()
        return 0
    except RunError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
