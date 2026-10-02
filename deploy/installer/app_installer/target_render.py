"""Fill in the values only the target host knows, in files rendered at build time.

The build host renders every Kube YAML file and Quadlet unit with Jinja2
(app_installer.bundle). A value that is not known until the bundle is
installed stays in those files as an explicit placeholder, ${TARGET_NAME}.
On the target host this module replaces exactly those placeholders, and
nothing else, with checked values. It needs only the Python standard
library, so an offline host needs neither Jinja2 nor PyYAML.

The supported values (TARGETS) and where each one comes from:

  TARGET_EXTERNAL_HOSTNAME  The public hostname users and Keycloak see
      (nginx server_name, the TLS certificate, the OIDC issuer, KC_HOSTNAME).
      From --target-external-hostname, else the environment variable
      TARGET_EXTERNAL_HOSTNAME, else the default the bundle was built with
      (runtime.publicHostname in values.yaml). Never the machine's own
      hostname: a host's name is not the name users reach it by.
  TARGET_PUBLISH_ADDRESS    The host IPv4 address nginx publishes HTTPS on.
      From --publish-address (install.sh's default is 127.0.0.1).

Only ${TARGET_...} is touched: $HOME, ${DATABASE_PASSWORD} and every other
dollar expression stay as they are. A placeholder this module does not know,
or a value that is missing or invalid, stops the install before any
installed file changes. There is no shell and no environment expansion: the
values are checked strings, put in place by a regular expression.

bundle.json tells where the bundle keeps those files. load() refuses a
bundle without it (an older format) and any path in it that is absolute or
leaves the bundle.
"""
import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import manifests

BUNDLE_METADATA = 'bundle.json'
BUNDLE_FORMAT = 'todo-offline-bundle'
BUNDLE_FORMAT_VERSION = 2
PLACEHOLDER = re.compile(r'\$\{(TARGET_[A-Z0-9_]+)\}')
EXTERNAL_HOSTNAME = 'TARGET_EXTERNAL_HOSTNAME'
PUBLISH_ADDRESS = 'TARGET_PUBLISH_ADDRESS'
# The address that selects the local-only proxy unit: nginx then publishes only
# on 127.0.0.1, which its unit always does (see deploy/quadlet/shared-proxy.kube.j2).
LOOPBACK = '127.0.0.1'


def placeholder(name):
    """The text that stands for a target value in a rendered file, e.g. ${TARGET_EXTERNAL_HOSTNAME}."""
    return '${' + name + '}'


def check_hostname(value):
    """A public hostname: a DNS name that is safe in nginx.conf, YAML and URLs."""
    manifests.validate_hostname(value)
    return value


def check_publish_address(value):
    """A host IPv4 address to publish on: not a wildcard, multicast or reserved address."""
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        raise ValueError(f'Not an IPv4 address: {value!r}') from None
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        raise ValueError(f'Not a host address to publish on: {value}')
    return str(address)


# Every supported target value and the check its value must pass where it is used.
TARGETS = {EXTERNAL_HOSTNAME: check_hostname, PUBLISH_ADDRESS: check_publish_address}


class TargetError(ValueError):
    """A target value or the bundle that needs it is wrong; nothing was installed."""


def resolve(given, environment, defaults):
    """Each target value, from the first source that has it: given (the CLI), environment, defaults.

    given holds the command line's values (None when an option was not used);
    only TARGET_EXTERNAL_HOSTNAME is read from the environment. Every value is
    checked; the error names the value and where it came from.
    """
    values = {}
    for name, check in TARGETS.items():
        sources = (('command line', given.get(name)),
                   ('environment', environment.get(name) if name == EXTERNAL_HOSTNAME else None),
                   ('bundle default', defaults.get(name)))
        source, value = next(((source, value) for source, value in sources if value not in (None, '')),
                             (None, None))
        if value is None:
            raise TargetError(f'{name} has no value: give it on the command line')
        try:
            values[name] = check(value)
        except ValueError as error:
            raise TargetError(f'{name} from the {source} is not valid: {error}') from None
    return values


def substitute(text, values, where):
    """Replace each ${TARGET_...} in text with its value; leave every other $ expression alone.

    where names the file in an error. An unknown placeholder or a value that
    is missing is an error, so a typo can never reach an installed file.
    """
    def value(match):
        name = match.group(1)
        if name not in TARGETS:
            raise TargetError(f'{where}: unknown placeholder ${{{name}}}')
        if name not in values:
            raise TargetError(f'{where}: no value for ${{{name}}}')
        return values[name]
    return PLACEHOLDER.sub(value, text)


def inside(bundle_directory, relative):
    """bundle_directory/relative, refused if relative is absolute or the path leaves the bundle."""
    if not isinstance(relative, str) or not relative or os.path.isabs(relative):
        raise TargetError(f'{BUNDLE_METADATA}: {relative!r} must be a path relative to the bundle')
    root = Path(bundle_directory).resolve()
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise TargetError(f'{BUNDLE_METADATA}: {relative!r} leaves the bundle')
    return path


@dataclass(frozen=True)
class TargetFiles:
    """The bundle's files with every target value in place, read and checked, in memory.

    manifests and quadlets map a file name to its content; network is the
    app-network.network unit. Workload installation writes them.
    """

    manifests: dict
    quadlets: dict
    network_name: str
    network: bytes
    applications: tuple
    public_port: int
    values: dict


def metadata(bundle_directory):
    """bundle.json, checked: the format, its version and the shape of each entry."""
    path = Path(bundle_directory) / BUNDLE_METADATA
    if not path.is_file():
        raise TargetError(
            f'{bundle_directory} has no {BUNDLE_METADATA}: it was built in an older format that '
            'still needs Jinja2 on this host. Build a new bundle with deploy/offline/build-bundle.sh.')
    try:
        data = json.loads(path.read_text())
    except ValueError as error:
        raise TargetError(f'{BUNDLE_METADATA} is not valid JSON: {error}') from None
    if not isinstance(data, dict) or data.get('format') != BUNDLE_FORMAT:
        raise TargetError(f'{BUNDLE_METADATA} does not describe a {BUNDLE_FORMAT}')
    if data.get('format_version') != BUNDLE_FORMAT_VERSION:
        raise TargetError(f'{BUNDLE_METADATA} has format version {data.get("format_version")!r}; '
                          f'this installer reads version {BUNDLE_FORMAT_VERSION}')
    for key in ('manifests', 'quadlets', 'local_only_quadlets'):
        entry = data.get(key)
        if not (isinstance(entry, dict) and isinstance(entry.get('directory'), str)
                and isinstance(entry.get('files'), list)
                and all(isinstance(name, str) and name == Path(name).name for name in entry['files'])):
            raise TargetError(f'{BUNDLE_METADATA}: {key} needs a directory and a list of file names')
    if not isinstance(data.get('network'), str) or not isinstance(data.get('defaults'), dict) \
            or not isinstance(data.get('applications'), list) or type(data.get('public_port')) is not int:
        raise TargetError(f'{BUNDLE_METADATA}: network, defaults, applications or public_port is missing')
    return data


def load(bundle_directory, given, environment=None):
    """Read the bundle's target files and fill in the target values; nothing is written.

    given maps TARGET_... names to the command line's values. The local-only
    proxy unit replaces the published one when nginx publishes only on
    127.0.0.1. Every unit's Yaml= and ConfigMap= must name a manifest of the
    bundle, and its Network= the bundle's network unit, so the installed files
    fit together.
    """
    data = metadata(bundle_directory)
    values = resolve(given, os.environ if environment is None else environment, data['defaults'])

    def read(key):
        directory = inside(bundle_directory, data[key]['directory'])
        files = {}
        for name in data[key]['files']:
            path = inside(bundle_directory, os.path.join(data[key]['directory'], name))
            if path.parent != directory or not path.is_file():
                raise TargetError(f'{BUNDLE_METADATA}: {key} file {name} is missing from the bundle')
            files[name] = substitute(path.read_text(), values, f'{key}/{name}').encode()
        return files

    manifest_files = read('manifests')
    quadlets = read('quadlets')
    if values[PUBLISH_ADDRESS] == LOOPBACK:
        quadlets.update(read('local_only_quadlets'))
    network_path = inside(bundle_directory, data['network'])
    if not network_path.is_file():
        raise TargetError(f'{BUNDLE_METADATA}: the network unit {data["network"]} is missing')
    network = substitute(network_path.read_text(), values, data['network']).encode()
    for name, content in quadlets.items():
        for key, wanted in re.findall(r'^(Yaml|ConfigMap|Network)=(.*)$', content.decode(), re.M):
            known = (network_path.name,) if key == 'Network' else manifest_files
            if wanted not in known:
                raise TargetError(f'{name}: {key}={wanted} is not a file of this bundle')
    return TargetFiles(manifest_files, quadlets, network_path.name, network,
                       tuple(data['applications']), data['public_port'], values)
