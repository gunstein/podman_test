Project goal:
Build a small Todo demo to evaluate Podman Kube YAML as a shared development
and production workload format against the accepted per-container Quadlet
reference implementation.

Current architecture and workflow:
- docs/ARCHITECTURE.md describes the current design; docs/LEARNING-GUIDE.md
  teaches it. Use docs/MANUAL-DR-QUICKSTART.md and docs/LAB-ACCEPTANCE.md for
  acceptance; a NEW run must not depend on old chat or development history.
- Helm renders workloads at build time. kube/runtime contains the canonical
  rendered YAML and .kube units; targets do not need Helm.
- todo-app groups migration init, FastAPI and nginx. Keycloak and PostgreSQL
  are separate workloads connected through the rootless todo-network.
- Functional DR has been demonstrated with repairs; unchanged-revision OL9
  acceptance remains pending. Preserve legacy transition evidence until it passes.

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
- Deployment: Ansible.
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
- Prove external secrets without plaintext YAML, network DNS, rootless
  SELinux storage, direct-development cleanup, .kube/systemd failure
  semantics and database persistence before completing the migration.
- Preserve the complete fencing, promotion, backup, PITR and standby-rebuild
  safety boundaries.
- Never commit secrets.
- Prefer simple, pedagogical solutions over abstraction.
