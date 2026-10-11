"""Authentication on every data endpoint, input bounds and database failures."""
import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
main = importlib.import_module("backend.main")


class GuestbookAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.connection = MagicMock()
        self.connect = patch.object(main, "connect", return_value=self.connection)
        self.open_database = self.connect.start()
        self.addCleanup(self.connect.stop)
        self.database = self.connection.__enter__.return_value
        self.addCleanup(main.app.dependency_overrides.clear)
        self.entry = {"id": 1, "message": "Hello", "created_at": "2026-01-01T12:00:00Z"}

    def authenticate(self):
        main.app.dependency_overrides[main.require_user] = lambda: {"sub": "a-user"}

    def test_every_data_endpoint_refuses_missing_and_invalid_tokens(self):
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            for method, path, payload in (
                ("GET", "/api/entries", None),
                ("POST", "/api/entries", {"message": "Hello"}),
                ("DELETE", "/api/entries/1", None),
            ):
                with self.subTest(method=method, headers=headers):
                    response = self.client.request(method, path, json=payload, headers=headers)
                    self.assertEqual(response.status_code, 401)
        self.open_database.assert_not_called()

    def test_authenticated_read_returns_entries(self):
        self.authenticate()
        self.database.execute.return_value.fetchall.return_value = [self.entry]
        response = self.client.get("/api/entries")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [self.entry])

    def test_create_treats_message_as_data(self):
        self.authenticate()
        message = "<script>alert(1)</script>'; --"
        self.database.execute.return_value.fetchone.return_value = {**self.entry, "message": message}
        response = self.client.post("/api/entries", json={"message": " " + message + " "})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["message"], message)
        query, parameters = self.database.execute.call_args.args
        self.assertIn("VALUES (%s)", query)
        self.assertNotIn(message, query)
        self.assertEqual(parameters, (message,))

    def test_blank_missing_and_oversized_messages_are_refused(self):
        self.authenticate()
        for payload in ({}, {"message": "  \n"}, {"message": "x" * 4001}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/api/entries", json=payload).status_code, 422)
        self.open_database.assert_not_called()

    def test_delete_and_missing_entry(self):
        self.authenticate()
        self.database.execute.return_value.fetchone.return_value = {"id": 1}
        response = self.client.delete("/api/entries/1")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(self.database.execute.call_args.args[1], (1,))
        self.database.execute.return_value.fetchone.return_value = None
        self.assertEqual(self.client.delete("/api/entries/1").status_code, 404)

    def test_health_and_ready_are_public_but_ready_requires_the_table(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.open_database.assert_not_called()
        self.assertEqual(self.client.get("/ready").status_code, 200)
        self.assertIn("FROM entries", self.database.execute.call_args.args[0])
        self.database.execute.side_effect = psycopg.errors.UndefinedTable("entries missing")
        self.assertEqual(self.client.get("/ready").status_code, 503)
