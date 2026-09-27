#!/usr/bin/env python3
"""Fixed acceptance steps for the Proxmox lab, each with its own log (BACKLOG A1).

Runs on the client/build host from the repository root. It never replaces the
product's own commands (install.sh, app-ops, app_dr.py, app_backup.py): those
are what acceptance tests, and they run exactly as docs/ACCEPTANCE.md writes
them. This tool runs the glue around them the same way every time, compares
the result with the expected values itself and records it.

  acceptance.py --run RUN_ID --step 03-4 check services 192.168.0.102 app
  acceptance.py --run RUN_ID --step 03-9 do reboot 107 192.168.0.102 app
  acceptance.py --run RUN_ID report full       (or quick)

check   reads only and may be repeated.
do      changes state. After a FAIL it refuses the same command with the same
        arguments for the rest of the run, unless --operator-approved says why.
        Some do commands (markers, firewall rules) refuse any second run.

Each call writes logs/<step>-<kind>-<command>.log in the run folder
(~/todo-acceptance-runs/RUN_ID, or $ACCEPTANCE_RUNS/RUN_ID) and appends one
JSON line to record.jsonl. Exit status: 0 PASS, 1 FAIL, 2 usage, 3 refused.

report writes REPORT.md in the run folder from record.jsonl and the other
logs, so no value in the run record is copied by hand (BACKLOG A3), and
compares the run with the guide (full: ACCEPTANCE-AGENT.md, quick:
ACCEPTANCE-QUICK.md) as it was at the recorded revision: every acceptance.py
line and every log the guide names must be there, and nothing else. It exits
0 only if every step passed, no do was left unfinished or needed approval,
every other log ends in exit=0 (exit=1 for a log named *-refused.log, a
refusal the guide asks for) and every step ran from the same clean
checkout. Each record line carries the revision and cleanliness of the
checkout at the time of that step; report never reads git itself.

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
"""
import argparse
import datetime
import ipaddress
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'deploy/installer'))
from app_installer import apps  # noqa: E402  (the registry of services, read-only)

TODO_URL = 'https://todo.test:8443'
NOTES_URL = 'https://notes.test:8443'
IDENTITY_ORIGIN = TODO_URL
ZONE = 'public'  # the firewalld zone of the lab VMs' LAN interface (ACCEPTANCE.md phase 3)
CA_PATH = '/var/lib/todo-tls/ca.crt'
PYTHON = ROOT / 'todo-backend/.venv/bin/python'
REBOOT_TIMEOUT = 600
POLL_SECONDS = 10
FIREWALL_SETTLE_SECONDS = 20  # the Proxmox firewall applies changes about every 10 s
DATABASES = ('todo', 'notes', 'keycloak')  # apps.REPLICATED_DATABASES; user and container share the name
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


def source_address(text):
    """Where a connection starts: the client itself, or a VM by its IPv4 address."""
    return text if text == 'client' else host_address(text)
SNAPSHOT = matching(r'[A-Za-z0-9_-]+')
MODE = matching(r'app|standby')
PHASE = matching(r'phase[0-9]+')


# --- checks (read only) -------------------------------------------------------------

def check_clean_host(step, host):
    """Phase 1: security services on, SELinux enforcing, rootless Podman, no Todo state.

    Target prerequisites such as python3-jinja2 are preflight.sh's job in phase 3.
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


def check_services(step, host, mode):
    """All services up (wait-ready.sh), no failed user units, and a valid nginx configuration."""
    ready = step.ssh(host, (ROOT / 'deploy/scripts/wait-ready.sh').read_text(), mode, timeout=400)
    step.expect(ready.returncode == 0 and 'READY:' in ready.stdout, f'wait-ready.sh {mode} printed READY')
    failed = step.ssh(host, 'systemctl --user --failed --no-legend --plain')
    step.expect(failed.returncode == 0 and not failed.stdout.strip(), 'no failed user units')
    if mode == 'app':
        nginx = step.ssh(host, 'podman exec nginx nginx -t -c /etc/todo-nginx/nginx.conf')
        step.expect(nginx.returncode == 0, 'nginx configuration is valid')
    else:
        serving = apps.services(databases=False)
        states = step.ssh(host, 'systemctl --user is-active ' + ' '.join(serving)).stdout.split()
        step.expect(len(states) == len(serving) and 'active' not in states,
                    'a database-only standby runs none of: ' + ', '.join(serving))


def fingerprint(text):
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
    url = TODO_URL + '/auth/realms/todo'
    result = step.run(['curl', '--silent', '--show-error', '--head', '--max-time', '10', url])
    step.expect(result.returncode == 0 and 'strict-transport-security' in headers(result.stdout),
                f'{url}: Strict-Transport-Security')


def password_environment(**extra):
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


# --- do (changes state) -------------------------------------------------------------

def pve(step, *arguments):
    return step.run([sys.executable, ROOT / 'deploy/scripts/pve_lab.py', *arguments], timeout=900)


def boot_id(step, host):
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
    if step.expect(result.returncode == 0 and match is not None, f'markers "{title}" created'):
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
    script = (ROOT / 'deploy/scripts/ports-closed.sh').read_text()
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
    result = pve(step, 'exec', vmid, '--', '/opt/todo/bin/app-quarantine.sh', action, name, step.user)
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
    services = apps.services()
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
    key = step.ssh(source, "test -f ~/.ssh/id_rsa || ssh-keygen -q -t rsa -b 3072 -N '' -C todo-ops-control "
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


GUIDES = {'full': 'docs/ACCEPTANCE-AGENT.md', 'quick': 'docs/ACCEPTANCE-QUICK.md'}


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
    """Every difference between the steps that ran and the steps the guide asks for."""
    tool, logs = guide_steps(text)
    ran = {}
    for entry in entries:
        if entry['result'] != 'STARTED':
            ran.setdefault(entry['step'], [entry['kind'], entry['command'], *entry['arguments']])
    differences = []
    for label, expected in tool.items():
        if label not in ran:
            differences.append(f'guide step {label} `{" ".join(expected)}` did not run')
        elif not same_step(expected, ran[label]):
            differences.append(f'step {label} ran `{" ".join(ran[label])}`, the guide says `{" ".join(expected)}`')
    differences += [f'step {label} `{" ".join(command)}` is not in the guide' for label, command in ran.items()
                    if label not in tool]
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

    passed = not attention and bool(finished)
    counts = {result: sum(entry['result'] == result for entry in finished) for result in ('PASS', 'FAIL', 'REFUSED')}
    lines = [f'# Acceptance run {run_directory.name}', '',
             f'Revision recorded by every step: `{revision}`, checkout '
             f'{"clean at every step" if clean else "NOT clean at every step"}.',
             f'acceptance.py steps: {len(finished)} (PASS {counts["PASS"]}, FAIL {counts["FAIL"]}, '
             f'REFUSED {counts["REFUSED"]}, unfinished {len(unfinished)}); other logs: {len(products)}.',
             '', f'**From the record: {"ALL STEPS PASS" if passed else "NOT CLEAN"}.** '
             f'Compared with {GUIDES[guide]} at that revision: every step and log it names, nothing else.', '',
             '## Steps', '', '| Step | Command | Result | Values | Log |', '|---|---|---|---|---|']
    for entry in finished:
        command = ' '.join([entry['kind'], entry['command'], *entry['arguments']])
        lines.append(f'| {entry["step"]} | `{command}` | {entry["result"]} | {short(entry["values"])} | {entry["log"]} |')
    lines += ['', '## Other logs (the product\'s own commands)', '', '| Log | Last exit | JSON |', '|---|---|---|']
    lines += [f'| {name} | {last} | {changed} |' for name, last, changed in products]
    lines += ['', '## Needs attention', '']
    lines += [f'- {item}' for item in attention] or ['- Nothing.']
    lines += ['', '## Repeated checks (allowed; listed for the record)', '']
    lines += [f'- {item}' for item in repeats] or ['- None.']
    return '\n'.join(lines) + '\n', passed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', required=True, help='run ID, the run folder name')
    parser.add_argument('--step', help='phase and step from the guide, for example 03-9 (not for report)')
    parser.add_argument('--user', default='gunstein', help='service user on the VMs')
    parser.add_argument('--operator-approved', default='', help='why the operator allows a second run')
    parser.add_argument('kind', choices=('check', 'do', 'report'))
    parser.add_argument('command', nargs='?')
    parser.add_argument('arguments', nargs='*')
    args = parser.parse_args(argv)
    runs = Path(os.environ.get('ACCEPTANCE_RUNS', Path.home() / 'todo-acceptance-runs'))
    if not re.fullmatch(r'[A-Za-z0-9.-]+', args.run) or args.run.strip('.') == '':
        parser.error('--run looks like 2026-09-27-app-ops-13')
    if args.kind == 'report':
        if args.command not in GUIDES or args.arguments or not (runs / args.run / 'record.jsonl').is_file():
            parser.error('report takes full or quick (the guide to compare with) and needs a run with record.jsonl')
        text, passed = report(runs / args.run, args.command)
        (runs / args.run / 'REPORT.md').write_text(text)
        print(text, end='')
        return 0 if passed else 1
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
