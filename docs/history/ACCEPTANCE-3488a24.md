# Oracle Linux acceptance with app-ops — 2026-10-03 (run 33)

**Blocked by a guide defect, not a product failure:**
`3488a24a98e824b6e4976cbe88398dfad3614e8e`. Run `2026-10-03-app-ops-33`, a new
agent session under `docs/ACCEPTANCE-AGENT.md` Part C, every step through
`acceptance.py step`.

Phases 1 and 2 passed (01-1 to 02-6), and phase 3 up to and including 03-12:
install `{"changed": true}`, client trust, browser tests, markers, reboot.
The new single-host backup steps then ran (B1):

- `03-12a do backup-nightly 192.168.0.102` passed: `install.sh` had turned on
  `todo-backup.timer`, and one run took a verified base backup of todo, notes
  and keycloak.
- `03-12b-after-backup` wrote the test row.
- `03-12c-restore` restored all three databases from that backup and printed
  `todo: restored base-20261003T093153Z`, `notes: restored
  base-20261003T093154Z`, `keycloak: restored base-20261003T093155Z` and
  `{"changed": true}`, `exit=0`. `acceptance.py step` nevertheless reported
  `STOP, did not print todo, notes, keycloak restored`.

Cause: the step's comment in the guide read `# → todo, notes, keycloak
restored; {"changed": true}`. `acceptance.py` takes what follows `→` up to
`;` as the expectation, either JSON fields or one whole output line, so it
looked for the line "todo, notes, keycloak restored", which the command never
prints. The guide line was wrong, not the restore. The agent stopped as the
rules require, changed nothing and asked; the restore is a one-shot step
that is never repeated, so the run cannot continue and was ended.

Fixed in the next revision: the comment reads `# → {"changed": true}; ...`,
and `tests/test_acceptance_step.py` now requires every expectation in the
guide to be JSON or one word, so a description there fails CI. The run did
not reach 03-12d (the row is gone), DR, failover, PITR or rebuild; none of
M4, Q5/Q6 or the DR backup change is accepted by it. Recorded from the
agent's report.
