Project goal:
Build a small Todo demo to evaluate Podman Kube YAML as a shared development
and production workload format against the accepted per-container Quadlet
reference implementation.

Constraints:
- Frontend: plain HTML, CSS and JavaScript. No Node.js framework.
- Backend: Python with FastAPI.
- Database: PostgreSQL, added later.
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
- Offline install must eventually work from a tar.gz bundle.
- Authentication with Keycloak will be added last.
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
