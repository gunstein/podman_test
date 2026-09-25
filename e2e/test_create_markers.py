"""create_markers.marker_id against a real HTTPS server whose CA only Chromium trusts.

In acceptance, the lab CA is installed in Chromium's certificate store, not in
the one Playwright's Node.js process uses. This test builds the same situation
with a throwaway certificate: Chromium is told to trust exactly that key, and
TLS errors stay fatal everywhere. No VM, IdP or database is needed.
"""
import base64
import hashlib
import http.server
import json
import os
import ssl
import subprocess
import threading

import create_markers
import pytest
from playwright.sync_api import Error

ROWS = {'/api/todos': [{'id': 7, 'title': 'marker'}, {'id': 8, 'title': 'other'}],
        '/api/notes': [{'id': 3, 'title': 'twice'}, {'id': 4, 'title': 'twice'}]}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            body, kind, status = b'<!doctype html><title>app</title>', 'text/html', 200
        elif self.path in ROWS:
            body, kind, status = json.dumps(ROWS[self.path]).encode(), 'application/json', 200
        else:
            body, kind, status = b'{"detail": "Not Found"}', 'application/json', 404
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *arguments):
        pass


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    directory = tmp_path_factory.mktemp('tls')
    key, certificate = directory / 'server.key', directory / 'server.crt'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=throwaway lab CA', '-addext', 'subjectAltName=IP:127.0.0.1',
                    '-keyout', str(key), '-out', str(certificate)], check=True, capture_output=True)
    public_key = subprocess.run(['openssl', 'x509', '-in', str(certificate), '-pubkey', '-noout'],
                                check=True, capture_output=True).stdout
    der = subprocess.run(['openssl', 'pkey', '-pubin', '-outform', 'der'], input=public_key,
                         check=True, capture_output=True).stdout
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f'https://127.0.0.1:{httpd.server_port}', base64.b64encode(hashlib.sha256(der).digest()).decode()
    httpd.shutdown()


@pytest.fixture
def page(browser_type, server):
    origin, key_hash = server
    # Trust this one throwaway key in Chromium only, like the lab CA in its store.
    browser = browser_type.launch(executable_path=os.getenv('E2E_CHROMIUM'),
                                  args=['--ignore-certificate-errors-spki-list=' + key_hash])
    page = browser.new_context(ignore_https_errors=False).new_page()
    page.goto(origin)
    yield page
    browser.close()


def test_playwright_node_requests_do_not_share_the_browser_trust(page, server):
    # Why marker_id must not use page.request: this is the acceptance failure.
    with pytest.raises(Error, match='certificate'):
        page.request.get(server[0] + '/api/todos')


def test_marker_id_reads_the_list_through_the_browser(page):
    assert create_markers.marker_id(page, '/api/todos', 'marker') == 7


def test_marker_id_requires_exactly_one_row(page):
    for path, title in (('/api/todos', 'missing'), ('/api/notes', 'twice')):
        with pytest.raises(SystemExit, match='Expected exactly one row'):
            create_markers.marker_id(page, path, title)


def test_marker_id_reports_an_http_error(page):
    with pytest.raises(Error, match='HTTP 404'):
        create_markers.marker_id(page, '/api/unknown', 'marker')
