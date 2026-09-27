"""Run in CI with TODO_ROLE_TEST=1, connected as the real todo_app role.

The tests in ../tests connect as todo_migrator, which owns the tables; this
one proves the API works with only the rights todo_app has in production.
"""
import importlib
import os
import unittest
from pathlib import Path


@unittest.skipUnless(os.getenv('TODO_ROLE_TEST') == '1', 'requires the real Todo runtime database')
class RuntimePrivilegesTests(unittest.TestCase):
    def test_api_crud_works_as_runtime_role(self):
        from fastapi.testclient import TestClient

        main = importlib.import_module(Path(__file__).resolve().parents[1].name + '.main')
        with main.connect() as connection:
            self.assertEqual(connection.info.user, 'todo_app')
        original = main.validate_access_token
        main.validate_access_token = lambda token: {'sub': 'test-user'}
        self.addCleanup(setattr, main, 'validate_access_token', original)
        client = TestClient(main.app)
        authorization = {'Authorization': 'Bearer test-token'}

        created = client.post('/api/todos', json={'title': 'Privilege probe'}, headers=authorization)
        self.assertEqual(created.status_code, 201, created.text)
        identifier = created.json()['id']
        updated = client.put(f'/api/todos/{identifier}', json={'title': 'Edited', 'completed': True},
                             headers=authorization)
        self.assertEqual(updated.json(), {'id': identifier, 'title': 'Edited', 'completed': True})
        self.assertIn(updated.json(), client.get('/api/todos').json())
        self.assertEqual(client.delete(f'/api/todos/{identifier}', headers=authorization).status_code, 204)
        self.assertNotIn(identifier, [todo['id'] for todo in client.get('/api/todos').json()])

    def test_runtime_role_cannot_ddl_or_read_migration_state(self):
        from psycopg.errors import InsufficientPrivilege

        connect = importlib.import_module(Path(__file__).resolve().parents[1].name + '.main').connect
        for statement in ('CREATE TABLE public.forbidden_probe(id integer)',
                          'SELECT version FROM schema_migrations',
                          'TRUNCATE TABLE todos'):
            with self.subTest(statement=statement), connect() as connection:
                try:
                    with self.assertRaises(InsufficientPrivilege):
                        connection.execute(statement)
                finally:
                    connection.rollback()
