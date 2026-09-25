# Single-host multi-app verification before DR generalization

This is a disposable-host functional verification, separate from the historical
Oracle Linux two-host acceptance records. The existing `todo-primary` and
`todo-standby` acceptance VMs were not accessed or modified.

## Target and scope

On 23 September 2026, the dedicated `notes-disposable` QEMU/KVM VM ran Fedora 44,
rootless Podman 5.8.1 (with `--no-pod-prefix`), user systemd and SELinux enforcing.
Both hostnames resolved to loopback inside that VM. Its Chromium browser trusted
the exported public nginx CA; HTTPS errors were not ignored. Application images
and eight rendered manifests came from the phase-1 OCI export, loaded through
the real offline installer without Helm on the target install path.

The source baseline was `e6eecd9`; the run added reverse-direction SSO coverage
and repaired the dev uninstall defect described below. It is therefore a
**repaired functional verification**, not a clean unchanged-revision full DR
acceptance verdict. No replication, promotion or rebuild is accepted by this run.

## Checks

For each lifecycle owner, `server` and `dev`, the verification procedure:

1. Removes this disposable host's app data and provisions fresh random bootstrap
   credentials in Podman memory/stdin, without recording secret values.
2. Installs both apps and records a separate persistent marker in each database.
3. Uses the real browser test in `e2e/test_multi_app.py`: Todo login → Notes SSO,
   then a fresh browser context with Notes login → Todo SSO. Both frontends,
   CRUD, shared logout, common issuer/subject and independent token audiences
   are checked. Each hostname passes `openssl verify -verify_hostname`; both
   serve the same SAN leaf certificate.
4. Repeats `install()` and requires `False`, unchanged container IDs and readiness.
5. Runs default uninstall, requires both data/backup volumes and all credentials
   to remain, reinstalls, then reads both original database markers.
6. Runs `uninstall(remove_data=True)` and compares volume/secret inventories:
   only app data/TLS volumes and installer-owned secrets may disappear. Backup
   volumes remain, as required by the pre-existing uninstall contract.
7. Checks separately created unrelated resources survive. The additional
   unrelated container retains its ID and running state; its separate network,
   volume and secret also remain.

The private execution evidence is under `/tmp/notes-app-vm/` on the development
machine: `phase1-server-20260923.log`, `phase1-dev-20260923.log`,
`phase1-dev-fixed-20260923.log`, `phase1-server-fixed-20260923.log` and
`phase1-unrelated-control.log`. Those transient files are observations, not
inputs required by the installer or future acceptance runs.

## Defect found by live verification

The original default dev uninstall removed named workload containers but left
Podman's pod infra containers. Removing `app-network` then failed with exit 2.
The original failing output is retained in `phase1-dev-20260923.log`.

Uninstall now removes only the six registered workload pods before removing the
network. This does not request volume deletion. Explicit data removal remains
a separate step, and the installer removes its obsolete dev state fingerprint.
The regression test checks the exact pod set, ordering before network removal,
absence of volume-removal options and preservation of unrelated state files.
The full repository suite passed 137 tests after the fix.

Both repaired lifecycle runs completed with `PASS dev` and `PASS server`.
The unrelated container kept ID
`c6d92009992f2ef978431d5454f9ad55de551bb65c335712b9e1cf8d209095bc`
and remained running after both final uninstall checks. This completes the
single-host prerequisite for beginning the separately phased DR work.
