# Oracle Linux acceptance with app-ops — 2026-09-28 (run 25)

**Clean pass:** `24b32ee647a52affbebf02cec3145c77f9f9add4`.
Run `2026-09-28-app-ops-25`, executed by a new agent session under
`docs/ACCEPTANCE-AGENT.md` Part C, the command list run through
`deploy/scripts/lab/acceptance.py`. It accepts R1-R3 from the code review of
`9627adb`:

- R1: `render()` replaces the whole output directory; a Kube secret that
  differs from its Podman secret is refused (the second install, the standby
  bootstrap, `failover`'s deploy step, the deploy repeat and the rebuild all
  went through the check); a failed PITR cleanup keeps the original error;
  `values.yaml` is checked; every `App` is built with keyword arguments.
- R2: `REPLICATED_DATABASES` holds only `Database`s and `describe()` is gone;
  the DR secret copy, every app-ops loop over the group, PITR and the reseed
  ran through the renamed code.
- R3: `app_dr.py` and `app_backup.py` found both packages only in
  `/opt/todo/lib`.

It is also the first run with the guide's fixed lines that read the PITR
backup names from `08-4` (run 23 stopped there, see below).

The agent reported from `acceptance.py report full`: 104 steps, all PASS,
none refused or unfinished; 57 product logs; the checkout clean at the same
revision at every step; ALL STEPS PASS and "Needs attention: Nothing". The
operator's reviewer read `00-readiness`, `06-10`, `08-9`, `08-10` and
`11-4`, but not the full `REPORT.md` tables, so this record gives no per-phase
table.

| Log | What it showed |
|---|---|
| `00-readiness` | `READY for the agent run.`, `exit=0` |
| `06-10-failover` | Started 22:13:53, ran once: promote, deploy, backup, services, login-page and users each done; `{"changed": true, "promoted_now": true}`, address `192.168.0.108`, CA `73:7E:01:EE:…`; `next` says a browser confirms the login; `exit=0` |
| `08-9-restore-todo`, `08-10-restore-notes` | `PITR paused at acceptance_before_after. Live database was not modified.`; `recovery\|paused\|read_only = t\|t\|on`; network `none`; the restored view held only row 42, the live view 42 and 43; `exit=0` |
| `11-4-restarts` | `NRestarts` 0, 0, 0; `exit=0` |

Final: VM 108 (`.108`, todo-standby) writable primary with application and
backup, boot `5194ff67-b13c-4409-88cb-78cad3e56da4`, CA `73:7E:01:EE:…`;
VM 107 (`.102`, todo-primary) database-only standby streaming over TLS 1.3
with 0 bytes apply lag, boot `7210eb4f-d44a-4a58-a675-e3798a9816fc`. Markers
3, 4, 5, 41, 42, 43 and 44 in Todo and Notes on both.

## Deviations

Expected (C7), which do not change the verdict: `python3-jinja2` and
`python3-pyyaml` installed on both VMs (documented prerequisites); the first
C9.13 sudo refusal check skipped; the operator ran the client trust scripts
(`CLIENT_SUDO: no`); the A3 lab sudoers file, a Proxmox API token instead of
the node Shell, and the testuser password in a tmpfs file.

## Runs 23 and 24 on the way

Recorded from the agents' own reports; their logs were not reviewed.

- **Run 23**, `68eece4`: stopped in phase 8. Phases 1-7 and the first half of
  phase 8 passed (72 tool steps, all PASS), with the same R1-R3 code as run
  25 apart from the guide. The guide asked the agent to put the backup names
  from `08-4` into `08-9` and `08-10` by hand; the agent filled in an empty
  value, and `app_backup.py` refused the command before any restore
  (`argument --backup: expected one argument`, exit 2). It then went on
  through `08-15` instead of stopping at once. A guide defect, no product
  defect. Fixed in `24b32ee`: two fixed lines read the names from the `08-4`
  log, and a test refuses any placeholder in a command line.
- **Run 24**, `24b32ee`: stopped in phases 1-3. The agent did not run the
  guide's commands as written: it replaced `01-7`/`01-8`
  (`dnf install python3-jinja2 python3-pyyaml`) with an import check, which
  fails on the clean snapshot, and `03-1` failed on a changed path. It went
  on past the first failure, and before the run it stopped the operator's
  own services on the client and started VM 107. The same agent session had
  run run 23. An agent defect; no product or guide change. Run 25 used a new
  agent session, and the kickoff now says to run every command character for
  character, to stop at the first failed product command, and not to change
  anything on the client outside the run folder.
