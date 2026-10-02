"""Build-time manifest rendering driven by the same registry as installation."""
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from . import apps, manifests


def _validate(name, content):
    """Raise if the rendered file is not valid YAML."""
    try:
        list(yaml.safe_load_all(content))
    except yaml.YAMLError as error:
        raise RuntimeError(f'Rendered {name} is not valid YAML: {error}') from error


def read_values(values_file):
    """publicHostname, publicPort and logLevel from the runtime section of values.yaml.

    Checked here, so a mistake in the file gives an error that names the
    file and the setting instead of a KeyError or a broken manifest.
    """
    try:
        document = yaml.safe_load(Path(values_file).read_text())
    except yaml.YAMLError as error:
        raise ValueError(f'{values_file}: not valid YAML: {error}') from error
    runtime = document.get('runtime') if isinstance(document, dict) else None
    if not isinstance(runtime, dict):
        raise ValueError(f'{values_file}: needs a runtime section')
    missing = [key for key in ('publicHostname', 'publicPort', 'logLevel') if key not in runtime]
    if missing:
        raise ValueError(f'{values_file}: runtime is missing {", ".join(missing)}')
    hostname, port, log_level = runtime['publicHostname'], runtime['publicPort'], runtime['logLevel']
    if not isinstance(hostname, str):
        raise ValueError(f'{values_file}: runtime.publicHostname must be a hostname')
    manifests.validate_hostname(hostname)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError(f'{values_file}: runtime.publicPort must be a port number, 1-65535')
    if not isinstance(log_level, str) or not log_level:
        raise ValueError(f'{values_file}: runtime.logLevel must be a word such as info')
    return hostname, port, log_level


def selection(application_names=()):
    """The registered apps by name, all of them if none are named; unknown names are an error."""
    selected = tuple(app for app in apps.APPS if not application_names or app.name in application_names)
    if not selected or set(application_names) - {app.name for app in apps.APPS}:
        raise ValueError('Unknown or empty application selection')
    return selected


def hostnames(selected, public_hostname):
    """Each app's public hostname: the shared-resource app on public_hostname, the others from the registry."""
    return {app.name: public_hostname if app is apps.SHARED_RESOURCE_OWNER else app.hostname for app in selected}


def files(project_root, selected, hostnames, port, log_level):
    """Every Kube YAML file of the selected apps, Keycloak and the proxy: {file name: bytes}, checked.

    hostnames maps each app's name to its public hostname; for an offline
    bundle (app_installer.bundle) each is a ${TARGET_...} placeholder, and
    the files are otherwise the same. The shared-resource app's hostname is
    also Keycloak's and the OIDC issuer's.
    """
    root = Path(project_root)
    hostname = hostnames[apps.SHARED_RESOURCE_OWNER.name]
    result = {}
    for app in selected:
        result[app.database.manifest] = manifests.render_postgres(root, app.database, app.database.image)
        result[app.config_manifest] = (manifests.render_postgres_config(root, app.database) + b'---\n'
                                       + manifests.render_app_config(root, app, hostname, port, log_level))
        result[app.manifest] = manifests.render_app(root, app, app.image('backend'), app.image('frontend'))

    result['keycloak.yaml'] = manifests.render_keycloak(
        root, apps.KEYCLOAK_DATABASE, apps.KEYCLOAK_KUBE_ADMIN_SECRET, hostname, port, apps.KEYCLOAK_IMAGE)
    result[apps.KEYCLOAK_DATABASE.manifest] = manifests.render_postgres(
        root, apps.KEYCLOAK_DATABASE, apps.KEYCLOAK_DATABASE.image)
    result[apps.KEYCLOAK_DATABASE.config_manifest] = manifests.render_postgres_config(root, apps.KEYCLOAK_DATABASE)
    result['shared-proxy.yaml'] = manifests.render_shared_proxy(
        root, selected, apps.SHARED_RESOURCE_OWNER, hostnames, port, apps.PROXY_IMAGE)

    for name, content in result.items():
        _validate(name, content)
    return result


def render(project_root, values_file, output_directory, application_names=()):
    """Render every Kube YAML file for the selected apps into output_directory.

    Runs at build time. Values come from the environment's values.yaml
    (public hostname, port and log level); names come from the app
    registry. Every file is rendered and checked first. Then the whole
    output directory is replaced (see _replace_directory), so it holds
    exactly this render: no file from an earlier render stays behind, and a
    failed render leaves the earlier output as it was.
    """
    selected = selection(application_names)
    hostname, port, log_level = read_values(values_file)
    _replace_directory(Path(output_directory),
                       files(project_root, selected, hostnames(selected, hostname), port, log_level))


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
        render(*sys.argv[1:4], application_names=sys.argv[4].split(',') if len(sys.argv) > 4 and sys.argv[4] else ())
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Rendering failed: {error}', file=sys.stderr)
        sys.exit(1)
