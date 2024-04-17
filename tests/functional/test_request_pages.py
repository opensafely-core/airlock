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


def test_request_group_edit_comment(live_server, context, page, bll):
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
