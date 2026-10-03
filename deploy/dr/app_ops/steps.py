"""Shared host steps: run app_dr_host on a host, stage files, and retry bounded checks."""
import json
import time
from pathlib import Path

from app_installer import apps, settings, target_render

from . import trust

PROJECT_ROOT = Path(__file__).resolve().parents[3]

GROUP = apps.REPLICATED_DATABASES

# A backstop for one app_dr_host step on a host. Each command inside it has
# its own limit (settings.COMMAND_TIMEOUT and the longer ones), so this only
# catches a step that hangs outside them. Copying databases takes longer.
STEP_TIMEOUT = 2 * 3600
COPY_STEP_TIMEOUT = len(GROUP) * settings.DATA_COPY_TIMEOUT + STEP_TIMEOUT


def paths(host):
    """Where things live on a host: staged installer, Quadlet units, Kube runtime, config and bundle."""
    home = host.spec.home
    quadlet = f'{home}/.config/containers/systemd'
    return {'target': f'{home}/.local/share/app-installer', 'quadlet': quadlet,
            'runtime': f'{quadlet}/{settings.KUBE_RUNTIME}', 'config': f'{home}/{settings.DR_CONFIG}',
            'bundle': host.spec.bundle or f'{home}/todo-offline-{settings.IMAGE_TAG}'}


def promotion_record(host):
    """The path of the promotion record on host, which app_dr.py writes when it promotes."""
    return f'{paths(host)["config"]}/{settings.PROMOTION_RECORD}'


def installed_pythonpath(host):
    """The PYTHONPATH of the packages an earlier step staged on host; read-only commands use it and never stage."""
    if trust.fapolicyd_active(host):
        return str(trust.LIBRARY)
    return paths(host)['target'] + '/deploy/installer'


def app_dr_host(host, pythonpath, *arguments, input=None, allowed=(0,), timeout=STEP_TIMEOUT):
    """Run python3 -m app_dr_host with the given arguments on host, using pythonpath."""
    return host.run(['env', f'PYTHONPATH={pythonpath}', 'PYTHONDONTWRITEBYTECODE=1',
                     'python3', '-m', 'app_dr_host', *arguments], input=input, allowed=allowed,
                    timeout=timeout)


def changed(result):
    """The "changed" flag from an app_dr_host JSON result."""
    return json.loads(result.stdout)['changed']


def put(host, path, content):
    """Private 0600 staging copy in 0700 directories."""
    host.run(['sh', '-c', 'umask 077 && mkdir -p "$(dirname "$1")" && cat > "$1"', 'put', path], input=content)


# Replace a file with stdin only if it differs; print whether it changed.
UPDATE_FILE = """umask 077 && mkdir -p "$(dirname "$1")" && new=$(mktemp "$1.XXXXXX") && cat > "$new"
if cmp -s "$new" "$1"; then rm -f "$new"; echo unchanged; else chmod 0644 "$new" && mv "$new" "$1"; echo changed; fi"""


def install_timer(project_root, host, name):
    """Install deploy/dr/systemd/<name>.service and .timer as user units on host, and turn the timer on.

    Returns whether anything changed. The units run a tool app-ops installed
    in settings.TOOLS_BIN; a failed run leaves the service failed, which is
    the alert (`systemctl --user --failed`, the journal).
    """
    directory = f'{host.spec.home}/.config/systemd/user'
    changed = False
    for unit in (f'{name}.service', f'{name}.timer'):
        content = (Path(project_root) / 'deploy/dr/systemd' / unit).read_text()
        result = host.run(['sh', '-c', UPDATE_FILE, 'unit', f'{directory}/{unit}'], input=content)
        changed = result.stdout.strip() == 'changed' or changed
    if changed:
        host.run(['systemctl', '--user', 'daemon-reload'])
    timer = f'{name}.timer'
    enabled = host.run(['systemctl', '--user', 'is-enabled', timer], allowed=(0, 1)).stdout.strip() == 'enabled'
    active = host.run(['systemctl', '--user', 'is-active', timer], allowed=(0, 3, 4)).stdout.strip() == 'active'
    if not (enabled and active):
        host.run(['systemctl', '--user', 'enable', '--now', timer])
        changed = True
    return changed


def stage_target_files(project_root, controller, host):
    """The installer and the package's rendered files (bundle.json, generated/target); returns the PYTHONPATH.

    app_dr_host on the host fills in that host's values (target_render) and
    installs them; nothing is rendered there.
    """
    pythonpath = trust.stage_installer(project_root, controller, host)
    target, root = paths(host)['target'], Path(project_root)
    for path in sorted([root / target_render.BUNDLE_METADATA, *(root / 'generated/target').rglob('*')]):
        if path.is_file():
            put(host, f'{target}/{path.relative_to(root)}', path.read_text())
    return pythonpath


def group_paths(host):
    """The directory options app_dr_host needs for the staged files on host."""
    p = paths(host)
    return ['--project-root', p['target'], '--quadlet-dir', p['quadlet'], '--kube-runtime-dir', p['runtime']]


def target_values(host, pythonpath, project_root):
    """The public hostnames host serves, {TARGET_...: hostname}, as JSON text: recorded, else the defaults.

    app-ops passes them on (--target-values) to the host that becomes its
    standby, so both serve the same names.
    """
    result = app_dr_host(host, pythonpath, 'target-values', '--project-root', str(project_root))
    return json.dumps(json.loads(result.stdout)['values'], sort_keys=True)


def hostnames(host, pythonpath, project_root):
    """Each app's public hostname on host: {app name: hostname}."""
    return target_render.hostnames(json.loads(target_values(host, pythonpath, project_root)))


def retry(action, attempts, delay, sleep=time.sleep):
    """Call action until it stops raising RuntimeError, up to attempts times, delay seconds apart."""
    for attempt in range(attempts):
        try:
            return action()
        except RuntimeError:
            if attempt + 1 == attempts:
                raise
            sleep(delay)


def preflight_addresses(ip_output):
    """The IPv4 addresses in 'ip -4 -o address' output, parsed as the installer does."""
    from app_installer import preflight
    return preflight.ipv4_addresses(ip_output)
