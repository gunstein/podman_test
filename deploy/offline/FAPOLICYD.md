# fapolicyd on Oracle Linux

This project keeps `fapolicyd` enabled. The tested Oracle Linux 9 policy may
deny direct execution or reading of newly extracted, non-RPM files even when
normal Unix permissions and SELinux labels are correct. A denial normally
appears as `Operation not permitted`.

Do not disable `fapolicyd` to install or operate this demo. Trust only the
verified files that must be interpreted as code.

## Why the project uses two approaches

The single-host installer uses OS-managed Python and Jinja2 plus project-owned
Python sources. Run extracted shell wrappers through the trusted system shell:

```bash
sh ./preflight.sh
sh ./install.sh
```

Before running the installer on an enforcing host, trust only the verified
`deploy/installer/app_installer/*.py` files using the exact-file recipe in
[offline installation](README.md#oracle-linux-9-with-fapolicyd). Replacing or
moving the extraction requires refreshing those paths and hashes. The wrapper
checks the internal manifest before importing the Python module.

app-ops, the DR tool, is itself project Python, so its files are trusted once
on each controller ([deploy/ops/README.md](../ops/README.md)). On every
hardened host it touches, it installs root-owned copies of the installer
module under `/opt/todo/lib/app_installer` and of the DR and backup tools under
`/opt/todo/bin`, and waits for exact source and target trust. It refreshes
exact source-file trust on the controller, sends each file over SSH standard
input, and registers only those exact target files, with `sudo -n`. No other
manual trust preparation is part of the supported workflow.

The trust logic lives in `deploy/scripts/trust-files.sh`, run through the
RPM-trusted system shell. It cannot be project Python, because the files it
trusts are that Python. `install DEST MODE` writes one root-owned file
atomically from base64 standard input. `trust TRUST_FILE PATH...` updates or
adds exact trust, reloads the daemon, and then waits until
`fapolicyd-cli --dump-db` shows every exact path, size and SHA-256 line. A
stale hash or a path prefix does not count. app-ops passes the script text as
an argument, so no helper file is written to the target.

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

- **A bundle-local Python virtual environment fails:** Do not create one on
  the hardened target. The installer and app-ops use the OS-managed Python.

- **`sha256sum` cannot read a Python extension, executable or script:** This is
  a policy denial, not proof that the checksum is wrong. Inspect the `fanotify`
  audit event. The bundle uses the OS-managed runtime; its project Python sources require
  exact-file trust after archive verification as described above.

- **app-ops itself is denied on the controller:** Its files are not trusted
  yet, or the package was replaced. Run the trust command from
  [deploy/ops/README.md](../ops/README.md) again.

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

The app-ops-managed tools use the dedicated `todo` trust source. Manual
single-host installer entries use `app-installer`. Remove an
exact source entry on its controller and an exact installed entry on its target
only when that tool is retired:

```bash
sudo fapolicyd-cli --file delete "$HOME/todo-operations/deploy/scripts/app_dr.py" --trust-file todo
sudo fapolicyd-cli --file delete "$HOME/todo-operations/deploy/scripts/app_backup.py" --trust-file todo

sudo fapolicyd-cli --file delete /opt/todo/bin/app_dr.py --trust-file todo
sudo fapolicyd-cli --file delete /opt/todo/bin/app_backup.py --trust-file todo
sudo fapolicyd-cli --update
```

Only run commands for paths present on that machine. Removing trust does not
delete a file, and deleting a file does not clean up trust. The DR config under
`~/.config/todo` is data and is not added to execution trust.

## Scaling host-side tools beyond the demo

Exact-file trust is intentionally visible in this lab because it teaches the
policy boundary. For repeated deployment across more hosts, package operational
tools such as `app_dr.py` and `app_backup.py` as a signed RPM and install or
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
