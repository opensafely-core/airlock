import re

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
            "workspaces": ["workspace"],
            "output_checker": False,
        },
    )

    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )
    factories.write_request_file(
        release_request,
        "group",
        "file1.txt",
        "file 1 content",
    )
    factories.write_request_file(
        release_request,
        "group",
        "file2.txt",
        "file 2 content",
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

    expect(file2_locator).not_to_have_class("withdrawn")

    page.locator("#withdraw-file-button").click()

    expect(file2_locator).to_have_class(re.compile("withdrawn"))


def test_request_group_edit_comment(live_server, context, page, bll, settings):
    settings.SHOW_C3 = True
    author = login_as_user(
        live_server,
        context,
        user_dict={
            "username": "author",
            "workspaces": ["workspace"],
            "output_checker": False,
        },
    )

    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )
    factories.write_request_file(
        release_request,
        "group",
        "file1.txt",
        "file 1 content",
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
    comment_locator = group_comment_locator.get_by_role("textbox", name="comment")

    comment_locator.fill("test comment")
    group_comment_locator.get_by_role("button", name="Comment").click()

    comments_locator = contents.locator(".comments")
    expect(comments_locator).to_contain_text("test comment")


def test_request_return(live_server, context, page, bll):
    author = login_as_user(
        live_server,
        context,
        user_dict={
            "username": "author",
            "workspaces": ["workspace"],
            "output_checker": False,
        },
    )

    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )
    factories.write_request_file(
        release_request,
        "group",
        "file1.txt",
        "file 1 content",
    )
    factories.write_request_file(
        release_request,
        "group",
        "file2.txt",
        "file 2 content",
    )
    bll.set_status(release_request, RequestStatus.SUBMITTED, author)

    return_request_button = page.locator("#return-request-button")
    page.goto(live_server.url + release_request.get_url())

    def _logout():
        # logout by clearing cookies
        context.clear_cookies()

    def _review_files(username):
        # logout current user, login as username
        _logout()
        login_as_user(
            live_server,
            context,
            user_dict={
                "username": username,
                "workspaces": [],
                "output_checker": True,
            },
        )
        page.goto(live_server.url + release_request.get_url())
        expect(return_request_button).to_be_disabled()

        page.goto(live_server.url + release_request.get_url("group/file1.txt"))
        page.locator("#file-approve-button").click()

        page.goto(live_server.url + release_request.get_url("group/file2.txt"))
        page.locator("#file-reject-button").click()

    # First output-checker reviews files
    _review_files("output-checker-1")

    # Return button is still disabled
    expect(return_request_button).to_be_disabled()

    # Second output-checker reviews files
    _review_files("output-checker-2")

    # Return button is now enabled
    expect(return_request_button).to_be_enabled()

    # Return the request
    return_request_button.click()

    # logout, login as author again
    _logout()
    login_as_user(
        live_server,
        context,
        user_dict={
            "username": "author",
            "workspaces": ["workspace"],
            "output_checker": False,
        },
    )
    page.goto(live_server.url + release_request.get_url())
    # Can re-submit a returned request
    page.locator("#submit-for-review-button").click()

    # logout, login as first output-checker
    _logout()
    login_as_user(
        live_server,
        context,
        user_dict={
            "username": "output-checker-1",
            "workspaces": [],
            "output_checker": True,
        },
    )

    status_locator = page.locator(".file-status")
    # go to previously approved file; still shown as approved
    page.goto(live_server.url + release_request.get_url("group/file1.txt"))
    expect(status_locator).to_contain_text("Approved")

    # go to previously rejected file; now shown as no-status
    page.goto(live_server.url + release_request.get_url("group/file2.txt"))
    expect(status_locator).to_contain_text("No status")


def test_request_releaseable(live_server, context, page, bll):
    release_request = factories.create_release_request(
        "workspace", status=RequestStatus.SUBMITTED
    )
    factories.write_request_file(
        release_request, "group", "file1.txt", "file 1 content", approved=True
    )
    release_request = factories.refresh_release_request(release_request)
    output_checker = login_as_user(
        live_server,
        context,
        user_dict={
            "username": "output_checker",
            "workspaces": [],
            "output_checker": True,
        },
    )

    page.goto(live_server.url + release_request.get_url())

    release_files_button = page.locator("#release-files-button")
    return_request_button = page.locator("#reject-request-button")
    reject_request_button = page.locator("#return-request-button")

    # Request is currently submitted and all files approved twice
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
