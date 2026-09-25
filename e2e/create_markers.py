"""Create one authenticated persistent marker in Todo and one in Notes.

Acceptance helper, not a test: it logs in through the real Keycloak flow with
TLS errors fatal, creates a Todo and a Note titled MARKER, leaves both in place
and prints their IDs. Needs E2E_PASSWORD and MARKER; optionally E2E_USERNAME,
E2E_TODO_URL, E2E_NOTES_URL and E2E_CHROMIUM. Never prints the password.
"""
import os

from playwright.sync_api import expect, sync_playwright

# fetch() runs inside Chromium, so it uses the same certificate trust as the page.
# page.request would run in Playwright's Node.js process instead, which does not
# trust a lab CA installed for the browser, and fails with TLS errors kept fatal.
READ_JSON = """async path => {
    const response = await fetch(path);
    if (!response.ok) throw new Error(path + ': HTTP ' + response.status);
    return response.json();
}"""


def marker_id(page, path, title):
    """The ID of the one row titled title in the public list at path, e.g. /api/todos."""
    rows = page.evaluate(READ_JSON, path)
    matches = [row['id'] for row in rows if row['title'] == title]
    if len(matches) != 1:
        raise SystemExit(f'Expected exactly one row titled {title!r} at {path}, found {len(matches)}')
    return matches[0]


def main():
    """Log in, create both markers through the UI, and print their IDs."""
    todo = os.getenv('E2E_TODO_URL', 'https://todo.test:8443')
    notes = os.getenv('E2E_NOTES_URL', 'https://notes.test:8443')
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
        finally:
            browser.close()
    print(f'MARKER {title!r}: todo id={todo_id} note id={note_id}')


if __name__ == '__main__':
    main()
