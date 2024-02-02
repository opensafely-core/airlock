import pytest
from playwright.sync_api import expect

from tests.factories import WorkspaceFactory


@pytest.fixture(autouse=True)
def workspaces():
    WorkspaceFactory("test-dir1")
    WorkspaceFactory("test-dir2")


@pytest.mark.parametrize(
    "username,allowed,not_allowed",
    [
        ("test_output_checker", ["test-dir1", "test-dir2"], []),
        ("test_researcher", ["test-dir1"], ["test-dir2"]),
    ],
)
def test_workspaces_index(live_server, logged_in_page, username, allowed, not_allowed):
    page = logged_in_page(username)
    page.goto(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text(f"Workspaces for {username}")
    for workspace_name in allowed:
        expect(page.locator("body")).to_contain_text(workspace_name)
    for workspace_name in not_allowed:
        expect(page.locator("body")).not_to_contain_text(workspace_name)
