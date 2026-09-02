Project goal:
Build a small Todo demo to evaluate rootless Podman Compose, Ansible and offline installation against the accepted Quadlet reference implementation.

Constraints:
- Frontend: plain HTML, CSS and JavaScript. No Node.js framework.
- Backend: Python with FastAPI.
- Database: PostgreSQL, added later.
- Runtime: rootless Podman.
- Application definition: Compose Specification, run only with rootless Podman.
- Compose provider: explicitly selected and pinned podman-compose. Do not depend on Docker or an auto-selected provider.
- Service management: one small user systemd service owns the complete Compose stack at boot.
- Deployment: Ansible.
- Reverse proxy: nginx.
- Offline install must eventually work from a tar.gz bundle.
- Authentication with Keycloak will be added last.
- Keep Bash scripts small and simple.
- Do not add Kubernetes, Docker Engine, Docker Compose or unnecessary dependencies.
- Keep the accepted Quadlet implementation recoverable through the quadlet-reference-v1 tag; do not rewrite that history.
- Prove provider, secrets, health dependencies, SELinux, reboot and database-only/full-stack modes before completing the migration.
- Never commit secrets.
- Prefer simple, pedagogical solutions over abstraction.
