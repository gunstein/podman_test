"""Print guest DR commands; never connect, reset, promote or rebuild by itself."""

import argparse
import shlex
from pathlib import Path

from lab_dr_acceptance import LabError, load_config

PHASES = (
    "status", "prepare-quarantine", "promote", "application", "application-repeat",
    "backup", "rebuild-preflight", "rebuild", "cluster-status",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("phase", choices=PHASES)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except LabError as error:
        parser.error(str(error))
    phase = args.phase
    host = config.primary if phase == "prepare-quarantine" else config.standby
    runner = ["python3", "/opt/todo/bin/todo_dr_run.py", "--inventory",
              "ansible/inventory-recovery.ini"]
    playbook = ["ansible-playbook", "--ask-become-pass", "--inventory",
                "ansible/inventory-recovery.ini"]
    commands = {
        "status": runner + ["status"],
        "prepare-quarantine": [
            "ansible-playbook", "--ask-become-pass", "--inventory",
            "ansible/inventory-initial.ini", "ansible/install-quarantine-tool.yml",
            "-e", "todo_quarantine_enable_guest_exec=true",
            "-e", "todo_quarantine_enable_selinux_entrypoint=true",
        ],
        "promote": runner + [
            "promote", "--confirm-primary-fenced", f"{config.primary.hostname} is fenced",
            "--confirm-promotion", config.standby.hostname,
        ],
        "application": runner + ["--ask-become-pass", "deploy-application"],
        "application-repeat": playbook + ["ansible/deploy-promoted-application.yml"],
        "backup": playbook + ["ansible/configure-backup.yml"],
        "cluster-status": playbook + ["ansible/cluster-status.yml"],
    }
    for name in ("rebuild-preflight", "rebuild"):
        commands[name] = runner + [
            "--ask-become-pass", name,
            "--confirm-old-primary-fenced", f"{config.primary.hostname} is fenced",
            "--confirm-reseed", config.primary.hostname,
        ]
    warnings = {
        "prepare-quarantine": "SECURITY OPT-IN: permits Guest Agent execution and unconfined SELinux entrypoint transitions.",
        "promote": "ROLE CHANGE: first independently fence old primary. Never boot it unrestricted afterwards.",
        "rebuild": "DATA DELETION: old primary database is replaced. Require backup/PITR, tested quarantine and preflight. Never retry blindly.",
        "rebuild-preflight": "READ-ONLY: first stop old services inside tested hypervisor quarantine; no reseeding occurs.",
    }
    print("# PRINT ONLY. Review and paste in the ThinkPad terminal; never pipe this output to a shell.")
    print("# Initial role mapping is fixed: primary=old host, standby=promoted host.")
    if phase in warnings:
        print("# " + warnings[phase])
    ssh = ["ssh", "-t", "-o", "StrictHostKeyChecking=yes"]
    if config.guest_identity_file:
        ssh += ["-i", str(config.guest_identity_file)]
    ssh += [f"{config.guest_user}@{host.address}",
            'cd "$HOME/todo-operations" && ' + shlex.join(commands[phase])]
    print(shlex.join(ssh))


if __name__ == "__main__":
    main()
