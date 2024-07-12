import pytest
from playwright.sync_api import expect

from airlock.business_logic import RequestStatus
from tests import factories
from tests.functional.conftest import login_as_user


def test_request_file_withdraw(live_server, context, page, bll):
    author = login_as_user(
        live_server,
        context,
        user_dict={
            "username": "author",
            "workspaces": {
                "workspace": {
                    "project_details": {"name": "Project 2", "ongoing": True},
                    "archived": False,
                }
            },
            "output_checker": False,
        },
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


def test_request_group_edit_comment(live_server, context, page, bll, settings):
    settings.SHOW_C3 = False  # context and controls visible, comments hidden
    author = login_as_user(
        live_server,
        context,
        user_dict={
            "username": "author",
            "workspaces": {
                "workspace": {
                    "project_details": {"name": "Project 2", "ongoing": True},
                    "archived": False,
                }
            },
            "output_checker": False,
        },
    )

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        files=[factories.request_file(group="group")],
        status=RequestStatus.SUBMITTED,
    )

    page.goto(live_server.url + release_request.get_url("group"))
    contents = page.locator("#selected-contents")

    group_edit_locator = contents.get_by_role("form", name="group-edit-form")
    context_locator = group_edit_locator.get_by_role("textbox", name="context")
    controls_locator = group_edit_locator.get_by_role("textbox", name="controls")

    context_locator.fill("test context")
    controls_locator.fill("test controls")

    group_edit_locator.get_by_role("button", name="Save").click()

    expect(context_locator).to_have_value("test context")
    expect(controls_locator).to_have_value("test controls")

    group_comment_locator = contents.get_by_role("form", name="group-comment-form")
    expect(group_comment_locator).not_to_be_visible()

    settings.SHOW_C3 = True
    page.reload()

    expect(group_comment_locator).to_be_visible()
    comment_locator = group_comment_locator.get_by_role("textbox", name="comment")

    comment_locator.fill("test comment")
    group_comment_locator.get_by_role("button", name="Comment").click()

    comments_locator = contents.locator(".comments")
    expect(comments_locator).to_contain_text("test comment")


def _workspace_dict():
    return {
        "workspace": {
            "project_details": {"name": "Project 2", "ongoing": True},
            "archived": False,
        }
    }


@pytest.mark.parametrize(
    "author,login_as,status,reviewer_buttons_visible,release_button_visible",
    [
        ("researcher", "researcher", RequestStatus.SUBMITTED, False, False),
        ("researcher", "checker", RequestStatus.SUBMITTED, True, True),
        ("checker", "checker", RequestStatus.SUBMITTED, False, False),
        ("researcher", "checker", RequestStatus.PARTIALLY_REVIEWED, True, True),
        ("checker", "checker", RequestStatus.PARTIALLY_REVIEWED, False, False),
        ("researcher", "checker", RequestStatus.REVIEWED, True, True),
        # APPROVED status - can be released, but other review buttons are hidden
        ("researcher", "checker", RequestStatus.APPROVED, False, True),
        ("researcher", "checker", RequestStatus.RELEASED, False, False),
        ("researcher", "checker", RequestStatus.REJECTED, False, False),
        ("researcher", "checker", RequestStatus.WITHDRAWN, False, False),
    ],
)
def test_request_buttons(
    live_server,
    context,
    page,
    bll,
    author,
    login_as,
    status,
    reviewer_buttons_visible,
    release_button_visible,
):
    user_data = {
        "researcher": dict(
            username="researcher", workspaces=_workspace_dict(), output_checker=False
        ),
        "checker": dict(
            username="checker", workspaces=_workspace_dict(), output_checker=True
        ),
    }

    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_user(**user_data[author]),
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
        "#return-request-button",
        "#reject-request-button",
        "#complete-review-button",
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


@pytest.mark.parametrize(
    "login_as,status,checkers, can_return",
    [
        ("author", RequestStatus.SUBMITTED, None, False),
        ("checker1", RequestStatus.PARTIALLY_REVIEWED, ["checker1"], False),
        ("checker2", RequestStatus.PARTIALLY_REVIEWED, ["checker1"], False),
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
    author = factories.create_user(**user_data["author"])
    if checkers is not None:
        checkers = [factories.create_user(**user_data[user]) for user in checkers]
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
                rejected=checkers is not None,
            ),
        ],
    )

    login_as_user(live_server, context, user_data[login_as])
    return_request_button = page.locator("#return-request-button")
    page.goto(live_server.url + release_request.get_url())

    if can_return:
        expect(return_request_button).to_be_enabled()
        return_request_button.click()
        expect(return_request_button).not_to_be_visible()
    elif login_as == "author":
        expect(return_request_button).not_to_be_visible()
    else:
        expect(return_request_button).to_be_disabled()


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
    author = factories.create_user(**user_data["author"])
    checkers = [
        factories.create_user(**user_data[user]) for user in ["checker1", "checker2"]
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
                group="group", path="file2.txt", checkers=checkers, rejected=True
            ),
        ],
    )

    # author resubmits
    login_as_user(live_server, context, user_data["author"])
    page.goto(live_server.url + release_request.get_url())
    # Can re-submit a returned request
    page.locator("#submit-for-review-button").click()

    # logout by clearing cookies
    context.clear_cookies()

    # checker looks at previously rejected/approved files
    login_as_user(live_server, context, user_data["checker1"])
    status_locator = page.locator(".file-status--approved")
    # go to previously approved file; still shown as approved
    page.goto(live_server.url + release_request.get_url("group/file1.txt"))
    expect(status_locator).to_contain_text("Approved")

    # go to previously rejected file; now shown as no-status
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
        user_dict={
            "username": "output_checker",
            "workspaces": {},
            "output_checker": True,
        },
    )

    page.goto(live_server.url + release_request.get_url())

    release_files_button = page.locator("#release-files-button")
    return_request_button = page.locator("#reject-request-button")
    reject_request_button = page.locator("#return-request-button")

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
