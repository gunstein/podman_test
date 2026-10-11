"""Create one authenticated persistent marker in each of Todo, Notes and Guestbook.

Acceptance helper, not a test: it logs in through the real Keycloak flow with
TLS errors fatal, creates one row per app with MARKER as its title/message, leaves all in place
and prints their IDs. Needs E2E_PASSWORD and MARKER; optionally E2E_USERNAME,
E2E_TODO_URL, E2E_NOTES_URL, E2E_GUESTBOOK_URL and E2E_CHROMIUM. Never prints the password.
"""
import os

from playwright.sync_api import expect, sync_playwright

# fetch() runs inside Chromium, so it uses the same certificate trust as the page.
# page.request would run in Playwright's Node.js process instead, which does not
# trust a lab CA installed for the browser, and fails with TLS errors kept fatal.
READ_JSON = """async ({path, authenticated}) => {
    const headers = {};
    if (authenticated) {
        const {default: auth} = await import('/auth.js');
        const token = await auth.getAccessToken();
        if (!token) throw new Error('No access token for the marker read');
        headers.Authorization = 'Bearer ' + token;
    }
    const response = await fetch(path, {headers});
    if (!response.ok) throw new Error(path + ': HTTP ' + response.status);
    return response.json();
}"""


def marker_id(page, path, title, *, field='title', authenticated=False):
    """The unique marker row; Guestbook uses message and an authenticated read."""
    rows = page.evaluate(READ_JSON, {'path': path, 'authenticated': authenticated})
    matches = [row['id'] for row in rows if row[field] == title]
    if len(matches) != 1:
        raise SystemExit(f'Expected exactly one row with {field} {title!r} at {path}, found {len(matches)}')
    return matches[0]


def main():
    """Log in, create all three markers through the UI, and print their IDs."""
    todo = os.getenv('E2E_TODO_URL', 'https://todo.test:8443')
    notes = os.getenv('E2E_NOTES_URL', 'https://notes.test:8443')
    guestbook = os.getenv('E2E_GUESTBOOK_URL', 'https://guestbook.test:8443')
    username = os.getenv('E2E_USERNAME', 'testuser')
    password = os.environ['E2E_PASSWORD']
    title = os.environ['MARKER']
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv('E2E_CHROMIUM'))
        try:
            page = browser.new_context(ignore_https_errors=False).new_page()
            page.goto(todo)
            page.locator('#login').click()
            page.locator('#username').fill(username)
            page.locator('#password').fill(password)
            page.locator('#kc-login').click()
            expect(page.locator('#user-status')).to_have_text('Logged in as ' + username)
            page.locator('#todo-title').fill(title)
            page.locator('#todo-form button').click()
            expect(page.locator('li.todo').filter(has_text=title)).to_have_count(1)
            todo_id = marker_id(page, '/api/todos', title)

            page.goto(notes)
            expect(page.locator('#user-status')).to_have_text('Logged in as ' + username)
            page.locator('#note-title').fill(title)
            page.locator('#note-body').fill('Acceptance marker')
            page.locator('#save').click()
            expect(page.locator('li.note').filter(has_text=title)).to_have_count(1)
            note_id = marker_id(page, '/api/notes', title)

            page.goto(guestbook)
            expect(page.locator('#session')).to_have_text('Signed in as ' + username)
            page.locator('#message').fill(title)
            page.locator('#submit').click()
            expect(page.locator('#entries li').filter(has_text=title)).to_have_count(1)
            guestbook_id = marker_id(page, '/api/entries', title, field='message', authenticated=True)
        finally:
            browser.close()
    print(f'MARKER {title!r}: todo id={todo_id} note id={note_id} guestbook id={guestbook_id}')


if __name__ == '__main__':
    main()
