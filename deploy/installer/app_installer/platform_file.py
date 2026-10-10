"""platform.yaml and each app's app.yaml: what a build makes an apps.Platform from.

The operator's platform.yaml names the apps (each by its directory), their
public hostnames and replication ports, Keycloak's hostname, and each
environment's public port and log level. Each app's app.yaml, in its own
directory, says what the app is: its name, OAuth client and REST collection.
docs/PLATFORM-PLAN.md, section 5.1, is the contract.

load() reads and checks both and returns the Platform and the environment's
settings. Only a build reads these files (it has PyYAML); an offline bundle
carries the Platform in bundle.json and a host its record (target_render).
Every mistake is a ValueError that names the file and the field.
"""
import functools
import re
from dataclasses import dataclass
from pathlib import Path

from . import apps, manifests

# platform.yaml at the root of a project, and app.yaml in each app's directory.
FILE = 'platform.yaml'
APP_FILE = 'app.yaml'
ENVIRONMENTS = ('local', 'prod')
# An app's name becomes resource names (shop-app, shop-postgres, the shop_migrator role).
NAME = re.compile(r'[a-z][a-z0-9]{0,29}')
WORD = re.compile(r'[a-z][a-z0-9-]{0,62}')


@dataclass(frozen=True)
class Environment:
    """One environment's settings: HTTPS on public_port, the apps' log level."""

    public_port: int
    log_level: str


def _fields(data, where, required, optional=()):
    """data as a mapping with every required key and no key outside required and optional."""
    if not isinstance(data, dict):
        raise ValueError(f'{where}: must be a mapping of {", ".join(required + optional)}')
    unknown = sorted(set(data) - set(required) - set(optional))
    if unknown:
        raise ValueError(f'{where}: unknown field {", ".join(map(str, unknown))}; '
                         f'the fields are {", ".join(required + optional)}')
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f'{where}: missing {", ".join(missing)}')
    return data


def _text(value, where, pattern=None):
    if not isinstance(value, str) or not value or (pattern and not pattern.fullmatch(value)):
        rule = f' matching {pattern.pattern}' if pattern else ''
        raise ValueError(f'{where}: must be a word{rule}, not {value!r}')
    return value


def _hostname(value, where):
    try:
        manifests.validate_hostname(value)
    except ValueError:
        raise ValueError(f'{where}: not a hostname: {value!r}') from None
    return value


def _port(value, where, lowest):
    if type(value) is not int or not lowest <= value <= 65535:
        raise ValueError(f'{where}: must be a port number, {lowest}-65535, not {value!r}')
    return value


def _yaml(path):
    """The YAML document in path; PyYAML is imported here, so a host without it can import this module."""
    import yaml
    try:
        return yaml.safe_load(Path(path).read_text())
    except OSError as error:
        raise ValueError(f'{path}: cannot be read: {error.strerror}') from None
    except yaml.YAMLError as error:
        raise ValueError(f'{path}: not valid YAML: {error}') from None


def _app(directory, hostname, replication_port):
    """The App in directory/app.yaml, served on hostname, its database replicating on replication_port."""
    path = directory / APP_FILE
    data = _fields(_yaml(path), path, ('name', 'keycloakClient'), ('apiCollection',))
    return apps.App(name=_text(data['name'], f'{path}: name', NAME), hostname=hostname,
                    keycloak_client=_text(data['keycloakClient'], f'{path}: keycloakClient', WORD),
                    replication_port=replication_port,
                    api_collection=_text(data['apiCollection'], f'{path}: apiCollection', WORD)
                    if 'apiCollection' in data else '')


def load(path, environment='prod'):
    """(Platform, Environment) from platform.yaml at path, for environment (local or prod).

    Paths in platform.yaml are relative to its own directory.
    """
    path = Path(path)
    data = _fields(_yaml(path), path, ('identityHostname', 'publicPort', 'logLevel', 'environments', 'apps'))
    environments = {name: _fields(changes or {}, f'{path}: environments.{name}', (), ('publicPort', 'logLevel'))
                    for name, changes in _fields(data['environments'], f'{path}: environments', ENVIRONMENTS).items()}
    if environment not in ENVIRONMENTS:
        raise ValueError(f'{path}: no environment {environment!r}; the environments are {", ".join(ENVIRONMENTS)}')
    settings = {'publicPort': data['publicPort'], 'logLevel': data['logLevel'], **environments[environment]}
    chosen = Environment(public_port=_port(settings['publicPort'], f'{path}: publicPort', 1024),
                         log_level=_text(settings['logLevel'], f'{path}: logLevel'))
    if not isinstance(data['apps'], list) or not data['apps']:
        raise ValueError(f'{path}: apps must be a list of at least one app')
    entries = [_fields(entry, f'{path}: apps[{index}]', ('path', 'hostname', 'replicationPort'))
               for index, entry in enumerate(data['apps'])]
    app_list = tuple(_app(path.parent / _text(entry['path'], f'{path}: apps[{index}].path'),
                          _hostname(entry['hostname'], f'{path}: apps[{index}].hostname'),
                          _port(entry['replicationPort'], f'{path}: apps[{index}].replicationPort', 1024))
                     for index, entry in enumerate(entries))
    identity = _hostname(data['identityHostname'], f'{path}: identityHostname')
    try:
        platform = apps.Platform(apps=app_list, identity_hostname=identity)
    except ValueError as error:  # two apps share a name, hostname, client or port
        raise ValueError(f'{path}: {error}') from None
    return platform, chosen


@functools.lru_cache(maxsize=None)
def checkout():
    """The platform of this checkout's own platform.yaml: the example apps, for the tests and the lab tools.

    Only in a Git checkout, where app_installer is deploy/installer/app_installer.
    """
    return load(Path(__file__).resolve().parents[3] / FILE)[0]
