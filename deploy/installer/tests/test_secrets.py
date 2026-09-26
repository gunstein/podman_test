import base64
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, cli, secrets  # noqa: E402


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
        return (patch.object(secrets, 'exists', self.exists), patch.object(secrets, 'read', self.read),
                patch.object(secrets, 'run', self.run))


def primary_values():
    return {name: 'value-' + name for name in secrets.replicated_names()}


class ReplicatedSecretTests(unittest.TestCase):
    def use(self, store):
        for patcher in store.patches():
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_names_cover_the_complete_group_once(self):
        names = secrets.replicated_names()
        expected = [name for database in apps.REPLICATED_DATABASES
                    for name in apps.describe(database)['raw_secrets']]
        self.assertEqual(sorted(names), sorted(set(expected)))
        self.assertEqual(len(names), len(set(names)))
        self.assertIn(apps.KEYCLOAK_ADMIN_SECRET, names)
        for database in apps.REPLICATED_DATABASES:
            self.assertIn(database.secret('replicator'), names)

    def test_export_reads_every_group_credential(self):
        self.use(FakeSecrets(primary_values()))
        self.assertEqual(secrets.export_replicated(), primary_values())

    def test_import_creates_only_missing_and_is_idempotent(self):
        values = primary_values()
        present = secrets.replicated_names()[0]
        store = FakeSecrets({present: values[present]})
        self.use(store)
        self.assertTrue(secrets.import_replicated(values))
        self.assertNotIn(present, store.created)
        self.assertEqual(store.stored, values)
        store.created.clear()
        self.assertFalse(secrets.import_replicated(values))
        self.assertEqual(store.created, [])

    def test_a_different_existing_value_refuses_before_any_create(self):
        values = primary_values()
        different = secrets.replicated_names()[-1]
        store = FakeSecrets({different: 'standby-only-value'})
        self.use(store)
        with self.assertRaises(RuntimeError) as refused:
            secrets.import_replicated(values)
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
        empty = {**values, secrets.replicated_names()[0]: ''}
        for transfer in (partial, extra, empty, list(values), {**values, 'todo-db-password': 1}):
            with self.subTest(transfer=type(transfer).__name__), self.assertRaises(ValueError):
                secrets.import_replicated(transfer)
        self.assertEqual(store.created, [])

    def test_cli_round_trip_keeps_values_out_of_import_output(self):
        primary = FakeSecrets(primary_values())
        self.use(primary)
        exported = io.StringIO()
        with redirect_stdout(exported):
            self.assertEqual(cli.main(['export-replication-secrets']), 0)
        standby = FakeSecrets()
        self.use(standby)
        imported, errors = io.StringIO(), io.StringIO()
        with patch('sys.stdin', io.StringIO(exported.getvalue())), redirect_stdout(imported), \
                redirect_stderr(errors):
            self.assertEqual(cli.main(['import-replication-secrets']), 0)
        self.assertEqual(json.loads(imported.getvalue()), {'changed': True})
        self.assertEqual(standby.stored, primary_values())
        for value in primary_values().values():
            self.assertNotIn(value, imported.getvalue() + errors.getvalue())

    def test_cli_reports_refusal_without_values(self):
        values = primary_values()
        different = secrets.replicated_names()[0]
        self.use(FakeSecrets({different: 'standby-only-value'}))
        output, errors = io.StringIO(), io.StringIO()
        transfer = base64.b64encode(json.dumps(values).encode()).decode()
        with patch('sys.stdin', io.StringIO(transfer)), redirect_stdout(output), \
                redirect_stderr(errors):
            self.assertEqual(cli.main(['import-replication-secrets']), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertIn(different, errors.getvalue())
        for value in [*values.values(), 'standby-only-value']:
            self.assertNotIn(value, errors.getvalue())

    def test_cli_rejects_malformed_transfer_before_any_create(self):
        store = FakeSecrets()
        self.use(store)
        for transfer in ('{"not": "base64"}', base64.b64encode(b'not json').decode()):
            with self.subTest(transfer=transfer), patch('sys.stdin', io.StringIO(transfer)), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['import-replication-secrets']), 1)
        self.assertEqual(store.created, [])

    def test_export_output_is_opaque_and_never_shows_a_value(self):
        self.use(FakeSecrets(primary_values()))
        exported = io.StringIO()
        with redirect_stdout(exported):
            cli.main(['export-replication-secrets'])
        self.assertFalse(exported.getvalue().lstrip().startswith(('{', '[')))
        for value in primary_values().values():
            self.assertNotIn(value, exported.getvalue())


if __name__ == '__main__':
    unittest.main()
