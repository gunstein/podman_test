"""Exact-file fapolicyd trust and staging of the installer module on each host."""
import base64
from pathlib import Path

TRUST_FILE = 'todo'
LIBRARY = Path('/opt/todo/lib')


def script(project_root):
    """The text of trust-files.sh, passed to /bin/sh as an argument rather than run as a file."""
    return (Path(project_root) / 'deploy/scripts/trust-files.sh').read_text()


def fapolicyd_active(host):
    """True if fapolicyd runs on host; then only trusted files may run as code."""
    result = host.run(['systemctl', 'is-active', 'fapolicyd'], allowed=(0, 3, 4))
    return result.stdout.strip() == 'active'


def install_trusted(project_root, controller, host, files, directory):
    """files: [(source, dest, mode)]. Controller source trust, stdin install, then target trust."""
    if not fapolicyd_active(host):
        raise RuntimeError(f'{host.name}: fapolicyd is not active')
    text = script(project_root)
    trust = ['/bin/sh', '-c', text, 'trust-files', 'trust', TRUST_FILE]
    changed = controller.run(trust + [str(source) for source, _, _ in files], sudo=True).stdout.strip() == 'changed'
    host.run(['install', '-d', '-o', 'root', '-g', 'root', '-m', '0755', str(directory)], sudo=True)
    for source, dest, mode in files:
        content = base64.b64encode(Path(source).read_bytes()).decode()
        result = host.run(['/bin/sh', '-c', text, 'trust-files', 'install', str(dest), mode],
                          sudo=True, input=content)
        changed = result.stdout.strip() == 'changed' or changed
    result = host.run(trust + [str(dest) for _, dest, _ in files], sudo=True)
    return result.stdout.strip() == 'changed' or changed


def stage_installer(project_root, controller, host):
    """The app_installer package a host's commands import; returns the PYTHONPATH to use there."""
    host.run(['python3', '-c', 'import jinja2'])
    sources = sorted((Path(project_root) / 'deploy/installer/app_installer').glob('*.py'))
    if fapolicyd_active(host):
        install_trusted(project_root, controller, host,
                        [(source, LIBRARY / 'app_installer' / source.name, '0644') for source in sources],
                        LIBRARY / 'app_installer')
        return str(LIBRARY)
    target = f'{host.spec.home}/.local/share/app-installer/deploy/installer'
    for source in sources:
        host.run(['sh', '-c', 'umask 077 && mkdir -p "$(dirname "$1")" && cat > "$1"', 'stage',
                  f'{target}/app_installer/{source.name}'], input=source.read_text())
    return target
