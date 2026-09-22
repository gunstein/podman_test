# Deployment sources

This directory contains the build-time workload definitions and installation
and operations tooling for the rootless Podman demo. The four workloads remain
`todo-app`, `keycloak`, `todo-postgres` and `shared-proxy`.

| Path | Responsibility |
|---|---|
| `charts/todo/`, `charts/shared-proxy/` | Helm workload templates and chart defaults |
| `environments/local/values.yaml`, `environments/prod/values.yaml` | Non-secret workload overrides, shared by both charts |
| `quadlet/` | One source for the network and four systemd workload templates |
| `ansible/inventories/` | Local installation and initial/recovery DR topologies |
| `ansible/inventories/*/group_vars/` | Host/account/operations settings for those inventories |
| `installer/` | Single-host Python installer and shared workload functions |
| `ansible/roles/`, `ansible/playbooks/` | Security integration and guarded DR/backup operations; single-host compatibility wrappers |
| `scripts/` | Rendering, direct development and operational tools |
| `offline/` | OCI bundle builder, installer and offline requirements |
| `runtime/` | Runtime documentation, not generated manifests |

Run the following commands from the repository root. The root `ansible.cfg`
sets the role search path, interpreter and SSH pipelining. Packages retain this
layout and include the same configuration; run playbooks from the package root.

```bash
# Render production YAML without starting anything.
deploy/scripts/render-kube-runtime.sh

# Development uses local values and direct podman kube play/down.
# Missing administrator secrets require an interactive terminal.
deploy/scripts/dev-up.sh
deploy/scripts/dev-down.sh

# Production installation uses Quadlet/user-systemd.
PYTHONPATH=deploy/installer python3 -m todo_installer install --mode server
```

Workload settings belong in Helm values; host and operational settings belong
in inventory/group_vars. Chart defaults stay inside each chart. Secrets stay
outside YAML and Git. Python copies rendered YAML without templating it a
second time; only the existing `.kube.j2` host-integration files use Jinja2.
The package needs Python 3.9+ and Jinja2. See [installer usage](installer/README.md).
DR keeps Ansible for remote transport, fencing, replication and backup, calling
the same Python workload functions on each target.

Rendering defaults to `generated/kube-runtime/` for production and
`generated/dev/` for development. These ignored build outputs are separate from
source templates. Both delivery packages contain rendered YAML and the shared
Quadlet templates. Only the offline bundle contains OCI image archives. Rebuild
and distribute both packages together after this layout change: older bundles
with YAML under `kube/runtime/` do not match these playbooks.

For DR, copy `inventories/initial/hosts.example.ini` or
`inventories/recovery/hosts.example.ini` within `deploy/ansible/` to `hosts.ini`
in the same directory and set the real host addresses. Review the adjacent
`group_vars` as well. The local inventory is a single-host installation target;
it is not a replacement for the role-aware DR inventories. Existing private
inventories from the old layout can be copied to these new ignored locations;
keep their host identities and group names. Local virtual environments are not
moved by the source refactor; recreate `deploy/ansible/.venv` as described in the
Ansible guide if needed.

See the [runtime guide](runtime/README.md), [Ansible operations](ansible/README.md),
[offline delivery](offline/README.md), [architecture](../docs/ARCHITECTURE.md)
and [acceptance procedure](../docs/ACCEPTANCE.md).
Historical per-container definitions remain in `quadlet-reference-v1`; earlier
acceptance records describe the source layout at their own revisions.
