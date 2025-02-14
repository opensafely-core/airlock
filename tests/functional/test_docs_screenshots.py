import os
import re

import pytest
from django.conf import settings
from playwright.sync_api import expect

from airlock.business_logic import bll
from airlock.enums import RequestFileType, RequestStatus
from airlock.types import UrlPath
from tests import factories

from .conftest import login_as_user
from .utils import screenshot_element_with_padding


@pytest.fixture(autouse=True)
def hide_nav_item_switch_user(monkeypatch):
    def mockreturn(request):
        return {"dev_users": False}

    monkeypatch.setattr("airlock.nav.dev_users", mockreturn)


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

    author = factories.create_airlock_user(
        username=author_username,
        workspaces=author_workspaces,
        output_checker=False,
    )

    return author, user_dicts


def move_mouse_from(page):
    """
    Move the mouse away from any elements it might be hovering over so that we
    don't screenshot tooltips.
    """
    # Note there's a locator.dispatch_event('mouseleave') method, which does move the
    # pointer off the button, but it doesn't get rid of the tooltip until you move the
    # mouse again. Moving it to 0, 0 ensures that it doesn't accidentally land on some
    # other element that has a tooltip
    page.mouse.move(0, 0)


@pytest.mark.skipif(
    os.getenv("RUN_SCREENSHOT_TESTS") is None,
    reason="screenshot tests skipped; set RUN_SCREENSHOT_TESTS env variable",
)
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
    expect(page.locator(".datatable-table")).to_be_visible()
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
    # Close the more dropdown and unfocus
    page.keyboard.press("Escape")
    more_locator.blur()

    # Add file button
    add_file_button = page.locator("button[value=add_files]")
    content.screenshot(path=settings.SCREENSHOT_DIR / "add_file_button.png")
    screenshot_element_with_padding(
        page, content, "add_file_button.png", crop={"height": 0.25}
    )

    # Click to add file and fill in the form with a new group name
    add_file_button.click()
    page.locator("#id_new_filegroup").fill("my-group")
    form_element = page.get_by_role("form")
    screenshot_element_with_padding(
        page, form_element, "add_file_modal.png", crop={"height": 0.5}
    )

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

        if screenshot:
            # Screenshot the request file page before voting
            page.screenshot(path=settings.SCREENSHOT_DIR / "file_review.png")

        page.locator("#file-approve-button").click()

        if screenshot:
            # More dropdown (includes download file option)
            more_locator = page.locator("#file-button-more")
            more_locator.click()
            screenshot_element_with_padding(
                page,
                more_locator,
                "more_dropdown_el_request_file.png",
                extra={"x": -180, "width": 180, "height": 160},
            )
            # Close the more dropdown and unfocus
            page.keyboard.press("Escape")
            more_locator.blur()

            # Click to open the context modal
            page.locator("button[data-modal=group-context]").click()
            page.screenshot(path=settings.SCREENSHOT_DIR / "context_modal.png")
            page.get_by_role("button", name="Close").click()

        if screenshot:
            # Screenshot the request file page after voting
            page.screenshot(path=settings.SCREENSHOT_DIR / "file_approved.png")

        # Request changes on file2.csv
        page.goto(
            live_server.url
            + release_request.get_url(UrlPath("my-group/outputs/file2.csv"))
        )

        page.locator("#file-request-changes-button").click()
        # Request changes on summary.txt
        page.goto(
            live_server.url
            + release_request.get_url(UrlPath("my-group/outputs/summary.txt"))
        )
        page.locator("#file-request-changes-button").click()

        # return to request homepage
        page.goto(live_server.url + release_request.get_url())

        if screenshot:
            # screenshot the tree after voting
            page.locator("#tree").screenshot(
                path=settings.SCREENSHOT_DIR / "request_tree_post_voting.png"
            )

        # Submit independent review
        if screenshot:
            page.screenshot(path=settings.SCREENSHOT_DIR / "submit_review.png")

        page.locator("#submit-review-button").click()

        if screenshot:
            # move mouse off button to avoid screenshotting tooltips
            move_mouse_from(page)
            page.screenshot(path=settings.SCREENSHOT_DIR / "submitted_review.png")

    # Login as output checker and visit pages
    login_as_user(live_server, context, user_dicts["checker1"])
    # Reviews index page
    page.goto(f"{live_server.url}/requests/output_checker")
    page.screenshot(path=settings.SCREENSHOT_DIR / "reviews_index.png")
    # Request view
    page.goto(live_server.url + release_request.get_url())
    page.screenshot(path=settings.SCREENSHOT_DIR / "request_overview.png")
    # File group
    page.goto(live_server.url + release_request.get_url(UrlPath("my-group")))
    page.screenshot(path=settings.SCREENSHOT_DIR / "file_group.png")

    # Review as each output checker
    do_review()
    login_as_user(live_server, context, user_dicts["checker2"])
    do_review(screenshot=False)

    # Add private comment
    page.goto(live_server.url + release_request.get_url(UrlPath("my-group")))
    comment_button = page.get_by_role("button", name=re.compile(r"^Comment"))
    comment_input = page.locator("#id_comment")

    comment_input.fill("Please update file2.csv with more descriptive variable names")

    comments = page.locator("#comments")

    comments.screenshot(path=settings.SCREENSHOT_DIR / "reviewed_request_comments.png")
    comment_button.click()
    # Add public comment
    public_visibility_radio = page.locator("input[name=visibility][value=PUBLIC]")
    public_visibility_radio.check()
    comment_input.fill("Is summmary.txt required for output?")
    comment_button.click()

    comments.screenshot(path=settings.SCREENSHOT_DIR / "reviewed_request_comments.png")

    # Return to researcher
    page.goto(live_server.url + release_request.get_url())
    page.locator("button[data-modal=returnRequest]").click()
    page.locator("#return-request-button").click()

    # Responding to returned request
    login_as_user(live_server, context, user_dicts["author"])

    # Screenshot tree for file review status
    screenshot_element_with_padding(page, page.locator("#tree"), "returned_tree.png")

    # View comments
    page.goto(live_server.url + release_request.get_url(UrlPath("my-group")))
    comments.screenshot(path=settings.SCREENSHOT_DIR / "returned_request_comments.png")

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
    # screenshot the tree icon and the page
    page.locator("#tree").get_by_role("link", name="file2.csv").screenshot(
        path=settings.SCREENSHOT_DIR / "changed_tree_file.png"
    )
    # file view
    page.goto(live_server.url + workspace.get_url(UrlPath("outputs/file2.csv")))
    page.screenshot(path=settings.SCREENSHOT_DIR / "file_update.png")
    page.locator("button[value=update_files]").click()
    page.screenshot(path=settings.SCREENSHOT_DIR / "file_update_modal.png")
    # Click the button to update the file in the release request
    page.get_by_role("form").locator("#update-file-button").click()

    # resubmit
    page.goto(live_server.url + release_request.get_url())
    page.locator("#resubmit-for-review-button").click()

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

    # Move the mouse off the button so we don't screenshot the tooltip
    move_mouse_from(page)
    page.screenshot(path=settings.SCREENSHOT_DIR / "ready_to_release.png")

    # Approve for release
    page.locator("#release-files-button").click()

    expect(page.locator("body")).to_contain_text(
        "Files have been released and will be uploaded to jobs.opensafely.org"
    )
    # Move the mouse off the button so we don't screenshot the tooltip
    move_mouse_from(page)
    page.screenshot(
        path=settings.SCREENSHOT_DIR / "request_approved_upload_in_progress.png"
    )

    # Progress the release request to all uploads failed
    for relpath in release_request.output_files():
        for _ in range(settings.UPLOAD_MAX_ATTEMPTS):
            bll.register_file_upload_attempt(release_request, relpath)

    page.goto(live_server.url + release_request.get_url())
    page.screenshot(path=settings.SCREENSHOT_DIR / "request_approved_upload_failed.png")

    # Progress the release request to all uploads complete and released
    release_request = factories.refresh_release_request(release_request)
    checker_user = factories.create_user(**user_dicts["checker1"])
    for relpath in release_request.output_files():
        for _ in range(settings.UPLOAD_MAX_ATTEMPTS):
            bll.register_file_upload(release_request, relpath, checker_user)
    bll.set_status(release_request, RequestStatus.RELEASED, checker_user)
    page.goto(live_server.url + release_request.get_url())
    page.screenshot(path=settings.SCREENSHOT_DIR / "request_released.png")


@pytest.mark.skipif(
    os.getenv("RUN_SCREENSHOT_TESTS") is None,
    reason="screenshot tests skipped; set RUN_SCREENSHOT_TESTS env variable",
)
def test_screenshot_comments(page, context, live_server):
    author, user_dicts = get_user_data()

    release_request = factories.create_request_at_status(
        "my-workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[factories.request_file(approved=True)],
    )
    # Log in as author
    login_as_user(live_server, context, user_dicts["author"])

    # Go to group
    page.goto(live_server.url + release_request.get_url(UrlPath("group")))

    # Locators for comment elements
    comment_button = page.get_by_role("button", name=re.compile(r"^Comment"))
    comment_input = page.locator("#id_comment")
    add_comment_element = page.locator('label:has-text("Add Comment")').locator("..")

    # Add markdown comments
    def add_comment(comment_text, filename):
        # Fill the comment input element with the markdown text
        comment_input.fill(comment_text)
        # Screenshot before submitting
        add_comment_element.screenshot(
            path=settings.SCREENSHOT_DIR / f"{filename}_1.png"
        )
        comment_button.click()

        # get the parent li element for the last submitted comment
        # we can't just use the comment_text, because it will be different in the rendered comment
        all_comments = page.locator("li.group.comment_public")
        last_comment = all_comments.nth(len(all_comments.all()) - 1)
        last_comment.scroll_into_view_if_needed()
        # The rendered comment is a bit wide for displaying side by side with the pre-submitted
        # version, so crop it to 50% of its actual width
        screenshot_element_with_padding(
            page,
            last_comment,
            f"{filename}_2.png",
            extra={"y": 10},
            crop={"width": 0.5},
        )

    add_comment("This is **a bold comment**", "markdown_comment_bold")
    add_comment("Example quote:\n>This is airlock!", "markdown_comment_blockquote")
    add_comment(
        "This is an [example link](https://www.example.com/my%20great%20page)",
        "markdown_comment_anchor",
    )
    add_comment("`code example`", "markdown_comment_code")
    add_comment("This is _italicised_ or _emphasised_ text", "markdown_comment_italics")
    add_comment(
        "1. first text\n1. second text\n1. third text\n8. fourth text",
        "markdown_comment_ordered_list",
    )
    add_comment(
        "- First item\n- Second item\n- Third item\n- Fourth item",
        "markdown_comment_unordered_list",
    )


@pytest.mark.skipif(
    os.getenv("RUN_SCREENSHOT_TESTS") is None,
    reason="screenshot tests skipped; set RUN_SCREENSHOT_TESTS env variable",
)
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


@pytest.mark.skipif(
    os.getenv("RUN_SCREENSHOT_TESTS") is None,
    reason="screenshot tests skipped; set RUN_SCREENSHOT_TESTS env variable",
)
def test_screenshot_request_partially_reviewed_icons(page, context, live_server):
    author, user_dicts = get_user_data()
    checker1 = factories.create_airlock_user(
        username="checker1",
        workspaces=[],
        output_checker=True,
    )
    workspace = factories.create_workspace("my-workspace")
    release_request = factories.create_request_at_status(
        workspace,
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(
                path="approved.txt",
                contents="approved",
                approved=True,
                checkers=[checker1],
            ),
            factories.request_file(
                path="changes_requested.txt",
                contents="changes",
                changes_requested=True,
                checkers=[checker1],
            ),
            factories.request_file(path="pending_review.txt", contents="pending"),
            factories.request_file(
                path="withdrawn.txt",
                contents="withdrawn",
                filetype=RequestFileType.WITHDRAWN,
            ),
            factories.request_file(
                path="supporting.txt",
                contents="supporting",
                filetype=RequestFileType.SUPPORTING,
            ),
        ],
    )

    login_as_user(live_server, context, user_dicts["checker1"])

    # View request
    page.goto(live_server.url + release_request.get_url())

    # screenshot the tree
    page.locator("#tree").screenshot(
        path=settings.SCREENSHOT_DIR / "request_independent_review_file_icons.png"
    )

    login_as_user(live_server, context, user_dicts["author"])

    # View request
    page.goto(live_server.url + release_request.get_url())

    # screenshot the tree
    page.locator("#tree").screenshot(
        path=settings.SCREENSHOT_DIR
        / "request_independent_review_researcher_file_icons.png"
    )


@pytest.mark.skipif(
    os.getenv("RUN_SCREENSHOT_TESTS") is None,
    reason="screenshot tests skipped; set RUN_SCREENSHOT_TESTS env variable",
)
def test_screenshot_request_reviewed_icons(page, context, live_server):
    author, user_dicts = get_user_data()
    checker1 = factories.create_airlock_user(
        username="checker1",
        workspaces=[],
        output_checker=True,
    )
    checker2 = factories.create_airlock_user(
        username="checker2",
        workspaces=[],
        output_checker=True,
    )
    workspace = factories.create_workspace("my-workspace")
    release_request = factories.create_request_at_status(
        workspace,
        author=author,
        status=RequestStatus.REVIEWED,
        files=[
            factories.request_file(
                path="approved.txt",
                contents="approved",
                approved=True,
                checkers=[checker1, checker2],
            ),
            factories.request_file(
                path="changes_requested.txt",
                contents="changes",
                changes_requested=True,
                checkers=[checker1, checker2],
            ),
            factories.request_file(
                path="conflicted.txt",
                contents="conflicted",
                approved=True,
                checkers=[checker1, checker2],
            ),
            factories.request_file(
                path="withdrawn.txt",
                contents="withdrawn",
                filetype=RequestFileType.WITHDRAWN,
            ),
            factories.request_file(
                path="supporting.txt",
                contents="supporting",
                filetype=RequestFileType.SUPPORTING,
            ),
        ],
    )
    # change one of the votes to changes requested on the conflicted file
    conflicted_file = release_request.get_request_file_from_output_path(
        "conflicted.txt"
    )
    bll.request_changes_to_file(release_request, conflicted_file, checker1)

    login_as_user(live_server, context, user_dicts["checker1"])

    # View request
    page.goto(live_server.url + release_request.get_url())

    # screenshot the tree
    page.locator("#tree").screenshot(
        path=settings.SCREENSHOT_DIR / "request_reviewed_file_icons.png"
    )


@pytest.mark.skipif(
    os.getenv("RUN_SCREENSHOT_TESTS") is None,
    reason="screenshot tests skipped; set RUN_SCREENSHOT_TESTS env variable",
)
def test_screenshot_workspace_icons(page, context, live_server, mock_old_api):
    author, user_dicts = get_user_data()
    checker1 = factories.create_airlock_user(
        username="checker1",
        workspaces=[],
        output_checker=True,
    )
    checker2 = factories.create_airlock_user(
        username="checker2",
        workspaces=[],
        output_checker=True,
    )
    workspace = factories.create_workspace("my-workspace")
    factories.write_workspace_file(
        workspace, path="not_added_to_request.txt", contents="not added"
    )
    factories.write_workspace_file(
        workspace, path="already_released.txt", contents="released"
    )
    factories.create_request_at_status(
        workspace,
        author=author,
        status=RequestStatus.RELEASED,
        files=[
            factories.request_file(
                path="already_released.txt", contents="released", approved=True
            )
        ],
    )
    factories.create_request_at_status(
        workspace,
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(path="added_to_request.txt", contents="approved"),
            factories.request_file(
                path="updated.txt",
                contents="updated",
                changes_requested=True,
                checkers=[checker1, checker2],
            ),
        ],
    )

    # update the contents of updated.txt on disk
    factories.write_workspace_file(
        workspace,
        "updated.txt",
        contents="new content",
    )

    login_as_user(live_server, context, user_dicts["author"])
    # View workspace
    page.goto(live_server.url + workspace.get_url())

    # screenshot the tree
    page.locator("#tree").screenshot(
        path=settings.SCREENSHOT_DIR / "workspace_file_icons.png"
    )
