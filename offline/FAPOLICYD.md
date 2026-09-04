# fapolicyd on Oracle Linux

This project keeps `fapolicyd` enabled. The tested Oracle Linux 9 policy may
deny direct execution or reading of newly extracted, non-RPM files even when
normal Unix permissions and SELinux labels are correct. A denial normally
appears as `Operation not permitted`.

Do not disable `fapolicyd` to install or operate this demo. Trust only the
verified files that must be interpreted as code.

## Why the project uses two approaches

The M12 offline installer uses only RPM-managed Python and Ansible. Run the
newly extracted shell scripts through the trusted system shell:

```bash
sh ./preflight.sh
sh ./install.sh
```

The package-level `ansible.cfg` enables Ansible pipelining for every local and
SSH connection. This avoids transferring and executing temporary Python
modules under `~/.ansible/tmp`, so the M12 bundle needs no custom trust entries.

The DR and backup workflows add project-owned Python tools. Their shared
`todo_fapolicyd` role refreshes exact source-file trust on the Ansible
controller, transfers files through pipelined standard input, installs
root-owned copies under `/opt/todo/bin`, and registers only those exact target
files. Supply normal Ansible become credentials; no manual trust preparation is
part of the supported workflow.

## Diagnose a denial

First confirm which security controls are active:

```bash
systemctl is-active fapolicyd
getenforce
```

Then reproduce the failure once and inspect recent audit records:

```bash
sudo ausearch --start recent -m fanotify
sudo ausearch --start recent -m avc
```

A `FANOTIFY` record with `resp=2` identifies an `fapolicyd` denial. An AVC
record identifies SELinux instead. The two controls are independent; adding a
file to the `fapolicyd` trust database does not fix an SELinux denial.

To confirm whether an exact path is present in the combined trust database:

```bash
sudo fapolicyd-cli --dump-db | grep -F -- "/absolute/path/to/file"
```

Use an absolute, resolved path. Trust is tied to the recorded path, size and
hash, so trusting an old extraction does not trust the same filename in a new
directory.

## Add or refresh trust

Verify the archive or manifest before trusting extracted code. For a new path:

```bash
sudo fapolicyd-cli --file add \
  "/absolute/path/to/file" \
  --trust-file todo-component
sudo fapolicyd-cli --update
```

After replacing the contents at a path already registered in that trust file:

```bash
sudo fapolicyd-cli --file update \
  "/absolute/path/to/file" \
  --trust-file todo-component
sudo fapolicyd-cli --update
```

The distinction matters:

- `add` creates a trust entry for a new path.
- `update` refreshes the stored size and hash after that path changes.
- `fapolicyd-cli --update` reloads all trust sources into the running daemon.

If `add` or `update` itself receives `Operation not permitted`, collect the
recent `fanotify` audit event and ask the host security administrator to approve
the exact verified file. Do not work around that policy by stopping the daemon
or trusting an entire home, extraction or temporary directory.

## Common symptoms

- **`./install.sh: /bin/sh: bad interpreter: Operation not permitted`:**
  Run `sh ./install.sh`. The shell remains RPM-trusted and reads the extracted
  script as data.

- **Ansible fails below `~/.ansible/tmp`:** Use the included installer or
  playbooks. They enable pipelining or use pipelined standard input. Do not
  create a bundle-local Python virtual environment on the hardened target.

- **`sha256sum` cannot read a Python extension, executable or script:** This is
  a policy denial, not proof that the checksum is wrong. Inspect the `fanotify`
  audit event. The supported M12 bundle avoids unpackaged Python runtime files
  and uses the RPM-managed runtime.

- **Ansible reports a checksum mismatch while copying a Python tool:** The
  target policy denied Ansible's temporary source file. Use the central `todo_fapolicyd` role. It refreshes exact source trust,
  installs through pipelined standard input and verifies exact target trust.

- **Ansible cannot read a role YAML file:** Use the current package and its
  package-level pipelining configuration. The installer no longer embeds Python
  in task YAML, so role files should remain data and should not need custom
  trust. Diagnose any remaining `FANOTIFY` denial before adding trust.

- **A previously working tool fails after an update:** Its stored hash is stale.
  Run `--file update` for every registered copy and then
  `fapolicyd-cli --update`.

## Remove project trust entries

Remove trust when the corresponding project file or component is retired:

```bash
sudo fapolicyd-cli --file delete \
  "/absolute/path/to/file" \
  --trust-file todo-component
sudo fapolicyd-cli --update
```

The Ansible-managed tools use one dedicated `todo` trust source. Remove an
exact source entry on its controller and an exact installed entry on its target
only when that tool is retired:

```bash
sudo fapolicyd-cli --file delete "$HOME/todo-operations/scripts/todo_dr.py" --trust-file todo
sudo fapolicyd-cli --file delete "$HOME/todo-operations/scripts/todo_dr_run.py" --trust-file todo
sudo fapolicyd-cli --file delete "$HOME/todo-operations/scripts/todo_backup.py" --trust-file todo

sudo fapolicyd-cli --file delete /opt/todo/bin/todo_dr.py --trust-file todo
sudo fapolicyd-cli --file delete /opt/todo/bin/todo_dr_run.py --trust-file todo
sudo fapolicyd-cli --file delete /opt/todo/bin/todo_backup.py --trust-file todo
sudo fapolicyd-cli --update
```

Only run commands for paths present on that machine. Removing trust does not
delete a file, and deleting a file does not clean up trust. The DR config under
`~/.config/todo` is data and is not added to execution trust.

## Scaling host-side tools beyond the demo

Exact-file trust is intentionally visible in this lab because it teaches the
policy boundary. For repeated deployment across more hosts, package operational
tools such as `todo_dr.py` and `todo_backup.py` as a signed RPM and install or
upgrade it with DNF. The fapolicyd DNF integration can then derive file trust
from the RPM database instead of requiring a manual hash refresh for every
copy. Package signature verification and fapolicyd trust are related controls,
but not the same: verify the configured RPM signing identity and repository
policy as well. Avoid direct `rpm` installation in this workflow because it can
bypass the DNF integration that refreshes fapolicyd trust.

The demo keeps exact-file trust rather than adding an RPM build system solely
for two small tools. This is a deliberate teaching profile, not the recommended
lifecycle for a fleet.

## Integrity and authenticity

`sha256sum` detects accidental or unauthorized content changes only when the
expected checksum came through a separately trusted channel. A checksum copied
beside the archive is not a publisher signature. A production distribution
should verify a separately signed manifest, RPM or bundle before adding custom
trust.
