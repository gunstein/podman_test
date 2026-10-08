"""nginx's certificates from the organisation's CA on both hosts of the pair (docs/TLS.md, provided mode).

  nginx-tls-request --output DIR
      on each host, a key made in its TLS volume and a CSR for the public
      hostnames it records; the CSRs land in DIR on this machine as
      <host>.csr. The standby gets its TLS volume and the proxy image from its
      offline bundle first, as it runs no nginx until a failover.
  nginx-tls-install --certificates DIR --ca FILE
      DIR/<host>.crt for both hosts, signed by the CA in FILE, checked and
      installed on each (the standby first), then the pair's mode set to
      provided on both. Both certificates are needed in one run: a pair whose
      standby has none would fail over to a new demo CA.

Running nginx-tls-request again keeps each host's waiting key, so a lost CSR
costs nothing. Renewal is the same two commands; app_dr.py check fails
below 30 days.
"""
import json
from pathlib import Path

from . import steps
from .steps import app_dr_host


def where(host):
    """The options app_dr_host nginx-tls needs on host: staged target files, offline bundle, its address."""
    p = steps.paths(host)
    return ['--project-root', p['target'], '--bundle-dir', p['bundle'], '--node-address', host.spec.address]


def request(project_root, controller, primary, standby, output):
    """Make a request on each host and write it to output/<host>.csr here; return the report."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    written = {}
    for host in (standby, primary):
        pythonpath = steps.stage_target_files(project_root, controller, host)
        result = json.loads(app_dr_host(host, pythonpath, 'nginx-tls', 'request', *where(host)).stdout)
        path = output / f'{host.name}.csr'
        path.write_text(result['request'])
        written[host.name] = {'request': str(path), 'hostnames': result['hostnames']}
    return {'changed': True, 'requests': written}


def install(project_root, controller, primary, standby, certificates, ca):
    """Install each host's certificate from certificates/<host>.crt, then set the pair to provided mode."""
    certificates, root = Path(certificates), Path(ca).read_text()
    signed = {}
    for host in (standby, primary):
        path = certificates / f'{host.name}.crt'
        if not path.is_file():
            raise RuntimeError(f'{path} is missing: sign {host.name}.csr too. Both hosts get their certificate '
                               'in one run, so the standby can never fail over to a demo CA.')
        signed[host.name] = path.read_text()
    changed, pythonpaths = False, {}
    for host in (standby, primary):
        pythonpath = pythonpaths[host.name] = steps.stage_target_files(project_root, controller, host)
        directory = f'{steps.paths(host)["target"]}/nginx-tls'
        steps.put(host, f'{directory}/server.crt', signed[host.name])
        steps.put(host, f'{directory}/ca.crt', root)
        changed = steps.changed(app_dr_host(
            host, pythonpath, 'nginx-tls', 'install', *where(host),
            '--certificate', f'{directory}/server.crt', '--ca', f'{directory}/ca.crt')) or changed
    for host in (standby, primary):
        changed = steps.changed(app_dr_host(host, pythonpaths[host.name], 'nginx-tls', 'mode',
                                            '--mode', 'provided')) or changed
    return changed
