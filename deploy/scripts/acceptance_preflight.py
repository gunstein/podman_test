#!/usr/bin/env python3
"""Read-only readiness check before an agent acceptance run (docs/ACCEPTANCE-AGENT.md).

Run on the client/build host from the repository root. It changes nothing:
local checks, Proxmox API GET requests and read-only SSH commands only.

  python3 deploy/scripts/acceptance_preflight.py
  python3 deploy/scripts/acceptance_preflight.py --snapshot clean-agent --revision <sha>

Prints PASS/WARN/FAIL per check and exits 1 if anything FAILed.
"""
import argparse
import os
import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pve_lab  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_PRIVILEGES = ('VM.Audit', 'VM.PowerMgmt', 'VM.Config.Network', 'VM.Config.Options',
                       'VM.Snapshot.Rollback')
GUEST_EXEC_PRIVILEGES = ('VM.Monitor', 'VM.GuestAgent.Unrestricted')


class Report:
    """Prints one PASS/WARN/FAIL/INFO line per check and counts the FAILs."""
    def __init__(self):
        self.failed = 0

    def line(self, level, name, detail=''):
        """Print one result line; a FAIL is counted."""
        if level == 'FAIL':
            self.failed += 1
        print(f'{level:4}  {name}' + (f': {detail}' if detail else ''))

    def check(self, condition, name, detail='', level='FAIL'):
        """PASS if condition holds, otherwise level (FAIL or WARN); returns condition."""
        self.line('PASS' if condition else level, name, detail)
        return condition


def run(argv, timeout=20):
    """Run a local command; return (exit code, stdout, stderr), with exit 1 if it could not run."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, '', str(error)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def check_local(report, args):
    """The client/build host: clean checkout at the kickoff revision, and the tools the run needs."""
    print('== Client/build host')
    code, head, _ = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'])
    report.check(code == 0, 'Git checkout', head)
    code, dirty, _ = run(['git', '-C', str(ROOT), 'status', '--porcelain'])
    report.check(code == 0 and not dirty, 'Clean working tree', 'uncommitted changes' if dirty else '')
    code, branch, _ = run(['git', '-C', str(ROOT), 'rev-parse', '--abbrev-ref', 'HEAD'])
    report.check(branch == 'feature/podman-kube', 'Branch feature/podman-kube', branch, level='WARN')
    if args.revision:
        report.check(head == args.revision, 'HEAD equals kickoff revision', args.revision)
    code, remote, _ = run(['git', '-C', str(ROOT), 'ls-remote', 'origin', 'refs/heads/feature/podman-kube'])
    report.check(code == 0 and remote.split()[:1] == [head], 'HEAD is pushed to origin',
                 remote.split()[0] if remote else 'could not read origin', level='WARN')

    for tool in ('python3', 'podman', 'ssh', 'scp', 'ssh-keygen', 'openssl', 'certutil', 'curl', 'tar', 'sha256sum'):
        report.check(shutil.which(tool) is not None, f'Tool {tool}')
    report.check(sys.version_info >= (3, 9), 'Python 3.9 or newer', sys.version.split()[0])
    code, _, error = run([sys.executable, '-c', 'import venv, ensurepip'])
    report.check(code == 0, 'Python venv and ensurepip', error)
    code, rootless, _ = run(['podman', 'info', '--format', '{{.Host.Security.Rootless}}'])
    report.check(rootless == 'true', 'Rootless Podman on the build host', rootless)

    runtime = os.environ.get('XDG_RUNTIME_DIR', '')
    report.check(bool(runtime) and Path(runtime).is_dir(), 'XDG_RUNTIME_DIR for the tmpfs password file', runtime)
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 8080))
            free = True
        except OSError:
            free = False
    report.check(free, 'Local port 8080 free (SSH tunnel for test-user provisioning)')
    nssdb = [path for path in (Path.home() / '.pki/nssdb', Path.home() / '.local/share/pki/nssdb') if path.is_dir()]
    report.check(bool(nssdb), 'Chromium NSS database', str(nssdb[0]) if nssdb else
                 'none yet; created on first Chromium start', level='WARN')
    hosts = [line for line in Path('/etc/hosts').read_text().splitlines()
             if 'todo.test' in line or 'notes.test' in line]
    report.line('INFO', '/etc/hosts entries', '; '.join(hosts) or 'none')
    code, _, _ = run(['curl', '-sS', '-o', '/dev/null', '--max-time', '10', 'https://pypi.org/simple/'])
    report.check(code == 0, 'Internet access for pip/Playwright downloads', level='WARN')


def check_proxmox(report, args):
    """The token, its privileges, both VMs and their snapshots, and the Proxmox firewalls.

    Only GET requests: nothing in Proxmox changes.
    """
    print('== Proxmox API (GET only)')
    env = Path(os.environ.get('PVE_ENV', Path.home() / '.config/todo-acceptance/pve.env'))
    if not report.check(env.is_file(), 'Token file', str(env)):
        return
    mode = stat.S_IMODE(env.stat().st_mode)
    report.check(mode & 0o077 == 0, 'Token file not readable by others', oct(mode))
    try:
        config = pve_lab.load_config()
    except (pve_lab.LabError, OSError) as error:
        report.check(False, 'Token file contents', str(error))
        return
    report.check(Path(config['PVE_CA']).is_file(), 'Proxmox CA file', config['PVE_CA'])
    client = pve_lab.Client(config)
    try:
        version = client.request('GET', '/version')
    except (pve_lab.LabError, OSError) as error:
        report.check(False, 'API reachable with token and verified TLS', str(error))
        return
    report.check(True, 'API reachable with token and verified TLS', 'PVE ' + str(version.get('version')))

    for vmid, name in ((args.primary_vmid, 'primary'), (args.standby_vmid, 'standby')):
        try:
            permissions = client.request('GET', f'/access/permissions?path=/vms/{vmid}').get(f'/vms/{vmid}', {})
            vm = client.request('GET', f'/nodes/{{node}}/qemu/{vmid}/config')
            status = client.request('GET', f'/nodes/{{node}}/qemu/{vmid}/status/current')
            snapshots = [item['name'] for item in client.request('GET', f'/nodes/{{node}}/qemu/{vmid}/snapshot')]
            firewall = client.request('GET', f'/nodes/{{node}}/qemu/{vmid}/firewall/options')
        except (pve_lab.LabError, OSError, KeyError) as error:
            report.check(False, f'VM {vmid} ({name}) readable', str(error))
            continue
        missing = [p for p in REQUIRED_PRIVILEGES if not permissions.get(p)]
        report.check(not missing, f'VM {vmid} token privileges', 'missing ' + ', '.join(missing) if missing else '')
        report.check(any(permissions.get(p) for p in GUEST_EXEC_PRIVILEGES), f'VM {vmid} Guest Agent exec privilege',
                     ' or '.join(GUEST_EXEC_PRIVILEGES))
        report.check(str(vm.get('agent', '')).split(',')[0] in ('1', 'enabled=1'), f'VM {vmid} QEMU Guest Agent enabled',
                     str(vm.get('agent', 'not set')))
        report.check(args.snapshot in snapshots, f'VM {vmid} snapshot {args.snapshot!r}',
                     'found: ' + ', '.join(name for name in snapshots if name != 'current'))
        nics = {key: value for key, value in vm.items() if key.startswith('net') and key[3:].isdigit()}
        report.check(bool(nics), f'VM {vmid} network devices', '; '.join(f'{k}={v}' for k, v in sorted(nics.items())))
        down = [key for key, value in nics.items() if 'link_down=1' in value.split(',')]
        report.check(not down, f'VM {vmid} links connected', ', '.join(down), level='WARN')
        report.check(not firewall.get('enable'), f'VM {vmid} VM firewall disabled',
                     'enabled; phase 1 will inspect its rules' if firewall.get('enable') else '', level='WARN')
        report.line('INFO', f'VM {vmid} status', f'{status.get("status")}, onboot={vm.get("onboot", "unset")}')
        try:
            rules = client.request('GET', f'/nodes/{{node}}/qemu/{vmid}/firewall/rules')
        except (pve_lab.LabError, OSError) as error:
            report.check(False, f'VM {vmid} firewall rules readable', str(error))
        else:
            stale = ', '.join(f'{r.get("pos")}:{r.get("comment", "no comment")}' for r in rules)
            report.check(not rules, f'VM {vmid} has no leftover firewall rules',
                         f'from an earlier run: {stale}; C9.6 step 4 clears todo-quarantine-* ones' if rules else '',
                         level='WARN')
    try:
        sdn_permissions = client.request('GET', '/access/permissions?path=/sdn').get('/sdn', {})
        report.check(bool(sdn_permissions.get('SDN.Use')), 'Token has SDN.Use on /sdn (needed to change VM NICs)',
                     'see ACCEPTANCE-AGENT.md A1 (pveum aclmod /sdn ... PVESDNUser)')
        node_firewall = client.request('GET', '/nodes/{node}/firewall/options')
        report.check(bool(node_firewall.get('enable')), "Node's own firewall enabled (needed for VM quarantine)",
                     '' if node_firewall.get('enable') else 'disabled: see ACCEPTANCE-AGENT.md A1 (pve-firewall)')
        cluster = client.request('GET', '/cluster/firewall/options')
        report.check(bool(cluster.get('enable')), 'Datacenter firewall enabled (needed for VM quarantine)',
                     '' if cluster.get('enable') else 'disabled: the agent will ask you in phase 5')
        resources = [item.get('sid') for item in client.request('GET', '/cluster/ha/resources')]
        for vmid in (args.primary_vmid, args.standby_vmid):
            report.check(f'vm:{vmid}' not in resources, f'VM {vmid} not an HA resource', level='WARN')
    except (pve_lab.LabError, OSError) as error:
        report.check(False, 'Cluster firewall/HA readable (PVEAuditor on /)', str(error))


SSH_CHECKS = r'''
echo "hostname=$(hostname)"
echo "client=${SSH_CLIENT%% *}"
echo "selinux=$(getenforce)"
for unit in sshd firewalld fapolicyd qemu-guest-agent; do echo "unit_$unit=$(systemctl is-active $unit)"; done
echo "linger=$(loginctl show-user "$USER" -p Linger --value)"
echo "rootless=$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null)"
echo "podman=$(podman --version 2>/dev/null)"
echo "jinja2_yaml=$(python3 -c 'import jinja2, yaml' 2>/dev/null && echo ok || echo missing)"
echo "sudo=$(sudo -n true 2>/dev/null && echo ok || echo password-required)"
echo "sudoers_file=$(sudo -n test -e /etc/sudoers.d/90-todo-acceptance 2>/dev/null && echo present || echo missing)"
echo "mem_mib=$(free -m | awk '/^Mem:/ {print $2}')"
echo "home_free=$(df -h --output=avail "$HOME" | tail -1 | tr -d ' ')"
echo "todo_state=$(podman ps -a --format '{{.Names}}' 2>/dev/null | grep -cE '^(todo|notes|keycloak|nginx)') containers"
'''


def check_guest(report, args, address, hostname):
    """One VM over SSH: identity, security services, rootless Podman, sudo and leftover state."""
    # The running VM, which may differ from its clean snapshot; phase 1 checks again after rollback.
    print(f'== {hostname} ({address}) over SSH, read-only (running state, not the snapshot)')
    code, out, error = _ssh(args, address)
    if not report.check(code == 0, 'Key-based SSH with verified host key', error.splitlines()[-1] if error else ''):
        if 'Permission denied' in error:
            report.line('INFO', 'Your SSH key is not authorized for this user; see ACCEPTANCE-AGENT.md A3 '
                        '(ssh-copy-id before taking the clean snapshot)')
        else:
            report.line('INFO', 'VM may be powered off or unreachable')
        return
    facts = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    report.check(facts.get('hostname') == hostname, 'Hostname', facts.get('hostname', ''))
    report.check(facts.get('client') == args.client_ip, 'Client source IP seen by the VM',
                 f'{facts.get("client")} (kickoff says {args.client_ip})')
    report.check(facts.get('selinux') == 'Enforcing', 'SELinux Enforcing', facts.get('selinux', ''))
    for unit in ('sshd', 'firewalld', 'fapolicyd', 'qemu-guest-agent'):
        report.check(facts.get('unit_' + unit) == 'active', f'{unit} active', facts.get('unit_' + unit, ''))
    report.check(facts.get('linger') == 'yes', 'User lingering', facts.get('linger', ''))
    report.check(facts.get('rootless') == 'true', 'Rootless Podman', facts.get('podman', ''))
    report.check(facts.get('jinja2_yaml') == 'ok', 'Python Jinja2 and PyYAML installed',
                 'run deploy/scripts/prepare-agent-snapshots.sh, or dnf install -y python3-jinja2 python3-pyyaml')
    report.check(facts.get('sudo') == 'ok', 'Passwordless sudo in the running VM', facts.get('sudo', ''), level='WARN')
    report.line('INFO', 'Lab sudoers file', facts.get('sudoers_file', '') +
                ' (what matters is that the clean snapshot contains it)')
    # A 4 GiB VM typically reports ~3450-3500 MiB to the guest OS (firmware/EFI
    # reservation); this only warns well below that, not on the documented lab spec.
    report.check(int(facts.get('mem_mib', '0') or 0) >= 3200, 'Memory', facts.get('mem_mib', '') + ' MiB', level='WARN')
    report.line('INFO', 'Free space in home', facts.get('home_free', ''))
    report.line('INFO', 'Current Todo/Notes/Keycloak state', facts.get('todo_state', '') +
                ' (the agent rolls back to the clean snapshot first)')


def _ssh(args, address):
    """Run SSH_CHECKS on the VM with host-key checking on; return (exit code, stdout, stderr)."""
    try:
        result = subprocess.run(
            ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
             f'{args.user}@{address}', 'bash -s'],
            input=SSH_CHECKS, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, '', str(error)
    return result.returncode, result.stdout, result.stderr.strip()


def main(argv=None):
    """Run every check and print READY or NOT READY; exit code 1 if anything FAILed."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--primary', default='192.168.0.102')
    parser.add_argument('--standby', default='192.168.0.108')
    parser.add_argument('--primary-vmid', default='107')
    parser.add_argument('--standby-vmid', default='108')
    parser.add_argument('--primary-hostname', default='todo-primary')
    parser.add_argument('--standby-hostname', default='todo-standby')
    parser.add_argument('--user', default='gunstein')
    parser.add_argument('--client-ip', default='192.168.0.100')
    parser.add_argument('--snapshot', default='clean-agent')
    parser.add_argument('--revision', default='')
    args = parser.parse_args(argv)

    report = Report()
    check_local(report, args)
    check_proxmox(report, args)
    check_guest(report, args, args.primary, args.primary_hostname)
    check_guest(report, args, args.standby, args.standby_hostname)
    print()
    print('READY for the agent run.' if not report.failed else
          f'NOT READY: {report.failed} check(s) failed. Fix them before starting the agent.')
    return 1 if report.failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
