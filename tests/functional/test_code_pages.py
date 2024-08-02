from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect

from airlock.enums import RequestStatus
from tests import factories


@pytest.fixture(autouse=True)
def release_request(researcher_user):
    workspace = factories.create_workspace("test-dir1")
    factories.write_workspace_file(workspace, "foo.txt", "")
    factories.create_repo(workspace)
    release_request = factories.create_request_at_status(
        workspace,
        author=researcher_user,
        status=RequestStatus.SUBMITTED,
        # Ensure the request file is written using the workspace previously
        # created (so it's assigned the correct commit from the manifest.json associated
        # with that workspace)
        files=[
            factories.request_file(group="group", path="foo.txt", workspace=workspace)
        ],
    )
    yield release_request


def test_code_from_workspace(live_server, page, context):
    more_button = page.locator("#file-button-more")
    code_button = page.locator("#file-code-button")

    # At a directory view, the code button is not displayed
    page.goto(live_server.url + "/workspaces/view/test-dir1/")
    expect(more_button).not_to_be_visible()
    expect(code_button).not_to_be_visible()

    # manifest.json itself doesn't have a manifest entry, so code button not displayed
    page.goto(live_server.url + "/workspaces/view/test-dir1/metadata/manifest.json")
    page.locator("#file-button-more").click()
    expect(more_button).to_be_visible()
    more_button.click()
    expect(code_button).not_to_be_visible()

    # output file does display code button
    file_url = "/workspaces/view/test-dir1/foo.txt"
    page.goto(live_server.url + file_url)
    more_button.click()
    expect(code_button).to_be_visible()

    with context.expect_page() as new_page_info:
        code_button.click()  # Opens code in a new tab
        new_page = new_page_info.value

    return_button = new_page.locator("#return-button")

    expect(new_page.locator("body")).to_contain_text("project.yaml")
    expect(return_button).to_be_visible()
    expect(return_button).to_have_attribute("href", file_url)

    # return url (the workspace file) is passed to code view as a query param,
    # and used as the href for the return button
    url_parts = urlsplit(new_page.url)
    assert url_parts.query == f"return_url={file_url}"

    file_link = (
        new_page.locator("#tree")
        .get_by_role("link", name="project.yaml")
        .locator(".file:scope")
    )
    file_link.click()
    expect(return_button).to_be_visible()
    expect(return_button).to_have_attribute("href", file_url)


def test_code_from_request(
    live_server, page, context, release_request, output_checker_user
):
    more_button = page.locator("#file-button-more")
    code_button = page.locator("#file-code-button")

    # At a directory view, the code button is not displayed
    page.goto(live_server.url + f"/requests/view/{release_request.id}/group/")
    expect(more_button).not_to_be_visible()

    # file view displays code button
    file_url = f"/requests/view/{release_request.id}/group/foo.txt"
    page.goto(live_server.url + file_url)
    expect(more_button).to_be_visible()
    more_button.click()
    expect(code_button).to_be_visible()

    with context.expect_page() as new_page_info:
        code_button.click()  # Opens code in a new tab
        new_page = new_page_info.value

    return_button = new_page.locator("#return-button")
    # return url (the release_request file) is passed to code view as a query param,
    # and used as the href for the return button
    url_parts = urlsplit(new_page.url)
    assert url_parts.query == f"return_url={file_url}"
    expect(new_page.locator("body")).to_contain_text("project.yaml")
    expect(return_button).to_be_visible()
    expect(return_button).to_have_attribute("href", file_url)

    file_link = (
        new_page.locator("#tree")
        .get_by_role("link", name="project.yaml")
        .locator(".file:scope")
    )
    file_link.click()
    expect(return_button).to_be_visible()
    expect(return_button).to_have_attribute("href", file_url)
