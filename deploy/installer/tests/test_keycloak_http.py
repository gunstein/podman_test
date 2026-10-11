"""keycloak.request: the HTTP Keycloak's admin API gets, asked of a local server in place of nginx."""
import http.server
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import keycloak  # noqa: E402


class Recorder(http.server.BaseHTTPRequestHandler):
    """Records each request and answers with the status and body the test set for its method and path."""

    answers = {}
    seen = []

    def answer(self):
        length = int(self.headers.get('Content-Length') or 0)
        self.seen.append({'method': self.command, 'path': self.path, 'headers': dict(self.headers),
                          'body': self.rfile.read(length).decode()})
        status, body = self.answers[(self.command, self.path)]
        self.send_response(status)
        content = json.dumps(body).encode() if body is not None else b''
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    do_GET = do_POST = do_PUT = answer

    def log_message(self, *args):
        pass


class RequestTests(unittest.TestCase):
    def setUp(self):
        Recorder.answers, Recorder.seen = {}, []
        server = http.server.HTTPServer(('127.0.0.1', 0), Recorder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = patch.object(keycloak, 'BASE', f'http://127.0.0.1:{server.server_port}')
        base.start()
        self.addCleanup(base.stop)

    def test_a_form_post_with_its_host_header_returns_the_json_reply(self):
        Recorder.answers[('POST', '/auth/realms/master/token')] = (200, {'access_token': 'abc'})
        reply = keycloak.request('/auth/realms/master/token', 'POST', {'grant_type': 'password', 'username': 'a b'},
                                 form=True, hostname='auth.test')
        self.assertEqual(reply, {'access_token': 'abc'})
        [seen] = Recorder.seen
        self.assertEqual(seen['headers']['Host'], 'auth.test')
        self.assertEqual(seen['headers']['Content-Type'], 'application/x-www-form-urlencoded')
        self.assertEqual(seen['body'], 'grant_type=password&username=a+b')
        self.assertNotIn('Authorization', seen['headers'])

    def test_json_with_a_bearer_token_and_the_status_each_method_must_answer(self):
        Recorder.answers.update({
            ('GET', '/admin/realms/todo'): (200, {'realm': 'todo'}),
            ('PUT', '/admin/realms/todo'): (204, None),
            ('POST', '/admin/realms/todo/clients'): (201, None),
        })
        self.assertEqual(keycloak.request('/admin/realms/todo', token='t0ken'), {'realm': 'todo'})
        self.assertEqual(keycloak.request('/admin/realms/todo', 'PUT', {'enabled': True}, token='t0ken'), {})
        self.assertEqual(keycloak.request('/admin/realms/todo/clients', 'POST', {'clientId': 'x'}, token='t0ken'), {})
        get, put, post = Recorder.seen
        self.assertEqual([get['method'], put['method'], post['method']], ['GET', 'PUT', 'POST'])
        self.assertTrue(all(seen['headers']['Authorization'] == 'Bearer t0ken' for seen in Recorder.seen))
        self.assertEqual(get['body'], '')
        self.assertNotIn('Content-Type', get['headers'])
        self.assertEqual(put['headers']['Content-Type'], 'application/json')
        self.assertEqual(json.loads(put['body']), {'enabled': True})
        self.assertEqual(json.loads(post['body']), {'clientId': 'x'})

    def test_another_success_status_or_an_error_is_refused(self):
        Recorder.answers.update({
            ('PUT', '/admin/realms/todo'): (200, {}),  # PUT must answer 204
            ('POST', '/admin/realms/todo/clients'): (200, {}),  # creating a client must answer 201
            ('GET', '/admin/realms/gone'): (404, {'error': 'not found'}),
        })
        for path, method in (('/admin/realms/todo', 'PUT'), ('/admin/realms/todo/clients', 'POST')):
            with self.subTest(method=method), \
                    self.assertRaisesRegex(RuntimeError, f'Unexpected HTTP status 200 for {path}'):
                keycloak.request(path, method, {})
        with self.assertRaises(HTTPError) as error:
            keycloak.request('/admin/realms/gone')
        error.exception.close()
        self.assertEqual(error.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
