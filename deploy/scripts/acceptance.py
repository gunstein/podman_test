#!/usr/bin/env python3
"""Fixed acceptance steps for the Proxmox lab, each with its own log (BACKLOG A1).

Runs on the client/build host from the repository root. It never replaces the
product's own commands (install.sh, app-ops, app_dr.py, app_backup.py): those
are what acceptance tests, and they run exactly as docs/ACCEPTANCE.md writes
them. This tool runs the glue around them the same way every time, compares
the result with the expected values itself and records it.

  acceptance.py --run RUN_ID --step 03-4 check services 192.168.0.102 app
  acceptance.py --run RUN_ID --step 03-9 do reboot 107 192.168.0.102 app
  acceptance.py --run RUN_ID report

check   reads only and may be repeated.
do      changes state. After a FAIL it refuses the same command with the same
        arguments for the rest of the run, unless --operator-approved says why.
        Some do commands (markers, firewall rules) refuse any second run.

Each call writes logs/<step>-<kind>-<command>.log in the run folder
(~/todo-acceptance-runs/RUN_ID, or $ACCEPTANCE_RUNS/RUN_ID) and appends one
JSON line to record.jsonl. Exit status: 0 PASS, 1 FAIL, 2 usage, 3 refused.

report writes REPORT.md in the run folder from record.jsonl and the other
logs, so no value in the run record is copied by hand (BACKLOG A3). It exits
0 only if every step passed, no do was left unfinished or needed approval,
every other log ends in exit=0 and the checkout is clean.

Commands:
  do    rollback VMID SNAPSHOT HOST      reset the VM, start it, wait for SSH
  check clean-host HOST                  no Todo state, security services on
  do    firewall-https HOST CLIENT_IP    permanent rich rule for 8443, reloaded
  check services HOST app|standby        wait-ready.sh, no failed units, nginx -t
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
"""
import argparse
import datetime
import ipaddress
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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
        """Run a bash script on host as the service user, with host-key checking on."""
        return self.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', f'{self.user}@{host}',
                         'bash', '-s', '--', *arguments], input=script, timeout=timeout)

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


def check_replication_tls(step, host):
    """On the primary: each database has a streaming standby connection, and it uses TLS."""
    for database in DATABASES:
        rows = psql(step, host, database, 'SELECT application_name, state, ssl, version FROM pg_stat_replication '
                                          'JOIN pg_stat_ssl USING (pid);').stdout.split()
        step.values[database] = rows
        step.expect(bool(rows) and all(row.split('|')[1:3] == ['streaming', 't'] for row in rows),
                    f'{database} on {host}: streaming over TLS ({", ".join(rows) or "no standby connection"})')


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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe():
            return True
        time.sleep(POLL_SECONDS)
    step.log(f'gave up waiting for {what} after {timeout}s')
    return False


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


def report(run_directory):
    """Build REPORT.md from record.jsonl and the logs; return (text, all steps passed)."""
    entries = read_record(run_directory)
    finished = [entry for entry in entries if entry['result'] != 'STARTED']
    finished_logs = {entry['log'] for entry in finished}
    unfinished = [entry for entry in entries if entry['result'] == 'STARTED' and entry['log'] not in finished_logs]
    revision = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    status = subprocess.run(['git', '-C', str(ROOT), 'status', '--porcelain'], capture_output=True, text=True)
    clean = revision.returncode == 0 and status.returncode == 0 and not status.stdout.strip()
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
        if last != 'exit=0':
            attention.append(f'{name}: {last}')
    if not clean:
        attention.append('the checkout is not clean, or its revision could not be read')

    seen, repeats = {}, []
    for entry in finished:
        key = (entry['kind'], entry['command'], tuple(entry['arguments']))
        seen.setdefault(key, []).append(entry['step'])
    for (kind, command, arguments), steps in seen.items():
        if len(steps) > 1 and kind == 'check':
            repeats.append(f'{kind} {command} {" ".join(arguments)}: steps {", ".join(steps)}')

    passed = not attention and bool(finished)
    counts = {result: sum(entry['result'] == result for entry in finished) for result in ('PASS', 'FAIL', 'REFUSED')}
    lines = [f'# Acceptance run {run_directory.name}', '',
             f'Revision: `{revision.stdout.strip() or "unknown"}`, checkout {"clean" if clean else "NOT clean"}.',
             f'acceptance.py steps: {len(finished)} (PASS {counts["PASS"]}, FAIL {counts["FAIL"]}, '
             f'REFUSED {counts["REFUSED"]}, unfinished {len(unfinished)}); other logs: {len(products)}.',
             '', f'**From the record: {"ALL STEPS PASS" if passed else "NOT CLEAN"}.** '
             'A CLEAN PASS of a full run also needs every step of the guide; check the step list against it.', '',
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
        if args.command or not (runs / args.run / 'record.jsonl').is_file():
            parser.error('report takes no arguments and needs an existing run with record.jsonl')
        text, passed = report(runs / args.run)
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

    def record(result):
        append_record(run_directory, {
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
