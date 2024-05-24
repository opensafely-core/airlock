from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from airlock.business_logic import RequestStatus
from tests import factories


@pytest.fixture(autouse=True)
def release_request(researcher_user):
    workspace = factories.create_workspace("test-dir1")
    factories.write_workspace_file(workspace, "foo.txt", "")
    factories.create_repo(workspace)
    release_request = factories.create_release_request(
        workspace, user=researcher_user, status=RequestStatus.SUBMITTED
    )
    # Ensure the request file is written using the workspace previously
    # created (so it's assigned the correct commit from the manifest.json associated
    # with that workspace)
    factories.write_request_file(
        release_request, "group", "foo.txt", workspace=workspace
    )
    yield release_request


def test_code_from_workspace(live_server, page, researcher_user):
    code_button = page.locator("#file-code-button")
    return_button = page.locator("#return-button")

    # At a directory view, the code button is not displayed
    page.goto(live_server.url + "/workspaces/view/test-dir1/")
    expect(code_button).not_to_be_visible()

    # manifest.json itself doesn't have a manifest entry, so code button not displayed
    page.goto(live_server.url + "/workspaces/view/test-dir1/metadata/manifest.json")
    expect(code_button).not_to_be_visible()

    # output file does display code button
    file_url = "/workspaces/view/test-dir1/foo.txt"
    page.goto(live_server.url + file_url)
    expect(code_button).to_be_visible()
    code_button.click()

    # return url (the workspace file) is passed to code view as a query param,
    # and used as the href for the return button
    url_parts = urlsplit(page.url)
    assert url_parts.query == f"return_url={file_url}"
    expect(page.locator("body")).to_contain_text("project.yaml")
    expect(return_button).to_be_visible()
    expect(return_button).to_have_attribute("href", file_url)

    file_link = (
        page.locator("#tree")
        .get_by_role("link", name="project.yaml")
        .locator(".file:scope")
    )
    file_link.click()
    expect(return_button).to_be_visible()
    expect(return_button).to_have_attribute("href", file_url)


def test_code_from_request(live_server, page, release_request, output_checker_user):
    code_button = page.locator("#file-code-button")
    return_button = page.locator("#return-button")

    # At a directory view, the code button is not displayed
    page.goto(live_server.url + f"/requests/view/{release_request.id}/group/")
    expect(code_button).not_to_be_visible()

    # file view displays code button
    file_url = f"/requests/view/{release_request.id}/group/foo.txt"
    page.goto(live_server.url + file_url)
    expect(code_button).to_be_visible()
    code_button.click()

    # return url (the release_request file) is passed to code view as a query param,
    # and used as the href for the return button
    url_parts = urlsplit(page.url)
    assert url_parts.query == f"return_url={file_url}"
    expect(page.locator("body")).to_contain_text("project.yaml")
    expect(return_button).to_be_visible()
    expect(return_button).to_have_attribute("href", file_url)

    file_link = (
        page.locator("#tree")
        .get_by_role("link", name="project.yaml")
        .locator(".file:scope")
    )
    file_link.click()
    expect(return_button).to_be_visible()
    expect(return_button).to_have_attribute("href", file_url)
