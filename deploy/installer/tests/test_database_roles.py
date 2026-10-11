"""database_roles: the platform makes each app's roles; the app's migrations grant table rights."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.platform_fixture import fixture_platform  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import database_roles  # noqa: E402


class StatementTests(unittest.TestCase):
    def setUp(self):
        self.sql = database_roles.statements('shop', 'shop_migrator', 'shop_app', "mig'pass", 'apppass')

    def test_both_roles_are_made_and_reset_without_extra_rights(self):
        for role in ('shop_migrator', 'shop_app'):
            self.assertIn(f"rolname = '{role}') THEN CREATE ROLE \"{role}\" LOGIN", self.sql)
            self.assertIn(f'ALTER ROLE "{role}" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
                          'NOINHERIT PASSWORD', self.sql)
            self.assertIn(f'ALTER ROLE "{role}" IN DATABASE "shop" SET search_path = "public";', self.sql)
        # A quote in a password stays inside its literal.
        self.assertIn("PASSWORD 'mig''pass';", self.sql)

    def test_only_the_two_roles_connect_and_the_migrator_owns_the_schema(self):
        for statement in ('REVOKE CONNECT ON DATABASE "shop" FROM PUBLIC;',
                          'GRANT CONNECT ON DATABASE "shop" TO "shop_migrator", "shop_app";',
                          'ALTER SCHEMA "public" OWNER TO "shop_migrator";',
                          'REVOKE ALL ON SCHEMA "public" FROM PUBLIC;',
                          'GRANT USAGE ON SCHEMA "public" TO "shop_app";'):
            self.assertIn(statement, self.sql)
        self.assertIn("OWNER TO %I', object.kind, 'public', object.relname, 'shop_migrator'", self.sql)

    def test_table_rights_are_left_to_the_migrations(self):
        # Neither granted nor revoked here, so a migration's own grants survive every install.
        for words in ('ON TABLE', 'ON ALL TABLES', 'ON ALL SEQUENCES', 'DEFAULT PRIVILEGES'):
            self.assertNotIn(words, self.sql)

    def test_each_migration_grants_its_tables_to_the_runtime_role(self):
        root = Path(__file__).resolve().parents[3]
        for app, table in (('todo', 'todos'), ('notes', 'notes')):
            up = next((root / f'examples/{app}/backend/migrations').glob('001_*.up.sql')).read_text()
            self.assertIn(f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {app}_app;', up)


class SetupTests(unittest.TestCase):
    def test_the_sql_goes_to_the_databases_own_psql_on_stdin_with_the_passwords_hidden(self):
        app = fixture_platform().app('todo')
        with patch.object(database_roles.secrets, 'read', side_effect=lambda name: name + '-value'), \
                patch.object(database_roles, 'run') as run:
            database_roles.setup(app)
        argv, keywords = run.call_args.args, run.call_args.kwargs
        self.assertEqual(argv, ('podman', 'exec', '--interactive', 'todo-postgres', 'psql', '--no-psqlrc',
                                '--set', 'ON_ERROR_STOP=1', '--quiet', '--username', 'todo', '--dbname', 'todo'))
        self.assertTrue(keywords['secret_output'])
        self.assertIn("PASSWORD 'todo-migrator-password-value'", keywords['input'])
        self.assertIn("PASSWORD 'todo-app-password-value'", keywords['input'])
        self.assertNotIn('value', ' '.join(argv))


if __name__ == '__main__':
    unittest.main()
