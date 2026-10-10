"""app-ops nginx-tls-request and nginx-tls-install against the fake two-host world."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests import test_app_ops_commands as commands
from tests.test_app_ops_commands import World, spec

sys.path.insert(0, "deploy/dr")
from app_ops import cli, nginx_tls  # noqa: E402
from app_ops.transport import Host  # noqa: E402


def setUpModule():
    commands.setUpModule()


def tearDownModule():
    commands.tearDownModule()


class TlsWorld(World):
    """Each host answers nginx-tls request with a CSR named after it."""

    def answer(self, host, command, stdin):
        if command[:1] == ['env'] and 'app_dr_host' in command:
            sub = command[command.index('app_dr_host') + 1:]
            if sub[0] == 'nginx-tls':
                if sub[1] == 'request':
                    return (('nginx-tls', 'request'), json.dumps(
                        {'changed': True, 'request': f'CSR of {host}\n', 'hostnames': ['todo.test', 'notes.test']}), 0)
                return ('nginx-tls', sub[1]), '{"changed": true}', 0
        if command[:2] == ['sh', '-c'] and command[3] == 'put':
            return ('put', command[4], stdin), '', 0
        return super().answer(host, command, stdin)


class NginxTlsCommandTests(unittest.TestCase):
    def hosts(self, world):
        return (Host(cli.LOCAL, runner=world), Host(spec('todo-primary', 'primary', '192.0.2.10'), runner=world),
                Host(spec('todo-standby', 'standby', '192.0.2.11'), runner=world))

    def tls_steps(self, world):
        return [(host, step) for host, step in world.log
                if step[0] == 'nginx-tls' or (step[0] == 'put' and '/nginx-tls/' in step[1])]

    def test_a_request_from_each_host_lands_here_standby_first(self):
        world = TlsWorld()
        with tempfile.TemporaryDirectory() as directory:
            report = nginx_tls.request(str(commands.PROJECT), *self.hosts(world), Path(directory) / 'csr')
            self.assertEqual((Path(directory) / 'csr/todo-standby.csr').read_text(), 'CSR of todo-standby\n')
            self.assertEqual((Path(directory) / 'csr/todo-primary.csr').read_text(), 'CSR of todo-primary\n')
        self.assertEqual([host for host, _ in self.tls_steps(world)], ['todo-standby', 'todo-primary'])
        self.assertEqual(report['requests']['todo-standby']['hostnames'], ['todo.test', 'notes.test'])
        # Each host's own address and offline bundle.
        self.assertEqual(world.option('todo-standby', ('nginx-tls', 'request'), '--node-address'), ['192.0.2.11'])
        self.assertEqual(world.option('todo-primary', ('nginx-tls', 'request'), '--bundle-dir'),
                         ['/home/todo-primary/platform-offline-m12'])

    def test_both_certificates_are_needed_before_anything_runs(self):
        world = TlsWorld()
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'todo-primary.crt').write_text('PRIMARY')
            (Path(directory) / 'ca.crt').write_text('ROOT')
            with self.assertRaisesRegex(RuntimeError, 'todo-standby.crt is missing'):
                nginx_tls.install(str(commands.PROJECT), *self.hosts(world), directory, Path(directory) / 'ca.crt')
        self.assertEqual(world.log, [])

    def test_each_host_gets_its_own_certificate_then_the_pair_is_set_to_provided(self):
        world = TlsWorld()
        with tempfile.TemporaryDirectory() as directory:
            for name, text in (('todo-primary.crt', 'PRIMARY'), ('todo-standby.crt', 'STANDBY'), ('ca.crt', 'ROOT')):
                (Path(directory) / name).write_text(text)
            self.assertTrue(nginx_tls.install(str(commands.PROJECT), *self.hosts(world), directory,
                                              Path(directory) / 'ca.crt'))
        steps = self.tls_steps(world)
        target = '/home/{}/.local/share/app-installer/nginx-tls/'
        self.assertEqual(steps, [
            ('todo-standby', ('put', target.format('todo-standby') + 'server.crt', 'STANDBY')),
            ('todo-standby', ('put', target.format('todo-standby') + 'ca.crt', 'ROOT')),
            ('todo-standby', ('nginx-tls', 'install')),
            ('todo-primary', ('put', target.format('todo-primary') + 'server.crt', 'PRIMARY')),
            ('todo-primary', ('put', target.format('todo-primary') + 'ca.crt', 'ROOT')),
            ('todo-primary', ('nginx-tls', 'install')),
            ('todo-standby', ('nginx-tls', 'mode')),
            ('todo-primary', ('nginx-tls', 'mode'))])
        self.assertEqual(world.option('todo-primary', ('nginx-tls', 'mode'), '--mode'), ['provided'])

    def test_the_commands_parse_with_the_initial_inventory(self):
        args = cli.parser().parse_args(['--inventory', 'initial.yaml', 'nginx-tls-install', '--certificates', 'signed',
                                        '--ca', 'ca.crt'])
        self.assertEqual(cli.COMMANDS[args.command], cli.INITIAL)
        args = cli.parser().parse_args(['--inventory', 'initial.yaml', 'nginx-tls-request', '--output', 'requests'])
        self.assertEqual(str(args.output), 'requests')


if __name__ == '__main__':
    unittest.main()
