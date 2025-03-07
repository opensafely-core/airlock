import re

import pytest
from playwright.sync_api import expect

from airlock.enums import RequestFileType, RequestFileVote, RequestStatus, Visibility
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


def test_request_file_update_properties(live_server, context, page, bll):
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
            factories.request_file(
                group="group", path="file1.txt", filetype=RequestFileType.SUPPORTING
            ),
            factories.request_file(group="group", path="file2.txt"),
        ],
    )

    page.goto(live_server.url + release_request.get_url("group/file1.txt"))

    # click to open modal
    page.locator("#update-file-modal-button").click()

    # The filegroup field is populated with the file's current group
    expect(page.get_by_label("Select a file group")).to_have_value("group")

    # The filetype field is populated with the file's current type
    expect(
        page.locator("input[name=form-0-filetype][value=SUPPORTING]")
    ).to_be_checked()

    page.locator("#id_new_filegroup").fill("new-group")

    # Click the button to move the file's to a new group
    page.locator("#add-or-change-file-button").click()

    expect(page).to_have_url(
        live_server.url + release_request.get_url("new-group/file1.txt")
    )


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
        user_dict=factories.create_api_user(
            username="checker",
            workspaces=["returned_collaborator"],
            output_checker=True,
        ),
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
    returned_release_request = factories.create_request_at_status(
        "returned",
        files=[factories.request_file(group="group", changes_requested=True)],
        status=RequestStatus.RETURNED,
    )
    returned_collaborator_release_request = factories.create_request_at_status(
        "returned_collaborator",
        files=[factories.request_file(group="group", changes_requested=True)],
        status=RequestStatus.RETURNED,
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

    # cannot edit context/controls for pending request or add comment
    page.goto(live_server.url + pending_release_request.get_url("group"))
    expect(context_locator).not_to_be_editable()
    expect(controls_locator).not_to_be_editable()
    expect(group_save_button).not_to_be_visible()
    # in pending status, output-checker cannot comment
    expect(comment_button).not_to_be_visible()

    # in returned status, output-checker without access to workspace cannot comment
    page.goto(live_server.url + returned_release_request.get_url("group"))
    expect(comment_button).not_to_be_visible()

    # in returned status, output-checker with access to workspace can comment (as
    # they may be commenting as a collaborator)
    page.goto(live_server.url + returned_collaborator_release_request.get_url("group"))
    expect(comment_button).to_be_visible()
    # but they cannot make a private comment
    expect(
        page.get_by_role("radio", name="Only visible to output-checkers")
    ).not_to_be_visible()
    # and still only the author can edit context/controls
    expect(context_locator).not_to_be_editable()
    expect(controls_locator).not_to_be_editable()
    expect(group_save_button).not_to_be_visible()


def test_request_group_comment_visibility_public_for_checker(
    live_server, context, page, bll
):
    login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(username="checker", output_checker=True),
    )
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )

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
    # file review buttons: on a file view approve/request changes
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
        ("researcher", "checker", RequestStatus.APPROVED, False, False, False),
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
    ]
    if file_review_buttons_visible:
        for button_id in file_review_buttons:
            expect(page.locator(button_id)).to_be_visible()
    else:
        for button_id in file_review_buttons:
            expect(page.locator(button_id)).not_to_be_visible()


@pytest.mark.parametrize(
    "status,current_vote,approved_selected_disabled,changes_requested_selected_disabled,decision_tooltip",
    # file review buttons: on a file view approve/request changes
    # Just testing the statuses that the voting buttons are visible in
    # The test above asserts that they are only visible in these 3 states, and
    # only for output-checkers
    [
        (
            RequestStatus.SUBMITTED,
            None,
            (False, False),
            (False, False),
            "Overall decision will be displayed after two independent output checkers have submitted their review",
        ),
        (
            RequestStatus.SUBMITTED,
            RequestFileVote.APPROVED,
            (True, False),
            (False, False),
            "Overall decision will be displayed after two independent output checkers have submitted their review",
        ),
        (
            RequestStatus.SUBMITTED,
            RequestFileVote.CHANGES_REQUESTED,
            (False, False),
            (True, False),
            "Overall decision will be displayed after two independent output checkers have submitted their review",
        ),
        (
            RequestStatus.PARTIALLY_REVIEWED,
            RequestFileVote.APPROVED,
            (True, True),
            (False, False),
            "Overall decision will be displayed after two independent output checkers have submitted their review",
        ),
        (
            RequestStatus.PARTIALLY_REVIEWED,
            RequestFileVote.CHANGES_REQUESTED,
            (False, False),
            (True, True),
            "Overall decision will be displayed after two independent output checkers have submitted their review",
        ),
        (
            RequestStatus.REVIEWED,
            RequestFileVote.APPROVED,
            (True, True),
            (False, False),
            "Two independent output checkers have approved this file",
        ),
        (
            RequestStatus.REVIEWED,
            RequestFileVote.CHANGES_REQUESTED,
            (False, False),
            (True, True),
            "Two independent output checkers have requested changes to this file",
        ),
    ],
)
def test_file_vote_buttons(
    live_server,
    context,
    page,
    bll,
    mock_old_api,
    status,
    current_vote,
    approved_selected_disabled,
    changes_requested_selected_disabled,
    decision_tooltip,
):
    checker = factories.create_api_user(
        username="checker", workspaces=_workspace_dict(), output_checker=True
    )

    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(
            username="researcher", workspaces=_workspace_dict(), output_checker=False
        ),
        status=status,
        withdrawn_after=RequestStatus.PENDING,
        files=[
            factories.request_file(
                group="group",
                path="file1.txt",
                approved=current_vote == RequestFileVote.APPROVED,
                changes_requested=current_vote == RequestFileVote.CHANGES_REQUESTED,
                checkers=[
                    factories.create_airlock_user(**checker),
                    factories.get_default_output_checkers()[0],
                ],
            ),
        ],
    )

    login_as_user(live_server, context, checker)
    page.goto(live_server.url + release_request.get_url("group/file1.txt"))

    approve_button = page.locator("#file-approve-button")
    changes_requested_button = page.locator("#file-request-changes-button")

    expected_button_state = {
        approve_button: {
            "selected": approved_selected_disabled[0],
            "disabled": approved_selected_disabled[1],
        },
        changes_requested_button: {
            "selected": changes_requested_selected_disabled[0],
            "disabled": changes_requested_selected_disabled[1],
        },
    }
    for button_locator, expected_state in expected_button_state.items():
        for state, expected in expected_state.items():
            class_regex = re.compile(rf"(^|\s)btn-group__btn--{state}(\s|$)")
            if expected:
                expect(button_locator).to_have_class(class_regex)
            else:
                expect(button_locator).not_to_have_class(class_regex)

    decision_locator = page.locator(".request-file-status")
    decision_locator.hover()
    expect(page.locator("body")).to_contain_text(decision_tooltip)

    # test the conflicted decision state
    if status == RequestStatus.REVIEWED and current_vote == RequestFileVote.APPROVED:
        changes_requested_button.click()
        decision_locator.hover()
        expect(page.locator("body")).to_contain_text(
            "Output checkers have reviewed this file and disagree"
        )


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


@pytest.mark.parametrize(
    "status,submit_button_id",
    [
        (RequestStatus.PENDING, "#submit-for-review-button"),
        (RequestStatus.RETURNED, "#resubmit-for-review-button"),
    ],
)
def test_submit_button_missing_context_controls(
    live_server, context, page, bll, status, submit_button_id
):
    user_data = dict(
        username="researcher", workspaces=_workspace_dict(), output_checker=False
    )
    author = factories.create_airlock_user(**user_data)
    # Create request with one file (group context/controls is added)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=status,
        files=[
            factories.request_file(
                path="file.txt", group="complete group", changes_requested=True
            )
        ],
    )
    # Add files for 2 more groups, with no context or controls
    factories.add_request_file(release_request, "group1", "path/test.txt")
    factories.add_request_file(release_request, "group2", "path/test1.txt")
    login_as_user(live_server, context, user_data)
    page.goto(live_server.url + release_request.get_url())

    submit_btn = page.locator(submit_button_id)
    expect(submit_btn).to_be_disabled()
    tooltip = "Incomplete context and/or controls for filegroup(s): 'group1', 'group2"
    submit_btn.hover()
    expect(submit_btn).to_contain_text(tooltip)

    # add context for both groups
    for group_name in ["group1", "group2"]:
        bll.group_edit(
            release_request, group_name, context="foo", controls="", user=author
        )
    page.reload()

    # button still disabled, both groups still incomplete
    expect(submit_btn).to_be_disabled()
    submit_btn.hover()
    expect(submit_btn).to_contain_text(tooltip)

    # Complete info for group1
    bll.group_edit(
        release_request, "group1", context="foo", controls="bar", user=author
    )
    page.reload()
    # button still disabled, only group2 still incomplete
    expect(submit_btn).to_be_disabled()
    tooltip = "Incomplete context and/or controls for filegroup(s): 'group2'"
    submit_btn.hover()
    expect(submit_btn).to_contain_text(tooltip)

    # Complete info for group2
    bll.group_edit(
        release_request, "group2", context="foo", controls="bar", user=author
    )
    page.reload()

    if status == RequestStatus.RETURNED:
        # For returned requests, groups with changes requested require a comment
        # button still disabled; "complete group" has changes requested
        expect(submit_btn).to_be_disabled()
        submit_btn.hover()
        expect(submit_btn).to_contain_text(
            "Filegroup(s) are missing comments: complete group, group1, group2"
        )

        # Add a public comment to each group
        for group in ["complete group", "group1", "group2"]:
            bll.group_comment_create(
                release_request, group, "a comment", Visibility.PUBLIC, author
            )
        page.reload()

    expect(submit_btn).to_be_enabled()


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
    "login_as,status,checkers,has_public_comment,can_return",
    [
        ("author", RequestStatus.SUBMITTED, None, False, False),
        ("checker1", RequestStatus.PARTIALLY_REVIEWED, ["checker1"], False, True),
        ("checker2", RequestStatus.PARTIALLY_REVIEWED, ["checker1"], False, True),
        ("checker1", RequestStatus.REVIEWED, ["checker1", "checker2"], False, False),
        ("checker1", RequestStatus.REVIEWED, ["checker1", "checker2"], True, True),
    ],
)
def test_request_returnable(
    live_server,
    context,
    page,
    bll,
    login_as,
    status,
    checkers,
    has_public_comment,
    can_return,
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

    if has_public_comment:
        bll.group_comment_create(
            release_request, "group", "a comment", Visibility.PUBLIC, checkers[0]
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
                group="group1",
                path="file2.txt",
                checkers=checkers,
                changes_requested=True,
            ),
        ],
    )

    # author resubmits
    login_as_user(live_server, context, user_data["author"])
    page.goto(live_server.url + release_request.get_url())

    resubmit_button = page.locator("#resubmit-for-review-button")
    # Can't resubmit without a comment on the request-changes group
    expect(resubmit_button).to_be_disabled()
    resubmit_button.hover()
    expect(resubmit_button).to_contain_text("Filegroup(s) are missing comments: group1")

    # Add a comment for group1 only
    bll.group_comment_create(
        release_request, "group1", "a comment", Visibility.PUBLIC, author
    )
    page.reload()

    # Can re-submit a returned request
    page.locator("#resubmit-for-review-button").click()

    # logout by clearing cookies
    context.clear_cookies()

    # checker looks at previously changes_requested/approved files
    login_as_user(live_server, context, user_data["checker1"])

    selected_class_regex = re.compile(r"(^|\s)btn-group__btn--selected(\s|$)")

    approve_button = page.locator("#file-approve-button")
    # go to previously approved file; still shown as approved
    page.goto(live_server.url + release_request.get_url("group/file1.txt"))
    expect(approve_button).to_contain_text("Approved")
    expect(approve_button).to_have_class(selected_class_regex)

    # go to previously changes_requested file; now shown as unselected
    page.goto(live_server.url + release_request.get_url("group1/file2.txt"))
    request_changes_button = page.locator("#file-request-changes-button")
    expect(request_changes_button).to_contain_text("Request changes")
    expect(approve_button).not_to_have_class(selected_class_regex)
    expect(request_changes_button).not_to_have_class(selected_class_regex)


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
    # output checker cannot release, return or reject
    for locator in [return_request_button, reject_request_button, release_files_button]:
        expect(locator).not_to_be_visible()


@pytest.mark.parametrize(
    "status,uploaded_files,uploaded_count,release_button_visible,icon_colour",
    [
        # not yet released
        (RequestStatus.REVIEWED, [], 0, True, ""),
        # approved, still uploading 1 file
        (RequestStatus.APPROVED, ["file1.txt"], 1, False, "text-bn-egg-500"),
        # approved, still uploading all files
        (RequestStatus.APPROVED, [], 0, False, "text-bn-egg-500"),
        # approved, all files uploaded, hasn't changed status yet
        (
            RequestStatus.APPROVED,
            ["file1.txt", "file2.txt"],
            2,
            False,
            "text-bn-egg-500",
        ),
        # released, all files uploaded
        (
            RequestStatus.RELEASED,
            ["file1.txt", "file2.txt"],
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
    uploaded_count,
    release_button_visible,
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

    release_request = factories.refresh_release_request(release_request)
    page.goto(live_server.url + release_request.get_url())

    release_files_button = page.locator("#release-files-button")
    uploaded_files_count_el = page.locator("#uploaded-files-count")

    expect(uploaded_files_count_el).to_contain_text(str(uploaded_count))
    # we're using the icon component, so the easiest way to check the icon is correct
    # is to look for its colour
    assert icon_colour in uploaded_files_count_el.inner_html()

    if release_button_visible:
        expect(release_files_button).to_be_visible()
        expect(release_files_button).to_be_enabled()
    else:
        expect(release_files_button).not_to_be_visible()


def test_request_uploaded_files_counts(
    live_server,
    context,
    page,
    bll,
    mock_old_api,
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

    # In approved status, the release files button is never visible
    release_files_button = page.locator("#release-files-button")
    expect(release_files_button).not_to_be_visible()

    uploaded_files_count_el = page.locator("#uploaded-files-count")
    uploaded_files_count_parent_el = uploaded_files_count_el.locator("..")

    def assert_still_uploading(uploaded_count):
        expect(uploaded_files_count_el).to_contain_text(str(uploaded_count))
        expect(uploaded_files_count_parent_el).to_have_attribute(
            "hx-get", release_request.uploaded_files_count_url()
        )

    assert_still_uploading(0)

    # update the uploaded file count in the background, without reloading the page
    bll.register_file_upload(release_request, UrlPath("file1.txt"), output_checker)
    assert_still_uploading(1)

    bll.register_file_upload(release_request, UrlPath("file2.txt"), output_checker)
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


def test_file_browser_expand_collapse(live_server, page, context):
    author = login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(
            username="author",
            workspaces={
                "workspace": factories.create_api_workspace(),
            },
        ),
    )

    pending_release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        files=[
            factories.request_file(group="group", path="folder/file1.txt"),
        ],
        status=RequestStatus.PENDING,
    )

    file1_locator = page.locator("#tree").get_by_role("link", name="file1.txt")

    page.goto(live_server.url + pending_release_request.get_url())

    # Clicking the far right of the file browser in line with the request id
    # should not collapse the groups beneath the request
    request_link = page.locator(".tree__folder-name").filter(
        has_text=pending_release_request.id
    )
    expect(file1_locator).to_be_visible()
    request_link.click(
        position={"x": request_link.bounding_box()["width"] - 10, "y": 10}
    )
    expect(file1_locator).to_be_visible()

    # Clicking the far right of the file browser in line with the group name
    # should not collapse the folders beneath the request
    group_link = page.locator(".tree__folder-name").filter(has_text="group")
    expect(file1_locator).to_be_visible()
    group_link.click(position={"x": group_link.bounding_box()["width"] - 10, "y": 1})
    expect(file1_locator).to_be_visible()

    # Clicking the far right of the file browser in line with the folder name
    # should not collapse the folders beneath the request
    folder_link = page.locator(".tree__folder-name").filter(has_text="folder")
    expect(file1_locator).to_be_visible()
    folder_link.click(position={"x": folder_link.bounding_box()["width"] - 10, "y": 1})
    expect(file1_locator).to_be_visible()

    # Clicking the far right of the file browser in line with the file name
    # should display the file
    file_link = page.locator(".tree__file").filter(has_text="file1")
    expect(page.locator("#file1txt-title")).not_to_be_visible()
    file_link.click(position={"x": file_link.bounding_box()["width"] - 10, "y": 1})
    expect(page.locator("#file1txt-title")).to_be_visible()


def test_request_header_content(live_server, page, context):
    # Test header content that differs on overview page, that is:
    # Request overview title (on overview page) or back link (on other bages)
    # Group name - hidden on overview page

    author = login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(
            username="author",
            workspaces={
                "workspace": factories.create_api_workspace(),
            },
        ),
    )

    pending_release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        files=[
            factories.request_file(group="the-file-group", path="folder/file1.txt"),
        ],
        status=RequestStatus.PENDING,
    )

    request_overview_as_link = page.get_by_role("link").get_by_text("Request overview")
    request_overview_as_header = page.get_by_role("heading").get_by_text(
        "Request overview"
    )
    request_header_subtitle = page.locator(".header__subtitle")

    # Test visibility of links with whole page refreshes
    page.goto(live_server.url + pending_release_request.get_url())
    expect(request_overview_as_link).not_to_be_visible()
    expect(request_overview_as_header).to_be_visible()
    expect(request_header_subtitle).not_to_contain_text("the-file-group")
    page.goto(live_server.url + pending_release_request.get_url("the-file-group"))
    expect(request_overview_as_link).to_be_visible()
    expect(request_overview_as_header).not_to_be_visible()
    expect(request_header_subtitle).to_contain_text("the-file-group")
    page.goto(
        live_server.url + pending_release_request.get_url("the-file-group/folder/")
    )
    expect(request_overview_as_link).to_be_visible()
    expect(request_overview_as_header).not_to_be_visible()
    expect(request_header_subtitle).to_contain_text("the-file-group")
    page.goto(
        live_server.url
        + pending_release_request.get_url("the-file-group/folder/file1.txt")
    )
    expect(request_overview_as_link).to_be_visible()
    expect(request_overview_as_header).not_to_be_visible()
    expect(request_header_subtitle).to_contain_text("the-file-group")

    # Now test visibility of links without whole page refreshes
    page.locator(".tree__folder-name").filter(
        has_text=pending_release_request.id
    ).click()
    expect(request_overview_as_link).not_to_be_visible()
    expect(request_overview_as_header).to_be_visible()
    expect(request_header_subtitle).not_to_contain_text("the-file-group")
    page.locator(".tree__folder-name").filter(has_text="the-file-group").click()
    expect(request_overview_as_link).to_be_visible()
    expect(request_overview_as_header).not_to_be_visible()
    expect(request_header_subtitle).to_contain_text("the-file-group")
    page.locator(".tree__folder-name").filter(has_text="folder").click()
    expect(request_overview_as_link).to_be_visible()
    expect(request_overview_as_header).not_to_be_visible()
    expect(request_header_subtitle).to_contain_text("the-file-group")
    page.locator(".tree__file").filter(has_text="file1").click()
    expect(request_overview_as_link).to_be_visible()
    expect(request_overview_as_header).not_to_be_visible()
    expect(request_header_subtitle).to_contain_text("the-file-group")


@pytest.mark.parametrize(
    "status,approved",
    [
        (RequestStatus.SUBMITTED, True),
        (RequestStatus.SUBMITTED, False),
        (RequestStatus.REVIEWED, True),
        (RequestStatus.REVIEWED, False),
    ],
)
def test_request_action_required_alert(
    live_server, page, context, bll, status, approved
):
    # This tests the different alerts on the overview page and group/dir/file pages
    # for requests that have all required group comments and the user can now
    # submit their review, or return/reject/release
    # Missing filegroup comments are tested elsewhere
    checker = login_as_user(
        live_server,
        context,
        user_dict=factories.create_api_user(username="checker", output_checker=True),
    )

    release_request = factories.create_request_at_status(
        "workspace",
        files=[
            factories.request_file(
                group="group",
                path="folder/file1.txt",
                approved=True if approved else None,
                changes_requested=True if not approved else None,
                checkers=[factories.get_default_output_checkers()[0], checker],
            )
        ],
        status=status,
    )
    # Add a public comment for the group
    bll.group_comment_create(
        release_request, "group", "a comment", Visibility.PUBLIC, checker
    )

    # The alert should be visible on all pages
    content_locator = page.locator("#content")
    content_alert = content_locator.get_by_role("alert")
    # But links in the alert are only visible on the overview page
    content_alert_link = content_alert.get_by_role("link")

    # Test visibility of links with whole page refreshes
    page.goto(live_server.url + release_request.get_url())
    expect(content_alert).to_be_visible()
    expect(content_alert_link).not_to_be_visible()
    page.goto(live_server.url + release_request.get_url("group"))
    expect(content_alert).to_be_visible()
    expect(content_alert_link).to_be_visible()
    page.goto(live_server.url + release_request.get_url("group/folder/"))
    expect(content_alert).to_be_visible()
    expect(content_alert_link).to_be_visible()
    page.goto(live_server.url + release_request.get_url("group/folder/file1.txt"))
    expect(content_alert).to_be_visible()
    expect(content_alert_link).to_be_visible()

    # Now test visibility of links without whole page refreshes
    page.locator(".tree__folder-name").filter(has_text=release_request.id).click()
    expect(content_alert).to_be_visible()
    expect(content_alert_link).not_to_be_visible()
    page.locator(".tree__folder-name").filter(has_text="group").click()
    expect(content_alert).to_be_visible()
    expect(content_alert_link).to_be_visible()
    page.locator(".tree__folder-name").filter(has_text="folder").click()
    expect(content_alert).to_be_visible()
    expect(content_alert_link).to_be_visible()
    page.locator(".tree__file").filter(has_text="file1").click()
    expect(content_alert).to_be_visible()
    expect(content_alert_link).to_be_visible()
