import pytest
from django.conf import settings
from playwright.sync_api import expect

from airlock.enums import RequestFileType, RequestStatus, Visibility
from airlock.types import UrlPath
from tests import factories
from tests.functional.conftest import login_as_user


def test_request_file_withdraw(live_server, context, page, bll):
    author = login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(username="author"),
    )

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(group="group", path="file1.txt"),
            factories.request_file(group="group", path="file2.txt"),
        ],
    )

    page.goto(live_server.url + release_request.get_url("group/file1.txt"))

    file1_locator = page.locator("#tree").get_by_role("link", name="file1.txt")

    expect(file1_locator).to_have_count(1)

    page.locator("#withdraw-file-button").click()

    expect(file1_locator).to_have_count(0)

    release_request = bll.get_release_request(release_request.id, author)
    bll.set_status(release_request, RequestStatus.SUBMITTED, author)

    file2_locator = page.locator("#tree").get_by_role("link", name="file2.txt")
    file2_locator.click()
    expect(page.locator("#withdraw-file-button")).not_to_be_visible()


def test_request_file_group_context_modal(live_server, context, page):
    author = login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(username="author"),
    )

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(group="group", path="file1.txt"),
            factories.request_file(group="group", path="file2.txt"),
        ],
    )

    page.goto(live_server.url + release_request.get_url("group/file1.txt"))

    modal_button = page.locator("button[data-modal=group-context]")
    modal_button.click()

    dialog = page.locator("dialog#group-context")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("This is some testing context")
    expect(dialog).to_contain_text("I got rid of all the small numbers")

    # context and controls instruction help text is not shown in the modal
    expect(page.get_by_test_id("c3")).not_to_contain_text("Please describe")

    dialog.locator("button[type=cancel]").click()
    expect(dialog).not_to_be_visible()


def test_request_group_edit_comment_for_author(live_server, context, page, bll):
    author = login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(
            username="author",
            workspaces={
                "workspace": factories.create_api_workspace(),
                "pending": factories.create_api_workspace(),
            },
        ),
    )

    pending_release_request = factories.create_request_at_status(
        "pending",
        author=author,
        files=[factories.request_file(group="group")],
        status=RequestStatus.PENDING,
    )

    page.goto(live_server.url + pending_release_request.get_url("group"))
    contents = page.locator("#selected-contents")

    group_edit_locator = contents.get_by_role("form", name="group-edit-form")
    context_locator = group_edit_locator.get_by_role("textbox", name="context")
    controls_locator = group_edit_locator.get_by_role("textbox", name="controls")

    context_locator.fill("test context")
    controls_locator.fill("test controls")

    group_save_button = group_edit_locator.get_by_role("button", name="Save")
    group_save_button.click()

    expect(context_locator).to_have_value("test context")
    expect(controls_locator).to_have_value("test controls")

    group_comment_locator = contents.get_by_role("form", name="group-comment-form")
    expect(group_comment_locator).to_be_visible()
    comment_locator = group_comment_locator.get_by_role("textbox", name="comment")

    comment_locator.fill("test _comment_")
    comment_button = group_comment_locator.get_by_role("button", name="Comment")
    comment_button.click()

    comments_locator = contents.locator(".comments")
    assert "test <em>comment</em>" in comments_locator.inner_html()

    delete_comment_button = page.get_by_role("button", name="Delete comment")
    expect(delete_comment_button).to_be_visible()

    # submit the pending request
    bll.submit_request(pending_release_request, author)

    # cannot edit context/controls for submitted request or add comment
    page.goto(live_server.url + pending_release_request.get_url("group"))

    # comment is still visible
    comments_locator = contents.locator(".comments")
    assert "test <em>comment</em>" in comments_locator.inner_html()
    # context/controls and all buttons (including delete comment for comment made
    # pre-submission) are not visible
    expect(context_locator).not_to_be_editable()
    expect(controls_locator).not_to_be_editable()
    expect(group_save_button).not_to_be_visible()
    expect(comment_button).not_to_be_visible()
    expect(delete_comment_button).not_to_be_visible()


def test_request_group_edit_comment_for_checker(
    live_server, context, page, bll, settings
):
    login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(username="checker", output_checker=True),
    )

    submitted_release_request = factories.create_request_at_status(
        "workspace",
        files=[factories.request_file(group="group")],
        status=RequestStatus.SUBMITTED,
    )
    pending_release_request = factories.create_request_at_status(
        "pending",
        files=[factories.request_file(group="group")],
        status=RequestStatus.PENDING,
    )

    page.goto(live_server.url + submitted_release_request.get_url("group"))
    contents = page.locator("#selected-contents")

    group_edit_locator = contents.get_by_role("form", name="group-edit-form")
    context_locator = group_edit_locator.get_by_role("textbox", name="context")
    controls_locator = group_edit_locator.get_by_role("textbox", name="controls")
    group_save_button = group_edit_locator.get_by_role("button", name="Save")

    group_comment_locator = contents.get_by_role("form", name="group-comment-form")
    comment_button = group_comment_locator.get_by_role("button", name="Comment")

    # only author can edit context/controls
    expect(context_locator).not_to_be_editable()
    expect(controls_locator).not_to_be_editable()
    expect(group_save_button).not_to_be_visible()
    # in submitted status, output-checker can comment
    expect(comment_button).to_be_visible()

    # cannot edit context/controls for submitted request or add comment
    page.goto(live_server.url + pending_release_request.get_url("group"))
    expect(context_locator).not_to_be_editable()
    expect(controls_locator).not_to_be_editable()
    expect(group_save_button).not_to_be_visible()
    # in pending status, output-checker cannot comment
    expect(comment_button).not_to_be_visible()


def test_request_group_comment_visibility_public_for_checker(
    live_server, context, page, bll
):
    login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(username="checker", output_checker=True),
    )
    checker = factories.create_airlock_user("checker", [], True)

    submitted_release_request = factories.create_request_at_status(
        "workspace",
        files=[factories.request_file(group="group")],
        status=RequestStatus.SUBMITTED,
        checker=checker,
        checker_comments=[("group", "checker comment", Visibility.PRIVATE)],
    )

    page.goto(live_server.url + submitted_release_request.get_url("group"))

    comment_button = page.get_by_role("button", name="Make comment visible to all")
    expect(comment_button).to_be_visible()
    comment_button.click()

    release_request = factories.refresh_release_request(submitted_release_request)
    checker_comment = release_request.filegroups["group"].comments[0]
    assert checker_comment.visibility == Visibility.PUBLIC


def _workspace_dict():
    return {
        "workspace": factories.create_api_workspace(project="Project 2"),
    }


@pytest.mark.parametrize(
    "author,login_as,status,reviewer_buttons_visible,release_button_visible,file_review_buttons_visible",
    # reviewer buttons: return/reject/submit review
    # file review buttons: on a file view approve/request changes/reset
    [
        ("researcher", "researcher", RequestStatus.SUBMITTED, False, False, False),
        ("researcher", "checker", RequestStatus.SUBMITTED, True, True, True),
        ("checker", "checker", RequestStatus.SUBMITTED, False, False, False),
        ("researcher", "checker", RequestStatus.PARTIALLY_REVIEWED, True, True, True),
        ("checker", "checker", RequestStatus.PARTIALLY_REVIEWED, False, False, False),
        ("researcher", "checker", RequestStatus.REVIEWED, True, True, True),
        ("researcher", "checker", RequestStatus.RETURNED, False, False, False),
        ("checker", "checker", RequestStatus.RETURNED, False, False, False),
        # APPROVED status - can be released, but other review buttons are hidden
        ("researcher", "checker", RequestStatus.APPROVED, False, True, False),
        ("researcher", "checker", RequestStatus.RELEASED, False, False, False),
        ("researcher", "checker", RequestStatus.REJECTED, False, False, False),
        ("researcher", "checker", RequestStatus.WITHDRAWN, False, False, False),
    ],
)
def test_request_buttons(
    live_server,
    context,
    page,
    bll,
    mock_old_api,
    author,
    login_as,
    status,
    reviewer_buttons_visible,
    release_button_visible,
    file_review_buttons_visible,
):
    user_data = {
        "researcher": factories.create_api_user(
            username="researcher", workspaces=_workspace_dict(), output_checker=False
        ),
        "checker": factories.create_api_user(
            username="checker", workspaces=_workspace_dict(), output_checker=True
        ),
    }

    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(**user_data[author]),
        status=status,
        withdrawn_after=RequestStatus.PENDING,
        files=[
            factories.request_file(
                group="group",
                path="file1.txt",
                approved=True,
            ),
        ],
    )

    login_as_user(live_server, context, user_data[login_as])
    page.goto(live_server.url + release_request.get_url())

    reviewer_buttons = [
        "button[data-modal=returnRequest]",
        "button[data-modal=rejectRequest]",
        "#submit-review-button",
    ]

    if reviewer_buttons_visible:
        for button_id in reviewer_buttons:
            expect(page.locator(button_id)).to_be_visible()
    else:
        for button_id in reviewer_buttons:
            expect(page.locator(button_id)).not_to_be_visible()

    if release_button_visible:
        expect(page.locator("#release-files-button")).to_be_visible()
    else:
        expect(page.locator("#release-files-button")).not_to_be_visible()

    page.goto(live_server.url + release_request.get_url("group/file1.txt"))

    file_review_buttons = [
        "#file-approve-button",
        "#file-request-changes-button",
        "#file-reset-button",
    ]
    if file_review_buttons_visible:
        for button_id in file_review_buttons:
            expect(page.locator(button_id)).to_be_visible()
    else:
        for button_id in file_review_buttons:
            expect(page.locator(button_id)).not_to_be_visible()


@pytest.mark.parametrize(
    "files,submit_enabled",
    [
        # output file
        ([factories.request_file(path="file.txt")], True),
        # no files
        ([], False),
        # supporting file only
        (
            [
                factories.request_file(
                    path="file.txt", filetype=RequestFileType.SUPPORTING
                )
            ],
            False,
        ),
        # withdrawn file only
        (
            [
                factories.request_file(
                    path="file.txt", filetype=RequestFileType.WITHDRAWN
                )
            ],
            False,
        ),
    ],
)
def test_submit_button_visibility(
    live_server,
    context,
    page,
    files,
    submit_enabled,
):
    user_data = dict(
        username="researcher", workspaces=_workspace_dict(), output_checker=False
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(**user_data),
        status=RequestStatus.PENDING,
        files=files,
    )

    login_as_user(live_server, context, user_data)
    page.goto(live_server.url + release_request.get_url())

    # For an initial submission, we use the submit modal (to require
    # agreement to the terms), which includes the submit button
    submit_btn = page.locator("#submit-for-review-button")
    submit_modal = page.locator("#submitRequest-modal-container")

    # This is an initial submission so the resubmit button is hidden
    expect(page.locator("#resubmit-for-review-button")).not_to_be_visible()

    if submit_enabled:
        # The submit button is inside the modal for a submittable request,
        # so not visible
        expect(submit_btn).not_to_be_visible()
        expect(submit_modal).to_be_visible()
    else:
        # Request not submittable, so we show a disabled submit button
        expect(submit_modal).not_to_be_visible()
        expect(submit_btn).to_be_visible()
        expect(submit_btn).to_be_disabled()


def test_resubmit_button_visibility(
    live_server,
    context,
    page,
    bll,
):
    user_data = dict(
        username="researcher", workspaces=_workspace_dict(), output_checker=False
    )
    author = factories.create_airlock_user(**user_data)
    # Create a returned release request with one output file
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[factories.request_file(path="file.txt", group="group", approved=True)],
    )

    login_as_user(live_server, context, user_data)
    page.goto(live_server.url + release_request.get_url())

    # For a resubmission, we just have a button, not the submit modal
    submit_btn = page.locator("#submit-for-review-button")
    submit_modal = page.locator("#submitRequest-modal-container")
    resubmit_btn = page.locator("#resubmit-for-review-button")

    # resubmit button is visible and enabled
    expect(resubmit_btn).to_be_visible()
    expect(resubmit_btn).to_be_enabled()
    # submit modal and button is hidden
    expect(submit_btn).not_to_be_visible()
    expect(submit_modal).not_to_be_visible()

    # withdraw the output file
    bll.withdraw_file_from_request(release_request, UrlPath("group/file.txt"), author)
    page.reload()
    # resubmit button is visible but disabled
    expect(resubmit_btn).to_be_visible()
    expect(resubmit_btn).to_be_disabled()
    # submit modal and button still hidden
    expect(submit_btn).not_to_be_visible()
    expect(submit_modal).not_to_be_visible()


@pytest.mark.parametrize(
    "login_as,status,checkers, can_return",
    [
        ("author", RequestStatus.SUBMITTED, None, False),
        ("checker1", RequestStatus.PARTIALLY_REVIEWED, ["checker1"], True),
        ("checker2", RequestStatus.PARTIALLY_REVIEWED, ["checker1"], True),
        ("checker1", RequestStatus.REVIEWED, ["checker1", "checker2"], True),
    ],
)
def test_request_returnable(
    live_server, context, page, bll, login_as, status, checkers, can_return
):
    user_data = {
        "author": dict(
            username="author", workspaces=_workspace_dict(), output_checker=False
        ),
        "checker1": dict(
            username="checker1", workspaces=_workspace_dict(), output_checker=True
        ),
        "checker2": dict(
            username="checker2", workspaces=_workspace_dict(), output_checker=True
        ),
    }
    author = factories.create_airlock_user(**user_data["author"])
    if checkers is not None:
        checkers = [
            factories.create_airlock_user(**user_data[user]) for user in checkers
        ]
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=status,
        files=[
            factories.request_file(
                group="group",
                path="file1.txt",
                checkers=checkers,
                approved=checkers is not None,
            ),
            factories.request_file(
                group="group",
                path="file2.txt",
                checkers=checkers,
                changes_requested=checkers is not None,
            ),
        ],
    )

    login_as_user(live_server, context, user_data[login_as])
    return_request_button = page.locator("button[data-modal=returnRequest]")
    modal_return_request_button = page.locator("#return-request-button")
    page.goto(live_server.url + release_request.get_url())

    if can_return:
        expect(return_request_button).to_be_enabled()
        expect(modal_return_request_button).not_to_be_visible()
        return_request_button.click()
        expect(modal_return_request_button).to_be_visible()

    else:
        expect(return_request_button).not_to_be_visible()


def test_returned_request(live_server, context, page, bll):
    user_data = {
        "author": dict(
            username="author", workspaces=_workspace_dict(), output_checker=False
        ),
        "checker1": dict(
            username="checker1", workspaces=_workspace_dict(), output_checker=True
        ),
        "checker2": dict(
            username="checker2", workspaces=_workspace_dict(), output_checker=True
        ),
    }
    author = factories.create_airlock_user(**user_data["author"])
    checkers = [
        factories.create_airlock_user(**user_data[user])
        for user in ["checker1", "checker2"]
    ]
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(
                group="group", path="file1.txt", checkers=checkers, approved=True
            ),
            factories.request_file(
                group="group",
                path="file2.txt",
                checkers=checkers,
                changes_requested=True,
            ),
        ],
    )

    # author resubmits
    login_as_user(live_server, context, user_data["author"])
    page.goto(live_server.url + release_request.get_url())
    # Can re-submit a returned request
    page.locator("#resubmit-for-review-button").click()

    # logout by clearing cookies
    context.clear_cookies()

    # checker looks at previously changes_requested/approved files
    login_as_user(live_server, context, user_data["checker1"])
    status_locator = page.locator(".file-status--approved")
    # go to previously approved file; still shown as approved
    page.goto(live_server.url + release_request.get_url("group/file1.txt"))
    expect(status_locator).to_contain_text("Approved")

    # go to previously changes_requested file; now shown as no-status
    page.goto(live_server.url + release_request.get_url("group/file2.txt"))
    status_locator = page.locator(".file-status--undecided")
    expect(status_locator).to_contain_text("Undecided")


def test_request_releaseable(live_server, context, page, bll):
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.REVIEWED,
        files=[
            factories.request_file(group="group", path="file1.txt", approved=True),
        ],
    )
    output_checker = login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(
            username="output_checker", output_checker=True
        ),
    )

    page.goto(live_server.url + release_request.get_url())

    release_files_button = page.locator("#release-files-button")
    reject_request_button = page.locator("button[data-modal=rejectRequest]")
    return_request_button = page.locator("button[data-modal=returnRequest]")

    # Request is currently reviewed twice
    # output checker can release, return or reject
    for locator in [release_files_button, return_request_button, reject_request_button]:
        expect(locator).to_be_visible()
        expect(locator).to_be_enabled()

    bll.set_status(release_request, RequestStatus.APPROVED, output_checker)
    page.goto(live_server.url + release_request.get_url())

    # Request is now approved
    # output checker cannot return or reject
    expect(release_files_button).to_be_enabled()
    for locator in [return_request_button, reject_request_button]:
        expect(locator).not_to_be_visible()


@pytest.mark.parametrize(
    "status,uploaded_files,failed_uploaded_files,uploaded_count,release_button_enabled,icon_colour",
    [
        (RequestStatus.REVIEWED, [], [], 0, True, ""),
        (RequestStatus.APPROVED, ["file1.txt"], [], 1, False, "text-bn-egg-500"),
        (RequestStatus.APPROVED, [], ["file1.txt"], 0, False, "text-bn-egg-500"),
        (RequestStatus.APPROVED, ["file1.txt"], ["file2.txt"], 1, True, "text-red-800"),
        (
            RequestStatus.APPROVED,
            [],
            ["file1.txt", "file2.txt"],
            0,
            True,
            "text-red-800",
        ),
        (
            RequestStatus.RELEASED,
            ["file1.txt", "file2.txt"],
            [],
            2,
            False,
            "text-green-700",
        ),
    ],
)
def test_request_uploaded_files_status(
    live_server,
    context,
    page,
    bll,
    mock_old_api,
    mock_notifications,
    status,
    uploaded_files,
    failed_uploaded_files,
    uploaded_count,
    release_button_enabled,
    icon_colour,
):
    release_request = factories.create_request_at_status(
        "workspace",
        status=status,
        files=[
            factories.request_file(group="group", path="file1.txt", approved=True),
            factories.request_file(group="group", path="file2.txt", approved=True),
        ],
    )
    output_checker = login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(
            username="output_checker", output_checker=True
        ),
    )
    for path in uploaded_files:
        bll.register_file_upload(release_request, UrlPath(path), output_checker)

    for path in failed_uploaded_files:
        for _ in range(settings.UPLOAD_MAX_ATTEMPTS):
            bll.register_file_upload_attempt(release_request, UrlPath(path))

    release_request = factories.refresh_release_request(release_request)
    page.goto(live_server.url + release_request.get_url())

    release_files_button = page.locator("#release-files-button")
    uploaded_files_count_el = page.locator("#uploaded-files-count")

    expect(uploaded_files_count_el).to_contain_text(str(uploaded_count))
    # we're using the icon component, so the easiest way to check the icon is correct
    # is to look for its colour
    assert icon_colour in uploaded_files_count_el.inner_html()

    if status == RequestStatus.RELEASED:
        expect(release_files_button).not_to_be_visible()
    else:
        expect(release_files_button).to_be_visible()
        if release_button_enabled:
            expect(release_files_button).to_be_enabled()
        else:
            expect(release_files_button).not_to_be_enabled()


def test_request_uploaded_files_counts(
    live_server,
    context,
    page,
    bll,
    mock_old_api,
    mock_notifications,
):
    # make a release request in APPROVED status
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.APPROVED,
        files=[
            factories.request_file(
                group="group", contents="1", path="file1.txt", approved=True
            ),
            factories.request_file(
                group="group", contents="2", path="file2.txt", approved=True
            ),
            factories.request_file(
                group="group", contents="3", path="file3.txt", approved=True
            ),
        ],
    )
    output_checker = login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(
            username="output_checker", output_checker=True
        ),
    )

    page.goto(live_server.url + release_request.get_url())

    release_files_button = page.locator("#release-files-button")
    uploaded_files_count_el = page.locator("#uploaded-files-count")
    uploaded_files_count_parent_el = uploaded_files_count_el.locator("..")

    def assert_still_uploading(uploaded_count):
        expect(uploaded_files_count_el).to_contain_text(str(uploaded_count))
        expect(uploaded_files_count_parent_el).to_have_attribute(
            "hx-get", release_request.uploaded_files_count_url()
        )
        expect(release_files_button).to_be_disabled()

    assert_still_uploading(0)

    # update the uploaded file count in the background, without reloading the page
    bll.register_file_upload(release_request, UrlPath("file1.txt"), output_checker)
    assert_still_uploading(1)

    bll.register_file_upload(release_request, UrlPath("file2.txt"), output_checker)
    assert_still_uploading(2)

    # make the last file failed
    for _ in range(settings.UPLOAD_MAX_ATTEMPTS):
        bll.register_file_upload_attempt(release_request, UrlPath("file3.txt"))
    # still shows 2 files; page has refreshed and no htmx attributes on the
    # parent element anymore, so it doesn't continue to poll, and the release files
    # button is enabled
    expect(uploaded_files_count_el).to_contain_text("2")
    expect(uploaded_files_count_parent_el).not_to_have_attribute(
        "hx-get", release_request.uploaded_files_count_url()
    )
    expect(release_files_button).to_be_enabled()

    # click the button to re-release
    release_files_button.click()
    assert_still_uploading(2)

    # complete the upload
    bll.register_file_upload(release_request, UrlPath("file3.txt"), output_checker)

    # test for the race condition where the upload has been completed but the
    # status hasn't yet been updated; in this case we want to continue to poll
    # until the status has change to RELEASED
    assert_still_uploading(3)

    # Now update the status to released
    bll.set_status(release_request, RequestStatus.RELEASED, output_checker)

    # all files uploaded AND status updated; page has refreshed and no
    # htmx attributes on the parent element anymore
    # the release files button is not visible because the release is now complete
    expect(uploaded_files_count_el).to_contain_text("3")
    expect(uploaded_files_count_parent_el).not_to_have_attribute(
        "hx-get", release_request.uploaded_files_count_url()
    )
    expect(release_files_button).not_to_be_visible()
