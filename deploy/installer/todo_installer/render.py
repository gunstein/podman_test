"""Build-time Helm invocation driven by the same registry as installation."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import apps


def render(project_root, values_file, output_directory, application_names=()):
    root, output = Path(project_root), Path(output_directory)
    selected = [app for app in apps.APPS if not application_names or app.name in application_names]
    if not selected or set(application_names) - {app.name for app in apps.APPS}:
        raise ValueError('Unknown or empty application selection')
    helm = os.environ.get('HELM', 'helm')
    if not shutil.which(helm):
        raise RuntimeError(f'Helm is required to render the Kube runtime: {helm}')
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        registry = directory / 'apps.json'
        registry.write_text(json.dumps({'apps': [{
            'name': app.name, 'hostname': app.hostname, 'keycloakClient': app.keycloak_client,
            'frontend': app.resource('app') + ':8080', 'backend': app.resource('app') + ':8000',
            'identity': app == apps.IDENTITY_DATABASE_APP,
        } for app in selected]}))
        manifests = [(app.name, app.chart, component, app.manifest(component))
                     for app in selected for component in ('app', 'postgres', 'config')]
        manifests += [('keycloak', 'keycloak', 'keycloak', 'keycloak.yaml'),
                      ('shared-proxy', 'shared-proxy', 'shared-proxy', 'shared-proxy.yaml')]
        for release, chart, component, filename in manifests:
            result = subprocess.run([
                helm, 'template', release, str(root / 'deploy/charts' / chart),
                '--values', str(values_file), '--values', str(registry),
                '--show-only', 'templates/' + component + '.yaml',
            ], check=True, capture_output=True)
            (directory / filename).write_bytes(result.stdout.rstrip(b'\n') + b'\n')
        # Finish all Helm invocations before replacing any previous rendered output.
        output.mkdir(parents=True, exist_ok=True)
        for _, _, _, filename in manifests:
            shutil.copyfile(directory / filename, output / filename)


if __name__ == '__main__':
    try:
        render(*sys.argv[1:4], application_names=sys.argv[4].split(',') if len(sys.argv) > 4 and sys.argv[4] else ())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'Rendering failed: {error}', file=sys.stderr)
        sys.exit(1)
