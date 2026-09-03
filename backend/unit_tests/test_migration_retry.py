import argparse
import unittest
from unittest import mock

import psycopg

from backend import migrate


class MigrationRetryTests(unittest.TestCase):
    def test_transient_connection_failure_is_retried(self):
        connection = mock.Mock()
        transient = psycopg.OperationalError("database offline")

        with (
            mock.patch.object(
                migrate, "connect", side_effect=[transient, connection]
            ) as connect,
            mock.patch.object(migrate.time, "monotonic", side_effect=[0.0, 5.0]),
            mock.patch.object(migrate.time, "sleep") as sleep,
            mock.patch("builtins.print"),
        ):
            result = migrate.connect_with_retry(10)

        self.assertIs(result, connection)
        self.assertEqual(connect.call_count, 2)
        sleep.assert_called_once_with(2.0)

    def test_startup_timeout_preserves_the_original_error(self):
        transient = psycopg.errors.CannotConnectNow("database starting")

        with (
            mock.patch.object(migrate, "connect", side_effect=transient),
            mock.patch.object(migrate.time, "monotonic", side_effect=[0.0, 3.0]),
            mock.patch.object(migrate.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                psycopg.errors.CannotConnectNow, "database starting"
            ):
                migrate.connect_with_retry(2)

        sleep.assert_not_called()

    def test_timeout_must_be_finite_and_non_negative(self):
        for value in ("-1", "inf", "nan"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    migrate.parse_connect_timeout(value)

    def test_authentication_failure_is_not_retried(self):
        authentication = psycopg.errors.InvalidPassword("bad password")

        with (
            mock.patch.object(
                migrate, "connect", side_effect=authentication
            ) as connect,
            mock.patch.object(migrate.time, "sleep") as sleep,
        ):
            with self.assertRaises(psycopg.errors.InvalidPassword):
                migrate.connect_with_retry(120)

        connect.assert_called_once_with()
        sleep.assert_not_called()

    def test_sql_failure_after_connect_is_not_retried(self):
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.side_effect = psycopg.errors.SyntaxError("bad SQL")

        with mock.patch.object(migrate, "connect", return_value=connection) as connect:
            with self.assertRaises(psycopg.errors.SyntaxError):
                migrate.migrate_up(connect_timeout=120)

        connect.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
