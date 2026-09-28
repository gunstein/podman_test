# deploy/scripts

| Where | What | Runs on |
|---|---|---|
| here | `render-kube-runtime.sh`, `wait-ready.sh`, `trust-files.sh`, `build-operations-package.sh`: used by the installer, app-ops and the build | build machine and hosts |
| here, for now | `app_dr.py`, `app_backup.py`, `app-quarantine.sh`, `bootstrap-ssh-key.sh`: DR tools; they move to `deploy/dr/` (backlog S1, step 2) | hosts |
| `dev/` | `dev-up.sh`, `dev-down.sh`, `run-e2e.sh`, `smoke-proxy.sh`, `run-mutation-tests.sh`: development and CI | a developer machine or CI |
| `lab/` | `acceptance.py`, `acceptance_preflight.py`, `pve_lab.py`, `ports-closed.sh`, `prepare-agent-snapshots.sh`, `trust-serving-ca.sh`: the acceptance lab | the lab client only, never shipped to a host |
