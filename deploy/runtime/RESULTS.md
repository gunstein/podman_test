# Podman Kube runtime validation status

The current single-host architecture has six pods: `todo-app`, `todo-postgres`,
`notes-app`, `notes-postgres`, `keycloak` and `shared-proxy`. The apps have separate
databases and audiences, sharing one realm, proxy, SAN certificate and network.
DR, backup, promotion and rebuild remain Todo-only; Notes DR is a follow-up.

## Disposable single-host validation, 22 September 2026

A separate Fedora 44 QEMU/KVM VM ran rootless Podman 5.8.1 with SELinux enforcing.
Neither existing `todo-primary` nor `todo-standby` acceptance VM was accessed.
This was a development validation across phased commits, not an unchanged-revision
Oracle Linux/fapolicyd/two-host acceptance verdict.

Verified against the real runtime:

- Todo-only install checkpoints throughout parameterization; unchanged repeats
  preserved container IDs and an existing database marker.
- Six-pod direct dev and Quadlet/server installation; all six exact systemd
  SourcePaths under `~/.config/containers/systemd/todo-kube-runtime/`.
- No-op workload functions and whole-install repeats; no `.volume` units.
- One SAN certificate verified for both hosts; adding the second SAN retained
  the CA, and a subsequent proxy restart retained both CA and leaf.
- Trusted Chromium login to Todo followed by Notes SSO without another password;
  create/delete Todo, create/edit/delete Notes, shared logout, same subject and
  issuer, and rejection of each app's token by the other app.
- Real Notes runtime-role CRUD privileges, refusal of DDL/schema-history access,
  and no Todo/Keycloak roles in the Notes database cluster.
- Normal uninstall preserved both database markers, backup volumes and credentials.
- Seven real OCI archives exported and loaded from a standalone offline tree,
  outside the checkout and without Helm; dev and server repeats were unchanged, with both database markers preserved.
- Todo remained ready with both Notes services stopped and Todo-only DR proxy
  dependencies selected. This checks the shared rename boundary, not Notes DR.

The browser test is `e2e/test_multi_app.py` (opt in with `E2E_MULTI_APP=1`).
It requires a trusted CA and never suppresses TLS errors. Automated repository
coverage includes all six Jinja/Ansible template comparisons for three address
scenarios, Helm rendering, package contents and DR staging with Ansible 2.14
and 2.20. The Notes runtime privilege test also runs against PostgreSQL in CI.

## Historical acceptance

Historical unchanged-revision acceptance passed on 688a0f6; see its
[record](../../docs/ACCEPTANCE-688a0f6.md). The subsequent simplification revision
12c3bef received a full CLEAN PASS on 7 September 2026. The four-pod shared-proxy
revision 9e54cfb received a lighter process-level acceptance; see its
[record](../../docs/ACCEPTANCE-9e54cfb.md). These records retain their original
scope and do not accept the six-pod topology or Notes DR.

Follow [Acceptance](../../docs/ACCEPTANCE.md) for a new full Oracle Linux DR run;
use separate disposable targets for multi-app development experiments.
