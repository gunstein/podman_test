"""Render an offline bundle's target files at build time: Kube YAML, Quadlet units and bundle.json.

Runs on the build host, which has Jinja2 and PyYAML (deploy/offline/build-bundle.sh
calls it). Everything is rendered here; only the values the target host
knows stay as ${TARGET_...} placeholders (target_render lists them). The
offline install fills them in with the standard library alone.

The bundle then holds, under generated/target/:

  manifests/          every Kube YAML file, with a ${TARGET_..._HOSTNAME} where
                      each app's public hostname goes
  quadlet/            every .kube unit and app-network.network; the proxy unit
                      publishes HTTPS on ${TARGET_PUBLISH_ADDRESS} as well
  quadlet/local-only/ the proxy unit for a host that publishes only on 127.0.0.1
  quadlet/replicated/ the database units of a DR primary, which also publish
                      replication on ${TARGET_PUBLISH_ADDRESS}

and bundle.json says where each of them is, the platform it was built for (apps.Platform), the
public port and the default target values (platform.yaml). The offline bundle and the operations package carry the same
files: install.sh installs them on a single host, the DR tools on the
primary and the standby.
"""
import json
import shutil
import sys
from pathlib import Path

from . import platform_file, quadlet, render, target_render, workloads
from .target_render import (
    IDENTITY_HOSTNAME,
    LOOPBACK,
    PUBLISH_ADDRESS,
    hostname_target,
    placeholder,
)

TARGET = 'generated/target'


def quadlets(project_root, platform, port, publish_address):
    """Every Quadlet unit of the platform's apps, Keycloak and the proxy, rendered: {file name: bytes}."""
    root = Path(project_root)
    units = {}
    for database in platform.replicated_databases:
        units[database.unit] = quadlet.render(root, 'postgres.kube', workloads.postgres_variables(database))
    for app in platform.apps:
        units[app.unit] = quadlet.render(root, 'app.kube', workloads.app_variables(app))
    if platform.has_identity:
        units['keycloak.kube'] = quadlet.render(root, 'keycloak.kube', workloads.keycloak_variables())
    units['shared-proxy.kube'] = quadlet.render(
        root, 'shared-proxy.kube', workloads.proxy_variables(publish_address, port, platform))
    return units


def build(project_root, environment_name, bundle_directory, application_names=()):
    """Write generated/target and bundle.json into bundle_directory, and check them; return bundle.json's data.

    The platform is project_root's platform.yaml in environment_name (prod
    for a real bundle), with only the named apps if any are named.

    The manifests are rendered with placeholder hostnames, then checked
    against a normal render with the default hostnames: putting those
    defaults in must give the very same bytes, so a placeholder can only ever
    stand where a hostname stood. Finally the bundle is loaded the way the
    target host loads it, once for each proxy unit.
    """
    root, bundle = Path(project_root), Path(bundle_directory)
    platform, environment = platform_file.load(root / platform_file.FILE, environment_name)
    platform = platform.select(application_names)
    render.require_supported(platform)
    port, log_level = environment.public_port, environment.log_level
    normal = render.hostnames(platform)
    manifest_files = render.files(root, platform,
                                  {app.name: placeholder(hostname_target(app)) for app in platform.apps},
                                  placeholder(IDENTITY_HOSTNAME), port, log_level)
    defaults = {IDENTITY_HOSTNAME: platform.identity_hostname,
                **{hostname_target(app): normal[app.name] for app in platform.apps}}
    filled = {name: target_render.substitute(content.decode(), defaults, name).encode()
              for name, content in manifest_files.items()}
    if filled != render.files(root, platform, normal, platform.identity_hostname, port, log_level):
        raise RuntimeError('The placeholder render differs from the normal render in more than the hostnames.')
    published = quadlets(root, platform, port, placeholder(PUBLISH_ADDRESS))
    local_only = {'shared-proxy.kube': quadlets(root, platform, port, LOOPBACK)['shared-proxy.kube']}
    replicated = {database.unit: quadlet.render(root, 'postgres.kube', workloads.postgres_variables(
        database, placeholder(PUBLISH_ADDRESS))) for database in platform.replicated_databases}
    network = (root / 'deploy/quadlet/app-network.network').read_bytes()

    target = bundle / TARGET
    shutil.rmtree(target, ignore_errors=True)
    for directory, contents in ((target / 'manifests', manifest_files), (target / 'quadlet', published),
                                (target / 'quadlet/local-only', local_only),
                                (target / 'quadlet/replicated', replicated)):
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
        'replicated_quadlets': {'directory': f'{TARGET}/quadlet/replicated', 'files': sorted(replicated)},
        'network': f'{TARGET}/quadlet/app-network.network',
        'platform': platform.to_json(),
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
