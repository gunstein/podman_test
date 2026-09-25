#!/usr/bin/env python3
"""Small Proxmox VE API client for lab acceptance runs. Standard library only.

Reads PVE_HOST, PVE_NODE, PVE_TOKEN_ID, PVE_TOKEN_SECRET and PVE_CA from the
file named by PVE_ENV (default ~/.config/todo-acceptance/pve.env). The token
secret is sent only in the Authorization header and never printed.

  pve_lab.py get PATH                   read; prints the JSON "data" member
  pve_lab.py set PATH key=value ...     PUT (synchronous changes, e.g. config)
  pve_lab.py post PATH key=value ...    POST without waiting (e.g. firewall rule)
  pve_lab.py delete PATH                DELETE
  pve_lab.py task PATH key=value ...    POST, then wait until the task stops;
                                        fails unless its exitstatus is OK
  pve_lab.py exec VMID -- CMD ARG ...   Guest Agent exec; waits for completion
                                        and exits with the command's exit code
  pve_lab.py nic VMID FLAG VALUE        set FLAG=VALUE on every netN device,
                                        keeping MAC, bridge and other options

PATH starts with "/" and may contain {node}, replaced by PVE_NODE.
Example: pve_lab.py get /nodes/{node}/qemu/107/status/current
"""
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REQUIRED = ('PVE_HOST', 'PVE_NODE', 'PVE_TOKEN_ID', 'PVE_TOKEN_SECRET', 'PVE_CA')


class LabError(Exception):
    """A Proxmox API or usage error, printed as "ERROR: ..." without the token."""
    pass


def load_config(environ=os.environ):
    """Read the KEY=VALUE token file; raise LabError naming any missing key."""
    path = Path(environ.get('PVE_ENV', Path.home() / '.config/todo-acceptance/pve.env'))
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip().strip('"').strip("'")
    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        raise LabError(f'{path}: missing {", ".join(missing)}')
    return values


class Client:
    """Proxmox API calls with the token, over TLS verified against PVE_CA."""
    def __init__(self, config, opener=None, sleep=time.sleep):
        self.config = config
        self.base = f'https://{config["PVE_HOST"]}:8006/api2/json'
        self.sleep = sleep
        if opener is None:
            context = ssl.create_default_context(cafile=config['PVE_CA'])
            handler = urllib.request.HTTPSHandler(context=context)
            opener = urllib.request.build_opener(handler).open
        self.open = opener

    def path(self, path):
        """Check that path starts with / and fill in {node}."""
        if not path.startswith('/'):
            raise LabError('API path must start with /')
        return path.replace('{node}', self.config['PVE_NODE'])

    def request(self, method, path, form=None, body=None):
        """Send one API request and return its "data" member; an HTTP error raises LabError."""
        headers = {'Authorization': 'PVEAPIToken={}={}'.format(
            self.config['PVE_TOKEN_ID'], self.config['PVE_TOKEN_SECRET'])}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        elif form:
            data = urllib.parse.urlencode(form).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        request = urllib.request.Request(self.base + self.path(path), data=data,
                                         headers=headers, method=method)
        try:
            with self.open(request, timeout=30) as response:
                return json.loads(response.read() or b'{}').get('data')
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors='replace')
            raise LabError(f'{method} {path}: HTTP {error.code}: {detail}') from None

    def wait_task(self, upid, timeout=900):
        """Poll a Proxmox task until it stops; raise unless it ended OK or if it runs past timeout."""
        quoted = urllib.parse.quote(upid, safe='')
        deadline = time.monotonic() + timeout
        while True:
            status = self.request('GET', f'/nodes/{{node}}/tasks/{quoted}/status')
            if status.get('status') == 'stopped':
                if status.get('exitstatus') != 'OK':
                    raise LabError(f'task {upid} failed: {status.get("exitstatus")}')
                return status
            if time.monotonic() > deadline:
                raise LabError(f'task {upid} still running after {timeout}s')
            self.sleep(2)

    def guest_exec(self, vmid, command, timeout=180):
        """Run a command in the VM through the Guest Agent and wait for it to finish.

        Returns the exec-status result. If it is still running after timeout, the
        result has exited=0 so the caller can report that instead of guessing.
        """
        started = self.request('POST', f'/nodes/{{node}}/qemu/{vmid}/agent/exec',
                               body={'command': list(command)})
        pid = started['pid']
        deadline = time.monotonic() + timeout
        while True:
            status = self.request('GET', f'/nodes/{{node}}/qemu/{vmid}/agent/exec-status?pid={pid}')
            if status.get('exited'):
                return dict(status, pid=pid)
            if time.monotonic() > deadline:
                return {'pid': pid, 'exited': 0}
            self.sleep(1)

    def set_nic_flag(self, vmid, flag, value):
        """Set flag=value on every network device of the VM, keeping all other options.

        Used for link_down (fencing) and firewall. Reads the config back and
        raises unless every device reports the new value.
        """
        if not re.fullmatch(r'[a-z_]+', flag) or not re.fullmatch(r'[0-9]+', value):
            raise LabError('nic FLAG must be a lowercase name and VALUE a number')
        config = self.request('GET', f'/nodes/{{node}}/qemu/{vmid}/config')
        nics = {key: text for key, text in config.items() if re.fullmatch(r'net[0-9]+', key)}
        if not nics:
            raise LabError(f'VM {vmid} has no network devices')
        changes = {}
        for key, text in sorted(nics.items()):
            parts = [part for part in text.split(',') if not part.startswith(flag + '=')]
            changes[key] = ','.join(parts + [f'{flag}={value}'])
        self.request('PUT', f'/nodes/{{node}}/qemu/{vmid}/config', form=changes)
        after = self.request('GET', f'/nodes/{{node}}/qemu/{vmid}/config')
        for key in nics:
            if f'{flag}={value}' not in after.get(key, '').split(','):
                raise LabError(f'VM {vmid} {key} does not report {flag}={value}')
        return {key: after[key] for key in sorted(nics)}


def pairs(arguments):
    """Turn key=value arguments into a form dict."""
    form = {}
    for argument in arguments:
        key, separator, value = argument.partition('=')
        if not separator:
            raise LabError(f'expected key=value, got {argument!r}')
        form[key] = value
    return form


def main(argv=None, client=None):
    """Run one action from the module docstring; exit code 0, 1 (error), 2 (usage) or the guest command's."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0 if argv else 2
    try:
        client = client or Client(load_config())
        action = argv[0]
        if action == 'get' and len(argv) == 2:
            result = client.request('GET', argv[1])
        elif action == 'set' and len(argv) >= 3:
            result = client.request('PUT', argv[1], form=pairs(argv[2:]))
        elif action == 'post' and len(argv) >= 2:
            result = client.request('POST', argv[1], form=pairs(argv[2:]))
        elif action == 'delete' and len(argv) == 2:
            result = client.request('DELETE', argv[1])
        elif action == 'task' and len(argv) >= 2:
            upid = client.request('POST', argv[1], form=pairs(argv[2:]))
            result = client.wait_task(upid)
        elif action == 'exec' and len(argv) >= 4 and argv[2] == '--':
            result = client.guest_exec(argv[1], argv[3:])
            print(json.dumps(result, indent=2, sort_keys=True))
            if not result.get('exited'):
                print(f'NOT COMPLETED: pid {result["pid"]} is still running; poll exec-status', file=sys.stderr)
                return 124
            return int(result.get('exitcode', 1))
        elif action == 'nic' and len(argv) == 4:
            result = client.set_nic_flag(argv[1], argv[2], argv[3])
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except (LabError, OSError, KeyError, ValueError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
