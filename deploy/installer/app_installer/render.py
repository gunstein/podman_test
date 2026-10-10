"""Build-time manifest rendering of one platform (platform_file.load reads it from platform.yaml)."""
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from . import apps, manifests, platform_file


def _validate(name, content):
    """Raise if the rendered file is not valid YAML."""
    try:
        list(yaml.safe_load_all(content))
    except yaml.YAMLError as error:
        raise RuntimeError(f'Rendered {name} is not valid YAML: {error}') from error


def hostnames(platform):
    """Each app's default public hostname: {app name: hostname}."""
    return {app.name: app.hostname for app in platform.apps}


def require_supported(platform):
    """Raise unless the one shared app pod template (app.yaml.j2) can run every app of platform.

    It runs a migration and an OIDC backend, so an app needs database: true
    and a keycloakClient until apps bring pod templates of their own (4f).
    install.check and bundle.build call this before anything is written.
    """
    for app in platform.apps:
        if not (app.has_database and app.has_login):
            raise ValueError(f'The app {app.name} needs database: true and a keycloakClient: the shared app pod '
                             'template is the only one so far, and it uses both')


def files(project_root, platform, hostnames, identity_hostname, port, log_level):
    """Every Kube YAML file of the platform's apps, Keycloak and the proxy: {file name: bytes}, checked.

    hostnames maps each app's name to its public hostname, and
    identity_hostname is Keycloak's, the OIDC issuer's; for an offline
    bundle (app_installer.bundle) each is a ${TARGET_...} placeholder, and
    the files are otherwise the same.
    """
    root = Path(project_root)
    require_supported(platform)
    hostname = identity_hostname
    result = {}
    for app in platform.apps:
        result[app.database.manifest] = manifests.render_postgres(root, app.database, app.database.image)
        result[app.config_manifest] = (manifests.render_postgres_config(root, app.database) + b'---\n'
                                       + manifests.render_app_config(root, app, hostname, port, log_level))
        result[app.manifest] = manifests.render_app(root, app, app.image('backend'), app.image('frontend'))

    if platform.has_identity:
        result['keycloak.yaml'] = manifests.render_keycloak(
            root, apps.KEYCLOAK_DATABASE, apps.KEYCLOAK_KUBE_ADMIN_SECRET, hostname, port, apps.KEYCLOAK_IMAGE)
        result[apps.KEYCLOAK_DATABASE.manifest] = manifests.render_postgres(
            root, apps.KEYCLOAK_DATABASE, apps.KEYCLOAK_DATABASE.image)
        result[apps.KEYCLOAK_DATABASE.config_manifest] = manifests.render_postgres_config(
            root, apps.KEYCLOAK_DATABASE)
    result['shared-proxy.yaml'] = manifests.render_shared_proxy(
        root, platform, hostnames, identity_hostname, port, apps.PROXY_IMAGE)

    for name, content in result.items():
        _validate(name, content)
    return result


def render(project_root, environment, output_directory, platform):
    """Render every Kube YAML file of platform into output_directory.

    Runs at build time: install.py renders the platform it installs, and
    render-kube-runtime.sh the one in platform.yaml. environment
    (platform_file.Environment) gives the port and the log level. Every file is
    rendered and checked first. Then the whole output directory is replaced
    (see _replace_directory), so it holds exactly this render: no file from
    an earlier render stays behind, and a failed render leaves the earlier
    output as it was.
    """
    _replace_directory(Path(output_directory), files(project_root, platform, hostnames(platform),
                                                     platform.identity_hostname, environment.public_port,
                                                     environment.log_level))


def _replace_directory(output, files):
    """Write files into a new directory next to output, then swap it in.

    The new directory is complete before the old one is touched. The swap is
    two renames on the same file system: the old directory aside, the new
    one into its place; only then is the old one deleted.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    new = Path(tempfile.mkdtemp(prefix=f'.{output.name}.new.', dir=output.parent))
    try:
        new.chmod(0o755)
        for name, content in files.items():
            (new / name).write_bytes(content)
    except BaseException:
        shutil.rmtree(new)
        raise
    old = output.parent / f'.{output.name}.old.{new.name.rsplit(".", 1)[-1]}'
    if output.exists():
        output.rename(old)
    new.rename(output)
    shutil.rmtree(old, ignore_errors=True)


if __name__ == '__main__':
    try:
        project_root, environment_name, output_directory = sys.argv[1:]
        platform, environment = platform_file.load(Path(project_root) / platform_file.FILE, environment_name)
        render(project_root, environment, output_directory, platform)
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Rendering failed: {error}', file=sys.stderr)
        sys.exit(1)
