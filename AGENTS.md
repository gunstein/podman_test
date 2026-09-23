Project goal:
Maintain and demonstrate the accepted rootless Podman Kube Todo architecture
as a shared development and production workload format. Preserve the historical
per-container reference in Git for comparison.

Current architecture and workflow:
- docs/ARCHITECTURE.md describes the current design; docs/LEARNING-GUIDE.md
  teaches it. Use docs/ACCEPTANCE.md for
  acceptance; a NEW run must not depend on old chat or development history.
- Jinja2 (deploy/manifests/*.yaml.j2) renders workloads at build time; bundles
  contain canonical rendered YAML. The Python installer renders target .kube
  units from deploy/quadlet templates; targets need only Python, never Helm.
- todo-app and notes-app each group migration init, FastAPI and HTTP-only frontend.
  Each app has its own PostgreSQL pod. Shared nginx and Keycloak bring the
  single-host topology to six pods on rootless app-network. The App registry in
  deploy/installer/todo_installer/apps.py owns per-app installer names.
  DR/backup/rebuild remain Todo-only; Notes DR is a separate follow-up phase.
  shared-proxy.service owns nginx and persistent TLS volume todo-nginx-data.
- Four-pod shared-proxy architecture passed a two-agent acceptance pass on
  9e54cfb; see docs/ACCEPTANCE-9e54cfb.md (process-level, not evidence-grade:
  no phase-by-phase counts, markers or topology were captured). The prior
  three-pod architecture's evidence-grade acceptance remains
  docs/ACCEPTANCE-688a0f6.md and docs/ACCEPTANCE-12c3bef.md. Legacy
  runtime/transition files are retired from the active tree; Git history and
  quadlet-reference-v1 preserve them.

Constraints:
- Frontend: plain HTML, CSS and JavaScript. No Node.js framework.
- Backend: Python with FastAPI.
- Database: PostgreSQL with separate bootstrap, migration and runtime roles.
- Runtime: rootless Podman.
- Application definition: the Podman-supported subset of Kubernetes YAML,
  treated as a Podman workload format without a Kubernetes cluster or
  portability promise.
- Development lifecycle: direct podman kube play/down.
- Production lifecycle: .kube Quadlet units managed by user systemd.
- Workload boundary: group containers in one pod only when they share a
  lifecycle; connect independent workloads through a user-defined network.
- Deployment: Python installer for single-host, Ansible for DR/multi-host.
- Shared workload installation lives in deploy/installer/todo_installer; DR calls
  it through deploy/ansible/tasks/install-workload.yml. Keep one implementation.
- Reverse proxy: nginx.
- Offline delivery: rendered YAML and OCI images in the offline bundle;
  separate operations package contains tools/playbooks, not image archives.
- Authentication: Keycloak is implemented behind frontend/auth.js and its
  provider adapter. Other IdPs require implementation and integration testing.
- Keep Bash scripts small and simple.
- Do not add Kubernetes orchestration, Docker Engine, Docker Compose,
  podman-compose or unnecessary dependencies.
- Keep the accepted per-container Quadlet implementation recoverable through
  the quadlet-reference-v1 tag; do not rewrite that history.
- Preserve and verify external secrets without plaintext YAML, network DNS, rootless
  SELinux storage, direct-development cleanup, .deploy/systemd failure
  semantics and database persistence when changing the accepted architecture.
- Preserve the complete fencing, promotion, backup, PITR and standby-rebuild
  safety boundaries.
- Never commit secrets.
- Prefer simple, pedagogical solutions over abstraction.
