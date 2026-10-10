#!/usr/bin/env python3
"""Fixed acceptance steps for the Proxmox lab, each with its own log.

Runs on the client/build host from the repository root. It never replaces the
product's own commands (install.sh, app-ops, app_dr.py, app_backup.py): those
are what acceptance tests, and they run exactly as docs/ACCEPTANCE.md writes
them. This tool runs the glue around them the same way every time, compares
the result with the expected values itself and records it.

  acceptance.py --run RUN_ID step 03-4           (the next line of the guide)
  acceptance.py --run RUN_ID --step 03-4 check services 192.168.0.102 app
  acceptance.py --run RUN_ID --step 03-9 do reboot 107 192.168.0.102 app
  acceptance.py --run RUN_ID report full
  acceptance.py --run RUN_ID evidence          (EVIDENCE.md: one file to copy and send)
  acceptance.py --run RUN_ID run               (the whole guide, no agent: docs/ACCEPTANCE-HUMAN.md)

step    runs one command line of docs/ACCEPTANCE-AGENT.md C9 exactly as the
        guide writes it, named by its step (what follows "$A --step", "vm",
        "ops" or "product" on that line). It refuses unless that is the next
        step of the guide and the step before it passed, so a changed
        command, a skipped step or a step after a failure cannot run. After
        a step that passed, it also runs the read-only check lines right
        after it in the same block, stopping at the first that does not
        pass, and ends by naming the next step.

check   reads only and may be repeated.
do      changes state. After a FAIL it refuses the same command with the same
        arguments for the rest of the run, unless --operator-approved says why.
        Some do commands (markers, firewall rules) refuse any second run.

Each call writes logs/<step>-<kind>-<command>.log in the run folder
(~/todo-acceptance-runs/RUN_ID, or $ACCEPTANCE_RUNS/RUN_ID) and appends one
JSON line to record.jsonl. Exit status: 0 PASS, 1 FAIL, 2 usage, 3 refused.

report writes REPORT.md in the run folder from record.jsonl and the other
logs, so no value in the run record is copied by hand, and
compares the run with the guide (full: ACCEPTANCE-AGENT.md) as it was at
the recorded revision: every acceptance.py
line and every log the guide names must be there, and nothing else. It exits
0 only if every step passed, no do was left unfinished or needed approval,
every other log ends in exit=0 (exit=1 for a log named *-refused.log, a
refusal the guide asks for) and every step ran from the same clean
checkout. Each record line carries the revision and cleanliness of the
checkout at the time of that step; report never reads git itself. Its last
section times the run from the logs: each phase, the time in steps and the
time between them (the agent and the operator), and the slowest steps. Its
header gives the failover time (G3), from the old primary's fence (06-3) to
users logging in on the promoted host (07-8); over 30 minutes needs attention.

Commands:
  do    rollback VMID SNAPSHOT HOST      reset the VM, start it, wait for SSH
  check clean-host HOST                  no Todo state, security services on
  do    firewall-https HOST CLIENT_IP    permanent rich rule for 8443, reloaded
  check services HOST app|standby        wait-ready.sh, no failed units; nginx -t (app)
                                         or no serving service active (standby)
  check ca HOST                          saves the public CA; same as last time
  check headers                          HSTS, CSP and frame headers from here
  check browser                          Todo, Notes and SSO tests, 0 skipped
  do    markers PHASE                    one Todo and one Note marker
  check markers HOST                     every recorded marker is on HOST
  do    reboot VMID HOST app|standby     new boot ID, then check services
  check roles HOST primary|archiving|standby   f|off, f|off|on|1h or t|on, all three
  check write-probe HOST                 the guide's rolled-back Todo and Notes inserts
  check replication-tls HOST             every standby connection streams over TLS
  check disk HOST                        backup and WAL sizes, at least 2 GiB free
  check monitor HOST ok|alert            the DR check timer is on; one run passes, ready to take over,
                                         or names a problem
  do    firewall-replication SOURCE HOST add|remove   5432-5434 from SOURCE to HOST
  do    proxmox-firewall VMID on|off     VM firewall switch, then a 20 s wait
  do    replication-exception VMID on|off   the todo-quarantine-replication rule, then 20 s
  do    fence VMID                       pve_lab.py fence, every field checked
  check ports-closed HOST FROM           ports-closed.sh from FROM (client or an IPv4)
  check connect FROM TO PORT open|blocked   one fresh TCP connection, 5 s limit
  check quarantine-ready VMID NAME       the Guest Agent helper answers READY
  do    quarantine-profile VMID CLIENT PEER   the three rules, firewall still off
  do    quarantine-stop VMID NAME        the helper stops every service: STOPPED
  check stopped HOST                     every service inactive or failed, no containers
  do    link VMID up|down HOST           every network link; up waits for SSH
  do    power VMID start|shutdown        start waits for the Guest Agent
  do    onboot VMID 0|1                  the start-at-boot flag, read back
  do    pin-ssh FROM TO                  key-based SSH FROM to TO, host key verified
  do    backup-nightly HOST              the nightly backup timer is on; one run backs up every database
"""
import argparse
import contextlib
import datetime
import ipaddress
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'deploy/installer'))
# platform.yaml of this checkout, read-only, until phase 3b
from app_installer import platform_file  # noqa: E402

TODO_URL = 'https://todo.test:8443'
NOTES_URL = 'https://notes.test:8443'
IDENTITY_ORIGIN = 'https://auth.test:8443'
ZONE = 'public'  # the firewalld zone of the lab VMs' LAN interface (ACCEPTANCE.md phase 3)
CA_PATH = '/var/lib/platform-tls/ca.crt'
PYTHON = ROOT / 'todo-backend/.venv/bin/python'
REBOOT_TIMEOUT = 600
POLL_SECONDS = 10
FIREWALL_SETTLE_SECONDS = 20  # the Proxmox firewall applies changes about every 10 s
DATABASES = ('todo', 'notes', 'keycloak')  # platform_file.checkout().replicated_databases; user and container share the name
MIN_FREE_KIB = 2 * 1024 * 1024


class Refused(Exception):
    """The tool will not run this command now; nothing was changed."""


class Step:
    """One command in one run: its log file, its checks and the values it found."""

    def __init__(self, run_directory, label, kind, name, arguments, user):
        self.run_directory, self.user = run_directory, user
        self.kind, self.name, self.arguments = kind, name, arguments
        logs = run_directory / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        base = f'{label}-{kind}-{name}'
        self.log_path = logs / f'{base}.log'
        number = 2
        while self.log_path.exists():
            self.log_path = logs / f'{base}-{number}.log'
            number += 1
        self.log_file = self.log_path.open('w')
        self.failures, self.values = [], {}

    def log(self, text):
        print(text)
        self.log_file.write(text + '\n')
        self.log_file.flush()

    def run(self, argv, input=None, env=None, timeout=None):
        """Run argv, log the command, its output and exit status; return the result."""
        self.log('$ ' + ' '.join(str(part) for part in argv))
        try:
            result = subprocess.run([str(part) for part in argv], input=input, capture_output=True,
                                    text=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            self.log(f'exit=timeout after {timeout}s')
            return subprocess.CompletedProcess(argv, 124, '', '')
        for text in (result.stdout, result.stderr):
            if text.strip():
                self.log(text.rstrip())
        self.log(f'exit={result.returncode}')
        return result

    def ssh(self, host, script, *arguments, timeout=None):
        """Run a bash script on host as the service user, with host-key checking on.

        ssh joins the remote command into one string that the remote shell
        splits again, so every argument is quoted here: an SSH public key
        ("ssh-rsa AAAA... comment") must arrive as one argument, not three.
        """
        remote = ' '.join(['bash', '-s', '--', *(shlex.quote(str(argument)) for argument in arguments)])
        return self.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', f'{self.user}@{host}', remote],
                        input=script, timeout=timeout)

    def expect(self, condition, message):
        self.log(('ok: ' if condition else 'FAIL: ') + message)
        if not condition:
            self.failures.append(message)
        return condition


def record_path(run_directory):
    return run_directory / 'record.jsonl'


def read_record(run_directory):
    path = record_path(run_directory)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_record(run_directory, entry):
    with record_path(run_directory).open('a') as record:
        record.write(json.dumps(entry, sort_keys=True) + '\n')


def host_address(text):
    """A plain IPv4 address; anything else would end up inside a shell or firewall rule."""
    return str(ipaddress.IPv4Address(text))


def matching(pattern):
    """A validator that accepts only text matching pattern completely."""
    def validate(text):
        if not re.fullmatch(pattern, text):
            raise ValueError(f'{text!r} does not match {pattern}')
        return text
    return validate


VMID = matching(r'[0-9]{3,9}')
ROLE = matching(r'primary|archiving|standby')
SWITCH = matching(r'on|off')
CHANGE = matching(r'add|remove')
NAME = matching(r'[a-z][a-z0-9-]{0,62}')
PORT = matching(r'[0-9]{1,5}')
EXPECT = matching(r'open|blocked')
LINK = matching(r'up|down')
POWER = matching(r'start|shutdown')
BIT = matching(r'[01]')
OUTCOME = matching(r'ok|alert')
SNAPSHOT = matching(r'[A-Za-z0-9_-]+')
MODE = matching(r'app|standby')
PHASE = matching(r'phase[0-9]+[a-z]?')


def source_address(text):
    """Where a connection starts: the client itself, or a VM by its IPv4 address."""
    return text if text == 'client' else host_address(text)


# --- checks (read only) -------------------------------------------------------------

def check_clean_host(step, host):
    """Phase 1: security services on, SELinux enforcing, rootless Podman, no Todo state.

    The install's own prerequisites are preflight.sh's job in phase 3.
    """
    result = step.ssh(host, """
getenforce
systemctl is-active firewalld fapolicyd sshd
podman info --format '{{.Host.Security.Rootless}}'
echo "containers=$(podman ps -aq | wc -l) volumes=$(podman volume ls -q | wc -l) secrets=$(podman secret ls -q | wc -l)"
test -e ~/.config/containers/systemd && echo quadlet=present || echo quadlet=none
cat /etc/machine-id
""")
    lines = result.stdout.split()
    step.expect(result.returncode == 0, 'all checks ran')
    step.expect('Enforcing' in lines, 'SELinux is Enforcing')
    step.expect(lines[1:4] == ['active'] * 3, 'firewalld, fapolicyd and sshd are active')
    step.expect('true' in lines, 'Podman runs rootless')
    step.expect('containers=0' in lines and 'volumes=0' in lines and 'secrets=0' in lines,
                'no containers, volumes or secrets')
    step.expect('quadlet=none' in lines, 'no Quadlet directory')
    if lines:
        step.values['machine_id'] = lines[-1]


# Podman runs each container health check as a transient unit named
# <container id>-<random>.service; one failed run leaves it "failed" until the next.
HEALTH_CHECK_UNIT = re.compile(r'[0-9a-f]{64}-[0-9a-f]+\.(service|timer)')
# The scheduled DR check (platform-dr-check.timer). Its failed state means DR needs attention, as
# after a failover until the rebuild, not that a service is down; check
# monitor tests it on its own.
DR_CHECK = 'platform-dr-check'
# The check's last line on a host that could take over (app_dr.readiness).
READY = 'Ready to take over:'
BACKUP = 'platform-backup'


def check_services(step, host, mode):
    """All services up (wait-ready.sh), no failed user units, and a valid nginx configuration.

    Podman's own health-check units are listed, not failed: wait-ready.sh
    already requires every container to be healthy, and the probe allows
    single failed runs while a container starts (run 19).
    """
    ready = step.ssh(host, (ROOT / 'deploy/scripts/wait-ready.sh').read_text(), mode, timeout=400)
    step.expect(ready.returncode == 0 and 'READY:' in ready.stdout, f'wait-ready.sh {mode} printed READY')
    failed = step.ssh(host, 'systemctl --user --failed --no-legend --plain')
    units = [line.split()[0] for line in failed.stdout.splitlines() if line.strip()]
    health_checks = [unit for unit in units if HEALTH_CHECK_UNIT.fullmatch(unit)]
    if health_checks:
        step.values['failed_health_check_runs'] = len(health_checks)
    if f'{DR_CHECK}.service' in units:
        step.values['dr_check'] = 'failed (see check monitor)'
    others = [unit for unit in units if unit not in health_checks and unit != f'{DR_CHECK}.service']
    step.expect(failed.returncode == 0 and not others,
                'no failed user units' + (f' (ignored {len(health_checks)} failed Podman health-check run)'
                                          if health_checks else '') + (f': {", ".join(others)}' if others else ''))
    if mode == 'app':
        nginx = step.ssh(host, 'podman exec nginx nginx -t -c /etc/platform-nginx/nginx.conf')
        step.expect(nginx.returncode == 0, 'nginx configuration is valid')
    else:
        serving = platform_file.checkout().services(databases=False)
        states = step.ssh(host, 'systemctl --user is-active ' + ' '.join(serving)).stdout.split()
        step.expect(len(states) == len(serving) and 'active' not in states,
                    'a database-only standby runs none of: ' + ', '.join(serving))


def fingerprint(text):
    """The SHA-256 fingerprint in openssl x509 -fingerprint output, or '' if there is none."""
    match = re.search(r'Fingerprint=([0-9A-F:]{95})', text)
    return match.group(1) if match else ''


def check_ca(step, host):
    """Save the serving host's public CA for the browser tests; it must not change on that host."""
    certificate = step.ssh(host, f'podman exec nginx cat {CA_PATH}')
    remote = fingerprint(step.ssh(host, f'podman exec nginx openssl x509 -in {CA_PATH} -noout '
                                        '-fingerprint -sha256').stdout)
    saved = step.run_directory / 'ca.crt'
    saved.write_text(certificate.stdout)
    local = fingerprint(step.run(['openssl', 'x509', '-in', saved, '-noout', '-fingerprint', '-sha256']).stdout)
    step.expect(bool(remote) and local == remote, 'the saved CA matches the host\'s own fingerprint')
    step.values.update(host=host, fingerprint=remote)
    earlier = [entry['values'].get('fingerprint') for entry in read_record(step.run_directory)
               if entry['kind'] == 'check' and entry['command'] == 'ca' and entry['values'].get('host') == host
               and entry['result'] == 'PASS']
    if earlier:
        step.expect(earlier[-1] == remote, f'CA unchanged on {host} since the last check')
    trusted = step.run(['curl', '--silent', '--show-error', '--fail', '--max-time', '10', TODO_URL + '/ready'])
    step.expect(trusted.returncode == 0, 'the client trusts it: curl without -k works')


def headers(text):
    """The response headers from curl --head as {lowercase name: [values]}."""
    found = {}
    for line in text.replace('\r', '').splitlines():
        if line.startswith('HTTP/'):
            found = {}
            continue
        name, _, value = line.partition(':')
        if value:
            found.setdefault(name.strip().lower(), []).append(value.strip())
    return found


def check_headers(step):
    """Security headers from the client, without -k (ACCEPTANCE.md phases 3 and 7)."""
    for url in (TODO_URL + '/', NOTES_URL + '/'):
        result = step.run(['curl', '--silent', '--show-error', '--head', '--max-time', '10', url])
        found = headers(result.stdout)
        step.expect(result.returncode == 0, f'{url} answers over trusted HTTPS')
        step.expect(found.get('strict-transport-security', [''])[0].startswith('max-age=31536000'),
                    f'{url}: Strict-Transport-Security')
        policy = found.get('content-security-policy', [''])[0]
        directives = dict((part.strip() + ' ').split(' ', 1) for part in policy.split(';') if part.strip())
        step.expect(directives.get('connect-src', '').strip() == f"'self' {IDENTITY_ORIGIN}",
                    f"{url}: connect-src is exactly 'self' {IDENTITY_ORIGIN}")
        step.expect(directives.get('frame-ancestors', '').strip() == "'none'", f"{url}: frame-ancestors 'none'")
        step.expect('unsafe-inline' not in policy and 'unsafe-eval' not in policy, f'{url}: no unsafe-*')
        step.expect(found.get('x-frame-options') == ['DENY'], f'{url}: X-Frame-Options DENY')
        step.expect(found.get('referrer-policy') == ['strict-origin-when-cross-origin'],
                    f'{url}: Referrer-Policy strict-origin-when-cross-origin')
    url = IDENTITY_ORIGIN + '/auth/realms/todo'
    result = step.run(['curl', '--silent', '--show-error', '--head', '--max-time', '10', url])
    step.expect(result.returncode == 0 and 'strict-transport-security' in headers(result.stdout),
                f'{url}: Strict-Transport-Security')


def password_environment(**extra):
    """The environment for an e2e script: ours, the testuser password from its runtime file, and extra."""
    path = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/%d' % os.getuid())) / 'todo-acceptance/e2e-password'
    if not path.is_file():
        raise Refused(f'the testuser password file {path} is missing (docs/ACCEPTANCE-AGENT.md C6)')
    return dict(os.environ, E2E_PASSWORD=path.read_text().strip(), **extra)


def check_browser(step):
    """The Todo, Notes and multi-app SSO browser tests with TLS errors fatal; a skip is a failure."""
    ca = step.run_directory / 'ca.crt'
    if not ca.is_file():
        raise Refused('run "check ca HOST" first: the SSO test needs the saved CA')
    common = dict(E2E_USERNAME='testuser', E2E_IGNORE_HTTPS_ERRORS='false')
    for name, test, extra in (
            ('Todo', 'e2e/test_todo_flow.py', dict(E2E_BASE_URL=TODO_URL)),
            ('Notes', 'e2e/test_notes_flow.py', dict(E2E_NOTES_URL=NOTES_URL)),
            ('multi-app SSO', 'e2e/test_multi_app.py', dict(E2E_MULTI_APP='1', E2E_CA_FILE=str(ca)))):
        arguments = [PYTHON, '-m', 'pytest', test, '-q', '-rs']
        if 'multi_app' not in test:
            arguments[4:4] = ['--browser', 'chromium']
        result = step.run(arguments, env=password_environment(**common, **extra), timeout=600)
        summary = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ''
        step.expect(result.returncode == 0 and ' passed' in summary
                    and not re.search(r'skipped|failed|error', summary),
                    f'{name}: all passed, none skipped ({summary or "no summary"})')


def check_markers(step, host):
    """Every marker this run created (do markers) is on host, with its ID and title."""
    expected = [entry['values'] for entry in read_record(step.run_directory)
                if entry['kind'] == 'do' and entry['command'] == 'markers' and entry['result'] == 'PASS']
    step.expect(bool(expected), 'this run has created markers')
    run_id = step.run_directory.name
    for database, table, key in (('todo', 'todos', 'todo_id'), ('notes', 'notes', 'note_id')):
        rows = step.ssh(host, f"podman exec {database}-postgres psql --username {database} --dbname {database} "
                              f"-At -F '|' -c \"SELECT id, title FROM {table} "
                              f"WHERE title LIKE 'acceptance {run_id} %' ORDER BY id\"")
        found = dict(line.split('|', 1) for line in rows.stdout.splitlines() if '|' in line)
        for marker in expected:
            identifier = str(marker[key])
            step.expect(found.get(identifier) == marker['title'],
                        f'{database} on {host}: id {identifier} "{marker["title"]}"')


def psql(step, host, database, statement):
    """Run one statement in the database's container; the statement goes on stdin, not in argv."""
    return step.ssh(host, f"podman exec -i {database}-postgres psql --no-psqlrc --set ON_ERROR_STOP=1 "
                          f"--username {database} --dbname {database} --tuples-only --no-align "
                          f"--field-separator='|' <<'SQL'\n{statement}\nSQL\n")


def check_roles(step, host, role):
    """All three databases in the same role: primary f|off, archiving f|off|on|1h, standby t|on."""
    statement, expected = {
        'primary': ("SELECT pg_is_in_recovery(), current_setting('default_transaction_read_only');", 'f|off'),
        'archiving': ("SELECT pg_is_in_recovery(), current_setting('default_transaction_read_only'), "
                      "current_setting('archive_mode'), current_setting('archive_timeout');", 'f|off|on|1h'),
        'standby': ("SELECT pg_is_in_recovery(), current_setting('transaction_read_only');", 't|on'),
    }[role]
    for database in DATABASES:
        value = psql(step, host, database, statement).stdout.strip()
        step.values[database] = value
        step.expect(value == expected, f'{database} on {host}: {value or "no answer"} (want {expected})')


def check_write_probe(step, host):
    """ACCEPTANCE.md phase 6: a rolled-back insert in Todo and in Notes on the promoted host."""
    for database, statement in (
            ('todo', "BEGIN; INSERT INTO todos (title, completed) VALUES ('promotion write probe', false); ROLLBACK;"),
            ('notes', "BEGIN; INSERT INTO notes (title) VALUES ('promotion write probe'); ROLLBACK;")):
        result = psql(step, host, database, statement)
        step.expect(result.returncode == 0 and 'INSERT 0 1' in result.stdout and 'ROLLBACK' in result.stdout,
                    f'{database} on {host}: insert accepted and rolled back')


STREAMING_TIMEOUT = 120


def check_replication_tls(step, host):
    """On the primary: each database has a streaming standby connection, and it uses TLS.

    Right after either host restarts, the standby's walreceiver reconnects on
    its own within seconds (PostgreSQL retries every few seconds), so the
    check waits for that, read-only, up to STREAMING_TIMEOUT seconds before it
    judges. Run 14 read an empty pg_stat_replication a moment after a reboot.
    """
    query = 'SELECT application_name, state, ssl, version FROM pg_stat_replication JOIN pg_stat_ssl USING (pid);'

    def streaming(rows):
        return bool(rows) and all(row.split('|')[1:3] == ['streaming', 't'] for row in rows)

    found = {}

    def all_streaming():
        found.update({database: psql(step, host, database, query).stdout.split() for database in DATABASES})
        return all(streaming(found[database]) for database in DATABASES)

    wait_for(step, 'every standby to stream', all_streaming, STREAMING_TIMEOUT)
    for database in DATABASES:
        rows = found.get(database, [])
        step.values[database] = rows
        step.expect(streaming(rows), f'{database} on {host}: streaming over TLS '
                                     f'({", ".join(rows) or "no standby connection"})')


def check_disk(step, host):
    """ACCEPTANCE.md phase 10 step 8: backup and WAL sizes, and enough free space for Podman."""
    lines = ''.join(f"""echo "backup {database} $(podman unshare du -sk "$(podman volume inspect -f '{{{{.Mountpoint}}}}' {database}-postgres-backup)" | cut -f1)"
echo "wal {database} $(podman exec {database}-postgres du -sk /var/lib/postgresql/data/pg_wal | cut -f1)"
""" for database in DATABASES)
    result = step.ssh(host, lines + "echo \"free $(df -Pk ~/.local/share/containers | awk 'NR == 2 {print $4}')\"\n")
    sizes = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts and parts[-1].isdigit():
            sizes[' '.join(parts[:-1])] = int(parts[-1])
    step.values.update({name: f'{kib // 1024} MiB' for name, kib in sizes.items()})
    step.expect(len(sizes) == 2 * len(DATABASES) + 1, 'every size was read')
    step.expect(sizes.get('free', 0) >= MIN_FREE_KIB, f'at least {MIN_FREE_KIB // 1024 // 1024} GiB free')


def timer_on(step, host, name):
    """name.timer is enabled (it starts at boot) and active (it is scheduled now)."""
    timer = step.ssh(host, f'systemctl --user is-enabled {name}.timer; systemctl --user is-active {name}.timer')
    step.expect(timer.stdout.split() == ['enabled', 'active'], f'{name}.timer is enabled and active')


def start_unit(step, host, name, marker, timeout):
    """Start name.service once, as its timer would, and return (exit code, its journal lines from this run).

    The journal is read until marker shows up (up to 10 s), because journald
    may still be writing the last lines when systemctl returns.
    """
    script = f"""start=$(date +%s)
systemctl --user start {name}.service
echo "exit=$?"
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    out=$(journalctl _SYSTEMD_USER_UNIT={name}.service --since "@$start" -o cat --no-pager 2>/dev/null)
    case "$out" in *{shlex.quote(marker)}*) break ;; esac
    sleep 1
done
printf '%s\\n' "$out"
"""
    result = step.ssh(host, script, timeout=timeout)
    match = re.search(r'^exit=(\d+)$', result.stdout, re.M)
    lines = result.stdout[match.end():].strip().splitlines() if match else []
    return (int(match.group(1)) if match else -1), lines


def check_monitor(step, host, expected):
    """The scheduled DR check: its timer is on, and one run now passes (ok) or fails naming why (alert)."""
    timer_on(step, host, DR_CHECK)
    code, lines = start_unit(step, host, DR_CHECK, READY if expected == 'ok' else 'ERROR:', 300)
    problems = [line.removeprefix('ERROR: ') for line in lines if line.startswith('ERROR: ')]
    step.values['problems'] = problems
    if expected == 'ok':
        step.expect(code == 0 and not problems, f'{DR_CHECK}.service passed')
        step.expect(any(line.startswith('Disk:') for line in lines), 'its report is in the journal')
        ready = [line for line in lines if line.startswith(READY)]
        step.expect(bool(ready), 'the host could take over (bundle, image archives and DR secrets)')
        step.values['ready'] = ready[0] if ready else ''
    else:
        step.expect(code != 0, f'{DR_CHECK}.service failed, as it must')
        step.expect(bool(problems), 'the journal names the problem')


# --- do (changes state) -------------------------------------------------------------

def pve(step, *arguments):
    """Run pve_lab.py (the Proxmox API through the lab token) with arguments, logged in step."""
    return step.run([sys.executable, ROOT / 'deploy/scripts/lab/pve_lab.py', *arguments], timeout=900)


def boot_id(step, host):
    """The host's kernel boot ID, which changes at every boot; '' if SSH does not answer."""
    result = step.ssh(host, 'cat /proc/sys/kernel/random/boot_id', timeout=20)
    return result.stdout.strip() if result.returncode == 0 else ''


def wait_for(step, what, probe, timeout):
    """Call probe until it is true or timeout seconds have passed; it always runs at least once."""
    deadline = time.monotonic() + timeout
    while True:
        if probe():
            return True
        if time.monotonic() >= deadline:
            step.log(f'gave up waiting for {what} after {timeout}s')
            return False
        time.sleep(POLL_SECONDS)


def do_rollback(step, vmid, snapshot, host):
    """Phase 1: roll the VM back to its clean snapshot, start it and wait for SSH."""
    step.expect(pve(step, 'task', f'/nodes/{{node}}/qemu/{vmid}/snapshot/{snapshot}/rollback').returncode == 0,
                f'VM {vmid} rolled back to {snapshot}')
    status = pve(step, 'get', f'/nodes/{{node}}/qemu/{vmid}/status/current').stdout
    if '"status": "running"' not in status:
        step.expect(pve(step, 'task', f'/nodes/{{node}}/qemu/{vmid}/status/start').returncode == 0,
                    f'VM {vmid} started')
    step.expect(wait_for(step, 'SSH', lambda: bool(boot_id(step, host)), REBOOT_TIMEOUT), f'SSH to {host} works')
    # Proxmox firewall state is not part of the snapshot; an earlier run may have left it on.
    firewall = json.loads(pve(step, 'get', f'/nodes/{{node}}/qemu/{vmid}/firewall/options').stdout or '{}')
    step.expect(firewall.get('enable') != 1, f'VM {vmid} firewall is off')
    config = json.loads(pve(step, 'get', f'/nodes/{{node}}/qemu/{vmid}/config').stdout or '{}')
    networks = {key: value for key, value in config.items() if re.fullmatch(r'net[0-9]+', key)}
    step.expect(bool(networks) and not any('link_down=1' in value for value in networks.values()),
                f'every network link of VM {vmid} is up')
    step.values.update(onboot=config.get('onboot', 0), networks=networks)


def do_firewall_https(step, host, client):
    """Phase 3: the client's HTTPS rule, permanent and reloaded, found in both configurations."""
    rule = (f'rule family="ipv4" source address="{client}/32" destination address="{host}" '
            'port port="8443" protocol="tcp" accept')
    step.ssh(host, f"sudo -n firewall-cmd --permanent --zone={ZONE} --add-rich-rule='{rule}' && "
                   'sudo -n firewall-cmd --reload')
    running = step.ssh(host, f'sudo -n firewall-cmd --zone={ZONE} --list-rich-rules').stdout.splitlines()
    permanent = step.ssh(host, f'sudo -n firewall-cmd --permanent --zone={ZONE} --list-rich-rules').stdout.splitlines()
    step.expect(rule in running, 'the rule is in the running configuration')
    step.expect(rule in permanent, 'the rule is in the permanent configuration')


def do_markers(step, phase):
    """One authenticated Todo and one Note titled "acceptance RUN PHASE"; records their IDs."""
    title = f'acceptance {step.run_directory.name} {phase}'
    result = step.run([PYTHON, ROOT / 'e2e/create_markers.py'], env=password_environment(MARKER=title), timeout=300)
    match = re.search(re.escape(repr(title)) + r': todo id=(\d+) note id=(\d+)', result.stdout)
    if step.expect(result.returncode == 0 and match is not None, f'markers "{title}" created') and match:
        step.values.update(title=title, todo_id=int(match.group(1)), note_id=int(match.group(2)))


def do_reboot(step, vmid, host, mode):
    """Reboot through Proxmox, wait for a new boot ID, then run check services."""
    before = boot_id(step, host)
    step.expect(bool(before), 'boot ID read before the reboot')
    step.expect(pve(step, 'task', f'/nodes/{{node}}/qemu/{vmid}/status/reboot').returncode == 0,
                f'VM {vmid} reboot task OK')
    after = []
    step.expect(wait_for(step, 'a new boot ID', lambda: after.append(boot_id(step, host)) or after[-1] not in ('', before),
                         REBOOT_TIMEOUT), f'{host} came back with a new boot ID')
    step.values.update(before=before, after=after[-1] if after else '')
    check_services(step, host, mode)


def replication_rule(source, host):
    return (f'rule family="ipv4" source address="{source}/32" destination address="{host}" '
            'port port="5432-5434" protocol="tcp" accept')


def do_firewall_replication(step, source, host, change):
    """Phases 4 and 9: allow (or stop allowing) SOURCE to reach HOST's replication ports, permanently."""
    rule = replication_rule(source, host)
    step.ssh(host, f"sudo -n firewall-cmd --permanent --zone={ZONE} --{change}-rich-rule='{rule}' && "
                   'sudo -n firewall-cmd --reload')
    running = step.ssh(host, f'sudo -n firewall-cmd --zone={ZONE} --list-rich-rules').stdout.splitlines()
    permanent = step.ssh(host, f'sudo -n firewall-cmd --permanent --zone={ZONE} --list-rich-rules').stdout.splitlines()
    present = change == 'add'
    step.expect((rule in running) == present, f'the rule is {"" if present else "not "}in the running configuration')
    step.expect((rule in permanent) == present, f'the rule is {"" if present else "not "}in the permanent configuration')


def settle(step):
    step.log(f'waiting {FIREWALL_SETTLE_SECONDS}s for the Proxmox firewall to apply the change')
    time.sleep(FIREWALL_SETTLE_SECONDS)


def do_proxmox_firewall(step, vmid, switch):
    """Turn the VM's Proxmox firewall on or off, read it back, and wait until it applies."""
    wanted = 1 if switch == 'on' else 0
    pve(step, 'set', f'/nodes/{{node}}/qemu/{vmid}/firewall/options', f'enable={wanted}')
    options = json.loads(pve(step, 'get', f'/nodes/{{node}}/qemu/{vmid}/firewall/options').stdout or '{}')
    step.expect(int(options.get('enable', 0)) == wanted, f'VM {vmid} firewall is {switch}')
    step.values['options'] = options
    settle(step)


def do_replication_exception(step, vmid, switch):
    """Phase 9 step 8: switch only the rule commented todo-quarantine-replication, found by its comment."""
    wanted = 1 if switch == 'on' else 0
    path = f'/nodes/{{node}}/qemu/{vmid}/firewall/rules'
    rules = json.loads(pve(step, 'get', path).stdout or '[]')
    matches = [rule for rule in rules if rule.get('comment') == 'todo-quarantine-replication']
    if not step.expect(len(matches) == 1, 'exactly one rule commented todo-quarantine-replication'):
        return
    rule = matches[0]
    step.expect(rule.get('type') == 'out' and rule.get('dport') == '5432:5434',
                'it is the outbound replication rule for ports 5432:5434')
    if step.failures:
        return
    pve(step, 'set', f'{path}/{rule["pos"]}', f'enable={wanted}')
    after = [rule for rule in json.loads(pve(step, 'get', path).stdout or '[]')
             if rule.get('comment') == 'todo-quarantine-replication']
    step.expect(len(after) == 1 and int(after[0].get('enable', 0)) == wanted, f'the rule is {switch}')
    step.values['rule'] = after[0] if after else {}
    settle(step)


def parsed(result, default):
    """The JSON a pve_lab.py call printed, or default if it printed none."""
    try:
        return json.loads(result.stdout)
    except ValueError:
        return default


def on(step, source, script, *arguments):
    """Run a bash script on the client or, over SSH, on a VM."""
    if source == 'client':
        return step.run(['bash', '-c', script, 'bash', *arguments], timeout=120)
    return step.ssh(source, script, *arguments, timeout=120)


def do_fence(step, vmid):
    """Phase 6: pve_lab.py fence, and every field of its evidence checked here."""
    result = pve(step, 'fence', vmid)
    data = parsed(result, {})
    networks = [value for key, value in data.items() if re.fullmatch(r'net[0-9]+', key)]
    step.expect(result.returncode == 0, 'pve_lab.py fence exited 0')
    step.expect(data.get('status') == 'stopped', 'status stopped')
    step.expect(data.get('onboot') == '0', 'onboot 0')
    step.expect(data.get('ha') == 'not managed', 'not managed by HA')
    step.expect(bool(networks) and all('link_down=1' in value.split(',') for value in networks),
                'every network link down')
    step.values.update(data)


PORTS = ('22', '5432', '5433', '5434', '8443')


def check_ports_closed(step, host, source):
    """Phase 6: nothing on the fenced host accepts a connection, seen from source."""
    script = (ROOT / 'deploy/scripts/lab/ports-closed.sh').read_text()
    result = on(step, source, script, host, *PORTS)
    step.expect(result.returncode == 0 and f'CLOSED: {host}' in result.stdout, f'{host} is closed seen from {source}')


def check_connect(step, source, target, port, expected):
    """One fresh TCP connection from source to target:port, which must open or be blocked."""
    result = on(step, source, 'timeout 5 bash -c "</dev/tcp/$1/$2"', target, port)
    how = {0: 'opened', 124: 'timed out'}.get(result.returncode, f'failed (exit {result.returncode})')
    step.values.update(outcome=how)
    step.expect((result.returncode == 0) == (expected == 'open'),
                f'{source} to {target}:{port} {how}, must be {expected}')


def quarantine_helper(step, vmid, action, name):
    """Run app-quarantine.sh ACTION through the Guest Agent; return the result and the agent's JSON."""
    result = pve(step, 'exec', vmid, '--', '/opt/platform/bin/app-quarantine.sh', action, name, step.user)
    return result, parsed(result, {})


def check_quarantine_ready(step, vmid, name):
    """Phase 5: the stop helper is installed and answers through the Guest Agent."""
    result, data = quarantine_helper(step, vmid, 'check', name)
    step.expect(result.returncode == 0 and data.get('exitcode') == 0 and 'READY' in data.get('out-data', ''),
                'the helper answers READY')


def do_quarantine_stop(step, vmid, name):
    """Phases 5 and 9: the helper stops every service; exited 1, exitcode 0 and STOPPED."""
    result, data = quarantine_helper(step, vmid, 'stop', name)
    step.expect(data.get('exited') == 1, 'the helper finished')
    step.expect(data.get('exitcode') == 0 and 'STOPPED' in data.get('out-data', ''), 'it printed STOPPED')
    if data.get('err-data'):
        step.values['warnings'] = data['err-data'].strip()


def check_stopped(step, host):
    """Every registered service inactive or failed with no process, and no running container."""
    services = platform_file.checkout().services()
    # One property per call: systemctl show does not keep the order properties were asked in.
    script = ''.join(f'echo "{unit}' + ''.join(f' $(systemctl --user show -p {name} --value {unit})'
                                              for name in ('ActiveState', 'MainPID', 'ControlPID')) + '"\n'
                     for unit in services)
    result = step.ssh(host, script + 'echo "containers $(podman ps -q | wc -l)"\n')
    found = {line.split()[0]: line.split()[1:] for line in result.stdout.splitlines() if line.split()}
    for unit in services:
        state = found.get(unit, [])
        step.expect(len(state) == 3 and state[0] in ('inactive', 'failed') and state[1:] == ['0', '0'],
                    f'{unit}: {" ".join(state) or "no answer"}')
    step.expect(found.get('containers') == ['0'], 'no running containers')


def do_backup_nightly(step, host):
    """The nightly backup: its timer is on, and one run backs up and prunes every database."""
    timer_on(step, host, BACKUP)
    code, lines = start_unit(step, host, BACKUP, 'keycloak: verified base backup', 2400)
    backups = {database: match.group(1) for database in DATABASES for line in lines
               if (match := re.match(rf'{database}: verified base backup (base-\S+?);', line))}
    step.values.update(backups)
    step.expect(code == 0, f'{BACKUP}.service passed')
    step.expect(sorted(backups) == sorted(DATABASES), 'a verified base backup of every database, pruned after')


def do_link(step, vmid, updown, host):
    """Every network link of the VM up or down, read back; up then waits for SSH from here."""
    value = '1' if updown == 'down' else '0'
    result = pve(step, 'nic', vmid, 'link_down', value)
    networks = parsed(result, {})
    step.expect(result.returncode == 0 and bool(networks)
                and all(f'link_down={value}' in text.split(',') for text in networks.values()),
                f'every link of VM {vmid} is {updown}')
    if updown == 'up':
        step.expect(wait_for(step, 'SSH', lambda: bool(boot_id(step, host)), 180), f'SSH to {host} works')


def do_power(step, vmid, action):
    """Start (and wait for the Guest Agent) or cleanly shut down the VM through Proxmox."""
    step.expect(pve(step, 'task', f'/nodes/{{node}}/qemu/{vmid}/status/{action}').returncode == 0,
                f'VM {vmid} {action} task OK')
    if action == 'start':
        step.expect(wait_for(step, 'the Guest Agent',
                             lambda: pve(step, 'post', f'/nodes/{{node}}/qemu/{vmid}/agent/ping').returncode == 0,
                             300), 'the Guest Agent answers')


def do_onboot(step, vmid, value):
    """Set whether Proxmox starts the VM at boot, and read it back."""
    pve(step, 'set', f'/nodes/{{node}}/qemu/{vmid}/config', f'onboot={value}')
    config = parsed(pve(step, 'get', f'/nodes/{{node}}/qemu/{vmid}/config'), {})
    step.expect(str(config.get('onboot', 0)) == value, f'onboot is {value}')


QUARANTINE_RULES = (
    ('todo-quarantine-ssh-client', {'type': 'in', 'action': 'ACCEPT', 'proto': 'tcp', 'dport': '22', 'enable': '1'}),
    ('todo-quarantine-ssh-peer', {'type': 'in', 'action': 'ACCEPT', 'proto': 'tcp', 'dport': '22', 'enable': '1'}),
    ('todo-quarantine-replication',
     {'type': 'out', 'action': 'ACCEPT', 'proto': 'tcp', 'dport': '5432:5434', 'enable': '0'}),
)


def do_quarantine_profile(step, vmid, client, peer):
    """Phase 5: the VM's quarantine rules and options, with the VM firewall still off.

    Refuses to touch a rule it did not create: only rules whose comment starts
    with todo-quarantine- are removed and recreated.
    """
    for path in ('/cluster/firewall/options', '/nodes/{node}/firewall/options'):
        step.expect(parsed(pve(step, 'get', path), {}).get('enable') == 1, f'{path} has the firewall enabled')
    base = f'/nodes/{{node}}/qemu/{vmid}/firewall'
    step.expect(parsed(pve(step, 'get', f'{base}/options'), {}).get('enable') != 1, f'VM {vmid} firewall is still off')
    existing = parsed(pve(step, 'get', f'{base}/rules'), [])
    foreign = [rule for rule in existing if not str(rule.get('comment', '')).startswith('todo-quarantine-')]
    step.expect(not foreign, f'no rules on VM {vmid} other than earlier todo-quarantine ones')
    if step.failures:
        return
    for rule in sorted(existing, key=lambda rule: rule['pos'], reverse=True):
        pve(step, 'delete', f'{base}/rules/{rule["pos"]}')
    step.expect(pve(step, 'nic', vmid, 'firewall', '1').returncode == 0, 'firewall=1 on every network device')
    addresses = {'todo-quarantine-ssh-client': f'source={client}/32', 'todo-quarantine-ssh-peer': f'source={peer}/32',
                 'todo-quarantine-replication': f'dest={peer}/32'}
    for comment, fields in QUARANTINE_RULES:
        pve(step, 'post', f'{base}/rules', *(f'{key}={value}' for key, value in fields.items()),
            addresses[comment], f'comment={comment}')
    pve(step, 'set', f'{base}/options', 'enable=0', 'policy_in=DROP', 'policy_out=DROP', 'dhcp=1', 'ndp=1')
    rules = {rule.get('comment'): rule for rule in parsed(pve(step, 'get', f'{base}/rules'), [])}
    step.expect(sorted(rules) == sorted(comment for comment, _ in QUARANTINE_RULES), 'exactly the three rules')
    step.expect(str(rules.get('todo-quarantine-replication', {}).get('enable')) == '0',
                'the replication exception is disabled')
    options = parsed(pve(step, 'get', f'{base}/options'), {})
    step.expect(options.get('enable') != 1 and options.get('policy_in') == 'DROP' and options.get('policy_out') == 'DROP',
                'options: firewall off, policy DROP both ways')
    step.values.update(rules=sorted(rules), options=options)


PIN_SCRIPT = """set -eu
host=$1 expected=$2
scanned=$(ssh-keyscan -t ed25519 "$host" 2>/dev/null)
actual=$(printf '%s\\n' "$scanned" | ssh-keygen -lf - | awk '{print $2}')
test "$actual" = "$expected" || { echo "FINGERPRINT MISMATCH: $actual != $expected" >&2; exit 1; }
ssh-keygen -F "$host" >/dev/null || printf '%s\\n' "$scanned" >> ~/.ssh/known_hosts
echo "PINNED $host $actual"
"""


def do_pin_ssh(step, source, target):
    """Key-based SSH from source to target, with target's host key checked over the client's trusted SSH."""
    key = step.ssh(source, "test -f ~/.ssh/id_rsa || ssh-keygen -q -t rsa -b 3072 -N '' -C platform-ops-control "
                           "-f ~/.ssh/id_rsa; cat ~/.ssh/id_rsa.pub").stdout.strip().splitlines()
    public = key[-1] if key and key[-1].startswith('ssh-') else ''
    if not step.expect(bool(public), f'{source} has an SSH key'):
        return
    step.ssh(target, 'umask 077; mkdir -p ~/.ssh; grep -qxF "$1" ~/.ssh/authorized_keys 2>/dev/null || '
                     'printf "%s\\n" "$1" >> ~/.ssh/authorized_keys', public)
    fields = step.ssh(target, 'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub').stdout.split()
    expected = fields[1] if len(fields) > 1 else ''
    pinned = step.ssh(source, PIN_SCRIPT, target, expected)
    step.expect(bool(expected) and f'PINNED {target} {expected}' in pinned.stdout, f'{target} host key verified and pinned')
    name = step.ssh(source, f'ssh -o BatchMode=yes {step.user}@{target} hostname')
    step.expect(name.returncode == 0 and bool(name.stdout.strip()), f'{source} reaches {target} with its key')
    step.values.update(fingerprint=expected, hostname=name.stdout.strip())


COMMANDS = {
    # (kind, name): (function, argument validators, refuse any second run)
    ('do', 'rollback'): (do_rollback, (VMID, SNAPSHOT, host_address), False),
    ('check', 'clean-host'): (check_clean_host, (host_address,), False),
    ('do', 'firewall-https'): (do_firewall_https, (host_address, host_address), True),
    ('check', 'services'): (check_services, (host_address, MODE), False),
    ('check', 'ca'): (check_ca, (host_address,), False),
    ('check', 'headers'): (check_headers, (), False),
    ('check', 'browser'): (check_browser, (), False),
    ('do', 'markers'): (do_markers, (PHASE,), True),
    ('check', 'markers'): (check_markers, (host_address,), False),
    ('do', 'reboot'): (do_reboot, (VMID, host_address, MODE), False),
    ('check', 'roles'): (check_roles, (host_address, ROLE), False),
    ('check', 'write-probe'): (check_write_probe, (host_address,), False),
    ('check', 'replication-tls'): (check_replication_tls, (host_address,), False),
    ('check', 'disk'): (check_disk, (host_address,), False),
    ('check', 'monitor'): (check_monitor, (host_address, OUTCOME), False),
    ('do', 'backup-nightly'): (do_backup_nightly, (host_address,), False),
    ('do', 'firewall-replication'): (do_firewall_replication, (host_address, host_address, CHANGE), True),
    ('do', 'proxmox-firewall'): (do_proxmox_firewall, (VMID, SWITCH), False),
    ('do', 'replication-exception'): (do_replication_exception, (VMID, SWITCH), False),
    ('do', 'fence'): (do_fence, (VMID,), False),
    ('check', 'ports-closed'): (check_ports_closed, (host_address, source_address), False),
    ('check', 'connect'): (check_connect, (source_address, host_address, PORT, EXPECT), False),
    ('check', 'quarantine-ready'): (check_quarantine_ready, (VMID, NAME), False),
    ('do', 'quarantine-profile'): (do_quarantine_profile, (VMID, host_address, host_address), False),
    ('do', 'quarantine-stop'): (do_quarantine_stop, (VMID, NAME), False),
    ('check', 'stopped'): (check_stopped, (host_address,), False),
    ('do', 'link'): (do_link, (VMID, LINK, host_address), False),
    ('do', 'power'): (do_power, (VMID, POWER), False),
    ('do', 'onboot'): (do_onboot, (VMID, BIT), False),
    ('do', 'pin-ssh'): (do_pin_ssh, (host_address, host_address), False),
}


def refusal(run_directory, name, arguments, once, approved):
    """Why this do command must not run now, or None.

    A do writes a STARTED line before it acts and its result after. A STARTED
    line without a result means the tool was killed or crashed in the middle:
    nobody knows what it changed, so it counts as a failure.
    """
    outcome = {}
    for entry in read_record(run_directory):
        if entry['kind'] == 'do' and entry['command'] == name and entry['arguments'] == arguments:
            if entry['result'] != 'REFUSED':
                outcome[entry['log']] = entry['result']
    for log, result in outcome.items():
        if result in ('FAIL', 'STARTED') and not approved:
            how = 'failed' if result == 'FAIL' else 'started but never finished'
            return f'"do {name}" {how} earlier in this run ({log}); a second run needs --operator-approved'
        if result == 'PASS' and once:
            return f'"do {name}" already ran in this run ({log}); it runs only once'
    return None


def checkout():
    """The checked-out revision and whether the working tree is clean, read now."""
    revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    status = subprocess.run(['git', '-C', str(ROOT), 'status', '--porcelain'], capture_output=True, text=True)
    clean = revision.returncode == 0 and status.returncode == 0 and not status.stdout.strip()
    return revision.stdout.strip() or 'unknown', clean


def short(values):
    """The values of one step on one line, long ones cut."""
    parts = []
    for key, value in values.items():
        text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        parts.append(f'{key}={text if len(text) <= 60 else text[:57] + "..."}')
    return ', '.join(parts).replace('|', '\\|')


def product_logs(run_directory, tool_logs):
    """Logs the tool did not write: the product's own commands, with their exit and JSON lines."""
    rows = []
    for path in sorted((run_directory / 'logs').glob('*.log')):
        name = str(path.relative_to(run_directory))
        if name in tool_logs:
            continue
        lines = path.read_text(errors='replace').splitlines()
        exits = [line for line in lines if line.startswith('exit=')]
        changed = [line.strip() for line in lines if line.strip().startswith('{"changed"')]
        rows.append((name, exits[-1] if exits else 'no exit= line', ' '.join(changed)))
    return rows


# The guide a report compares with. The one-VM quick guide is retired; Git history keeps it.
GUIDES = {'full': 'docs/ACCEPTANCE-AGENT.md'}
SLOWEST = 8
ISO_TIME = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d')


def log_times(run_directory):
    """(name, start, end) for every log, by start: the time its first line names, and its last write.

    Each log starts with the time it began (acceptance.py's '# <time> ...',
    helpers.sh's '# start <time>'); the file's modification time is when the
    last line was written, so a log is timed without a line of its own.
    """
    times = []
    for path in (run_directory / 'logs').glob('*.log'):
        with path.open(errors='replace') as log:
            found = ISO_TIME.search(log.readline())
        if found:
            start = datetime.datetime.fromisoformat(found.group()).timestamp()
            times.append((path.stem, start, max(start, path.stat().st_mtime)))
    return sorted(times, key=lambda item: item[1])


def minutes(seconds):
    """12 min 05 s, or 1 h 02 min."""
    seconds = round(seconds)
    if seconds >= 3600:
        return f'{seconds // 3600} h {seconds % 3600 // 60:02d} min'
    return f'{seconds // 60} min {seconds % 60:02d} s'


# G3: Trondheim must serve within 30 minutes of "Oslo is lost". In the drill
# that is from the fence of the old primary (06-3, the decision acted on) to
# the browser test logging users in to both apps on the promoted host (07-8),
# with the operator's client trust step between them.
FAILOVER_FROM, FAILOVER_TO = '06-3-', '07-8-'
FAILOVER_GOAL = 30 * 60


def failover_time(run_directory):
    """Seconds from the fence's first line to the promoted host's browser test's last, or None without both."""
    times = log_times(run_directory)
    starts = [start for name, start, _ in times if name.startswith(FAILOVER_FROM)]
    ends = [end for name, _, end in times if name.startswith(FAILOVER_TO)]
    return max(ends) - min(starts) if starts and ends else None


def timing(run_directory):
    """The report's Time section: where a run spends its time, read from the logs alone.

    Time between steps is the gap from one log's last line to the next
    log's start: the agent reading and choosing the next step, or waiting
    for the operator. It counts towards the phase of the step after it.
    """
    times = log_times(run_directory)
    if not times:
        return ['- No timed logs.']
    phases, previous_end = {}, None
    for name, start, end in times:
        phase = phases.setdefault(name[:2], {'steps': 0, 'first': start, 'last': end, 'in': 0.0, 'between': 0.0})
        phase['steps'] += 1
        phase['last'] = max(phase['last'], end)
        phase['in'] += end - start
        if previous_end is not None and start > previous_end:
            phase['between'] += start - previous_end
        previous_end = end if previous_end is None else max(previous_end, end)
    lines = ['| Phase | Logs | From first start to last end | In steps | Between steps |', '|---|---|---|---|---|']
    for phase, value in sorted(phases.items()):
        lines.append(f'| {phase} | {value["steps"]} | {minutes(value["last"] - value["first"])} | '
                     f'{minutes(value["in"])} | {minutes(value["between"])} |')
    total_in = sum(value['in'] for value in phases.values())
    total_between = sum(value['between'] for value in phases.values())
    lines.append(f'| all | {len(times)} | {minutes(max(end for _, _, end in times) - times[0][1])} | {minutes(total_in)} | '
                 f'{minutes(total_between)} |')
    slowest = sorted(times, key=lambda item: item[2] - item[1], reverse=True)[:SLOWEST]
    lines += ['', 'Slowest steps: ' + ', '.join(f'{name} {minutes(end - start)}' for name, start, end in slowest)
              + '.', '', 'A step that ran in the background (failover, rebuild) overlaps the steps after it, '
              'so "In steps" can add up to more than the phase took.']
    return lines

# The logs a reviewer reads after a run, by the start of their name, in run order.
EVIDENCE_LOGS = ('00-readiness', '03-2-install', '03-12a-', '03-12c-restore', '03-12d-restored',
                 '03-13-install-again', '05-1-install-dr-tool', '05-7a-', '06-10-failover', '06-15-',
                 '08-7-mark', '08-9-restore-todo', '08-10-restore-notes', '08-15-backup-status', '08-16-',
                 '09-10-rebuild', '09-11d-cluster-status', '09-13c-reseed', '09-13f-cluster-status', '10-7d-',
                 '10-7e-', '11-4-restarts')
EVIDENCE_TAIL = 25
# Lines that are never evidence: SSH's post-quantum warning and Podman's events in the journal.
NOISE = re.compile(r'^\*\* |^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+ [+-]\d{4} \w+ m=\+\S+ (container|pod) ')


def attempt(path):
    """('03-4-check-services', 2) for logs/03-4-check-services-2.log, so repeats sort after the first log."""
    name, _, number = path.stem.rpartition('-')
    return (name, int(number)) if number.isdigit() and name else (path.stem, 1)


def evidence(run_directory):
    """EVIDENCE.md: REPORT.md, the agent's notes and the end of every key log, as one file to copy.

    It only reads the run folder. Noise lines (SSH warnings, Podman events)
    are left out of the log tails, and each tail says how many.
    """
    parts = [f'# Evidence for acceptance run {run_directory.name}', '']
    for name in ('REPORT.md', 'FINAL-REPORT.md', 'run-record.md'):
        path = run_directory / name
        parts += [f'## {name}', '', path.read_text().rstrip() if path.is_file() else '(missing)', '']
    logs = sorted((run_directory / 'logs').glob('*.log'), key=attempt)
    for prefix in EVIDENCE_LOGS:
        matching = [log for log in logs if log.name.startswith(prefix)]
        # A repeated step logs to name-2.log, name-3.log: the last attempt is the one that counts.
        for path in [log for log in matching if attempt(log)[0] == attempt(matching[0])[0]][-1:]:
            lines = path.read_text(errors='replace').splitlines()
            kept = [line for line in lines if not NOISE.match(line)]
            parts += [f'## logs/{path.name} (last {min(EVIDENCE_TAIL, len(kept))} lines, '
                      f'{len(lines) - len(kept)} noise lines left out)', '', '```text',
                      *kept[-EVIDENCE_TAIL:], '```', '']
    return '\n'.join(parts)


def guide_steps(text):
    """What a guide asks for: its acceptance.py lines by step label, and the log names of its other commands."""
    tool = {}
    for label, kind, rest in re.findall(r'^\$A --step (\S+) (check|do) (.*)$', text, re.M):
        tool[label] = [kind, *shlex.split(rest.split('#')[0])]
    logs = set(re.findall(r'^(?:vm|ops|product) ([0-9][\w.-]*) ', text, re.M))
    logs |= set(re.findall(r'logs/([0-9][\w.-]*)\.log', text))
    return tool, {f'logs/{name}.log' for name in logs}


def same_step(expected, ran):
    """True if a recorded step is the guide's line; a guide argument like "$ONBOOT" or <...> matches any value."""
    return len(expected) == len(ran) and all(
        want == got or want.startswith('$') or want.startswith('<') for want, got in zip(expected, ran))


def compare_with_guide(entries, products, text):
    """Every difference between the steps that ran and the steps the guide asks for.

    Every finished record is compared, not only the first one under a label, so
    an extra command cannot hide behind a correct one. A check may be repeated
    under its label with the same arguments; a do under one label runs once.
    """
    tool, logs = guide_steps(text)
    ran = {}
    for entry in entries:
        if entry['result'] != 'STARTED':
            ran.setdefault(entry['step'], []).append([entry['kind'], entry['command'], *entry['arguments']])
    differences = []
    for label, expected in tool.items():
        if label not in ran:
            differences.append(f'guide step {label} `{" ".join(expected)}` did not run')
            continue
        for command in dict.fromkeys(map(tuple, ran[label])):
            if not same_step(expected, command):
                differences.append(f'step {label} ran `{" ".join(command)}`, the guide says `{" ".join(expected)}`')
        actions = [command for command in ran[label] if command[0] == 'do']
        if len(actions) > 1:
            differences.append(f'step {label} ran a do {len(actions)} times; a do step runs once')
    differences += [f'step {label} `{" ".join(command)}` is not in the guide'
                    for label, commands in ran.items() if label not in tool
                    for command in dict.fromkeys(map(tuple, commands))]
    names = {name for name, _, _ in products}
    differences += [f'guide log {name} is missing' for name in sorted(logs - names)]
    differences += [f'log {name} is not in the guide' for name in sorted(names - logs)]
    return differences


def report(run_directory, guide):
    """Build REPORT.md from record.jsonl, the logs and the guide; return (text, all steps passed)."""
    entries = read_record(run_directory)
    finished = [entry for entry in entries if entry['result'] != 'STARTED']
    finished_logs = {entry['log'] for entry in finished}
    unfinished = [entry for entry in entries if entry['result'] == 'STARTED' and entry['log'] not in finished_logs]
    # Every step records the checkout it ran from; the report never reads git itself,
    # so updating the checkout after the run cannot change what the report says.
    checkouts = sorted({(entry.get('revision', 'unknown'), entry.get('clean', False)) for entry in entries})
    revision = checkouts[0][0] if len(checkouts) == 1 else 'more than one'
    clean = len(checkouts) == 1 and checkouts[0][1] and revision != 'unknown'
    products = product_logs(run_directory, {entry['log'] for entry in entries})

    attention = []
    for entry in finished:
        if entry['result'] != 'PASS':
            attention.append(f'{entry["step"]} {entry["kind"]} {entry["command"]}: {entry["result"]} ({entry["log"]})')
        if entry['approved']:
            attention.append(f'{entry["step"]} {entry["kind"]} {entry["command"]}: run with operator approval '
                             f'"{entry["approved"]}" ({entry["log"]})')
    for entry in unfinished:
        attention.append(f'{entry["step"]} do {entry["command"]}: started but never finished ({entry["log"]})')
    for name, last, _ in products:
        # A log named ...-refused.log records a refusal the guide asks for: it must exit 1.
        expected = 'exit=1' if name.endswith('-refused.log') else 'exit=0'
        if last != expected:
            attention.append(f'{name}: {last}, expected {expected}')
    for name, _, changed in products:
        if '"promoted_now": false' in changed:
            attention.append(f'{name}: "promoted_now": false, the group was promoted before this step '
                             '(an earlier attempt?); a clean run promotes here')
    if not clean:
        described = '; '.join(f'{rev} ({"clean" if ok else "NOT clean"})' for rev, ok in checkouts)
        attention.append(f'the steps did not all run from one clean checkout: {described}')

    seen, repeats = {}, []
    for entry in finished:
        key = (entry['kind'], entry['command'], tuple(entry['arguments']))
        seen.setdefault(key, []).append(entry['step'])
    for (kind, command, arguments), steps in seen.items():
        if len(steps) > 1 and kind == 'check':
            repeats.append(f'{kind} {command} {" ".join(arguments)}: steps {", ".join(steps)}')

    if revision not in ('unknown', 'more than one'):
        shown = subprocess.run(['git', '-C', str(ROOT), 'show', f'{revision}:{GUIDES[guide]}'],
                               capture_output=True, text=True)
        guide_text = shown.stdout if shown.returncode == 0 else ''
    else:
        guide_text = ''
    if guide_text:
        attention += compare_with_guide(entries, products, guide_text)
    else:
        attention.append(f'{GUIDES[guide]} at the recorded revision could not be read to compare the steps')
    failover = failover_time(run_directory)
    if failover is not None and failover > FAILOVER_GOAL:
        attention.append(f'failover took {minutes(failover)} from {FAILOVER_FROM}fence to {FAILOVER_TO}browser, '
                         f'over the goal of {minutes(FAILOVER_GOAL)}')

    passed = not attention and bool(finished)
    counts = {result: sum(entry['result'] == result for entry in finished) for result in ('PASS', 'FAIL', 'REFUSED')}
    lines = [f'# Acceptance run {run_directory.name}', '',
             f'Revision recorded by every step: `{revision}`, checkout '
             f'{"clean at every step" if clean else "NOT clean at every step"}.',
             f'acceptance.py steps: {len(finished)} (PASS {counts["PASS"]}, FAIL {counts["FAIL"]}, '
             f'REFUSED {counts["REFUSED"]}, unfinished {len(unfinished)}); other logs: {len(products)}.',
             '', f'**From the record: {"ALL STEPS PASS" if passed else "NOT CLEAN"}.** '
             f'Compared with {GUIDES[guide]} at that revision: every step and log it names, nothing else.', '']
    if failover is not None:
        lines += [f'Failover (G3): {minutes(failover)} from the fence of the old primary (06-3) to users logging '
                  f'in to both apps on the promoted host (07-8); the goal is under {minutes(FAILOVER_GOAL)}.', '']
    lines += ['## Steps', '', '| Step | Command | Result | Values | Log |', '|---|---|---|---|---|']
    for entry in finished:
        command = ' '.join([entry['kind'], entry['command'], *entry['arguments']])
        lines.append(f'| {entry["step"]} | `{command}` | {entry["result"]} | {short(entry["values"])} | {entry["log"]} |')
    lines += ['', '## Other logs (the product\'s own commands)', '', '| Log | Last exit | JSON |', '|---|---|---|']
    lines += [f'| {name} | {last} | {changed} |' for name, last, changed in products]
    lines += ['', '## Needs attention', '']
    lines += [f'- {item}' for item in attention] or ['- Nothing.']
    lines += ['', '## Repeated checks (allowed; listed for the record)', '']
    lines += [f'- {item}' for item in repeats] or ['- None.']
    lines += ['', '## Time', ''] + timing(run_directory)
    return '\n'.join(lines) + '\n', passed


# The agent guide's command lines, run by "step": one, then the read-only checks right after it.
AGENT_GUIDE = 'docs/ACCEPTANCE-AGENT.md'
HELPERS = 'deploy/scripts/lab/helpers.sh'
STEP_LINE = re.compile(r'^(?:\$A --step (\S+) |(?:vm|ops|product) ([0-9][\w.-]*) )')


CHECK_LINE = re.compile(r'^\$A --step \S+ check ')


def guide_blocks(text):
    """The guide's steps by bash block: for each block that has any, its (name, command line) pairs in order."""
    blocks = []
    for block in re.findall(r'```bash\n(.*?)```', text, re.S):
        steps = []
        for line in block.splitlines():
            match = STEP_LINE.match(line)
            if match:
                steps.append((match.group(1) or match.group(2), line))
        if steps:
            blocks.append(steps)
    return blocks


def guide_lines(text):
    """The guide's steps in order: (name, command line) for each fixed line in its bash blocks."""
    return [step for block in guide_blocks(text) for step in block]


def without_comment(line):
    """The command part of a guide line: everything before its '   # ...' comment."""
    return re.sub(r'\s+#\s.*$', '', line)


# What a product line's comment gives after '→', up to ';', one or more
# separated by commas: a JSON line with those fields ({"changed": true}),
# nothing (no output at all), N× "text" (exactly N output lines contain
# text), only "text" (every output line is text, and there is one), "text"
# (some output line contains text), or a word (a whole output line).
EXPECTATION = re.compile(r'\s*(?:(?P<count>[0-9]+)× "(?P<counted>[^"]*)"|only "(?P<only>[^"]*)"|'
                         r'"(?P<contains>[^"]*)"|(?P<word>[^\s,"]+))\s*(?:,|$)')


def expectations(line):
    """The expectations of a guide line, as (kind, text, count) tuples; [] without '→'; ValueError if unreadable."""
    found = re.search(r'#\s*→\s*([^;]+)', line)
    if not found:
        return []
    text = found.group(1).strip()
    if text.startswith('{'):
        return [('json', text, 0)]
    result, position = [], 0
    while position < len(text):
        match = EXPECTATION.match(text, position)
        if not match or match.end() == position:
            raise ValueError(f'unreadable expectation after →: {text}')
        if match['count']:
            result.append(('count', match['counted'], int(match['count'])))
        elif match['only'] is not None:
            result.append(('only', match['only'], 0))
        elif match['contains'] is not None:
            result.append(('contains', match['contains'], 0))
        elif match['word'] == 'nothing':
            result.append(('nothing', '', 0))
        else:
            result.append(('word', match['word'], 0))
        position = match.end()
    return result


def unmet(expectation, lines):
    """Why the output lines do not meet one expectation, or ''."""
    kind, text, count = expectation
    printed = [line.strip() for line in lines if line.strip()]
    if kind == 'json':
        pairs = re.findall(r'"\w+": (?:true|false|[0-9]+|"[^"]*")', text)
        met = any(line.startswith('{"changed"') and all(pair in line for pair in pairs) for line in printed)
    elif kind == 'nothing':
        met = not printed
    elif kind == 'count':
        met = sum(text in line for line in printed) == count
    elif kind == 'only':
        met = bool(printed) and all(line == text for line in printed)
    elif kind == 'contains':
        met = any(text in line for line in printed)
    else:
        met = text in printed
    if met:
        return ''
    described = {'json': text, 'nothing': 'nothing', 'count': f'{count}× "{text}"', 'only': f'only "{text}"',
                 'contains': f'"{text}"', 'word': text}[kind]
    return f'did not print {described}'


def product_state(run_directory, name, line):
    """'not run', 'running', 'passed', or why a product step failed, read from its log.

    It must end with exit=0 (exit=1 for a *-refused step), and print what its
    comment gives after '→' (EXPECTATION). Only the command's own output
    counts: not the log's '# ' header lines, its exit= line or SSH warnings.
    """
    log = run_directory / 'logs' / f'{name}.log'
    if not log.exists():
        return 'not run'
    lines = log.read_text(errors='replace').splitlines()
    exits = [text for text in lines if text.startswith('exit=')]
    if not exits:
        return 'running'
    wanted = 'exit=1' if name.endswith('-refused') else 'exit=0'
    if exits[-1] != wanted:
        return f'ended with {exits[-1]}, not {wanted}'
    output = [text for text in lines if not (text.startswith(('# start ', '# command:', 'exit='))
                                             or NOISE.match(text))]
    for expectation in expectations(line):
        problem = unmet(expectation, output)
        if problem:
            return problem
    return 'passed'


def tool_state(entries, name):
    """'not run', 'running', 'passed', or why an acceptance.py step failed, read from record.jsonl."""
    results = [entry for entry in entries if entry['step'] == name]
    if not results:
        return 'not run'
    last = results[-1]
    if last['result'] == 'STARTED':
        return 'running'
    return 'passed' if last['result'] == 'PASS' else f'{last["result"]} ({last["log"]})'


def readiness_state(run_directory):
    """'passed', or why the C1a readiness check is not on record: its last attempt in logs/00-readiness.log.

    Run 21 and run 34 ran the check in the terminal but not into that log,
    so the run had no record of it and was not clean; the first step now
    refuses until the log shows a passing attempt.
    """
    log = run_directory / 'logs' / '00-readiness.log'
    if not log.exists():
        return 'has not run into logs/00-readiness.log'
    attempt = log.read_text(errors='replace').split('# start ')[-1].splitlines()
    exits = [line for line in attempt if line.startswith('exit=')]
    if exits[-1:] != ['exit=0'] or 'READY for the agent run.' not in attempt:
        return 'did not end with READY for the agent run. and exit=0 in logs/00-readiness.log'
    return 'passed'


def run_step(run_directory, run_id, name, quiet=False):
    """Run the guide line named name if it is the next step and the one before passed; 0 if it passed.

    A step that passed is followed at once by the read-only check lines right
    after it in the same block, each run and judged the same way, until one
    does not pass. Each is a round of reading and thinking for an agent that
    adds nothing a check needs (A2). A do line, a product line or the end of
    the block always waits for the agent's next call.
    """
    blocks = guide_blocks((ROOT / AGENT_GUIDE).read_text())
    steps = [step for block in blocks for step in block]
    names = [step for step, _ in steps]
    if name not in names:
        raise Refused(f'{name} is not a step in {AGENT_GUIDE} C9')
    entries = read_record(run_directory)

    def state(step, line):
        return tool_state(entries, step) if line.startswith('$A ') else product_state(run_directory, step, line)

    states = [state(step, line) for step, line in steps]
    index = names.index(name)
    line = steps[index][1]
    if states[index] == 'passed':
        # Run 43: the agent dropped the output of a call that had run this check after the step
        # before it, and asked for it again. Nothing runs; it says so and names the next step.
        following = names[states.index('not run')] if 'not run' in states else None
        print(f'STEP {name}: already passed, nothing run (the call that passed it may have run it as a '
              'check right after the step before)')
        print(f'NEXT: $A step {following}' if following else 'NEXT: the final report (C9.11)')
        return 0
    if states[index] != 'not run':
        # C8: a failed read-only check may run once more (after check services passes).
        checks = [entry for entry in entries if entry['step'] == name and entry['result'] != 'STARTED']
        repeat = ' check ' in line and states[index] not in ('passed', 'running') and len(checks) == 1
        if not repeat:
            raise Refused(f'{name} already ran ({states[index]}); a step runs once. STOP and ask the operator')
    else:
        following = names[states.index('not run')]
        if name != following:
            raise Refused(f'the next step is {following}, not {name}')
        if index == 0 and readiness_state(run_directory) != 'passed':
            raise Refused(f'the readiness check (C1a) {readiness_state(run_directory)}: run it as C1a '
                          'writes it, before the first step')
        if index and states[index - 1] != 'passed':
            previous = names[index - 1]
            if states[index - 1] == 'running':
                raise Refused(f'{previous} is still running: wait until its log ends with exit=')
            raise Refused(f'{previous} {states[index - 1]}: STOP (docs/ACCEPTANCE-AGENT.md C3)')

    block = next(block for block in blocks if (name, line) in block)
    checks_after = []
    for step, step_line in block[block.index((name, line)) + 1:]:
        if not CHECK_LINE.match(step_line) or tool_state(entries, step) != 'not run':
            break
        checks_after.append((step, step_line))

    code = run_line(run_directory, run_id, name, line, quiet)
    last = index
    if code == 0 and not without_comment(line).endswith('&'):
        for step, step_line in checks_after:
            if not quiet:
                print(f'\n{step} is the read-only check right after it: run in the same call', flush=True)
            code = run_line(run_directory, run_id, step, step_line, quiet)
            last = names.index(step)
            if code:
                break
    if code == 0 and not quiet:
        print(f'NEXT: $A step {names[last + 1]}' if last + 1 < len(names) else 'NEXT: the final report (C9.11)')
    return code


def run_line(run_directory, run_id, name, line, quiet=False):
    """Run one guide line with the helpers; print STEP NAME: PASS or STOP and return 0 if it passed.

    quiet (acceptance.py run) keeps the step's output off the terminal unless
    it stops; its log holds all of it either way.
    """
    (run_directory / 'logs').mkdir(parents=True, exist_ok=True)
    command = without_comment(line)
    background = command.endswith('&')
    script = f'source {HELPERS}\n{command.rstrip("& ")}\n'
    environment = {**os.environ, 'RUN': str(run_directory), 'RUN_ID': run_id,
                   'A': f'python3 deploy/scripts/lab/acceptance.py --run {run_id}'}
    if not quiet:
        print(f'$ {line}', flush=True)
    if background:
        # A long step (failover, rebuild) runs on even if this process is stopped.
        process = subprocess.Popen(['bash', '-c', script], cwd=ROOT, env=environment, start_new_session=True,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Until bash has written the step's log, the step looks 'not run', and run_all would
        # start it a second time. Wait for the log, or for bash to end without one.
        log = run_directory / 'logs' / f'{name}.log'
        while not log.exists() and process.poll() is None:
            time.sleep(0.05)
        print(f'STARTED in the background ({name}): logs/{name}.log ends with exit= when it is done; '
              'the next step waits for that', flush=True)
        return 0
    output = subprocess.PIPE if quiet else None
    shown = subprocess.run(['bash', '-c', script], cwd=ROOT, env=environment, text=True, stdout=output,
                           stderr=subprocess.STDOUT if quiet else None)
    if line.startswith('$A '):
        result = tool_state(read_record(run_directory), name)  # the acceptance.py step has added its result
    else:
        result = product_state(run_directory, name, line)
    if quiet and result != 'passed' and shown.stdout:
        print('\n'.join(shown.stdout.splitlines()[-30:]))
    print(f'STEP {name}: ' + ('PASS' if result == 'passed' else f'STOP, {result}'), flush=True)
    return 0 if result == 'passed' else 1


# acceptance.py run: the whole guide without an agent. A person starts it
# and types their sudo password once, at the start, for the client trust
# steps; it stops at the first step that does not pass. It runs the same
# guide lines in the same order as an agent run, so its report is the same report.
CLIENT_TRUST = {'03-4a-browser-env': '192.168.0.102', '07-5': '192.168.0.108'}  # the step it comes before
BACKGROUND_LIMIT = 2 * 3600
SUDO_REFRESH_SECONDS = 60  # well inside sudo's default 15-minute timestamp_timeout
HOSTS = Path('/etc/hosts')


def guide_block(text, marker):
    """The bash block of the guide that holds marker, as text."""
    for block in re.findall(r'```bash\n(.*?)```', text, re.S):
        if marker in block:
            return block
    raise RuntimeError(f'the guide has no bash block with {marker!r}')


def password_file():
    """The C6 testuser password file in tmpfs, made (0600, never printed) if it is missing; returns its path."""
    directory = Path(os.environ['XDG_RUNTIME_DIR']) / 'todo-acceptance'
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / 'e2e-password'
    if not path.exists():
        import secrets
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as file:
            file.write(secrets.token_urlsafe(24) + '\n')
    return path


@contextlib.contextmanager
def sudo_kept():
    """Ask for the person's sudo password once, now, and keep sudo's timestamp valid until the block ends.

    sudo -v is the only prompt of the run. A background thread renews the
    timestamp (sudo -n -v, which never prompts) every SUDO_REFRESH_SECONDS;
    at the end, a stop or Ctrl-C, the thread stops and sudo -k drops the
    timestamp, so no root access stays cached after the run. Raises Refused
    if sudo does not accept the password.
    """
    print('sudo asks for your password once, now; the run needs it for the client trust '
          'before 03-4a and 07-5 and does not ask again.', flush=True)
    if subprocess.run(['sudo', '-v']).returncode != 0:
        raise Refused('sudo did not accept the password')
    stop = threading.Event()

    def refresh():
        while not stop.wait(SUDO_REFRESH_SECONDS):
            subprocess.run(['sudo', '-n', '-v'], capture_output=True)

    thread = threading.Thread(target=refresh, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
        subprocess.run(['sudo', '-k'])


# Prepended to the client trust lines: every sudo in them, and in
# trust-serving-ca.sh (export -f), uses the timestamp from sudo_kept and
# fails instead of prompting if it has run out.
SUDO_NO_PROMPT = 'sudo() { command sudo -n "$@"; }\nexport -f sudo\n'


def client_trust(run_directory, text, address):
    """The guide's C9.4 lines for address, with the sudo access from sudo_kept; True if both names then answer.

    The output goes to operator/client-trust-<address>.log, not logs/: it is
    not a guide step, so the report must not count it.
    """
    record = run_directory / 'operator' / f'client-trust-{address}.log'
    if record.exists() and record.read_text().rstrip().endswith('exit=0'):
        return True
    record.parent.mkdir(exist_ok=True)
    block = re.sub(r'^IP=\S+$', f'IP={address}', guide_block(text, 'trust-serving-ca.sh'), count=1, flags=re.M)
    print(f'\nclient trust: auth.test, todo.test and notes.test at {address}, and its CA', flush=True)
    result = subprocess.run(['bash', '-c', 'set -e\n' + SUDO_NO_PROMPT + block], cwd=ROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    hosts = HOSTS.read_text()
    answered = all(subprocess.run(['curl', '--silent', '--fail', '--max-time', '10', url],
                                  capture_output=True).returncode == 0
                   for url in (f'{TODO_URL}/ready', f'{NOTES_URL}/ready'))
    mapped = f'{address} auth.test todo.test notes.test' in hosts
    passed = result.returncode == 0 and mapped and answered
    record.write_text(result.stdout + f'\n/etc/hosts maps the three names to {address}: {mapped}\n'
                      f'both apps answer over trusted HTTPS: {answered}\n'
                      + ('exit=0' if passed else 'exit=1') + '\n')
    print('\n'.join(line for line in result.stdout.splitlines() if not NOISE.match(line)))
    print(f'client trust for {address}: ' + ('done' if passed else f'FAILED, see {record}'), flush=True)
    if 'a password is required' in result.stdout:
        print('sudo access ran out; run the same command again: it asks for the password once more', flush=True)
    return passed


def run_all(run_directory, run_id):
    """Run the whole agent guide in order, unattended after one sudo prompt; 0 if every step passed.

    A clean checkout, then the sudo password (sudo_kept), then run_guide.
    """
    revision, clean = checkout()
    if not clean:
        print(f'STOP: the checkout {revision} is not clean')
        return 1
    print(f'Acceptance run {run_id} of {revision}; logs in {run_directory}/logs', flush=True)
    try:
        with sudo_kept():
            return run_guide(run_directory, run_id)
    except Refused as error:
        print(f'STOP: {error}')
        return 1


def run_guide(run_directory, run_id):
    """The steps of run_all; 0 if every step passed.

    Readiness first (the guide's C1a line), then the password file, then
    each next step through run_step, waiting while a background step runs,
    with the client trust before the steps that need it. It stops at the
    first step that does not pass and can be run again to go on after an
    interruption: a step that ran is never run again. At the end it removes
    the password file and writes REPORT.md and EVIDENCE.md.
    """
    text = (ROOT / AGENT_GUIDE).read_text()
    (run_directory / 'logs').mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, 'RUN': str(run_directory)}
    if readiness_state(run_directory) != 'passed':
        # C1a's line, which appends to logs/00-readiness.log; Part A's only prints.
        line = next(line for line in guide_block(text, '00-readiness.log').splitlines()
                    if 'acceptance_preflight.py' in line and '00-readiness.log' in line).strip()
        subprocess.run(['bash', '-c', line], cwd=ROOT, env=environment)
        if readiness_state(run_directory) != 'passed':
            print(f'STOP: the readiness check {readiness_state(run_directory)}; fix what it names (FAIL lines)')
            return 1
    password_file()
    steps = guide_lines(text)
    waited = 0.0
    while True:
        entries = read_record(run_directory)
        states = [tool_state(entries, name) if line.startswith('$A ') else product_state(run_directory, name, line)
                  for name, line in steps]
        pending = [index for index, state in enumerate(states) if state != 'passed']
        if not pending:
            break
        name, state = steps[pending[0]][0], states[pending[0]]
        if state == 'running':
            if waited >= BACKGROUND_LIMIT:
                print(f'STOP: {name} still runs after {minutes(waited)}; see logs/{name}.log')
                return 1
            if waited % 60 == 0:
                print(f'waiting for {name} to finish (logs/{name}.log)', flush=True)
            time.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            continue
        waited = 0.0
        if state != 'not run':
            print(f'STOP: {name} {state}. Nothing more runs; see its log under {run_directory}/logs')
            return 1
        if name in CLIENT_TRUST and not client_trust(run_directory, text, CLIENT_TRUST[name]):
            print('STOP: the client trust did not complete')
            return 1
        try:
            stopped = run_step(run_directory, run_id, name, quiet=True)
        except Refused as error:
            print(f'STOP: {error}')
            return 1
        if stopped:
            print(f'STOP at {name}. Nothing more runs; its log is under {run_directory}/logs')
            return 1
    password_file().unlink()
    report_text, passed = report(run_directory, 'full')
    (run_directory / 'REPORT.md').write_text(report_text)
    (run_directory / 'EVIDENCE.md').write_text(evidence(run_directory))
    print('\n' + report_text.split('## Steps')[0].strip())
    print(f'\nREPORT.md and EVIDENCE.md are in {run_directory}; send EVIDENCE.md for review.')
    return 0 if passed else 1


def main(argv=None):
    """Run one step, check, do or report; return the exit status from the module docstring."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', required=True, help='run ID, the run folder name')
    parser.add_argument('--step', help='phase and step from the guide, for example 03-9 (not for report)')
    parser.add_argument('--user', default='gunstein', help='service user on the VMs')
    parser.add_argument('--operator-approved', default='', help='why the operator allows a second run')
    parser.add_argument('kind', choices=('check', 'do', 'report', 'step', 'evidence', 'run'))
    parser.add_argument('command', nargs='?')
    parser.add_argument('arguments', nargs='*')
    args = parser.parse_args(argv)
    runs = Path(os.environ.get('ACCEPTANCE_RUNS', Path.home() / 'todo-acceptance-runs'))
    if not re.fullmatch(r'[A-Za-z0-9.-]+', args.run) or args.run.strip('.') == '':
        parser.error('--run looks like 2026-09-27-app-ops-13')
    if args.kind == 'report':
        if args.command not in GUIDES or args.arguments or not (runs / args.run / 'record.jsonl').is_file():
            parser.error('report takes full (the guide to compare with) and needs a run with record.jsonl')
        text, passed = report(runs / args.run, args.command)
        (runs / args.run / 'REPORT.md').write_text(text)
        print(text, end='')
        return 0 if passed else 1
    if args.kind == 'evidence':
        if args.command or args.arguments or not (runs / args.run).is_dir():
            parser.error('evidence takes no command and needs an existing run folder')
        path = runs / args.run / 'EVIDENCE.md'
        path.write_text(evidence(runs / args.run))
        print(f'Wrote {path}\nCopy it in one go: wl-copy < {path}   (X11: xclip -selection clipboard < {path})')
        return 0
    if args.kind == 'run':
        if args.command or args.arguments or args.step:
            parser.error('run takes no command: it runs the whole guide')
        return run_all(runs / args.run, args.run)
    if args.kind == 'step':
        if not args.command or args.arguments or args.step or not re.fullmatch(r'[0-9]{2}-[\w.-]+', args.command):
            parser.error('step takes one step name from the guide, for example 06-6-preflight or 06-3')
        try:
            return run_step(runs / args.run, args.run, args.command)
        except Refused as error:
            print(f'REFUSED: {error}', file=sys.stderr)
            return 3
    name = args.command
    if not name or not args.step:
        parser.error('check and do need --step and a command')
    if (args.kind, name) not in COMMANDS:
        parser.error(f'unknown command: {args.kind} {name}')
    function, validators, once = COMMANDS[args.kind, name]
    if not re.fullmatch(r'[0-9]{2}-[0-9A-Za-z-]+', args.step) or not re.fullmatch(r'[A-Za-z0-9.-]+', args.run):
        parser.error('--step looks like 03-9 and --run like 2026-09-27-app-ops-13')
    if len(args.arguments) != len(validators):
        parser.error(f'{args.kind} {name} takes {len(validators)} argument(s)')
    try:
        arguments = [validate(text) for validate, text in zip(validators, args.arguments)]
    except ValueError as error:
        parser.error(str(error))
    run_directory = runs / args.run
    run_directory.mkdir(parents=True, exist_ok=True)
    reason = refusal(run_directory, name, arguments, once, args.operator_approved) if args.kind == 'do' else None
    step = Step(run_directory, args.step, args.kind, name, arguments, args.user)
    step.log(f'# {datetime.datetime.now().astimezone().isoformat(timespec="seconds")} '
             f'{args.kind} {name} {" ".join(arguments)}')
    if args.operator_approved:
        step.log(f'# operator approved: {args.operator_approved}')

    revision, clean = checkout()
    if not clean:
        step.log(f'# checkout {revision} is not clean')

    def record(result):
        append_record(run_directory, {
            'revision': revision, 'clean': clean,
            'time': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'step': args.step,
            'kind': args.kind, 'command': name, 'arguments': arguments, 'result': result,
            'values': step.values, 'log': str(step.log_path.relative_to(run_directory)),
            'approved': args.operator_approved})

    result = 'FAIL'
    try:
        if reason:
            raise Refused(reason)
        if args.kind == 'do':
            record('STARTED')
        function(step, *arguments)
        result = 'FAIL' if step.failures else 'PASS'
    except Refused as error:
        step.log(f'REFUSED: {error}')
        result = 'REFUSED'
    except Exception as error:  # the tool itself broke: record it, never lose the attempt
        step.failures.append(f'{type(error).__name__}: {error}')
    except KeyboardInterrupt:
        step.failures.append('interrupted')
    finally:
        step.log(f'RESULT: {result}' + (f' ({"; ".join(step.failures)})' if step.failures else ''))
        step.log_file.close()
        record(result)
    return {'PASS': 0, 'FAIL': 1, 'REFUSED': 3}[result]


if __name__ == '__main__':
    sys.exit(main())
