# Stable Podman Kube database name contract results

## Oracle Linux gate — passed

The isolated contract was tested on Oracle Linux 9 with rootless Podman 5.8.2.
The generated service command contained `podman kube play --no-pod-prefix`.
The service became active and both the pod and application container resolved
by the exact name `todo-kube-name-contract`. `podman inspect` reported the
container healthy, `podman exec` worked through the stable name, and the
process ran as UID/GID 1000.

A normal systemd stop removed both Podman objects. The Quadlet directory and
generated unit were then removed, while all four accepted Todo services stayed
active and application readiness passed.

The first draft embedded a multiline Python process in the YAML. Libmagic
classified the complete manifest as `Python script, ASCII text executable`, so
fapolicyd denied even reading or overwriting the untrusted file. Replacing the
embedded program with declarative `sleep` and `test` arguments made the same
manifest plain `ASCII text`; it then installed and ran without adding a trust
exception. Production manifests must avoid content that causes a data file to
be classified as executable source.

This gate proves that a future database `.kube` unit can preserve all three
existing operational names:

```text
todo-postgres.service
todo-postgres pod
todo-postgres container
```

Existing DR, backup and Ansible commands can therefore continue to target
`podman exec todo-postgres` while the workload definition moves to Kube YAML.
The design now requires a Podman release supporting `--no-pod-prefix`; the
accepted platform is pinned to the tested Podman 5.8.2 release.
