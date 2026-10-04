# Oracle Linux acceptance with app-ops — 2026-10-04 (run 41)

**Passed, not clean:** `0fe52caed5a68ad3498041e192082992a081af3d`.
Run `2026-10-04-app-ops-41`, a new agent session on another vendor's model,
under `docs/ACCEPTANCE-AGENT.md` Part C, every step through
`acceptance.py step`. The current clean pass stays on `fae11ba`
([record](ACCEPTANCE-fae11ba.md)); `0fe52ca` changed only the acceptance
tooling (`evidence`, the readiness output) and documentation since then.

`acceptance.py report full` built `REPORT.md`: 115 steps, all PASS, none
refused or unfinished; the checkout clean at the same revision at every step;
62 product logs, each with the expected exit and JSON (install `true` then
`false`, bootstrap `true`, `failover` `{"changed": true, "promoted_now":
true}`, `rebuild-standby` `true`, `cluster-status` streaming with 0 lag and the
archive healthy). Every `check monitor ... ok` said `Ready to take over:
offline bundle 0fe52caed5a6 with 7 image archives, all 13 DR secrets`, and
`06-15` failed as required after the fence. Its one item under "Needs
attention" makes it not clean: `logs/05-7-safety-review.log` is not in the
guide. This record rests on `REPORT.md` alone.

The agent stopped twice, each time on a real gap in the guide, and the
operator granted a scoped exception for this run:

1. Before phase 2: the kickoff forbade "any change on the client outside the
   run folder", which the builds and the browser environment need (`dist/`,
   Podman image storage, `todo-backend/.venv`, the Playwright cache). The
   kickoff template names those writes since `553f407`.
2. Before `05-7`: C2 rule 7 forbade changing a firewall rule not created in
   this run, but a snapshot rollback leaves VM 107's three
   `todo-quarantine-*` rules from the run before, and `05-7` and `09-8`
   replace exactly those. The agent read them first into
   `05-7-safety-review.log`, the extra log. Rule 7 names them as its one
   exception since `ab2adc4`.

Final: VM 108 (`.108`) writable primary with application, WAL archive and
both timers, CA
`4D:36:76:17:2C:4E:27:42:8A:68:69:A6:58:12:3C:A0:AF:59:33:5B:45:BB:6B:4D:DF:F9:CA:3D:4F:07:6D:C4`;
VM 107 (`.102`) database-only standby streaming over TLS 1.3 with 0 bytes
apply lag, Proxmox firewall off, `onboot` 0.
