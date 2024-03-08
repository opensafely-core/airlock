import json
import re

import pytest
from playwright.sync_api import expect

from airlock.business_logic import Status, bll
from tests import factories


@pytest.fixture
def dev_users(tmp_path, settings):
    settings.AIRLOCK_DEV_USERS_FILE = tmp_path / "dev_users.json"
    settings.AIRLOCK_DEV_USERS_FILE.write_text(
        json.dumps(
            {
                "output_checker": {
                    "token": "output_checker",
                    "details": {
                        "username": "output_checker",
                        "fullname": "Output Checker",
                        "output_checker": True,
                        "staff": True,
                        "workspaces": [],
                    },
                },
                "researcher": {
                    "token": "researcher",
                    "details": {
                        "username": "researcher",
                        "fullname": "Researcher",
                        "output_checker": False,
                        "staff": False,
                        "workspaces": ["test-workspace"],
                    },
                },
            }
        )
    )


def find_and_click(locator):
    """
    Helper function to find a locator element and click on it.
    Asserts that the element is visible before trying to click.
    This avoids playwright hanging on clicking and unavailable
    element, which happens if we just chain the locator and click
    methods.
    """
    expect(locator).to_be_visible()
    locator.click()


def login_as(live_server, page, username):
    page.goto(live_server.url + "/login/?next=/")
    page.locator("#id_user").fill(username)
    page.locator("#id_token").fill(username)
    find_and_click(page.locator("button[type=submit]"))

    expect(page).to_have_url(live_server.url + "/")
    expect(page.locator("body")).to_contain_text(f"Logged in as: {username}")


def test_e2e_release_files(page, live_server, dev_users, release_files_stubber):
    """
    Test full Airlock process to create, submit and release files
    """
    # set up a workspace file in a subdirectory
    factories.write_workspace_file(
        "test-workspace", "subdir/file.txt", "I am the file content"
    )

    # Log in as a researcher
    login_as(live_server, page, "researcher")

    # Click on to workspaces link
    find_and_click(page.get_by_test_id("nav-workspaces"))
    expect(page.locator("body")).to_contain_text("Workspaces for researcher")

    # Click on the workspace
    find_and_click(page.locator("#workspaces").get_by_role("link"))
    expect(page.locator("body")).to_contain_text("subdir")
    # subdirectories start off collapsed; the file links are not present
    assert page.get_by_role("link", name="file.txt").all() == []

    # Click on the subdir and then the file link to view in the workspace
    # There will be more than one link to the folder/file in the page,
    # one in the explorer, and one in the main folder view
    # Click on the first link we find
    find_and_click(page.get_by_role("link", name="subdir").first)
    find_and_click(page.get_by_role("link", name="file.txt").first)
    expect(page.locator("body")).to_contain_text("I am the file content")

    # Add file to request, with custom named group
    # Find the add file button and click on it to open the modal
    find_and_click(page.locator("[data-modal=addRequestFile]"))
    # Fill in the form with a new group name
    page.locator("#id_new_filegroup").fill("my-new-group")

    # Click the button to add the file to a release request
    find_and_click(page.get_by_role("form").locator("#add-file-button"))

    expect(page).to_have_url(
        f"{live_server.url}/workspaces/view/test-workspace/subdir/file.txt"
    )
    expect(page.locator("body")).to_contain_text("I am the file content")

    # The "Add file to request" button is disabled
    add_file_button = page.locator("#add-file-modal-button-disabled")
    expect(add_file_button).to_be_disabled()

    # We now have a "Current release request" button
    find_and_click(page.locator("#current-request-button"))
    # Clicking it takes us to the release
    url_regex = re.compile(rf"{live_server.url}\/requests\/view\/([A-Z0-9].+)/")
    expect(page).to_have_url(url_regex)
    # get the request ID for the just-created request, for later reference
    request_id = url_regex.match(page.url).groups()[0]

    # Find the filegroup in the tree
    # Note: `get_by_role`` gets all links on the page; `locator` searches
    # for elements with the filegroup class; the `scope` pseudoselector
    # lets us search on the elements themselves as well as their children
    filegroup_link = page.get_by_role("link").locator(".filegroup:scope")
    expect(filegroup_link).to_be_visible()
    expect(filegroup_link).to_contain_text(re.compile("my-new-group", flags=re.I))

    # In the initial request view, the tree is collapsed
    file_link = page.get_by_role("link").locator(".file:scope")
    assert file_link.all() == []

    # Click to open the filegroup tree
    filegroup_link.click()

    # Click on the directory to ensure that renders correctly.
    subdir_link = page.get_by_role("link").locator(".directory:scope")
    find_and_click(subdir_link)
    expect(page.locator("#selected-contents")).to_contain_text("file.txt")

    # Tree opens fully expanded, so now the file (in its subdir) is visible
    find_and_click(file_link)
    expect(page.locator("body")).to_contain_text("I am the file content")

    # Submit request
    submit_button = page.locator("#submit-for-review-button")
    find_and_click(submit_button)
    expect(page.locator("body")).to_contain_text("SUBMITTED")
    # After the request is submitted, the submit button is no longer visible
    expect(submit_button).not_to_be_visible()

    # Ensure researcher can go back to the Workspace view
    find_and_click(page.locator("#workspace-home-button"))
    expect(page).to_have_url(live_server.url + "/workspaces/view/test-workspace/")

    # Before we log the researcher out and continue, let's just check
    # their requests
    find_and_click(page.get_by_test_id("nav-requests"))
    expect(page).to_have_url(live_server.url + "/requests/")

    request_link = page.locator("#authored-requests").get_by_role("link")
    expect(request_link).to_contain_text("SUBMITTED")
    # The literal request URL in the html includes the root path (".")
    expect(request_link).to_have_attribute("href", f"/requests/view/{request_id}/.")

    # Log out
    find_and_click(page.get_by_test_id("nav-logout"))

    # Login button is visible now
    login_button = page.get_by_test_id("nav-login")
    expect(login_button).to_be_visible()

    # Log in as output checker
    login_as(live_server, page, "output_checker")

    # View requests
    find_and_click(page.get_by_test_id("nav-requests"))

    # View submitted request (in the output-checker's outstanding requests for review)
    find_and_click(page.locator("#outstanding-requests").get_by_role("link"))
    expect(page.locator("body")).to_contain_text(request_id)

    # Reuse the locators from the workspace view to click on filegroup and then file
    # Click to open the filegroup tree
    find_and_click(filegroup_link)
    find_and_click(file_link)
    expect(page.locator("body")).to_contain_text("I am the file content")

    # Download the file
    with page.expect_download() as download_info:
        find_and_click(page.locator("#download-button"))
    download = download_info.value
    assert download.suggested_filename == "file.txt"

    # Mock the responses from job-server
    release_request = bll.get_release_request(request_id)
    release_files_stubber(release_request)

    # Release the files
    find_and_click(page.locator("#release-files-button"))
    expect(page.locator("body")).to_contain_text(
        "Files have been released to jobs.opensafely.org"
    )

    # Requests view does not show released request
    find_and_click(page.get_by_test_id("nav-requests"))
    expect(page.locator("body")).not_to_contain_text("test-workspace by researcher")


def test_e2e_reject_request(page, live_server, dev_users):
    """
    Test output-checker rejects a release request
    """
    # set up a submitted file
    factories.write_workspace_file("test-workspace", "file.txt")
    release_request = factories.create_release_request(
        "test-workspace",
        status=Status.SUBMITTED,
    )
    factories.create_filegroup(
        release_request, group_name="default", filepaths=["file.txt"]
    )

    # Log in as output checker
    login_as(live_server, page, "output_checker")

    # View requests
    find_and_click(page.get_by_test_id("nav-requests"))

    # View submitted request
    find_and_click(page.get_by_role("link", name="test-workspace by testuser"))

    # Reject request
    find_and_click(page.locator("#reject-request-button"))
    # Page contains rejected message text
    expect(page.locator("body")).to_contain_text("Request has been rejected")
    # Requests view does not show rejected request
    find_and_click(page.get_by_test_id("nav-requests"))
    expect(page.locator("body")).not_to_contain_text("test-workspace by testuser")
