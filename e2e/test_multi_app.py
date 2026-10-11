"""Opt-in real browser acceptance against an installed ten-pod stack.

Set E2E_MULTI_APP=1, E2E_PASSWORD, E2E_CA_FILE and optionally E2E_CHROMIUM.
The browser's trust store must already trust the same CA. TLS errors are fatal.
"""
import base64
import hashlib
import json
import os
import socket
import ssl
import unittest
import uuid
from urllib.parse import urlsplit


@unittest.skipUnless(os.getenv('E2E_MULTI_APP') == '1', 'requires the real multi-app stack')
class SharedIdentityBrowserTests(unittest.TestCase):
    def test_shared_sso_independent_audiences_and_app_crud(self):
        from playwright.sync_api import expect, sync_playwright

        identity = os.getenv('E2E_IDENTITY_URL', 'https://auth.test:8443')
        todo = os.getenv('E2E_TODO_URL', 'https://todo.test:8443')
        notes = os.getenv('E2E_NOTES_URL', 'https://notes.test:8443')
        guestbook = os.getenv('E2E_GUESTBOOK_URL', 'https://guestbook.test:8443')
        username = os.getenv('E2E_USERNAME', 'testuser')
        password = os.environ['E2E_PASSWORD']
        tls = ssl.create_default_context(cafile=os.environ['E2E_CA_FILE'])
        certificates = []
        for origin in (identity, todo, notes, guestbook):
            url = urlsplit(origin)
            with socket.create_connection((url.hostname, url.port or 443), timeout=10) as connection:
                with tls.wrap_socket(connection, server_hostname=url.hostname) as secure:
                    certificates.append(hashlib.sha256(secure.getpeercert(binary_form=True)).digest())
        self.assertEqual(len(set(certificates)), 1, 'Keycloak and all three apps must serve the same SAN certificate')
        tokens = {}

        def capture(request):
            authorization = request.headers.get('authorization', '')
            if authorization.startswith('Bearer '):
                tokens[urlsplit(request.url).hostname] = authorization.removeprefix('Bearer ')

        def login_settings(page):
            # The login settings the frontend on this page reads: from its own app.yaml through the
            # pod's ConfigMap, and only that one key; the database settings beside it are not served.
            return page.evaluate('''async () => {
                const settings = await (await fetch('/config/login.json')).json();
                const others = await Promise.all(['DATABASE_HOST', 'FRONTEND_LOGIN', 'OIDC_AUDIENCE'].map(
                    async key => (await fetch('/config/' + key)).status));
                return {clientId: settings.clientId, realm: settings.realm, others};
            }''')

        def claims(token):
            return json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '==='))

        # The ID tokens Keycloak hands the frontends, read from its token endpoint's answer as the
        # browser receives it: the API must refuse them (docs/IDENTITY.md, section 2), which only a
        # real Keycloak can show.
        id_tokens = []

        def token_answer(response):
            return response.url.endswith('/protocol/openid-connect/token') and response.request.method == 'POST'

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.getenv('E2E_CHROMIUM'))
            try:
                context = browser.new_context(ignore_https_errors=False)
                context.on('request', capture)
                page = context.new_page()
                page.goto(todo)
                expect(page.locator('#login')).to_be_visible()
                page.locator('#login').click()
                page.locator('#username').fill(username)
                page.locator('#password').fill(password)
                with page.expect_response(token_answer) as answer:
                    page.locator('#kc-login').click()
                id_tokens.append(answer.value.json()['id_token'])
                expect(page.locator('#user-status')).to_have_text('Logged in as ' + username)
                marker = 'SSO check ' + uuid.uuid4().hex[:10]
                page.locator('#todo-title').fill(marker)
                page.locator('#todo-form button').click()
                item = page.locator('li.todo').filter(has_text=marker)
                expect(item).to_have_count(1)

                # This navigation must authenticate without a second password entry.
                with page.expect_response(token_answer) as answer:
                    page.goto(notes)
                id_tokens.append(answer.value.json()['id_token'])
                expect(page.locator('#user-status')).to_have_text('Logged in as ' + username)
                expect(page.locator('h1')).to_have_text('Notes')
                page.locator('#note-title').fill(marker)
                page.locator('#note-body').fill('Shared login; independent storage.\nSecond line.')
                page.locator('#save').click()
                note = page.locator('li.note').filter(has_text=marker)
                expect(note).to_have_count(1)
                expect(note.locator('.note-body')).to_have_text('Shared login; independent storage.\nSecond line.')
                note.get_by_role('button', name='Edit', exact=True).click()
                page.locator('#note-body').fill('Edited note')
                page.locator('#save').click()
                expect(note.locator('.note-body')).to_have_text('Edited note')

                self.assertEqual(login_settings(page), {'clientId': 'notes-frontend', 'realm': 'todo',
                                                        'others': [404, 404, 404]})
                todo_token, notes_token = (tokens[urlsplit(origin).hostname] for origin in (todo, notes))
                todo_claims, notes_claims = claims(todo_token), claims(notes_token)
                self.assertEqual(todo_claims['sub'], notes_claims['sub'])
                # Both apps log in at Keycloak's own hostname.
                self.assertEqual(todo_claims['iss'], identity + '/auth/realms/todo')
                self.assertEqual(notes_claims['iss'], todo_claims['iss'])
                # Each access token carries its API's audience, never the frontend's client ID.
                for payload, audience, client in ((todo_claims, 'todo-api', 'todo-frontend'),
                                                  (notes_claims, 'notes-api', 'notes-frontend')):
                    audiences = payload['aud'] if isinstance(payload['aud'], list) else [payload['aud']]
                    self.assertIn(audience, audiences)
                    self.assertNotIn(client, audiences)
                    self.assertEqual(payload['azp'], client)
                # An ID token names the frontend's client, and neither API accepts it.
                self.assertTrue(id_tokens, 'no ID token came from the token endpoint')
                for id_token in id_tokens:
                    audiences = claims(id_token)['aud']
                    audiences = audiences if isinstance(audiences, list) else [audiences]
                    self.assertFalse({'todo-api', 'notes-api'} & set(audiences), audiences)
                for id_token in id_tokens:
                    rejected = page.evaluate('''async token => (await fetch('/api/notes', {
                        method: 'POST', headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + token},
                        body: JSON.stringify({title: 'An ID token'})})).status''', id_token)
                    self.assertEqual(rejected, 401)
                rejected = page.evaluate('''async token => (await fetch('/api/notes', {
                    method: 'POST', headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + token},
                    body: JSON.stringify({title: 'Wrong audience'})})).status''', todo_token)
                self.assertEqual(rejected, 401)
                note.get_by_role('button', name='Delete', exact=True).click()
                expect(note).to_have_count(0)

                # Guestbook requires login for reads too; SSO must obtain its own access token.
                with page.expect_response(token_answer) as answer:
                    page.goto(guestbook)
                guestbook_id_token = answer.value.json()['id_token']
                expect(page.locator('#session')).to_have_text('Signed in as ' + username)
                self.assertEqual(login_settings(page), {'clientId': 'guestbook-frontend', 'realm': 'todo',
                                                        'others': [404, 404, 404]})
                page.locator('#message').fill(marker)
                page.locator('#submit').click()
                entry = page.locator('#entries li').filter(has_text=marker)
                expect(entry).to_have_count(1)
                page.reload()
                expect(page.locator('#session')).to_have_text('Signed in as ' + username)
                expect(entry.locator('p')).to_have_text(marker)
                guestbook_token = tokens[urlsplit(guestbook).hostname]
                guestbook_claims = claims(guestbook_token)
                self.assertEqual(guestbook_claims['sub'], todo_claims['sub'])
                self.assertEqual(guestbook_claims['iss'], todo_claims['iss'])
                audiences = guestbook_claims['aud']
                audiences = audiences if isinstance(audiences, list) else [audiences]
                self.assertIn('guestbook-api', audiences)
                self.assertNotIn('guestbook-frontend', audiences)
                self.assertEqual(guestbook_claims['azp'], 'guestbook-frontend')
                id_audiences = claims(guestbook_id_token)['aud']
                id_audiences = id_audiences if isinstance(id_audiences, list) else [id_audiences]
                self.assertIn('guestbook-frontend', id_audiences)
                self.assertNotIn('guestbook-api', id_audiences)
                for token in ('', guestbook_id_token, todo_token):
                    # Both reading and writing reject anonymous requests, the app's real ID token,
                    # and a valid access token issued to Todo. Neither token is fabricated here.
                    statuses = page.evaluate('''async token => {
                        const headers = {'Content-Type': 'application/json'};
                        if (token) headers.Authorization = 'Bearer ' + token;
                        const read = await fetch('/api/entries', {headers});
                        const write = await fetch('/api/entries', {method: 'POST', headers,
                            body: JSON.stringify({message: 'Rejected token probe'})});
                        return [read.status, write.status];
                    }''', token)
                    self.assertEqual(statuses, [401, 401])
                entry.get_by_role('button', name='Delete entry').click()
                expect(entry).to_have_count(0)
                page.reload()
                expect(page.locator('#session')).to_have_text('Signed in as ' + username)
                expect(entry).to_have_count(0)

                page.goto(todo)
                expect(page.locator('#user-status')).to_have_text('Logged in as ' + username)
                self.assertEqual(login_settings(page), {'clientId': 'todo-frontend', 'realm': 'todo',
                                                        'others': [404, 404, 404]})
                rejected = page.evaluate('''async token => (await fetch('/api/todos', {
                    method: 'POST', headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + token},
                    body: JSON.stringify({title: 'Wrong audience'})})).status''', notes_token)
                self.assertEqual(rejected, 401)
                item = page.locator('li.todo').filter(has_text=marker)
                item.get_by_role('button', name='Delete', exact=True).click()
                expect(item).to_have_count(0)
                page.goto(notes)
                expect(page.locator('#logout')).to_be_visible()
                page.locator('#logout').click()
                expect(page.locator('#user-status')).to_have_text('Reading publicly')
                page.goto(todo)
                expect(page.locator('#user-status')).to_have_text('Reading publicly')

                # Start a fresh session from Notes and verify SSO in the other direction.
                context.close()
                context = browser.new_context(ignore_https_errors=False)
                page = context.new_page()
                page.goto(notes)
                page.locator('#login').click()
                page.locator('#username').fill(username)
                page.locator('#password').fill(password)
                page.locator('#kc-login').click()
                expect(page.locator('#user-status')).to_have_text('Logged in as ' + username)
                page.goto(todo)
                expect(page.locator('#user-status')).to_have_text('Logged in as ' + username)
                page.locator('#logout').click()
                expect(page.locator('#user-status')).to_have_text('Reading publicly')
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
