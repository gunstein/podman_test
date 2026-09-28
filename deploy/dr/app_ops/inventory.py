"""The operator's small YAML inventory: which machine plays which role, and how to reach it."""
import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9.-]{0,62}')


@dataclass(frozen=True)
class HostSpec:
    """One inventory host: name, role, address, login user, home, and whether it is this machine."""
    name: str
    role: str
    address: str
    user: str
    home: str
    local: bool = False
    ssh_host: str = ''
    bundle: str = ''

    @property
    def destination(self):
        """user@address (or ssh_host) for ssh."""
        return f'{self.user}@{self.ssh_host or self.address}'


def load(path, roles):
    """Hosts keyed by role; refuses anything but exactly one host per required role."""
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict) or not isinstance(raw.get('hosts'), dict):
        raise ValueError(f'{path}: expected a mapping with a hosts mapping')
    user = raw.get('user')
    if not isinstance(user, str) or not NAME.fullmatch(user):
        raise ValueError(f'{path}: user must be a plain login name')
    hosts = {}
    for name, entry in raw['hosts'].items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(entry, dict):
            raise ValueError(f'{path}: invalid host entry {name!r}')
        role = entry.get('role')
        if role in hosts:
            raise ValueError(f'{path}: more than one host has role {role}')
        hosts[role] = HostSpec(
            name=name, role=role, address=str(ipaddress.IPv4Address(entry.get('address', ''))),
            user=user, home=str(entry.get('home') or raw.get('home') or f'/home/{user}'),
            local=entry.get('local') is True, ssh_host=str(entry.get('ssh_host', '')),
            bundle=str(entry.get('bundle') or raw.get('bundle') or ''))
    if sorted(hosts) != sorted(roles):
        raise ValueError(f'{path}: this command needs exactly the roles {", ".join(sorted(roles))}, '
                         f'found {", ".join(sorted(map(str, hosts)))}')
    if len({host.address for host in hosts.values()}) != len(hosts):
        raise ValueError(f'{path}: every host needs a distinct address')
    return hosts
