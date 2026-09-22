import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
with patch.dict(os.environ, {'SERVE_FRONTEND': 'false'}):
    main = importlib.import_module('notes-backend.main')


class NotesAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.connection = MagicMock()
        self.connect = patch.object(main, 'connect', return_value=self.connection)
        self.connect.start()
        self.addCleanup(self.connect.stop)
        self.database = self.connection.__enter__.return_value
        self.addCleanup(main.app.dependency_overrides.clear)

    def authenticate(self):
        main.app.dependency_overrides[main.require_user] = lambda: {'sub': 'test-user'}

    def test_mutations_require_authentication(self):
        for method, path, payload in [('POST', '/api/notes', {'title': 'Private change'}),
                                      ('PUT', '/api/notes/1', {'title': 'Changed'}),
                                      ('DELETE', '/api/notes/1', None)]:
            response = self.client.request(method, path, json=payload)
            self.assertEqual(response.status_code, 401)
        self.connection.assert_not_called()

    def test_public_read_returns_notes(self):
        expected = [{'id': 1, 'title': 'A note', 'body': 'Two\nlines'}]
        self.database.execute.return_value.fetchall.return_value = expected
        response = self.client.get('/api/notes')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)

    def test_create_and_edit_bind_title_and_body_as_parameters(self):
        self.authenticate()
        title, body = "A title'; --", 'Text\nwith a second line'
        self.database.execute.return_value.fetchone.return_value = {
            'id': 1, 'title': title, 'body': body}
        response = self.client.post('/api/notes', json={'title': ' ' + title + ' ', 'body': body})
        self.assertEqual(response.status_code, 201)
        query, parameters = self.database.execute.call_args.args
        self.assertIn('VALUES (%s, %s)', query)
        self.assertEqual(parameters, (title, body))
        response = self.client.put('/api/notes/1', json={'title': title, 'body': body})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.database.execute.call_args.args[1], (title, body, 1))

    def test_invalid_input_and_missing_rows(self):
        self.authenticate()
        for payload in ({'title': '   '}, {'title': 'a', 'body': 'x' * 10001}):
            self.assertEqual(self.client.post('/api/notes', json=payload).status_code, 422)
        self.database.execute.return_value.fetchone.return_value = None
        self.assertEqual(self.client.put('/api/notes/99', json={'title': 'Missing'}).status_code, 404)
        self.assertEqual(self.client.delete('/api/notes/99').status_code, 404)

    def test_delete_returns_no_content(self):
        self.authenticate()
        self.database.execute.return_value.fetchone.return_value = {'id': 1}
        response = self.client.delete('/api/notes/1')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b'')
        self.assertEqual(self.database.execute.call_args.args[1], (1,))
