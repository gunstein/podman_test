# Two-agent acceptance — 2026-09-08

**Process PASS, evidence-light:** `9e54cfb800f99a7a5a93b87a014421b8966093cb`.
Two independent agents each ran the full acceptance sequence from
`docs/ACCEPTANCE.md` against real lab VMs on the same day, one after the
other. Unlike [688a0f6](ACCEPTANCE-688a0f6.md) and
[12c3bef](ACCEPTANCE-12c3bef.md), no ok/changed counts, marker IDs, LSNs or
CA fingerprints were captured during either run. This record documents the
process and its outcome, not phase-by-phase evidence; it must not be cited
as an evidence-grade pass.

## Run 1: Claude Code agent

Ran the full sequence, including the destructive standby-rebuild phase,
against the revision that started the day at `8f8b25f`. The run itself
found and fixed real defects left behind by an earlier, incomplete
shared-proxy extraction:

- `8f8b25f` — the prior split of Nginx into its own `shared-proxy` Pod was
  non-functional: the Ansible role never installed `shared-proxy.yaml`,
  `promoted_application` checked the wrong image for the proxy label (would
  fail on every failover), the proxy service/image was never loaded or
  started during application recovery, TLS CA was read from the wrong
  container, and the Helm chart requested a PVC with no matching volume
  Quadlet.
- `6b167b1` — `postgres_backup`, `postgres_primary` and
  `postgres_redundancy_primary` still stopped/started `todo-app.service`
  around PostgreSQL restarts instead of the new `shared-proxy.service`,
  leaving the proxy tier out of the restart cycle after backups and
  primary restarts.

Near the end of the run, during the destructive standby-rebuild phase, the
agent itself made an operational mistake attributed to missing or stale
sudo credentials on one of the hosts (exact symptom not retained). No
source defect was found for this; the agent's own procedure was at fault.
The fix, `9e54cfb`, adds an explicit root-access verification gate
(`any_errors_fatal`, checked on both the controller and the rebuild host)
to `ansible/rebuild-standby.yml` before any destructive change, plus a
regression test (`tests/test_rebuild_privileges.py`) that exercises the
real gate against simulated sudo denial on either side.

## Run 2: Google Antigravity agent

Ran an independent full acceptance sequence against the resulting revision
(`9e54cfb`). Reported a clean pass with no defects found and no source
changes needed. No transcript, evidence table, or final VM topology was
retained from this run.

## Scope and limits of this record

- No phase-by-phase ok/changed counts, no marker IDs, no replication LSNs,
  no CA fingerprints, no final VM role/boot-ID topology were captured for
  either run. Do not treat the final topology from
  [12c3bef](ACCEPTANCE-12c3bef.md) as current: a destructive standby
  rebuild was exercised again during Run 1, so recorded topology from the
  prior run is stale. Verify current primary/standby roles freshly before
  any operation.
- What this record does establish: two independently-operated coding
  agents each drove the complete acceptance procedure, including its
  destructive phases, against `9e54cfb` or its immediate predecessor
  without requiring further source fixes on the second pass. The defects
  found and fixed in Run 1 are captured in the commits above, not
  reconstructed here from memory.
- If a fully-evidenced pass is needed as a new baseline (the way
  [688a0f6](ACCEPTANCE-688a0f6.md) and [12c3bef](ACCEPTANCE-12c3bef.md)
  are), run the sequence again and capture ok/changed counts, markers,
  LSNs and CA fingerprints per phase as `docs/ACCEPTANCE.md` describes.

Later runtime changes require their own validation; this result applies to
the exact tested revision `9e54cfb800f99a7a5a93b87a014421b8966093cb`.
