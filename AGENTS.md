Project goal:
Build a small Todo demo to demonstrate Podman, Quadlet, Ansible and offline installation.

Constraints:
- Frontend: plain HTML, CSS and JavaScript. No Node.js framework.
- Backend: Python with FastAPI.
- Database: PostgreSQL, added later.
- Runtime: rootless Podman.
- Service management: Quadlet/systemd.
- Deployment: Ansible.
- Reverse proxy: Caddy.
- Offline install must eventually work from a tar.gz bundle.
- Authentication with Keycloak will be added last.
- Keep Bash scripts small and simple.
- Do not add Kubernetes, Docker Compose or unnecessary dependencies.
- Never commit secrets.
- Prefer simple, pedagogical solutions over abstraction.
