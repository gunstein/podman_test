Project goal:
Turn the accepted rootless Podman Kube Todo architecture into a small,
reusable platform for web apps: the platform owns databases, identity,
routing, installation and DR; an app is a directory with its own description
and pod template, plus one entry in the platform's configuration. Todo and
Notes (and later Help) are example apps. Rootless Podman, Kubernetes YAML as
the workload format, podman kube play for development and Quadlet in
production stay. Preserve the historical per-container reference in Git for
comparison.

Platform work:
- docs/PLATFORM-PLAN.md is the plan: scope, simplicity rules, contracts and
  phases. Work one phase at a time on feature/platform, in small commits.
- Maintainability for a moderately experienced Python developer overrides
  everything else in the plan. Version 1 supports today's apps and one static
  app, with PostgreSQL as the only storage with DR. Do not build support for
  unknown future service kinds; a new kind may later need bounded core changes.
- After each phase, record the understandability check (plan, section 3).
- tests/test_render_baseline.py pins the rendered output: update it
  (python3 tests/render_baseline.py --update) only for an intended change and
  explain the diff. tests/test_example_app_names.py lists the platform files
  that still name todo or notes; shrink it, never grow it.
- Until a phase changes it, the section below describes what runs today.

Current architecture and workflow:
- docs/ARCHITECTURE.md describes the current design; docs/LEARNING-GUIDE.md
  teaches it. Use docs/ACCEPTANCE.md for
  acceptance; a NEW run must not depend on old chat or development history.
- Jinja2 (deploy/manifests/*.yaml.j2, deploy/quadlet/*.kube.j2) renders workloads
  at build time; offline bundles carry the rendered YAML and .kube units with
  ${TARGET_*} placeholders and bundle.json, and the installer fills those in with
  the standard library (target_render.py), on a single host and on DR primary
  and standby alike. An offline target needs only Python, never Helm or Jinja2
  (DR hosts also need PyYAML); only build mode renders on a host.
- todo-app and notes-app each group migration init, FastAPI and HTTP-only frontend,
  and each has its own PostgreSQL pod; help-app is static (one nginx, no
  database, no login). Each app's pod comes from its own
  examples/<app>/pod.yaml.j2 (checked by pod_contract.py); every .kube unit
  from one template per kind in deploy/quadlet. Shared nginx, Keycloak and
  Keycloak's own keycloak-postgres pod bring the single-host topology to eight
  pods on rootless app-network. App and Platform in
  deploy/installer/app_installer/apps.py own per-app installer names.
  DR/backup/rebuild act on one group of three databases (todo, notes,
  keycloak; Platform.replicated_databases), never on a partial group. The
  Platform comes from bundle.json or the host's record; a build reads it from
  platform.yaml and each app's examples/<app>/app.yaml (platform_file.py).
  There is no list of apps in the code.
  shared-proxy.service owns nginx, which reads its TLS files read-only from
  Podman secrets the installer makes (tls_secrets.py, the worked example of
  files as Podman secrets); the TLS volume platform-nginx-data (tls.py) is kept,
  commented out in shared-proxy.yaml.j2, for going back (settings.NGINX_TLS_STORAGE).
- Legacy runtime/transition files are retired from the active tree; Git
  history and quadlet-reference-v1 preserve them. See PROJECT.md#acceptance
  for the current acceptance verdict, its scope and every historical run
  record; do not restate it here, to avoid this file drifting from that one.

Constraints:
- Example apps: plain HTML, CSS and JavaScript frontends (no Node.js
  framework) and Python FastAPI backends. Other apps may choose otherwise; the
  platform makes no assumption about an app's languages.
- Database: PostgreSQL with separate bootstrap, migration and runtime roles.
- Runtime: rootless Podman.
- Application definition: the Podman-supported subset of Kubernetes YAML,
  treated as a Podman workload format without a Kubernetes cluster or
  portability promise.
- Development lifecycle: direct podman kube play/down.
- Production lifecycle: .kube Quadlet units managed by user systemd.
- Workload boundary: group containers in one pod only when they share a
  lifecycle; connect independent workloads through a user-defined network.
- Deployment: Python installer for single-host, app-ops (deploy/dr, plain SSH)
  for DR/multi-host. Ansible is retired; Git history keeps its playbooks.
- Shared workload installation lives in deploy/installer/app_installer (the
  single-host installer). DR lives in deploy/dr: app-ops on the controller and
  app_dr_host on each host, which reuses app_installer. DR may import the
  installer, never the other way (tests/test_dr_boundary.py). Keep one
  implementation.
- Reverse proxy: nginx.
- Offline delivery: rendered YAML and OCI images in the offline bundle;
  separate operations package contains tools and docs, not image archives.
- Authentication: one shared Keycloak. The example apps use it behind their
  auth.js (todo-frontend/, notes-frontend/) and provider adapter. Other IdPs
  require implementation and integration testing.
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
- Simplicity (docs/PLATFORM-PLAN.md, section 3): an abstraction needs an
  existing need; prefer functions and dataclasses; keep the control flow
  visible (validate, prepare, install, start, check); no dynamic imports,
  plugin discovery, metaclasses or registration by import side effects; one
  model and few layers; some duplication is fine; YAML describes supported
  choices and is not a programming language; small deliveries.
