"""Run in the real notes-backend container with NOTES_ROLE_TEST=1."""
import importlib
import os
import unittest
from pathlib import Path


@unittest.skipUnless(os.getenv('NOTES_ROLE_TEST') == '1', 'requires the real Notes runtime database')
class RuntimePrivilegesTests(unittest.TestCase):
    def test_runtime_role_can_crud_but_cannot_ddl_or_read_migration_state(self):
        from psycopg.errors import InsufficientPrivilege

        connect = importlib.import_module(Path(__file__).resolve().parents[1].name + ".main").connect

        with connect() as connection:
            try:
                row = connection.execute(
                    'INSERT INTO notes(title, body) VALUES (%s, %s) RETURNING id',
                    ('Privilege probe', 'Original body')).fetchone()
                identifier = row['id']
                connection.execute('UPDATE notes SET body=%s WHERE id=%s', ('Edited', identifier))
                self.assertEqual(connection.execute('SELECT body FROM notes WHERE id=%s',
                                                    (identifier,)).fetchone()['body'], 'Edited')
                self.assertEqual(connection.execute('DELETE FROM notes WHERE id=%s RETURNING id',
                                                    (identifier,)).fetchone()['id'], identifier)
                self.assertEqual(connection.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname IN ('todo_app','todo_migrator','keycloak_app')"
                ).fetchall(), [])
            finally:
                connection.rollback()
        for statement in ('CREATE TABLE public.forbidden_probe(id integer)',
                          'SELECT version FROM schema_migrations'):
            with connect() as connection:
                try:
                    with self.assertRaises(InsufficientPrivilege):
                        connection.execute(statement)
                finally:
                    connection.rollback()
