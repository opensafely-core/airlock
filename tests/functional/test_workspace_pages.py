import pytest
from playwright.sync_api import expect

from tests.factories import WorkspaceFactory


@pytest.fixture(autouse=True)
def workspaces():
    WorkspaceFactory("test-dir1")
    WorkspaceFactory("test-dir2")


def test_workspaces_index_as_ouput_checker(live_server, page, output_checker_user):
    page.goto(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text("Workspaces for test_output_checker")
    for workspace_name in ["test-dir1", "test-dir2"]:
        expect(page.locator("body")).to_contain_text(workspace_name)


def test_workspaces_index_as_researcher(live_server, page, researcher_user):
    page.goto(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text("Workspaces for test_researcher")
    expect(page.locator("body")).to_contain_text("test-dir1")
    expect(page.locator("body")).not_to_contain_text("test-dir2")