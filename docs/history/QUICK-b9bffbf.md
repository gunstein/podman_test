# Quick acceptance on one VM — 2026-09-27 (quick run 1)

**QUICK PASS:** `b9bffbfd175509936f61994a1ba087e464e476f7`.
Run `2026-09-27-quick-1`, executed by an agent under
[ACCEPTANCE-QUICK.md](../ACCEPTANCE-QUICK.md), the first run with
`deploy/scripts/acceptance.py`. The checkout stayed clean; no source was
changed, nothing was committed or pushed, and no step ran twice. From the
rollback to the last browser test it took nine minutes (08:22 to 08:31).

A quick pass covers single-host behaviour only. It is not a CLEAN PASS of the
DR design.

The operator's reviewer read `record.jsonl`: 14 entries, all `PASS`, no
`REFUSED`, no `--operator-approved`.

| Step | Evidence (`record.jsonl`) |
|---|---|
| 01-1 `do rollback` | VM 107 at `clean-agent`, VM firewall off, `net0` up, `onboot` 0 |
| 01-2 `check clean-host` | Machine ID `3c60cb3c2d5849fdbb47fd09679cb41c`, no Todo state |
| 03-3 `do firewall-https` | HTTPS rule for `.100` in the running and the permanent configuration |
| 03-4 `check services` | `wait-ready.sh app` READY, no failed units, `nginx -t` |
| 04-1 `check ca` | CA `73:B2:74:46:…:6E:D6`; `curl` without `-k` works |
| 04-2 `check headers` | HSTS, `connect-src 'self' https://todo.test:8443`, `frame-ancestors 'none'`, no `unsafe-*`, `DENY`, `strict-origin-when-cross-origin` |
| 05-3 `check browser` | Todo, Notes and SSO passed, none skipped |
| 05-4 `do markers` | Todo ID 3, Notes ID 3 |
| 05-5 `check markers` | Both markers on `.102` |
| 06-1 `do reboot` | Boot `cc6f4147-…` to `13b56e0f-…`, then READY, no failed units, `nginx -t` |
| 06-2 `check ca` | Same CA as step 04-1 |
| 06-3 `check markers` | Both markers after the reboot |
| 06-5 `check services` | READY after the second install |
| 06-6 `check browser` | Todo, Notes and SSO passed, none skipped |

The product's own commands, reported by the agent from their logs: build,
transfer and verification `exit=0` with `source_state=clean`; first
`install.sh` `{"changed": true}`, second `{"changed": false}`, both `exit=0`.

Expected deviations: `python3-jinja2` installed with `dnf` (documented
prerequisite); the operator set the client's `/etc/hosts` and CA trust
(`CLIENT_SUDO: no`); the fapolicyd recipe's `update` errors before `add`.
