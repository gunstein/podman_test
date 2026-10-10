import base64
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / 'installer')]
import dr_target  # noqa: E402
from app_dr_host import cli, transfer  # noqa: E402
from app_installer import apps  # noqa: E402


class FakeSecrets:
    """In-memory podman secret store; records creates."""

    def __init__(self, stored=None):
        self.stored = dict(stored or {})
        self.created = []

    def exists(self, kind, name):
        return name in self.stored

    def read(self, name):
        return self.stored[name]

    def run(self, *argv, input=None, allowed=(0,)):
        assert argv[:3] == ('podman', 'secret', 'create') and argv[4] == '-'
        self.stored[argv[3]] = input
        self.created.append(argv[3])

    def patches(self):
        return (patch.object(transfer, 'exists', self.exists), patch.object(transfer, 'read', self.read),
                patch.object(transfer, 'run', self.run))


def primary_values():
    return {name: 'value-' + name for name in transfer.transfer_names(apps.registry())}


class ReplicatedSecretTests(unittest.TestCase):
    def use(self, store):
        for patcher in store.patches():
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_names_cover_the_complete_group_once(self):
        names = transfer.replicated_names(apps.registry())
        expected = [name for app in apps.registry().apps for name in (
            app.database.secret('db'), app.database.secret('migrator'), app.database.secret('app'), app.database.secret('replicator'))]
        expected += [apps.KEYCLOAK_DATABASE.secret('db'), apps.KEYCLOAK_DATABASE.secret('replicator')]
        self.assertEqual(sorted(names), sorted(expected + [apps.KEYCLOAK_ADMIN_SECRET]))
        self.assertEqual(len(names), len(set(names)))
        self.assertIn(apps.KEYCLOAK_ADMIN_SECRET, names)
        for database in apps.registry().replicated_databases:
            self.assertIn(database.secret('replicator'), names)

    def test_the_copy_also_carries_the_replication_ca_but_the_promoted_host_does_not_need_it(self):
        self.assertEqual(transfer.transfer_names(apps.registry()), transfer.replicated_names(apps.registry()) + list(apps.REPLICATION_CA_SECRETS))
        for name in apps.REPLICATION_CA_SECRETS:
            self.assertNotIn(name, transfer.replicated_names(apps.registry()))

    def test_export_reads_every_group_credential(self):
        self.use(FakeSecrets(primary_values()))
        self.assertEqual(transfer.export_replicated(apps.registry()), primary_values())

    def test_import_creates_only_missing_and_is_idempotent(self):
        values = primary_values()
        present = transfer.transfer_names(apps.registry())[0]
        store = FakeSecrets({present: values[present]})
        self.use(store)
        self.assertTrue(transfer.import_replicated(apps.registry(), values))
        self.assertNotIn(present, store.created)
        self.assertEqual(store.stored, values)
        store.created.clear()
        self.assertFalse(transfer.import_replicated(apps.registry(), values))
        self.assertEqual(store.created, [])

    def test_a_different_existing_value_refuses_before_any_create(self):
        values = primary_values()
        different = transfer.transfer_names(apps.registry())[-1]
        store = FakeSecrets({different: 'standby-only-value'})
        self.use(store)
        with self.assertRaises(RuntimeError) as refused:
            transfer.import_replicated(apps.registry(), values)
        self.assertEqual(store.created, [])
        self.assertEqual(store.stored, {different: 'standby-only-value'})
        self.assertIn(different, str(refused.exception))
        for value in [*values.values(), 'standby-only-value']:
            self.assertNotIn(value, str(refused.exception))

    def test_partial_extra_or_malformed_transfers_are_refused(self):
        store = FakeSecrets()
        self.use(store)
        values = primary_values()
        partial = dict(list(values.items())[1:])
        extra = {**values, 'unrelated-password': 'x'}
        empty = {**values, transfer.replicated_names(apps.registry())[0]: ''}
        for payload in (partial, extra, empty, list(values), {**values, 'todo-db-password': 1}):
            with self.subTest(payload=type(payload).__name__), self.assertRaises(ValueError):
                transfer.import_replicated(apps.registry(), payload)
        self.assertEqual(store.created, [])

    def test_cli_round_trip_keeps_values_out_of_import_output(self):
        primary = FakeSecrets(primary_values())
        self.use(primary)
        exported = io.StringIO()
        with redirect_stdout(exported):
            self.assertEqual(cli.main(['--project-root', str(dr_target.bundle()), 'export-replication-secrets']), 0)
        standby = FakeSecrets()
        self.use(standby)
        imported, errors = io.StringIO(), io.StringIO()
        with patch('sys.stdin', io.StringIO(exported.getvalue())), redirect_stdout(imported), \
                redirect_stderr(errors):
            self.assertEqual(cli.main(['--project-root', str(dr_target.bundle()), 'import-replication-secrets']), 0)
        self.assertEqual(json.loads(imported.getvalue()), {'changed': True})
        self.assertEqual(standby.stored, primary_values())
        for value in primary_values().values():
            self.assertNotIn(value, imported.getvalue() + errors.getvalue())

    def test_cli_reports_refusal_without_values(self):
        values = primary_values()
        different = transfer.replicated_names(apps.registry())[0]
        self.use(FakeSecrets({different: 'standby-only-value'}))
        output, errors = io.StringIO(), io.StringIO()
        payload = base64.b64encode(json.dumps(values).encode()).decode()
        with patch('sys.stdin', io.StringIO(payload)), redirect_stdout(output), \
                redirect_stderr(errors):
            self.assertEqual(cli.main(['--project-root', str(dr_target.bundle()), 'import-replication-secrets']), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertIn(different, errors.getvalue())
        for value in [*values.values(), 'standby-only-value']:
            self.assertNotIn(value, errors.getvalue())

    def test_cli_rejects_malformed_transfer_before_any_create(self):
        store = FakeSecrets()
        self.use(store)
        for payload in ('{"not": "base64"}', base64.b64encode(b'not json').decode()):
            with self.subTest(payload=payload), patch('sys.stdin', io.StringIO(payload)), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['--project-root', str(dr_target.bundle()), 'import-replication-secrets']), 1)
        self.assertEqual(store.created, [])

    def test_export_output_is_opaque_and_never_shows_a_value(self):
        self.use(FakeSecrets(primary_values()))
        exported = io.StringIO()
        with redirect_stdout(exported):
            cli.main(['--project-root', str(dr_target.bundle()), 'export-replication-secrets'])
        self.assertFalse(exported.getvalue().lstrip().startswith(('{', '[')))
        for value in primary_values().values():
            self.assertNotIn(value, exported.getvalue())


if __name__ == '__main__':
    unittest.main()
