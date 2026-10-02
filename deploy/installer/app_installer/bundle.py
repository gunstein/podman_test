"""Render an offline bundle's target files at build time: Kube YAML, Quadlet units and bundle.json.

Runs on the build host, which has Jinja2 and PyYAML (deploy/offline/build-bundle.sh
calls it). Everything is rendered here; only the values the target host
knows stay as ${TARGET_...} placeholders (target_render lists them). The
offline install fills them in with the standard library alone.

The bundle then holds, under generated/target/:

  manifests/          every Kube YAML file, with ${TARGET_EXTERNAL_HOSTNAME}
  quadlet/            every .kube unit and app-network.network; the proxy unit
                      publishes HTTPS on ${TARGET_PUBLISH_ADDRESS} as well
  quadlet/local-only/ the proxy unit for a host that publishes only on 127.0.0.1

and bundle.json says where each of them is, the apps it was built for, the
public port and the default target values (from values.yaml). The database
units have no LAN publication: that is a DR step, and the DR tools keep
rendering their own units from deploy/quadlet and generated/kube-runtime.
"""
import json
import shutil
import sys
from pathlib import Path

from . import apps, quadlet, render, target_render, workloads
from .target_render import EXTERNAL_HOSTNAME, LOOPBACK, PUBLISH_ADDRESS, placeholder

TARGET = 'generated/target'


def quadlets(project_root, selected, port, publish_address):
    """Every Quadlet unit of the selected apps, Keycloak and the proxy, rendered: {file name: bytes}."""
    root = Path(project_root)
    units = {}
    for database in [app.database for app in selected] + [apps.KEYCLOAK_DATABASE]:
        units[database.unit] = quadlet.render(root, database.unit, workloads.postgres_variables(database))
    for app in selected:
        units[app.unit] = quadlet.render(root, app.unit, workloads.application_variables(publish_address, port))
    units['keycloak.kube'] = quadlet.render(root, 'keycloak.kube', {})
    units['shared-proxy.kube'] = quadlet.render(
        root, 'shared-proxy.kube', workloads.proxy_variables(publish_address, port, selected))
    return units


def build(project_root, values_file, bundle_directory, application_names=()):
    """Write generated/target and bundle.json into bundle_directory, and check them; return bundle.json's data.

    The manifests are rendered with the placeholder hostname, then checked
    against a normal render with values.yaml's hostname: putting that
    default in must give the very same bytes, so a placeholder can only ever
    stand where the hostname stood. Finally the bundle is loaded the way the
    target host loads it, once for each proxy unit.
    """
    root, bundle = Path(project_root), Path(bundle_directory)
    selected = render.selection(application_names)
    hostname, port, log_level = render.read_values(values_file)
    manifest_files = render.files(root, selected, placeholder(EXTERNAL_HOSTNAME), port, log_level)
    defaults = {EXTERNAL_HOSTNAME: hostname}
    filled = {name: target_render.substitute(content.decode(), defaults, name).encode()
              for name, content in manifest_files.items()}
    if filled != render.files(root, selected, hostname, port, log_level):
        raise RuntimeError('The placeholder render differs from the normal render in more than the hostname.')
    published = quadlets(root, selected, port, placeholder(PUBLISH_ADDRESS))
    local_only = {'shared-proxy.kube': quadlets(root, selected, port, LOOPBACK)['shared-proxy.kube']}
    network = (root / 'deploy/quadlet/app-network.network').read_bytes()

    target = bundle / TARGET
    shutil.rmtree(target, ignore_errors=True)
    for directory, contents in ((target / 'manifests', manifest_files), (target / 'quadlet', published),
                                (target / 'quadlet/local-only', local_only)):
        directory.mkdir(parents=True)
        for name, content in contents.items():
            (directory / name).write_bytes(content)
    (target / 'quadlet/app-network.network').write_bytes(network)
    data = {
        'format': target_render.BUNDLE_FORMAT,
        'format_version': target_render.BUNDLE_FORMAT_VERSION,
        'manifests': {'directory': f'{TARGET}/manifests', 'files': sorted(manifest_files)},
        'quadlets': {'directory': f'{TARGET}/quadlet', 'files': sorted(published)},
        'local_only_quadlets': {'directory': f'{TARGET}/quadlet/local-only', 'files': sorted(local_only)},
        'network': f'{TARGET}/quadlet/app-network.network',
        'applications': [app.name for app in selected],
        'public_port': port,
        'defaults': defaults,
    }
    (bundle / target_render.BUNDLE_METADATA).write_text(json.dumps(data, indent=2) + '\n')
    for address in ('192.0.2.1', LOOPBACK):
        target_render.load(bundle, {PUBLISH_ADDRESS: address}, environment={})
    return data


if __name__ == '__main__':
    try:
        build(*sys.argv[1:4], application_names=sys.argv[4].split(',') if len(sys.argv) > 4 and sys.argv[4] else ())
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Bundle rendering failed: {error}', file=sys.stderr)
        sys.exit(1)
