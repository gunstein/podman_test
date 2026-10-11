"""Run in CI with GUESTBOOK_ROLE_TEST=1, connected as the real guestbook_app role."""
import os
import unittest


@unittest.skipUnless(os.getenv('GUESTBOOK_ROLE_TEST') == '1', 'requires the real Guestbook runtime database')
class RuntimePrivilegesTests(unittest.TestCase):
    def test_runtime_role_can_crud_but_cannot_ddl_or_read_migration_state(self):
        from backend.main import connect
        from psycopg.errors import InsufficientPrivilege

        with connect() as connection:
            try:
                row = connection.execute(
                    'INSERT INTO entries(message) VALUES (%s) RETURNING id',
                    ('Privilege probe',)).fetchone()
                identifier = row['id']
                connection.execute('UPDATE entries SET message=%s WHERE id=%s', ('Edited', identifier))
                self.assertEqual(connection.execute('SELECT message FROM entries WHERE id=%s',
                                                    (identifier,)).fetchone()['message'], 'Edited')
                self.assertEqual(connection.execute('DELETE FROM entries WHERE id=%s RETURNING id',
                                                    (identifier,)).fetchone()['id'], identifier)
                self.assertEqual(connection.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname IN ('todo_app','notes_app','keycloak_app')"
                ).fetchall(), [])
            finally:
                connection.rollback()
        for statement in ('CREATE TABLE public.forbidden_probe(id integer)',
                          'ALTER TABLE entries ADD COLUMN forbidden integer',
                          'DROP TABLE entries',
                          'SELECT version FROM schema_migrations'):
            with connect() as connection:
                try:
                    with self.assertRaises(InsufficientPrivilege):
                        connection.execute(statement)
                finally:
                    connection.rollback()
