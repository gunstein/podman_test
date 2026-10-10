# deploy/scripts

| Where | What | Runs on |
|---|---|---|
| here | `render-kube-runtime.sh`, `wait-ready.sh`, `trust-files.sh`, `build-operations-package.sh`: used by the installer, app-ops and the build | build machine and hosts |
| here | `app_ca.py`: the CA that signs nginx's certificates in provided mode, and `platform-ca-sign`, its narrow sudo wrapper for v1 ([TLS.md](../../docs/TLS.md#provided-mode-a-separate-ca-process)) | the host itself, from CA storage outside Podman (or any other machine) |
| `dev/` | `dev-up.sh`, `dev-down.sh`, `run-e2e.sh`, `smoke-proxy.sh`, `run-mutation-tests.sh`, `check_failover_login_page.py`, `spike_keycloak_pfx.py` (BACKLOG X1's Keycloak spike): development and CI | a developer machine or CI |
| `lab/` | `acceptance.py`, `acceptance_preflight.py`, `pve_lab.py`, `ports-closed.sh`, `prepare-agent-snapshots.sh`, `trust-serving-ca.sh`: the acceptance lab | the lab client only, never shipped to a host |
