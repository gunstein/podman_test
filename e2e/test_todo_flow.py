import os
from uuid import uuid4

from playwright.sync_api import Page, expect


BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:8080")


def test_complete_todo_user_flow(page: Page):
    title = f"E2E Todo {uuid4().hex}"
    page.goto(BASE_URL)

    try:
        page.get_by_label("New todo").fill(title)
        page.get_by_role("button", name="Add").click()

        todo = page.locator("li.todo", has_text=title)
        expect(todo).to_be_visible()

        todo.get_by_role("checkbox").check()
        expect(todo.locator(".completed")).to_have_text(title)

        todo.get_by_role("button", name="Delete").click()
        expect(todo).not_to_be_visible()
    finally:
        response = page.request.get(f"{BASE_URL}/api/todos")
        if response.ok:
            for item in response.json():
                if item["title"] == title:
                    page.request.delete(f"{BASE_URL}/api/todos/{item['id']}")
