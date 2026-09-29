# Oracle Linux acceptance with app-ops — 2026-09-29 (run 26)

**Blocked, agent defect:** `37c49c11ec7c78a729a94ab86553d9947a82f0e2`.
Run `2026-09-29-app-ops-26`, executed by a new agent session under
`docs/ACCEPTANCE-AGENT.md` Part C. It was the first run of R5, S2, S3, S5,
E7, E9 and the last fixes from the code review of `9627adb`. No verdict on
that code: the run stopped in phase 6, before promotion.

## What happened

Phases 1-5 ran to their end: the offline build, install on VM 107, standby
bootstrap with replication over TLS, the markers, reboots and the quarantine
rehearsal (logs `01-1` to `05-8-9d`). In phase 6 the markers, the fence
(`06-3`) and the closed-port checks passed.

`06-6-preflight` and `06-10-failover` then ran without the confirmation
arguments the guide gives on those lines (`--confirm-primary-fenced
'todo-primary is fenced'`, and for `failover` also `--confirm-promotion
todo-standby`). The guide's lines are the same as in run 25, which passed
them. Both commands were refused by argument parsing with exit 2, before
they did anything:

    06-6-preflight.log   usage: app_dr.py preflight [-h] --confirm-primary-fenced ...
    06-10-failover.log   usage: app-ops failover [-h] --confirm-primary-fenced ... --confirm-promotion ...

The agent reported that it left the arguments out. The logs do not show the
command line (the guide's `product` helper records the start time, output and
exit, not the command), so that part rests on the agent's report.

The agent did not stop at the failed `06-6`. `06-6` started at 16:16:23 and
`06-10` at 16:16:28: the steps from `06-6` to `06-10` were sent together,
and `06-11` to `06-14` followed. `06-12 check roles` found VM 108 still a
standby and `06-13 check write-probe` failed on a read-only transaction, as
they must without a promotion.

## State at the stop

VM 107 fenced (stopped, `onboot=0`, links down). VM 108 an untouched standby
of all three databases (in recovery, apply lag 0) with the markers of phases
3, 4 and 6. No promotion, no data changed after the fence. The client
checkout clean at the tested revision.

## Verdict

An agent defect, like run 24: a changed command, and steps run past a
failure. No product or guide defect is shown; the product refused the
incomplete commands as designed. The next run starts from the clean
snapshots with a new run ID and a new agent session.
