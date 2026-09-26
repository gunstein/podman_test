"""Shared host steps: run app_installer on a host, stage files, and retry bounded checks."""
import json
import sys
import time
from pathlib import Path

from . import trust

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / 'deploy/installer'))
from app_installer import apps  # noqa: E402

GROUP = [apps.describe(database) for database in apps.REPLICATED_DATABASES]


def paths(host):
    """Where things live on a host: staged installer, Quadlet units, Kube runtime, config and bundle."""
    home = host.spec.home
    quadlet = f'{home}/.config/containers/systemd'
    return {'target': f'{home}/.local/share/app-installer', 'quadlet': quadlet,
            'runtime': f'{quadlet}/todo-kube-runtime', 'config': f'{home}/.config/todo',
            'bundle': host.spec.bundle or f'{home}/todo-offline-m12'}


def installed_pythonpath(host):
    """The registry an earlier step installed; read-only callers never stage."""
    if trust.fapolicyd_active(host):
        return str(trust.LIBRARY)
    return paths(host)['target'] + '/deploy/installer'


def app_installer(host, pythonpath, *arguments, input=None, allowed=(0,)):
    """Run python3 -m app_installer with the given arguments on host, using pythonpath."""
    return host.run(['env', f'PYTHONPATH={pythonpath}', 'PYTHONDONTWRITEBYTECODE=1',
                     'python3', '-m', 'app_installer', *arguments], input=input, allowed=allowed)


def changed(result):
    """The "changed" flag from an app_installer JSON result."""
    return json.loads(result.stdout)['changed']


def put(host, path, content):
    """Private 0600 staging copy in 0700 directories."""
    host.run(['sh', '-c', 'umask 077 && mkdir -p "$(dirname "$1")" && cat > "$1"', 'put', path], input=content)


def stage_postgres_group(project_root, controller, host, rendered=None):
    """Installer, every database's Quadlet templates and canonical YAML; returns the PYTHONPATH."""
    pythonpath = trust.stage_installer(project_root, controller, host)
    target = paths(host)['target']
    rendered = Path(rendered or Path(project_root) / 'generated/kube-runtime')
    for name in sorted({name for entry in GROUP for name in entry['templates']}):
        put(host, f'{target}/deploy/quadlet/{name}', (Path(project_root) / 'deploy/quadlet' / name).read_text())
    for name in sorted({name for entry in GROUP for name in entry['manifests']['postgres']}):
        put(host, f'{target}/generated/kube-runtime/{name}', (rendered / name).read_text())
    return pythonpath


def group_paths(host):
    """The directory options app_installer needs for the staged database group on host."""
    p = paths(host)
    return ['--project-root', p['target'], '--quadlet-dir', p['quadlet'], '--kube-runtime-dir', p['runtime'],
            '--rendered-manifest-dir', p['target'] + '/generated/kube-runtime']


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
