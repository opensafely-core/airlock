import pytest
from playwright.sync_api import expect

from tests import factories


@pytest.fixture(autouse=True)
def workspaces():
    factories.create_workspace("test-dir1")
    factories.create_workspace("test-dir2")


def test_workspaces_index_as_ouput_checker(live_server, page, output_checker_user):
    # this should only list their workspaces, even though they can access all workspaces
    page.goto(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text("Workspaces for test_output_checker")
    expect(page.locator("body")).not_to_contain_text("test-dir1")
    expect(page.locator("body")).to_contain_text("test-dir2")


def test_workspaces_index_as_researcher(live_server, page, researcher_user):
    page.goto(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text("Workspaces for test_researcher")
    expect(page.locator("body")).to_contain_text("test-dir1")
    expect(page.locator("body")).not_to_contain_text("test-dir2")
