import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, cli, preflight  # noqa: E402

IP_OUTPUT = (
    '2: eth0    inet 192.0.2.10/24 brd 192.0.2.255 scope global eth0\\       valid_lft forever\n'
    '3: eth1    inet 198.51.100.7/24 brd 198.51.100.255 scope global eth1\\       valid_lft forever\n')
VOLUMES = [database.volume('data') for database in apps.REPLICATED_DATABASES]


def host(hostname='primary', machine_id='a' * 32, volumes=(), ip_output=IP_OUTPUT, systemd_rc=0):
    """A fake run/exists pair answering like one host."""
    calls = []

    def run(*argv, input=None, allowed=(0,)):
        calls.append(argv)
        outputs = {('hostname',): hostname + '\n', ('cat', '/etc/machine-id'): machine_id + '\n',
                   ('ip', '-4', '-o', 'address', 'show', 'scope', 'global'): ip_output}
        rc = systemd_rc if argv[:2] == ('systemctl', '--user') else 0
        if rc not in allowed:
            raise RuntimeError(f'{argv[0]} {argv[1]} failed (exit {rc})')
        return subprocess.CompletedProcess(argv, rc, outputs.get(argv, ''), '')

    return (patch.object(preflight, 'run', run),
            patch.object(preflight, 'exists', lambda kind, name: name in volumes), calls)


def facts(hostname='primary', role='primary', address='192.0.2.10', **options):
    run, exists, _ = host(hostname=hostname, **options)
    with run, exists:
        return preflight.node_facts(hostname, role, address)


class NodeFactsTests(unittest.TestCase):
    def test_ipv4_addresses_drop_prefixes_and_ignore_other_lines(self):
        self.assertEqual(preflight.ipv4_addresses(IP_OUTPUT + 'garbage inet\n'), ['192.0.2.10', '198.51.100.7'])

    def test_reports_identity_and_every_registered_volume(self):
        result = facts(volumes=VOLUMES[:1])
        self.assertEqual(result['hostname'], 'primary')
        self.assertEqual(result['machine_id'], 'a' * 32)
        self.assertEqual(result['ipv4_addresses'], ['192.0.2.10', '198.51.100.7'])
        self.assertEqual(result['data_volumes'], {volume: volume == VOLUMES[0] for volume in VOLUMES})

    def test_degraded_user_systemd_is_accepted_but_unreachable_is_not(self):
        facts(systemd_rc=1)
        with self.assertRaises(RuntimeError):
            facts(systemd_rc=3)

    def test_every_identity_problem_is_reported_together(self):
        run, exists, calls = host(hostname='other', machine_id='short')
        with run, exists, self.assertRaises(RuntimeError) as refused:
            preflight.node_facts('primary', 'witness', '203.0.113.9')
        message = str(refused.exception)
        for fragment in ("role 'witness'", "hostname 'other'", '203.0.113.9', 'machine-id'):
            self.assertIn(fragment, message)
        self.assertFalse(any(argv[0] == 'podman' and argv[1] != '--version' for argv in calls))

    def test_address_must_match_a_whole_address_not_a_prefix(self):
        with self.assertRaisesRegex(RuntimeError, '192.0.2.1 is not'):
            facts(address='192.0.2.1')


class PairTests(unittest.TestCase):
    def pair(self, primary_volumes=VOLUMES, standby_volumes=(), **standby):
        primary = facts(volumes=primary_volumes)
        options = dict(hostname='standby', role='standby', address='198.51.100.7', machine_id='b' * 32)
        options.update(standby)
        return primary, facts(volumes=standby_volumes, **options)

    def test_a_valid_initial_pair_passes(self):
        preflight.check_pair(*self.pair())

    def test_cloned_machine_id_and_shared_address_are_both_reported(self):
        primary, standby = self.pair(machine_id='a' * 32, address='192.0.2.10')
        with self.assertRaises(RuntimeError) as refused:
            preflight.check_pair(primary, standby)
        self.assertIn('machine ID', str(refused.exception))
        self.assertIn('same address', str(refused.exception))

    def test_primary_must_have_every_data_volume(self):
        with self.assertRaisesRegex(RuntimeError, 'missing: ' + VOLUMES[-1]):
            preflight.check_pair(*self.pair(primary_volumes=VOLUMES[:-1]))

    def test_standby_with_any_data_volume_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'will not overwrite'):
            preflight.check_pair(*self.pair(standby_volumes=VOLUMES[-1:]))

    def test_swapped_roles_or_same_host_are_refused(self):
        primary, standby = self.pair()
        for first, second in ((standby, primary), (primary, primary)):
            with self.subTest(first=first['inventory_hostname'], second=second['inventory_hostname']):
                with self.assertRaises(RuntimeError):
                    preflight.check_pair(first, second)

    def test_a_different_database_group_is_refused(self):
        primary, standby = self.pair()
        del standby['data_volumes'][VOLUMES[0]]
        with self.assertRaisesRegex(RuntimeError, 'different database group'):
            preflight.check_pair(primary, standby)


class CliTests(unittest.TestCase):
    def test_node_facts_and_pair_round_trip_through_the_cli(self):
        outputs = []
        for hostname, role, address, machine_id, volumes in (
                ('primary', 'primary', '192.0.2.10', 'a' * 32, VOLUMES), ('standby', 'standby', '198.51.100.7', 'b' * 32, ())):
            run, exists, _ = host(hostname=hostname, machine_id=machine_id, volumes=volumes)
            stdout = io.StringIO()
            with run, exists, redirect_stdout(stdout):
                self.assertEqual(cli.main(['node-facts', '--inventory-hostname', hostname,
                                           '--role', role, '--address', address]), 0)
            outputs.append(stdout.getvalue().strip())
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(cli.main(['check-standby-pair', *outputs]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), {'changed': False})

    def test_pair_refusal_is_one_readable_error(self):
        primary = facts(volumes=VOLUMES)
        errors = io.StringIO()
        with redirect_stderr(errors):
            self.assertEqual(cli.main(['check-standby-pair', json.dumps(primary), json.dumps(primary)]), 1)
        self.assertTrue(errors.getvalue().startswith('app-installer: Standby preflight failed:'))


if __name__ == '__main__':
    unittest.main()
