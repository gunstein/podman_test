# Quick acceptance on one VM

A 20-minute run for changes that cannot affect DR: frontend, nginx headers,
Keycloak settings, documentation. It installs on VM 107 from a clean
snapshot, checks the application through a real browser, reboots and
installs again. It never touches VM 108.

| The change touches | Run |
|---|---|
| Frontend, backend code, nginx configuration, Keycloak realm settings, docs | This quick run |
| Installer, Quadlet templates, Kube YAML structure, replication, app-ops, backup, DR tools | The full run ([ACCEPTANCE-AGENT.md](ACCEPTANCE-AGENT.md)) |

The verdict is `QUICK PASS` or `QUICK FAIL`. A quick pass accepts the change
for single-host behaviour only; it is not a CLEAN PASS of the DR design.

The run uses `deploy/scripts/lab/acceptance.py` for everything around the
product's own commands. Each call writes its own log and one line in
`record.jsonl`, compares the result itself and prints `RESULT: PASS`,
`FAIL` or `REFUSED`. The product's commands (`install.sh`, the fapolicyd
recipe) run as written below, with their exit status written into their log.

## Kickoff message (fill in and paste to the agent)

```text
Run the quick acceptance in docs/ACCEPTANCE-QUICK.md exactly, step by step.

Revision to test: <full 40-char commit SHA on feature/podman-kube>
CI on that revision: <green | red>
Run ID: <e.g. 2026-09-27-quick-1>
VM: todo-primary, 192.168.0.102, VMID 107, clean snapshot clean-agent
Client/build host source IPv4 as seen by the VM: 192.168.0.100
Service user: gunstein
CLIENT_SUDO: <yes only if 'sudo -n true' works on the client | no>

Pre-approved: roll VM 107 back to the snapshot above, install on it and
reboot it; store the generated testuser password in a 0600 tmpfs file.
Not approved: anything on VM 108, any other destructive action, any source
change, commit or push.
```

## Rules

- Run every command below as written, in order, from the repository root on
  the client. Do not write your own commands around them.
- `acceptance.py` prints `RESULT: FAIL` or `RESULT: REFUSED`: STOP, and report
  the step, its log and the current state. Do not run a `do` step again; the
  tool refuses it anyway unless the operator approves with
  `--operator-approved "<why>"`. A `check` may be repeated.
- A product command whose log ends in `exit=` other than `0`, or whose JSON is
  not the one given: STOP.
- Never print a secret. The testuser password lives only in the tmpfs file.
- Do not commit or push. Leave everything in the run folder.

## Steps

Set the run once, in every shell you use:

```bash
RUN_ID=<run ID from the kickoff>
RUN=~/todo-acceptance-runs/$RUN_ID
A="python3 deploy/scripts/lab/acceptance.py --run $RUN_ID"
mkdir -p "$RUN/logs"
```

**0. Checkout.** `git status --porcelain` must be empty and `git rev-parse HEAD`
must equal the kickoff revision:

```bash
{ git status --porcelain; git rev-parse HEAD; } > "$RUN/logs/00-1-git.log" 2>&1; echo "exit=$?" >> "$RUN/logs/00-1-git.log"
```

**1. Clean VM.**

```bash
$A --step 01-1 do rollback 107 clean-agent 192.168.0.102
$A --step 01-2 check clean-host 192.168.0.102
ssh gunstein@192.168.0.102 'sudo -n dnf install -y python3-jinja2 python3-pyyaml' > "$RUN/logs/01-3-prerequisites.log" 2>&1; echo "exit=$?" >> "$RUN/logs/01-3-prerequisites.log"
```

Installing `python3-jinja2` is a documented target prerequisite
(`deploy/offline/README.md`), not a source change; record it as an expected
deviation.

**2. Build and stage.**

```bash
deploy/offline/build-bundle.sh > "$RUN/logs/02-1-build.log" 2>&1; echo "exit=$?" >> "$RUN/logs/02-1-build.log"
scp dist/todo-offline-m12.tar.gz dist/todo-offline-m12.tar.gz.sha256 gunstein@192.168.0.102: > "$RUN/logs/02-2-transfer.log" 2>&1; echo "exit=$?" >> "$RUN/logs/02-2-transfer.log"
ssh gunstein@192.168.0.102 'sha256sum -c todo-offline-m12.tar.gz.sha256 && tar -xzf todo-offline-m12.tar.gz && cd todo-offline-m12 && sha256sum --quiet -c SHA256SUMS && cat VERSION' > "$RUN/logs/02-3-verify.log" 2>&1; echo "exit=$?" >> "$RUN/logs/02-3-verify.log"
```

`VERSION` must show the kickoff revision and `source_state=clean`.

**3. Install.** The fapolicyd recipe from `deploy/offline/README.md`, then
the installer. Require `{"changed": true}`:

```bash
ssh gunstein@192.168.0.102 'cd ~/todo-offline-m12 && for source in "$PWD"/deploy/installer/app_installer/*.py; do source=$(realpath "$source"); sudo -n fapolicyd-cli --file update "$source" --trust-file app-installer || sudo -n fapolicyd-cli --file add "$source" --trust-file app-installer; done && sudo -n fapolicyd-cli --update' > "$RUN/logs/03-1-trust.log" 2>&1; echo "exit=$?" >> "$RUN/logs/03-1-trust.log"
ssh gunstein@192.168.0.102 'cd ~/todo-offline-m12 && sh ./install.sh --publish-address 192.168.0.102' > "$RUN/logs/03-2-install.log" 2>&1; echo "exit=$?" >> "$RUN/logs/03-2-install.log"
$A --step 03-3 do firewall-https 192.168.0.102 192.168.0.100
$A --step 03-4 check services 192.168.0.102 app
```

The trust log shows `update` errors for files not yet trusted, followed by a
successful `add`; that is expected.

**4. Client.** Name resolution and CA trust as
[ACCEPTANCE-AGENT.md](ACCEPTANCE-AGENT.md) C9.4 describes for phase 3
(`IP=192.168.0.102`; with `CLIENT_SUDO: no` the operator runs it). Then:

```bash
$A --step 04-1 check ca 192.168.0.102
$A --step 04-2 check headers
```

**5. Browser.** Generate the testuser password (C6), provision the user
(C9.3 step 6), then:

```bash
$A --step 05-3 check browser
$A --step 05-4 do markers phase3
$A --step 05-5 check markers 192.168.0.102
```

**6. Reboot and repeat install.** Require `{"changed": false}` from the
second install:

```bash
$A --step 06-1 do reboot 107 192.168.0.102 app
$A --step 06-2 check ca 192.168.0.102
$A --step 06-3 check markers 192.168.0.102
ssh gunstein@192.168.0.102 'cd ~/todo-offline-m12 && sh ./install.sh --publish-address 192.168.0.102' > "$RUN/logs/06-4-install.log" 2>&1; echo "exit=$?" >> "$RUN/logs/06-4-install.log"
$A --step 06-5 check services 192.168.0.102 app
$A --step 06-6 check browser
```

`check ca` fails by itself if the CA changed since step 4.

**7. Verdict.** Delete the password file and build the report:

```bash
rm -f "$XDG_RUNTIME_DIR/todo-acceptance/e2e-password"
$A report quick
```

`report` writes `REPORT.md` in the run folder from `record.jsonl` and the
other logs; nothing is copied by hand. `QUICK PASS` only if it says
**ALL STEPS PASS**, lists nothing under "Needs attention", and its table of
other logs shows `{"changed": true}` for `03-2-install.log` and
`{"changed": false}` for `06-4-install.log`. List every expected deviation.
Report the verdict, the revision and the run folder; send the operator
`REPORT.md`.
