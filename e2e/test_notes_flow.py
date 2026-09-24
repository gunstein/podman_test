import os
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect

# Notes is not nginx's default server block (Todo is); reaching it needs the
# real hostname, mapped in /etc/hosts or DNS, not a bare localhost fallback.
BASE_URL = os.getenv("E2E_NOTES_URL", "https://notes.test:8443")


def test_public_notes_list(page: Page):
    page.goto(BASE_URL)

    expect(page.get_by_text("Reading publicly")).to_be_visible()
    expect(page.get_by_role("button", name="Log in")).to_be_visible()
    expect(page.get_by_label("Title")).to_be_hidden()
    expect(page.locator("#note-list")).not_to_contain_text("Loading...")


def test_authenticated_notes_flow(page: Page):
    username = os.getenv("E2E_USERNAME")
    password = os.getenv("E2E_PASSWORD")
    if not username or not password:
        pytest.skip("Set E2E_USERNAME and E2E_PASSWORD to test authenticated writes")

    title = f"E2E Note {uuid4().hex}"
    page.goto(BASE_URL)
    page.get_by_role("button", name="Log in").click()
    page.get_by_label("Username or email").fill(username)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign In").click()

    if page.get_by_role("heading", name="Update Account Information").is_visible():
        pytest.fail(
            "The Keycloak test user has an incomplete profile. "
            "Set email, first name and last name in the Keycloak admin console."
        )

    expect(page.get_by_text(f"Logged in as {username}")).to_be_visible()
    page.get_by_label("Title").fill(title)
    page.get_by_label("Note").fill("Created by e2e/test_notes_flow.py")
    page.get_by_role("button", name="Add note").click()

    note = page.locator("li.note", has_text=title)
    expect(note).to_be_visible()
    expect(note.locator(".note-body")).to_have_text("Created by e2e/test_notes_flow.py")

    note.get_by_role("button", name="Edit", exact=True).click()
    page.get_by_label("Note").fill("Edited by e2e/test_notes_flow.py")
    page.get_by_role("button", name="Save note").click()
    expect(note.locator(".note-body")).to_have_text("Edited by e2e/test_notes_flow.py")

    note.get_by_role("button", name="Delete", exact=True).click()
    expect(note).not_to_be_visible()
