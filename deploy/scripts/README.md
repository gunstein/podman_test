# deploy/scripts

| Where | What | Runs on |
|---|---|---|
| here | `render-kube-runtime.sh`, `wait-ready.sh`, `trust-files.sh`, `build-operations-package.sh`: used by the installer, app-ops and the build | build machine and hosts |
| here | `app_ca.py`: the offline CA that signs nginx's certificates in provided mode ([TLS.md](../../docs/TLS.md#provided-mode-the-organisations-own-ca)) | an administrator's machine, never a host |
| `dev/` | `dev-up.sh`, `dev-down.sh`, `run-e2e.sh`, `smoke-proxy.sh`, `run-mutation-tests.sh`, `check_failover_login_page.py`: development and CI | a developer machine or CI |
| `lab/` | `acceptance.py`, `acceptance_preflight.py`, `pve_lab.py`, `ports-closed.sh`, `prepare-agent-snapshots.sh`, `trust-serving-ca.sh`: the acceptance lab | the lab client only, never shipped to a host |
