"""Build-time manifest rendering driven by the same registry as installation."""
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from . import apps, manifests


def _validate(name, content):
    try:
        list(yaml.safe_load_all(content))
    except yaml.YAMLError as error:
        raise RuntimeError(f'Rendered {name} is not valid YAML: {error}') from error


def render(project_root, values_file, output_directory, application_names=()):
    root, output = Path(project_root), Path(output_directory)
    selected = [app for app in apps.APPS if not application_names or app.name in application_names]
    if not selected or set(application_names) - {app.name for app in apps.APPS}:
        raise ValueError('Unknown or empty application selection')
    runtime = yaml.safe_load(Path(values_file).read_text())['runtime']
    hostname, port, log_level = runtime['publicHostname'], runtime['publicPort'], runtime['logLevel']
    manifests.validate_hostname(hostname)

    files = {}
    for app in selected:
        files[app.manifest('postgres')] = manifests.render_postgres(root, app.database, app.image('postgres'))
        files[app.manifest('config')] = (manifests.render_postgres_config(root, app.database) + b'---\n'
                                          + manifests.render_app_config(root, app, hostname, port, log_level))
        files[app.manifest('app')] = manifests.render_app(root, app, app.image('backend'), app.image('frontend'))

    files['keycloak.yaml'] = manifests.render_keycloak(
        root, apps.KEYCLOAK_DATABASE, apps.KEYCLOAK_KUBE_ADMIN_SECRET, hostname, port, apps.KEYCLOAK_IMAGE)
    files[apps.KEYCLOAK_DATABASE.manifest('postgres')] = manifests.render_postgres(
        root, apps.KEYCLOAK_DATABASE, apps.KEYCLOAK_DATABASE.image('postgres'))
    files[apps.KEYCLOAK_DATABASE.manifest('config')] = manifests.render_postgres_config(root, apps.KEYCLOAK_DATABASE)
    files['shared-proxy.yaml'] = manifests.render_shared_proxy(
        root, selected, apps.IDENTITY_DATABASE_APP, hostname, apps.PROXY_IMAGE)

    for name, content in files.items():
        _validate(name, content)

    # Render and validate everything above before replacing any previous rendered output.
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        for name, content in files.items():
            (directory / name).write_bytes(content)
        output.mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copyfile(directory / name, output / name)


if __name__ == '__main__':
    try:
        render(*sys.argv[1:4], application_names=sys.argv[4].split(',') if len(sys.argv) > 4 and sys.argv[4] else ())
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Rendering failed: {error}', file=sys.stderr)
        sys.exit(1)
