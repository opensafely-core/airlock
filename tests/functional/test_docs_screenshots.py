import re

from django.conf import settings
from playwright.sync_api import expect

from airlock.enums import RequestFileType, RequestStatus
from airlock.types import UrlPath
from tests import factories

from .conftest import login_as_user
from .utils import screenshot_element_with_padding


def get_user_data():
    author_username = "researcher"
    author_workspaces = ["my-workspace"]
    user_dicts = {
        "author": dict(
            username=author_username, workspaces=author_workspaces, output_checker=False
        ),
        "checker1": dict(username="checker1", workspaces=[], output_checker=True),
        "checker2": dict(username="checker2", workspaces=[], output_checker=True),
    }

    author = factories.create_user(
        username=author_username,
        workspaces=author_workspaces,
        output_checker=False,
    )

    return author, user_dicts


def test_screenshot_from_creation_to_release(
    page, live_server, context, release_files_stubber
):
    author, user_dicts = get_user_data()

    # set up a workspace with files in a subdirectory
    workspace = factories.create_workspace("my-workspace")

    factories.write_workspace_file(
        workspace,
        "outputs/file1.csv",
        "Age Band,Mean\n0-20,10\n21-40,20\n41-60,30\n60+,40",
    )
    factories.write_workspace_file(
        workspace,
        "outputs/file2.csv",
        "Variable 1,Variable 2\nA,1\nB,2\nC,3\nD,4",
    )
    factories.write_workspace_file(
        workspace,
        "outputs/summary.txt",
        "A summary of the data for output.",
    )

    factories.write_workspace_file(
        workspace,
        "outputs/supporting.txt",
        "The supporting content",
    )

    # Log in as a researcher
    login_as_user(live_server, context, user_dicts["author"])
    page.goto(live_server.url)

    # workspaces index page
    page.screenshot(path=settings.SCREENSHOT_DIR / "workspaces_index.png")

    # workspace view page
    page.goto(live_server.url + workspace.get_url())
    page.screenshot(path=settings.SCREENSHOT_DIR / "workspace_view.png")

    # Directory view
    page.goto(live_server.url + workspace.get_url(UrlPath("outputs")))
    # let the data table load
    expect(page.locator("#customTable.datatable-table")).to_be_visible()
    page.screenshot(path=settings.SCREENSHOT_DIR / "workspace_directory_view.png")
    # Content only in directory view
    content = page.locator("#selected-contents")
    content.screenshot(path=settings.SCREENSHOT_DIR / "workspace_directory_content.png")

    # File view page
    page.goto(live_server.url + workspace.get_url(UrlPath("outputs/file1.csv")))
    # wait briefly (100ms) for the table to load before screenshotting
    page.wait_for_timeout(100)
    page.screenshot(path=settings.SCREENSHOT_DIR / "workspace_file_view.png")

    # More dropdown
    more_locator = page.locator("#file-button-more")
    more_locator.click()
    # Screenshot both the full page and the element; these will be used in different
    # places in the docs
    page.screenshot(path=settings.SCREENSHOT_DIR / "more_dropdown.png")
    screenshot_element_with_padding(
        page,
        more_locator,
        "more_dropdown_el.png",
        extra={"x": -180, "width": 180, "height": 120},
    )

    # Add file button
    add_file_button = page.locator("button[value=add_files]")
    screenshot_element_with_padding(page, add_file_button, "add_file_button.png")

    # Click to add file and fill in the form with a new group name
    add_file_button.click()
    page.locator("#id_new_filegroup").fill("my-group")
    form_element = page.get_by_role("form")
    screenshot_element_with_padding(page, form_element, "add_file_modal.png")

    # create the release request outside of the browser so we can use its methods
    # and avoid clicking through all the files to add them
    release_request = factories.create_request_at_status(
        workspace,
        RequestStatus.PENDING,
        author,
        files=[
            factories.request_file(group="my-group", path="outputs/file1.csv"),
            factories.request_file(group="my-group", path="outputs/file2.csv"),
            factories.request_file(group="my-group", path="outputs/summary.txt"),
            factories.request_file(
                group="my-group",
                path="outputs/supporting.txt",
                filetype=RequestFileType.SUPPORTING,
            ),
        ],
    )

    page.goto(live_server.url + release_request.get_url(UrlPath("my-group")))
    # screenshot the tree
    page.locator("#tree").screenshot(path=settings.SCREENSHOT_DIR / "request_tree.png")

    # Add context & controls to the filegroup
    page.screenshot(path=settings.SCREENSHOT_DIR / "context_and_controls.png")

    context_input = page.locator("#id_context")
    # context_input.click()
    context_input.fill("These files describe data by age band.")

    controls_input = page.locator("#id_controls")
    # controls_input.click()
    controls_input.fill("Small numbers have been suppressed.")
    # Save
    page.locator("#edit-group-button").click()

    # Submit request
    page.goto(live_server.url + release_request.get_url())
    page.locator("button[data-modal=submitRequest]").click()
    page.screenshot(path=settings.SCREENSHOT_DIR / "submit_request.png")
    page.locator("#submit-for-review-button").click()
    page.screenshot(path=settings.SCREENSHOT_DIR / "submitted_request.png")

    def do_review(screenshot=True):
        # Approve file1.csv
        page.goto(
            live_server.url
            + release_request.get_url(UrlPath("my-group/outputs/file1.csv"))
        )
        page.locator("#file-approve-button").click()
        # Request changes on file2.csv
        page.goto(
            live_server.url
            + release_request.get_url(UrlPath("my-group/outputs/file2.csv"))
        )
        if screenshot:
            page.screenshot(path=settings.SCREENSHOT_DIR / "file_review.png")
        page.locator("#file-request-changes-button").click()
        # Request changes on summary.txt
        page.goto(
            live_server.url
            + release_request.get_url(UrlPath("my-group/outputs/summary.txt"))
        )
        page.locator("#file-request-changes-button").click()
        # Submit independent review
        page.goto(live_server.url + release_request.get_url())
        page.locator("#submit-review-button").click()

    # Review as each output checker
    login_as_user(live_server, context, user_dicts["checker1"])
    # Requests index
    page.goto(f"{live_server.url}/requests")
    page.screenshot(path=settings.SCREENSHOT_DIR / "requests_index.png")
    # Request view
    page.goto(live_server.url + release_request.get_url())
    page.screenshot(path=settings.SCREENSHOT_DIR / "request_overview.png")
    do_review()

    login_as_user(live_server, context, user_dicts["checker2"])
    do_review(screenshot=False)

    # Add public comments
    page.goto(live_server.url + release_request.get_url(UrlPath("my-group")))
    comment_button = page.get_by_role("button", name=re.compile(r"^Comment"))
    comment_input = page.locator("#id_comment")
    public_visibility_radio = page.locator("input[name=visibility][value=PUBLIC]")
    public_visibility_radio.check()
    comment_input.fill("Please update file2.csv with more descriptive variable names")
    comment_button.click()
    public_visibility_radio.check()
    comment_input.fill("Is summmary.txt required for output?")
    comment_button.click()

    # Return to researcher
    page.goto(live_server.url + release_request.get_url())
    page.locator("#return-request-button").click()

    # Responding to returned request
    login_as_user(live_server, context, user_dicts["author"])

    # Screenshot tree for file review status
    screenshot_element_with_padding(page, page.locator("#tree"), "returned_tree.png")

    # View comments
    page.goto(live_server.url + release_request.get_url(UrlPath("my-group")))
    page.get_by_test_id("c3").screenshot(
        path=settings.SCREENSHOT_DIR / "returned_request_comments.png"
    )

    # Withdraw a file after request returned
    page.goto(
        live_server.url
        + release_request.get_url(UrlPath("my-group/outputs/summary.txt"))
    )
    page.locator("#withdraw-file-button").click()
    page.screenshot(path=settings.SCREENSHOT_DIR / "withdrawn_file.png")

    # Update a file after request returned
    # change the file on disk
    factories.write_workspace_file(
        workspace,
        "outputs/file2.csv",
        contents="Category,Result\nA,1\nB,2\nC,3\nD,4",
    )
    # multiselect view
    page.goto(live_server.url + workspace.get_url(UrlPath("outputs")))
    page.screenshot(path=settings.SCREENSHOT_DIR / "multiselect_update.png")
    # file view
    page.goto(live_server.url + workspace.get_url(UrlPath("outputs/file2.csv")))
    # screenshot the tree icon and the page
    page.get_by_role("link", name="file2.csv").screenshot(
        path=settings.SCREENSHOT_DIR / "changed_tree_file.png"
    )
    page.screenshot(path=settings.SCREENSHOT_DIR / "file_update.png")
    page.locator("button[value=update_files]").click()
    page.screenshot(path=settings.SCREENSHOT_DIR / "file_update_modal.png")
    # Click the button to update the file in the release request
    page.get_by_role("form").locator("#update-file-button").click()

    # resubmit
    page.goto(live_server.url + release_request.get_url())
    page.locator("#submit-for-review-button").click()

    # checker 1 and 2 review, approve and release
    def do_review_and_approve(username):
        login_as_user(live_server, context, user_dicts[username])
        # file1.csv is already approved, summary.txt has been withdrawn.
        # Approve file2.csv
        page.goto(
            live_server.url
            + release_request.get_url(UrlPath("my-group/outputs/file2.csv"))
        )
        page.locator("#file-approve-button").click()
        # Submit independent review
        page.goto(live_server.url + release_request.get_url())
        page.locator("#submit-review-button").click()

    for username in ["checker1", "checker2"]:
        do_review_and_approve(username)

    # release
    # Mock the responses from job-server
    release_request = factories.refresh_release_request(release_request)
    release_files_stubber(release_request)
    page.goto(live_server.url + release_request.get_url())
    page.locator("#release-files-button").click()
    # Make sure we've waited for the files to be released
    expect(page.locator("body")).to_contain_text(
        "Files have been released to jobs.opensafely.org"
    )
    page.screenshot(path=settings.SCREENSHOT_DIR / "files_released.png")


def test_screenshot_withdraw_request(page, context, live_server):
    author, user_dicts = get_user_data()

    release_request = factories.create_request_at_status(
        "my-workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[factories.request_file(changes_requested=True)],
    )

    # Log in as author
    login_as_user(live_server, context, user_dicts["author"])

    # View submitted request
    page.goto(live_server.url + release_request.get_url())
    page.screenshot(path=settings.SCREENSHOT_DIR / "withdraw_request.png")

    page.locator("[data-modal=withdrawRequest]").click()
    page.screenshot(path=settings.SCREENSHOT_DIR / "withdraw_request_modal.png")

    page.locator("#withdraw-request-confirm").click()
