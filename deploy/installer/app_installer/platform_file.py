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
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import apps, manifests

# platform.yaml at the root of a project, and app.yaml in each app's directory.
FILE = 'platform.yaml'
APP_FILE = 'app.yaml'
ENVIRONMENTS = ('local', 'prod')
# An app's name becomes resource names (shop-app, shop-postgres, the shop_migrator role).
NAME = re.compile(r'[a-z][a-z0-9]{0,29}')
WORD = re.compile(r'[a-z][a-z0-9-]{0,62}')
# The images the shared app pod template runs (deploy/manifests/app.yaml.j2: the
# migration and the backend, and the frontend), so every app declares them until
# it brings its own pod template (docs/PLATFORM-PLAN.md, section 5.9).
TEMPLATE_IMAGES = ('backend', 'frontend')
# A route's path goes into nginx.conf as written: a slash, then only letters,
# digits and . _ ~ / - (no spaces, quotes, ; { } or $ that could change nginx).
PATH = re.compile(r'/[A-Za-z0-9._~/-]*')
# The platform's own path on every app's hostname: Keycloak (apps.Route).
IDENTITY_PATH = '/auth'


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


def _images(data, path, root):
    """The images an app.yaml declares, {name: {context, containerfile}}, as apps.AppImage.

    context is relative to the app's directory and may lead out of it (a
    source tree elsewhere); it is kept relative to root, platform.yaml's
    directory. containerfile (default Containerfile) is relative to the
    context and stays inside it. Neither has to exist until a build.
    """
    if not isinstance(data, dict) or not data:
        raise ValueError(f'{path}: images must map each image name to its build (context, containerfile)')
    images = []
    for name, build in data.items():
        where = f'{path}: images.{name}'
        _text(name, where, WORD)
        build = _fields(build, where, ('context',), ('containerfile',))
        context = _text(build['context'], f'{where}.context')
        containerfile = _text(build.get('containerfile', 'Containerfile'), f'{where}.containerfile')
        if PurePosixPath(containerfile).is_absolute() or '..' in PurePosixPath(containerfile).parts:
            raise ValueError(f'{where}.containerfile: must be a path inside the context, not {containerfile!r}')
        directory = os.path.normpath(path.parent / context)
        images.append(apps.AppImage(name=name, context=Path(os.path.relpath(directory, root)).as_posix(),
                                    containerfile=containerfile))
    missing = [name for name in TEMPLATE_IMAGES if name not in data]
    if missing:
        raise ValueError(f'{path}: images: the shared app pod template (deploy/manifests/app.yaml.j2) runs '
                         f'{" and ".join(TEMPLATE_IMAGES)}; declare {", ".join(missing)}')
    return tuple(images)


def _endpoints(data, path):
    """The endpoints an app.yaml declares, {name: {port}}, as apps.Endpoint: the ports in its pod nginx reaches."""
    if not isinstance(data, dict) or not data:
        raise ValueError(f'{path}: endpoints must map each endpoint name to its port, for example frontend: '
                         '{port: 8080}')
    endpoints = []
    for name, endpoint in data.items():
        where = f'{path}: endpoints.{name}'
        _text(name, where, WORD)
        endpoint = _fields(endpoint, where, ('port',))
        endpoints.append(apps.Endpoint(name=name, port=_port(endpoint['port'], f'{where}.port', 1)))
    return tuple(endpoints)


def _routes(data, path, endpoints):
    """The routes an app.yaml declares, a list of {path, to, exact}, as apps.Route.

    A prefix path ends with a slash, so /api/ never also matches /apifoo; an
    exact path (exact: true) is matched whole. to names one of the app's
    endpoints. /auth and everything under it is the platform's (Keycloak), and
    two routes for the same location are an error.
    """
    if not isinstance(data, list) or not data:
        raise ValueError(f'{path}: routes must be a list of {{path, to}}, for example - {{path: /, to: frontend}}')
    names = [endpoint.name for endpoint in endpoints]
    routes = []
    for index, route in enumerate(data):
        where = f'{path}: routes[{index}]'
        route = _fields(route, where, ('path', 'to'), ('exact',))
        exact = route.get('exact', False)
        if type(exact) is not bool:
            raise ValueError(f'{where}.exact: must be true or false, not {exact!r}')
        location = route['path']
        if not isinstance(location, str) or not PATH.fullmatch(location):
            raise ValueError(f'{where}.path: must be a path such as /api/, not {location!r}')
        if not exact and not location.endswith('/'):
            raise ValueError(f'{where}.path: a prefix path ends with a slash ({location}/), or set exact: true')
        if location == IDENTITY_PATH or location.startswith(IDENTITY_PATH + '/'):
            raise ValueError(f'{where}.path: {location} is the platform\'s: {IDENTITY_PATH}/ goes to Keycloak')
        if route['to'] not in names:
            raise ValueError(f'{where}.to: {route["to"]!r} is not one of the endpoints {", ".join(names)}')
        route = apps.Route(path=location, to=route['to'], exact=exact)
        if any(other.location == route.location for other in routes):
            raise ValueError(f'{where}: a second route for {route.location}')
        routes.append(route)
    return tuple(routes)


def _app(directory, hostname, replication_port, root):
    """The App in directory/app.yaml, served on hostname, its database replicating on replication_port."""
    path = directory / APP_FILE
    data = _fields(_yaml(path), path, ('name', 'keycloakClient', 'images', 'endpoints', 'routes'),
                   ('apiCollection',))
    endpoints = _endpoints(data['endpoints'], path)
    return apps.App(name=_text(data['name'], f'{path}: name', NAME), hostname=hostname,
                    keycloak_client=_text(data['keycloakClient'], f'{path}: keycloakClient', WORD),
                    replication_port=replication_port,
                    api_collection=_text(data['apiCollection'], f'{path}: apiCollection', WORD)
                    if 'apiCollection' in data else '',
                    images=_images(data['images'], path, root), endpoints=endpoints,
                    routes=_routes(data['routes'], path, endpoints))


def load(path, environment='prod'):
    """(Platform, Environment) from platform.yaml at path, for environment (local or prod).

    Paths in platform.yaml are relative to its own directory.
    """
    path = Path(path)
    data = _fields(_yaml(path), path, ('identityHostname', 'publicPort', 'logLevel', 'environments', 'apps'))
    # An empty environment (prod: {} or prod:) changes nothing.
    environments = {name: _fields({} if changes is None else changes, f'{path}: environments.{name}', (),
                                  ('publicPort', 'logLevel'))
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
                          _port(entry['replicationPort'], f'{path}: apps[{index}].replicationPort', 1024),
                          path.parent)
                     for index, entry in enumerate(entries))
    _require_distinct(path, entries, app_list)
    identity = _hostname(data['identityHostname'], f'{path}: identityHostname')
    try:
        platform = apps.Platform(apps=app_list, identity_hostname=identity)
    except ValueError as error:  # the reserved name identity
        raise ValueError(f'{path}: {error}') from None
    return platform, chosen


def _require_distinct(path, entries, app_list):
    """No two apps share a name, client, hostname or replication port; Keycloak's database port is taken.

    The message names the YAML field and the apps (by their app.yaml), so
    the fix needs no Python. apps.Platform checks the same, for JSON.
    """
    sources = [f'{entry["path"]}/{APP_FILE}' for entry in entries]
    for field, attribute in (('name', 'name'), ('keycloakClient', 'keycloak_client'),
                             ('hostname', 'hostname'), ('replicationPort', 'replication_port')):
        seen = {}
        for source, app in zip(sources, app_list):
            value = getattr(app, attribute)
            if value in seen:
                raise ValueError(f'{path}: the apps {seen[value]} and {source} share {field} {value!r}')
            seen[value] = source
    keycloak = apps.KEYCLOAK_DATABASE.replication_port
    for source, app in zip(sources, app_list):
        if app.replication_port == keycloak:
            raise ValueError(f'{path}: the app {source} has replicationPort {keycloak}, '
                             "which is Keycloak's database's")


# This checkout's own platform.yaml, in a Git checkout (app_installer is deploy/installer/app_installer).
CHECKOUT_FILE = Path(__file__).resolve().parents[3] / FILE


@functools.lru_cache(maxsize=None)
def checkout():
    """The platform of this checkout's platform.yaml (prod): the example apps, for the tests and the lab tools.

    Read once per process: a long-running tool does not see later edits.
    """
    return load(CHECKOUT_FILE)[0]
