#!/usr/bin/env python3
"""Fixed acceptance steps for the Proxmox lab, each with its own log (BACKLOG A1).

Runs on the client/build host from the repository root. It never replaces the
product's own commands (install.sh, app-ops, app_dr.py, app_backup.py): those
are what acceptance tests, and they run exactly as docs/ACCEPTANCE.md writes
them. This tool runs the glue around them the same way every time, compares
the result with the expected values itself and records it.

  acceptance.py --run RUN_ID --step 03-4 check services 192.168.0.102 app
  acceptance.py --run RUN_ID --step 03-9 do reboot 107 192.168.0.102 app

check   reads only and may be repeated.
do      changes state. After a FAIL it refuses the same command with the same
        arguments for the rest of the run, unless --operator-approved says why.
        Some do commands (markers, firewall rules) refuse any second run.

Each call writes logs/<step>-<kind>-<command>.log in the run folder
(~/todo-acceptance-runs/RUN_ID, or $ACCEPTANCE_RUNS/RUN_ID) and appends one
JSON line to record.jsonl. Exit status: 0 PASS, 1 FAIL, 2 usage, 3 refused.

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
}


def refusal(run_directory, name, arguments, once, approved):
    """Why this do command must not run now, or None."""
    for entry in read_record(run_directory):
        if entry['kind'] != 'do' or entry['command'] != name or entry['arguments'] != arguments:
            continue
        if entry['result'] == 'FAIL' and not approved:
            return f'"do {name}" failed earlier in this run ({entry["log"]}); a second run needs --operator-approved'
        if entry['result'] == 'PASS' and once:
            return f'"do {name}" already ran in this run ({entry["log"]}); it runs only once'
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', required=True, help='run ID, the run folder name')
    parser.add_argument('--step', required=True, help='phase and step from the guide, for example 03-9')
    parser.add_argument('--user', default='gunstein', help='service user on the VMs')
    parser.add_argument('--operator-approved', default='', help='why the operator allows a second run')
    parser.add_argument('kind', choices=('check', 'do'))
    parser.add_argument('command')
    parser.add_argument('arguments', nargs='*')
    args = parser.parse_args(argv)
    name = args.command
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
    run_directory = Path(os.environ.get('ACCEPTANCE_RUNS', Path.home() / 'todo-acceptance-runs')) / args.run
    run_directory.mkdir(parents=True, exist_ok=True)
    reason = refusal(run_directory, name, arguments, once, args.operator_approved) if args.kind == 'do' else None
    step = Step(run_directory, args.step, args.kind, name, arguments, args.user)
    step.log(f'# {datetime.datetime.now().astimezone().isoformat(timespec="seconds")} '
             f'{args.kind} {name} {" ".join(arguments)}')
    if args.operator_approved:
        step.log(f'# operator approved: {args.operator_approved}')
    try:
        if reason:
            raise Refused(reason)
        function(step, *arguments)
        result = 'FAIL' if step.failures else 'PASS'
    except Refused as error:
        step.log(f'REFUSED: {error}')
        result = 'REFUSED'
    step.log(f'RESULT: {result}' + (f' ({"; ".join(step.failures)})' if step.failures else ''))
    step.log_file.close()
    append_record(run_directory, {
        'time': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'step': args.step,
        'kind': args.kind, 'command': name, 'arguments': arguments, 'result': result,
        'values': step.values, 'log': str(step.log_path.relative_to(run_directory)),
        'approved': args.operator_approved})
    return {'PASS': 0, 'FAIL': 1, 'REFUSED': 3}[result]


if __name__ == '__main__':
    sys.exit(main())
