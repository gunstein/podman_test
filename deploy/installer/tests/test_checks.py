"""checks.py: an app's ready path and checks, asked of nginx with the app's hostname."""
import http.server
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import apps, checks  # noqa: E402

SHOP = apps.App(name='shop', hostname='shop.test', keycloak_client='shop-frontend', ready='/ready',
                checks=(apps.Check(path='/health', status=200), apps.Check(path='/api/items', status=200)))
HELP = apps.App(name='help', hostname='help.test', has_database=False, replication_port=0)


class Answers(http.server.BaseHTTPRequestHandler):
    """200 for shop.test/ready, 503 for its /api/items, 302 to /ready for /old, 404 for anything else."""

    def do_GET(self):
        code = {('shop.test', '/ready'): 200, ('shop.test', '/api/items'): 503, ('shop.test', '/old'): 302}.get(
            (self.headers['Host'], self.path), 404)
        self.send_response(code)
        if code == 302:
            self.send_header('Location', '/ready')
        self.end_headers()

    def log_message(self, *args):
        pass


class CheckTests(unittest.TestCase):
    def test_status_is_the_answer_of_the_hostnames_virtual_host_or_none(self):
        server = http.server.HTTPServer(('127.0.0.1', 0), Answers)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.object(checks, 'BASE', f'http://127.0.0.1:{server.server_port}'):
            self.assertEqual(checks.status('/ready', 'shop.test'), 200)
            self.assertEqual(checks.status('/api/items', 'shop.test'), 503)
            self.assertEqual(checks.status('/ready', 'other.test'), 404)
            # A redirect is the check's answer; it is not followed to the page it names.
            self.assertEqual(checks.status('/old', 'shop.test'), 302)
        with patch.object(checks, 'BASE', 'http://127.0.0.1:1'):
            self.assertIsNone(checks.status('/ready', 'shop.test'))

    def test_ready_is_waited_for_and_an_app_without_one_is_not_asked(self):
        answers = iter([None, 503, 200])
        with patch.object(checks, 'status', side_effect=lambda path, hostname: next(answers)) as status, \
                patch.object(checks.time, 'sleep'):
            checks.wait_ready(SHOP, 'shop.example.org')
            self.assertEqual(status.call_count, 3)
            status.assert_called_with('/ready', 'shop.example.org')
            status.reset_mock()
            checks.wait_ready(HELP, 'help.test')
            status.assert_not_called()
        with patch.object(checks, 'status', return_value=503), patch.object(checks.time, 'sleep'), \
                self.assertRaisesRegex(RuntimeError, 'shop: /ready on shop.test did not answer 200 after 3 attempts'):
            checks.wait_ready(SHOP, 'shop.test', attempts=3)

    def test_verify_waits_for_each_app_then_runs_its_checks_once(self):
        asked = []
        platform = apps.Platform(apps=(SHOP, HELP), identity_hostname='auth.test')
        with patch.object(checks, 'status', side_effect=lambda path, hostname: asked.append((hostname, path)) or 200):
            checks.verify(platform, {'shop': 'shop.example.org', 'help': 'help.test'})
        self.assertEqual(asked, [('shop.example.org', '/ready'), ('shop.example.org', '/health'),
                                 ('shop.example.org', '/api/items')])
        with patch.object(checks, 'status', side_effect=lambda path, hostname: 503 if path == '/api/items' else 200), \
                self.assertRaisesRegex(RuntimeError, 'shop: GET /api/items on shop.test answered 503, not 200'):
            checks.run(SHOP, 'shop.test')
        with patch.object(checks, 'status', return_value=None), \
                self.assertRaisesRegex(RuntimeError, 'answered nothing, not 200'):
            checks.run(SHOP, 'shop.test')

    def test_wait_ready_sh_gets_each_apps_hostname_and_ready_path(self):
        platform = apps.Platform(apps=(SHOP, HELP), identity_hostname='auth.test')
        self.assertEqual(checks.ready_urls(platform, {'shop': 'shop.example.org', 'help': 'help.test'}),
                         ['shop.example.org/ready'])


if __name__ == '__main__':
    unittest.main()
