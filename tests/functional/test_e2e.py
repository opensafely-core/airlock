import json
import re

import pytest
from playwright.sync_api import expect

from airlock.business_logic import RequestStatus, bll
from airlock.types import UrlPath
from tests import factories


admin_user = factories.create_user("admin", output_checker=True)


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
                        "workspaces": {},
                    },
                },
                "output_checker_1": {
                    "token": "output_checker_1",
                    "details": {
                        "username": "output_checker_1",
                        "fullname": "Output Checker 1",
                        "output_checker": True,
                        "staff": True,
                        "workspaces": {},
                    },
                },
                "researcher": {
                    "token": "researcher",
                    "details": {
                        "username": "researcher",
                        "fullname": "Researcher",
                        "output_checker": False,
                        "staff": False,
                        "workspaces": {
                            "test-workspace": {
                                "project_details": {
                                    "name": "Project 1",
                                    "ongoing": True,
                                },
                                "archived": False,
                            }
                        },
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

    expect(page).to_have_url(live_server.url + "/workspaces/")
    expect(page.locator("body")).to_contain_text(f"Logged in as: {username}")


def assert_tree_element_is_not_selected(page, locator):
    # We need to wait for a tiny amount to ensure the js that swaps the
    # selected classes around has had time to run before we assert that the
    # class isn't present. An arbitrary 20ms seems to be enough.
    page.wait_for_timeout(20)
    expect(locator).not_to_have_class(re.compile("selected"))


def assert_tree_element_is_selected(locator):
    # expect waits for the default timeout period, so we don't need
    # to rely on an explicit timeout
    expect(locator).to_have_class(re.compile("selected"))


def test_e2e_release_files(
    page, live_server, context, dev_users, release_files_stubber
):
    """
    Test full Airlock process to create, submit and release files

    1) Login as researcher
    - Go to Workspaces
    - Click on a Workspace
    - Expand the tree
    - View a file in a sub directory
    - Add output file to request, with new group name
      - Check add-to-file button now disabled
    - Click the "Current release request" button to go to the request
    - Open the filegroup tree
    - View contents of directory in tree
    - View contents of output file
    - Use the "Workspace Home" button to go back to the workspave
    - Add a supporting file to the same (existing) group
    - Go back to the request
    - View supporting file
    - Ensure selected classes are added properly when selecting
      output and supporting files
    - Submit request
    - View requests list again and check status
    - Log out

    2) Log in as output checker
    - View requests list
    - Click and view submitted request
    - View output file
    - Reject output file
    - Approve output file
    - Download output file
    - View supporting file
    - Reject output file
    - Approve output file
    - Complete review
    - Logout

    3) Log in as second output checker
    - Approve output file
    - Complete review
    - Release files
    - View requests list again and confirm released request is not shown
    """
    # set up a workspace file in a subdirectory
    workspace = factories.create_workspace("test-workspace")
    factories.write_workspace_file(
        workspace, "subdir/file.txt", "I am the file content"
    )
    factories.write_workspace_file(
        workspace, "subdir/file.foo", "I am an invalid file type"
    )
    factories.write_workspace_file(
        workspace,
        "subdir/supporting.txt",
        "I am the supporting file content",
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
    assert page.get_by_role("link", name="file.foo").all() == []

    # Click on the subdir and then the file link to view in the workspace
    # There will be more than one link to the folder/file in the page,
    # one in the explorer, and one in the main folder view
    # Click on the first link we find
    find_and_click(page.get_by_role("link", name="subdir").first)

    # Get and click on the invalid file
    find_and_click(page.get_by_role("link", name="file.foo").first)
    expect(page.locator("iframe")).to_have_attribute(
        "src", workspace.get_contents_url(UrlPath("subdir/file.foo"))
    )
    # The add file button is disabled for an invalid file
    add_file_button = page.locator("#add-file-modal-button-disabled")
    expect(add_file_button).to_be_disabled()

    # Get and click on the valid file
    find_and_click(page.get_by_role("link", name="file.txt").first)
    expect(page.locator("iframe")).to_have_attribute(
        "src", workspace.get_contents_url(UrlPath("subdir/file.txt"))
    )

    # Add file to request, with custom named group
    # Find the add file button and click on it to open the modal
    find_and_click(page.locator("button[value=add_files]"))
    # Fill in the form with a new group name
    page.locator("#id_new_filegroup").fill("my-new-group")

    # By default, the selected filetype is OUTPUT
    expect(page.locator("input[name=form-0-filetype][value=OUTPUT]")).to_be_checked()
    expect(
        page.locator("input[name=form-0-filetype][value=SUPPORTING]")
    ).not_to_be_checked()

    # Click the button to add the file to a release request
    find_and_click(page.get_by_role("form").locator("#add-file-button"))

    expect(page).to_have_url(
        f"{live_server.url}/workspaces/view/test-workspace/subdir/file.txt"
    )
    expect(page.locator("iframe")).to_have_attribute(
        "src", workspace.get_contents_url(UrlPath("subdir/file.txt"))
    )

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
    release_request = bll.get_release_request(request_id, admin_user)

    # Find the filegroup in the tree
    # Note: `get_by_role`` gets all links on the page; `locator` searches
    # for elements with the filegroup class; the `scope` pseudoselector
    # lets us search on the elements themselves as well as their children
    filegroup_link = page.get_by_role("link").locator(".filegroup:scope")
    expect(filegroup_link).to_be_visible()
    expect(filegroup_link).to_contain_text(re.compile("my-new-group", flags=re.I))

    # Locate the link by its name, because later when we're looking at
    # the request, there will be 2 files that match .locator(".file:scope")
    file_link = (
        page.locator("#tree")
        .get_by_role("link", name="file.txt")
        .locator(".file:scope")
    )

    # Click to open the filegroup tree
    filegroup_link.click()

    # Click on the output directory to ensure that renders correctly.
    subdir_link = page.get_by_role("link").locator(".directory:scope")
    find_and_click(subdir_link)
    expect(page.locator("#selected-contents")).to_contain_text("file.txt")

    # Tree opens fully expanded, so now the file (in its subdir) is visible
    find_and_click(file_link)
    expect(page.locator("iframe")).to_have_attribute(
        "src", release_request.get_contents_url(UrlPath("my-new-group/subdir/file.txt"))
    )

    # Go back to the Workspace view so we can add a supporting file
    find_and_click(page.locator("#workspace-home-button"))
    expect(page).to_have_url(live_server.url + "/workspaces/view/test-workspace/")

    # Expand the tree and click on the supporting file
    find_and_click(page.get_by_role("link", name="subdir").first)
    find_and_click(page.get_by_role("link", name="supporting.txt").first)

    # Add supporting file to request, choosing the group we created previously
    # Find the add file button and click on it to open the modal
    find_and_click(page.locator("button[value=add_files]"))

    page.locator("select[name=filegroup]").select_option("my-new-group")
    # Select supporting file
    page.locator("input[name=form-0-filetype][value=SUPPORTING]").check()

    # Click the button to add the file to a release request
    find_and_click(page.get_by_role("form").locator("#add-file-button"))

    # refresh release_request
    release_request = bll.get_release_request(request_id, admin_user)

    # Go back to the request
    find_and_click(page.locator("#current-request-button"))

    # Expand the tree
    filegroup_link.click()

    # Click on the output directory to ensure that renders correctly.
    subdir_link = page.get_by_role("link").locator(".directory:scope")

    assert_tree_element_is_not_selected(page, subdir_link)
    find_and_click(subdir_link)
    # subdir link is shown as selected
    assert_tree_element_is_selected(subdir_link)
    expect(page.locator("#selected-contents")).to_contain_text("file.txt")

    # Tree opens fully expanded, so now the file (in its subdir) is visible
    find_and_click(file_link)
    # File is selected, subdir is now unselected
    assert_tree_element_is_selected(file_link)
    assert_tree_element_is_not_selected(page, subdir_link)
    expect(page.locator("iframe")).to_have_attribute(
        "src", release_request.get_contents_url(UrlPath("my-new-group/subdir/file.txt"))
    )

    # Click on the supporting file link.
    supporting_file_link = page.get_by_role("link").locator(".supporting.file:scope")
    find_and_click(supporting_file_link)
    assert_tree_element_is_selected(supporting_file_link)
    assert_tree_element_is_not_selected(page, file_link)
    expect(page.locator("iframe")).to_have_attribute(
        "src",
        release_request.get_contents_url(UrlPath("my-new-group/subdir/supporting.txt")),
    )

    # Click back to the output file link and ensure the selected classes are correctly applied
    find_and_click(file_link)
    assert_tree_element_is_selected(file_link)
    assert_tree_element_is_not_selected(page, supporting_file_link)
    expect(page.locator("iframe")).to_have_attribute(
        "src", release_request.get_contents_url(UrlPath("my-new-group/subdir/file.txt"))
    )

    # Submit request
    submit_button = page.locator("button[data-modal=submitRequest]")
    find_and_click(submit_button)
    confirm_button = page.locator("#submit-for-review-button")
    find_and_click(confirm_button)
    expect(page.locator("body")).to_contain_text("SUBMITTED")
    # After the request is submitted, the submit button is no longer visible
    expect(submit_button).not_to_be_visible()

    # Before we log the researcher out and continue, let's just check
    # their requests
    find_and_click(page.get_by_test_id("nav-requests"))
    expect(page).to_have_url(live_server.url + "/requests/")

    request_link = page.locator("#authored-requests").get_by_role("link")
    expect(request_link).to_contain_text("SUBMITTED")
    # The literal request URL in the html includes the root path (".")
    expect(request_link).to_have_attribute("href", f"/requests/view/{request_id}/")

    # Log out with buttons
    find_and_click(page.get_by_test_id("nav-account"))
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

    complete_review_button = page.locator("#complete-review-button")
    # output checker hasn't reviewed files yet, complete review button visible but disabled
    expect(complete_review_button).to_be_visible()
    expect(complete_review_button).to_be_disabled()

    # Reuse the locators from the workspace view to click on filegroup and then file
    # Click to open the filegroup tree
    find_and_click(filegroup_link)
    find_and_click(file_link)
    expect(page.locator("iframe")).to_have_attribute(
        "src", release_request.get_contents_url(UrlPath("my-new-group/subdir/file.txt"))
    )

    # File is not yet approved, so the release button is disabled
    release_button = page.locator("#release-files-button")
    expect(release_button).to_be_disabled()

    # Reject the file
    expect(page.locator("#file-reject-button")).to_have_attribute(
        "aria-pressed", "false"
    )
    find_and_click(page.locator("#file-reject-button"))
    expect(page.locator("#file-reset-button")).to_have_attribute("aria-pressed", "true")

    # output checker has now reviewed all output files
    expect(complete_review_button).to_be_visible()
    expect(complete_review_button).to_be_enabled()

    # Change our minds & remove the review
    expect(page.locator("#file-reset-button")).to_have_attribute("aria-pressed", "true")
    find_and_click(page.locator("#file-reset-button"))
    expect(page.locator("#file-reject-button")).to_have_attribute(
        "aria-pressed", "false"
    )

    # complete review button disabled again
    expect(complete_review_button).to_be_visible()
    expect(complete_review_button).to_be_disabled()

    # Think some more & finally approve the file
    expect(page.locator("#file-approve-button")).to_have_attribute(
        "aria-pressed", "false"
    )
    find_and_click(page.locator("#file-approve-button"))
    expect(page.locator("#file-reset-button")).to_have_attribute("aria-pressed", "true")

    # File is only approved once, so the release files button is still disabled
    expect(release_button).to_be_disabled()

    find_and_click(page.locator("#file-button-more"))

    # Download the file
    with page.expect_download() as download_info:
        find_and_click(page.locator("#download-button"))
    download = download_info.value
    assert download.suggested_filename == "file.txt"

    # Look at a supporting file & verify it can't be approved
    supporting_file_link = page.get_by_role("link", name="supporting.txt").locator(
        ".file:scope"
    )
    find_and_click(supporting_file_link)
    expect(page.locator("iframe")).to_have_attribute(
        "src",
        release_request.get_contents_url(UrlPath("my-new-group/subdir/supporting.txt")),
    )
    expect(page.locator("#file-approve-button")).not_to_be_visible()
    expect(page.locator("#file-reject-button")).not_to_be_visible()
    expect(page.locator("#file-reset-button")).not_to_be_visible()

    # complete review for this output-checker
    find_and_click(page.locator("#complete-review-button"))
    # Logout (by clearing cookies) and log in as second output-checker to do second approval
    # and release
    context.clear_cookies()
    login_as(live_server, page, "output_checker_1")
    # Approve the file
    page.goto(live_server.url + release_request.get_url("my-new-group/subdir/file.txt"))
    find_and_click(page.locator("#file-approve-button"))

    # The file has 2 approvals, but the release files button is not yet enabled until this
    # reviewer completes their review
    expect(release_button).to_be_disabled()

    # complete review
    find_and_click(page.locator("#complete-review-button"))
    expect(release_button).to_be_enabled()

    # Mock the responses from job-server
    release_request = bll.get_release_request(request_id, admin_user)
    release_files_stubber(release_request)

    # Release the files
    find_and_click(release_button)
    expect(page.locator("body")).to_contain_text(
        "Files have been released to jobs.opensafely.org"
    )

    # Requests view does not show released request
    find_and_click(page.get_by_test_id("nav-requests"))
    expect(page.locator("body")).not_to_contain_text("test-workspace by researcher")


def test_e2e_update_file(page, live_server, dev_users):
    """
    Test researcher updates a modified file in a returned request
    """
    # set up a returned file & request
    author = factories.create_user("researcher", ["test-workspace"], False)
    path = "subdir/file.txt"

    factories.create_request_at_status(
        "test-workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[factories.request_file(path=path, group="default", rejected=True)],
    )

    # change the file on disk
    workspace = bll.get_workspace("test-workspace", author)
    factories.write_workspace_file(workspace, path, contents="changed")

    # Log in as researcher
    login_as(live_server, page, "researcher")

    # Click on to workspaces link
    find_and_click(page.get_by_test_id("nav-workspaces"))
    expect(page.locator("body")).to_contain_text("Workspaces for researcher")

    # Click on the workspace
    find_and_click(page.locator("#workspaces").get_by_role("link"))
    expect(page.locator("body")).to_contain_text("subdir")
    # subdirectories start off collapsed; the file links are not present
    assert page.get_by_role("link", name="file.txt").all() == []
    assert page.get_by_role("link", name="file.foo").all() == []

    # Click on the subdir and then the file link to view in the workspace
    # There will be more than one link to the folder/file in the page,
    # one in the explorer, and one in the main folder view
    # Click on the first link we find
    find_and_click(page.get_by_role("link", name="subdir").first)

    # click on the multi-select checkbox
    find_and_click(page.locator('input[name="selected"]'))

    # Update file in request
    # Find the add file button and click on it to open the modal
    find_and_click(page.locator("button[value=add_files]"))

    # By default, the selected filetype is OUTPUT
    expect(page.locator("input[name=form-0-filetype][value=OUTPUT]")).to_be_checked()
    expect(
        page.locator("input[name=form-0-filetype][value=SUPPORTING]")
    ).not_to_be_checked()

    # Click the button to add the file to a release request
    find_and_click(page.get_by_role("form").locator("#add-file-button"))

    expect(page.locator("body")).to_contain_text(
        "Output file has been updated in request"
    )


def test_e2e_reject_request(page, live_server, dev_users):
    """
    Test output-checker rejects a release request
    """
    # set up a reviewed request
    release_request = factories.create_request_at_status(
        "test-workspace",
        author=factories.create_user("author", workspaces=["test-workspace"]),
        status=RequestStatus.REVIEWED,
        files=[factories.request_file(rejected=True)],
    )

    # Log in as output checker
    login_as(live_server, page, "output_checker")

    # View submitted request
    page.goto(live_server.url + release_request.get_url())

    # Reject request
    find_and_click(page.locator("#reject-request-button"))
    # Page contains rejected message text
    expect(page.locator("body")).to_contain_text("Request has been rejected")
    # Requests view does not show rejected request
    find_and_click(page.get_by_test_id("nav-requests"))
    expect(page.locator("body")).not_to_contain_text("test-workspace by author")


def test_e2e_withdraw_request(page, live_server, dev_users):
    """
    Request author withdraws their request
    """
    # set up a submitted request
    user = factories.create_user("researcher", ["test-workspace"], False)
    release_request = factories.create_request_at_status(
        "test-workspace",
        author=user,
        status=RequestStatus.RETURNED,
        files=[factories.request_file(rejected=True)],
    )

    # Log in as a researcher
    login_as(live_server, page, user.username)

    # View submitted request
    page.goto(live_server.url + release_request.get_url())

    find_and_click(page.locator("[data-modal=withdrawRequest]"))
    find_and_click(page.locator("#withdraw-request-confirm"))

    expect(page.locator("body")).to_contain_text("Request has been withdrawn")
