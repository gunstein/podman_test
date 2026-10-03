"""Exact-file fapolicyd trust and staging of the installer and DR host packages on each host."""
import base64
from pathlib import Path

from app_installer import settings

TRUST_FILE = 'todo'
LIBRARY = settings.TOOLS_LIB


def script(project_root):
    """The text of trust-files.sh, passed to /bin/sh as an argument rather than run as a file."""
    return (Path(project_root) / 'deploy/scripts/trust-files.sh').read_text()


def fapolicyd_active(host):
    """True if fapolicyd runs on host; then only trusted files may run as code."""
    result = host.run(['systemctl', 'is-active', 'fapolicyd'], allowed=(0, 3, 4))
    return result.stdout.strip() == 'active'


def install_trusted(project_root, controller, host, files, directory):
    """Install files, [(source, dest, mode)], on host with exact-file fapolicyd trust; True if anything changed.

    First the controller trusts its own source copies, then each file goes to
    host over stdin (trust-files.sh install), and last host trusts the
    installed copies by exact path, size and SHA-256.
    """
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
    """Stage the installer and the DR host package a host's commands import; returns the PYTHONPATH there.

    Both packages go side by side into one directory: app_installer (the
    single-host installer) and app_dr_host (the DR building blocks, which
    import app_installer).
    """
    root = Path(project_root)
    packages = {name: sorted(path.glob('*.py')) for name, path in (
        ('app_installer', root / 'deploy/installer/app_installer'), ('app_dr_host', root / 'deploy/dr/app_dr_host'))}
    if fapolicyd_active(host):
        for name, sources in packages.items():
            install_trusted(project_root, controller, host,
                            [(source, LIBRARY / name / source.name, '0644') for source in sources], LIBRARY / name)
        return str(LIBRARY)
    target = f'{host.spec.home}/.local/share/app-installer/deploy/installer'
    for name, sources in packages.items():
        for source in sources:
            host.run(['sh', '-c', 'umask 077 && mkdir -p "$(dirname "$1")" && cat > "$1"', 'stage',
                      f'{target}/{name}/{source.name}'], input=source.read_text())
    return target
