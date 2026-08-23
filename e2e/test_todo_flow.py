import os
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


BASE_URL = os.getenv("E2E_BASE_URL", "https://localhost:8443")


def test_public_todo_list(page: Page):
    page.goto(BASE_URL)

    expect(page.get_by_text("Reading publicly")).to_be_visible()
    expect(page.get_by_role("button", name="Log in")).to_be_visible()
    expect(page.get_by_label("New todo")).to_be_hidden()
    expect(page.locator("#todo-list")).not_to_contain_text("Loading...")


def test_authenticated_todo_flow(page: Page):
    username = os.getenv("E2E_USERNAME")
    password = os.getenv("E2E_PASSWORD")
    if not username or not password:
        pytest.skip("Set E2E_USERNAME and E2E_PASSWORD to test authenticated writes")

    title = f"E2E Todo {uuid4().hex}"
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
    page.get_by_label("New todo").fill(title)
    page.get_by_role("button", name="Add").click()

    todo = page.locator("li.todo", has_text=title)
    expect(todo).to_be_visible()

    todo.get_by_role("checkbox").check()
    expect(todo.locator(".completed")).to_have_text(title)

    todo.get_by_role("button", name="Delete").click()
    expect(todo).not_to_be_visible()
