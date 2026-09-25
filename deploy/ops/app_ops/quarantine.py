"""install-quarantine-tool: pre-stage the Guest Agent stop helper on the initial primary."""
import re
import time

from . import trust

HELPER = '/opt/todo/bin/app-quarantine.sh'
HELPER_REGEX = '/opt/todo/bin/app-quarantine\\.sh'
HELPER_TYPE = 'virt_qemu_ga_unconfined_exec_t'
GA_POLICY = '/etc/sysconfig/qemu-ga'


def guest_agent_policy(text):
    """The policy with guest-exec and guest-exec-status added; refuses unfamiliar or ambiguous policy."""
    lists = re.findall(r'(?m)^FILTER_RPC_ARGS="--allow-rpcs=([a-z0-9,-]+)"$', text)
    if len(lists) != 1 or len(re.findall(r'(?m)^\s*FILTER_RPC_ARGS=', text)) != 1:
        raise RuntimeError('Expected one explicit allow-rpcs assignment; review configuration manually.')
    allowed = list(dict.fromkeys(lists[0].split(',') + ['guest-exec', 'guest-exec-status']))
    line = f'FILTER_RPC_ARGS="--allow-rpcs={",".join(allowed)}"'
    return re.sub(r'(?m)^FILTER_RPC_ARGS=.*$', lambda _: line, text, count=1)


def helper_contexts(semanage_output):
    """Existing local overrides for exactly the helper path; refuses a conflicting one."""
    lines = [line for line in semanage_output.splitlines()
             if re.match('^' + re.escape(HELPER_REGEX) + r'\s+', line)]
    if len(lines) > 1 or (lines and f'object_r:{HELPER_TYPE}:' not in lines[0]):
        raise RuntimeError(f'A conflicting SELinux file context exists for {HELPER}; review it manually.')
    return lines


def enable_guest_exec(host):
    current = host.run(['cat', GA_POLICY], sudo=True).stdout
    wanted = guest_agent_policy(current)
    changed = wanted != current
    if changed:
        backup = f'{GA_POLICY}.{time.strftime("%Y-%m-%d@%H:%M:%S")}~'
        # cat > keeps the file's inode, owner, mode and SELinux label, as lineinfile did.
        host.run(['sh', '-c', 'cp -p "$1" "$2" && cat > "$1"', 'policy', GA_POLICY, backup],
                 sudo=True, input=wanted)
        host.run(['systemctl', 'restart', 'qemu-guest-agent'], sudo=True)
    if host.run(['systemctl', 'is-active', 'qemu-guest-agent'], allowed=(0, 3)).stdout.strip() != 'active':
        raise RuntimeError(f'{host.name}: qemu-guest-agent is not active')
    return changed


def enable_selinux_entrypoint(host):
    if host.run(['getenforce']).stdout.strip() != 'Enforcing':
        raise RuntimeError(f'{host.name}: SELinux must be Enforcing')
    changed = False
    if not helper_contexts(host.run(['semanage', 'fcontext', '-l', '-C'], sudo=True).stdout):
        host.run(['semanage', 'fcontext', '-a', '-t', HELPER_TYPE, HELPER_REGEX], sudo=True)
        changed = True
    host.run(['chown', 'root:root', HELPER], sudo=True)
    host.run(['chmod', '0755', HELPER], sudo=True)
    host.run(['chcon', '-t', HELPER_TYPE, HELPER], sudo=True)
    booleans = host.run(['semanage', 'boolean', '-l', '-C'], sudo=True).stdout
    if not re.search(r'(?m)^virt_qemu_ga_run_unconfined\s+\(on\s*,\s*on\)', booleans):
        host.run(['setsebool', '-P', 'virt_qemu_ga_run_unconfined', 'on'], sudo=True)
        changed = True
    return changed


def install(project_root, controller, primary, *, guest_exec=False, selinux_entrypoint=False):
    changed = False
    trust.stage_installer(project_root, controller, primary)
    if guest_exec:
        changed = enable_guest_exec(primary) or changed
    changed = trust.install_trusted(project_root, controller, primary,
                                    [(f'{project_root}/deploy/scripts/app-quarantine.sh', HELPER, '0755')],
                                    '/opt/todo/bin') or changed
    if selinux_entrypoint:
        changed = enable_selinux_entrypoint(primary) or changed
    # Atomic replacement can lose the persistent label: apply only existing policy.
    changed = bool(primary.run(['restorecon', '-v', HELPER], sudo=True).stdout.strip()) or changed
    return changed
