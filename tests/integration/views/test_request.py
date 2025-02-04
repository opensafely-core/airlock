from io import BytesIO

import pytest
import requests
from django.contrib.messages import get_messages
from django.template.response import TemplateResponse

from airlock import exceptions, permissions
from airlock.business_logic import bll
from airlock.enums import (
    AuditEventType,
    RequestFileType,
    RequestFileVote,
    RequestStatus,
    RequestStatusOwner,
    Visibility,
)
from airlock.types import UrlPath
from tests import factories
from tests.conftest import get_trace


pytestmark = pytest.mark.django_db


def get_messages_text(response):
    return "\n".join(m.message for m in get_messages(response.wsgi_request))


def test_request_index_no_user(airlock_client):
    release_request = factories.create_release_request("workspace")
    response = airlock_client.get(f"/requests/view/{release_request.id}/")
    assert response.status_code == 302


def test_request_view_index(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace", airlock_client.user)
    factories.add_request_file(release_request, "group", "file.txt")
    response = airlock_client.get(f"/requests/view/{release_request.id}/")
    assert "group" in response.rendered_content


def test_request_workspace_does_not_exist(airlock_client):
    airlock_client.login(output_checker=True)
    response = airlock_client.get("/requests/view/bad/id/")
    assert response.status_code == 404


def test_request_id_does_not_exist(airlock_client):
    airlock_client.login(output_checker=True)
    response = airlock_client.get("/requests/view/bad_id/")
    assert response.status_code == 404


def test_request_view_root_summary(airlock_client):
    airlock_client.login(output_checker=True)
    audit_user = factories.create_user("audit_user", workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        "workspace",
        author=audit_user,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file("group1", "some_dir/file1.txt"),
            factories.request_file(
                "group1",
                "some_dir/file2.txt",
                filetype=RequestFileType.SUPPORTING,
            ),
            factories.request_file("group2", "some_dir/file3.txt"),
            factories.request_file(
                "group2", "some_dir/file4.txt", filetype=RequestFileType.WITHDRAWN
            ),
        ],
    )

    response = airlock_client.get(f"/requests/view/{release_request.id}/")
    assert response.status_code == 200
    assert "PENDING" in response.rendered_content
    # output files
    assert ">\n      2\n    <" in response.rendered_content
    # supporting files
    assert ">\n      1\n    <" in response.rendered_content
    assert "Recent activity" in response.rendered_content
    assert "audit_user" in response.rendered_content


def test_request_view_root_group(airlock_client):
    airlock_client.login(output_checker=True)
    audit_user = factories.create_user("audit_user", workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        "workspace",
        author=audit_user,
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group1", "some_dir/file1.txt"),
            factories.request_file(
                "group1",
                "some_dir/file2.txt",
                filetype=RequestFileType.SUPPORTING,
            ),
        ],
    )

    bll.group_comment_create(
        release_request,
        group="group1",
        comment="private comment",
        visibility=Visibility.PRIVATE,
        user=airlock_client.user,
    )

    response = airlock_client.get(f"/requests/view/{release_request.id}/group1/")
    assert response.status_code == 200
    assert "Recent activity" in response.rendered_content
    assert "audit_user" in response.rendered_content
    assert "private comment" in response.rendered_content


def test_request_view_with_directory(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    factories.add_request_file(release_request, "group", "some_dir/file.txt")
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/some_dir/"
    )
    assert response.status_code == 200
    assert "file.txt" in response.rendered_content


def test_request_view_cannot_have_empty_directory(airlock_client):
    author = factories.create_user("author", workspaces=["workspace"])
    release_request = factories.create_release_request("workspace", author)
    factories.add_request_file(release_request, "group", "some_dir/file.txt")

    airlock_client.login(output_checker=True)
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/some_dir/"
    )
    assert response.status_code == 200

    # Withdrawing the only file from a directory removes the directory as well as
    # the file from the request
    release_request = factories.refresh_release_request(release_request)
    bll.withdraw_file_from_request(
        release_request, UrlPath("group/some_dir/file.txt"), author
    )
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/some_dir/"
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "filetype", [RequestFileType.OUTPUT, RequestFileType.SUPPORTING]
)
def test_request_view_with_file(airlock_client, filetype):
    release_request = factories.create_release_request("workspace")
    airlock_client.login(output_checker=True)
    release_request = factories.add_request_file(
        release_request, "group", "file.txt", "foobar", filetype=filetype
    )
    response = airlock_client.get(f"/requests/view/{release_request.id}/group/file.txt")
    assert response.status_code == 200
    assert (
        release_request.get_contents_url(UrlPath("group/file.txt"))
        in response.rendered_content
    )
    assert response.template_name == "file_browser/request/index.html"


def test_request_view_with_file_htmx(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    release_request = factories.add_request_file(
        release_request, "group", "file.txt", "foobar"
    )
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/file.txt",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert (
        release_request.get_contents_url(UrlPath("group/file.txt"))
        in response.rendered_content
    )
    assert response.template_name == "file_browser/contents.html"
    assert '<ul id="tree"' not in response.rendered_content


def test_request_view_with_submitted_request(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace", status=RequestStatus.SUBMITTED, files=[factories.request_file()]
    )
    response = airlock_client.get(f"/requests/view/{release_request.id}", follow=True)
    assert "Rejecting a request is disabled" in response.rendered_content
    assert "Releasing to jobs.opensafely.org is disabled" in response.rendered_content
    assert "Returning a request is disabled" in response.rendered_content
    assert "Submit review" in response.rendered_content


@pytest.mark.parametrize(
    "files,has_message",
    [
        ([factories.request_file()], False),
        (
            [
                factories.request_file(approved=True),
                factories.request_file(path="unapproved.txt"),
            ],
            False,
        ),
        ([factories.request_file(changes_requested=True)], True),
        (
            [
                factories.request_file(approved=True),
                factories.request_file(
                    path="supporting.txt", filetype=RequestFileType.SUPPORTING
                ),
            ],
            True,
        ),
    ],
)
def test_request_view_submit_review_alert(airlock_client, files, has_message):
    checker = factories.get_default_output_checkers()[0]
    airlock_client.login(username=checker.username, output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace", status=RequestStatus.SUBMITTED, files=files
    )

    response = airlock_client.get(f"/requests/view/{release_request.id}", follow=True)

    # The all-files-reviewed reminder message is only shown if the request has
    # output files and all have been reviewed
    assert (
        "You can now submit your review" in response.rendered_content
    ) == has_message

    # The all-files-reviewed reminder message is never shown to an author
    airlock_client.login(username=release_request.author, workspaces=["workspace"])
    response = airlock_client.get(f"/requests/view/{release_request.id}", follow=True)
    assert "You can now submit your review" not in response.rendered_content


@pytest.mark.parametrize(
    "request_status,author,login_as,files,has_message,can_release",
    [
        (
            # invalid status for message
            RequestStatus.PARTIALLY_REVIEWED,
            "researcher",
            "checker",
            [factories.request_file(approved=True)],
            False,
            False,
        ),
        (
            # reviewed and all approved, output-checker can return/reject/release
            RequestStatus.REVIEWED,
            "researcher",
            "checker",
            [factories.request_file(approved=True)],
            True,
            True,
        ),
        (
            # reviewed and not all approved, output-checker can return/reject
            RequestStatus.REVIEWED,
            "researcher",
            "checker",
            [
                factories.request_file(approved=True, path="foo.txt"),
                factories.request_file(changes_requested=True, path="bar.txt"),
            ],
            True,
            False,
        ),
        (
            # reviewed and all approved, output-checker is author
            RequestStatus.REVIEWED,
            "checker",
            "checker",
            [factories.request_file(approved=True)],
            False,
            False,
        ),
        (
            # reviewed and all approved, logged in as non output-checker
            RequestStatus.REVIEWED,
            "researcher",
            "researcher1",
            [factories.request_file(approved=True)],
            False,
            False,
        ),
        (
            # reviewed and all approved, logged in as author
            RequestStatus.REVIEWED,
            "researcher",
            "researcher",
            [factories.request_file(approved=True)],
            False,
            False,
        ),
    ],
)
def test_request_view_complete_turn_alert(
    airlock_client, request_status, author, login_as, files, has_message, can_release
):
    """
    Alert message shown when a request has two submitted reviews and
    can now be progressed by returning/rejecting/releasing
    """
    users = {
        "researcher": factories.create_user("researcher", workspaces=["workspace"]),
        "researcher1": factories.create_user("researcher", output_checker=False),
        "checker": factories.create_user(
            "checker", output_checker=True, workspaces=["workspace"]
        ),
    }
    airlock_client.login(username=users[login_as].username, output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace", author=users[author], status=request_status, files=files
    )

    response = airlock_client.get(f"/requests/view/{release_request.id}", follow=True)

    # The reminder message is only shown if the request in REVIEWED status
    # and the user is an output checker and not the author
    if has_message:
        if can_release:
            assert (
                "You can now return, reject or release this request"
                in response.rendered_content
            )
        else:
            assert "You can now return or reject" in response.rendered_content
    else:
        assert "You can now return" not in response.rendered_content


def test_request_view_with_reviewed_request(airlock_client):
    # Login as 1st default output-checker
    airlock_client.login(username="output-checker-0", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.REVIEWED,
        files=[factories.request_file(approved=True)],
    )
    response = airlock_client.get(f"/requests/view/{release_request.id}", follow=True)

    expected_buttons = [
        ("Reject request", "Rejecting a request is disabled"),
        ("Release files", "Releasing to jobs.opensafely.org is disabled"),
        ("Return request", "Returning a request is disabled"),
    ]

    for button_text, diabled_tooltip in expected_buttons:
        assert button_text in response.rendered_content
        assert diabled_tooltip not in response.rendered_content

    assert "Submit review" in response.rendered_content
    assert "You have already submitted your review" in response.rendered_content


@pytest.mark.parametrize("status", list(RequestStatus))
def test_request_view_with_authored_request_file(airlock_client, status):
    airlock_client.login(output_checker=True, workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        "workspace",
        author=airlock_client.user,
        status=status,
        files=[
            factories.request_file(
                "group", "file.txt", contents="foobar", approved=True
            ),
        ],
        withdrawn_after=RequestStatus.RETURNED
        if status == RequestStatus.WITHDRAWN
        else None,
    )
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/file.txt", follow=True
    )
    can_withdraw = permissions.STATUS_OWNERS[status] == RequestStatusOwner.AUTHOR
    assert ("Withdraw this file" in response.rendered_content) == can_withdraw


def test_request_view_with_submitted_file(airlock_client):
    airlock_client.login(username="checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", "file.txt", contents="foobar"),
        ],
    )
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/file.txt", follow=True
    )
    assert "Remove this file" not in response.rendered_content
    assert "Approve file" in response.rendered_content
    assert "Request changes" in response.rendered_content


def test_request_view_with_submitted_file_approved(airlock_client):
    airlock_client.login(username="checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", "file.txt", contents="foobar"),
        ],
    )
    airlock_client.post(f"/requests/approve/{release_request.id}/group/file.txt")
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/file.txt", follow=True
    )
    assert "Approve file" in response.rendered_content
    assert "Request changes" in response.rendered_content


def test_request_view_with_submitted_file_changes_requested(airlock_client):
    airlock_client.login(username="checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", "file.txt", contents="foobar"),
        ],
    )
    airlock_client.post(
        f"/requests/request_changes/{release_request.id}/group/file.txt"
    )
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/file.txt", follow=True
    )
    assert "Approve file" in response.rendered_content
    assert "Request changes" in response.rendered_content


def test_request_view_with_404(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/no_such_file.txt"
    )
    assert response.status_code == 404


def test_request_view_404_with_files(airlock_client):
    airlock_client.login(output_checker=True)
    # write a file and a supporting file to the group
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.PENDING,
        files=[
            factories.request_file("group", "file.txt"),
            factories.request_file(
                "group", "supporting_file.txt", filetype=RequestFileType.SUPPORTING
            ),
        ],
    )
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/no_such_file.txt"
    )
    assert response.status_code == 404


def test_request_view_redirects_to_directory(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    factories.add_request_file(release_request, "group", "some_dir/file.txt", "foobar")

    # test for group
    response = airlock_client.get(f"/requests/view/{release_request.id}/group")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/requests/view/{release_request.id}/group/"

    # test for dir
    response = airlock_client.get(f"/requests/view/{release_request.id}/group/some_dir")
    assert response.status_code == 302
    assert (
        response.headers["Location"]
        == f"/requests/view/{release_request.id}/group/some_dir/"
    )


def test_request_view_redirects_to_file(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    factories.add_request_file(release_request, "group", "file.txt")
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/file.txt/"
    )
    assert response.status_code == 302
    assert (
        response.headers["Location"]
        == f"/requests/view/{release_request.id}/group/file.txt"
    )


def test_request_contents_file(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    factories.add_request_file(release_request, "default", "file.txt", contents="test")
    response = airlock_client.get(
        f"/requests/content/{release_request.id}/default/file.txt"
    )
    assert response.status_code == 200
    assert response.content == b'<pre class="txt">\ntest\n</pre>\n'
    audit_log = bll.get_request_audit_log(
        user=airlock_client.user,
        request=release_request,
    )
    assert audit_log[0].type == AuditEventType.REQUEST_FILE_VIEW
    assert audit_log[0].path == UrlPath("default/file.txt")
    assert audit_log[0].extra["group"] == "default"


def test_request_contents_dir(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    factories.add_request_file(
        release_request, "default", "foo/file.txt", contents="test"
    )
    response = airlock_client.get(f"/requests/content/{release_request.id}/default/foo")
    assert response.status_code == 404


def test_request_contents_file_not_exists(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    factories.add_request_file(
        release_request, "default", "foo/file.txt", contents="test"
    )
    response = airlock_client.get(
        f"/requests/content/{release_request.id}/default/notexists.txt"
    )
    assert response.status_code == 404


def test_request_contents_group_not_exists(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    factories.add_request_file(
        release_request, "default", "foo/file.txt", contents="test"
    )
    response = airlock_client.get(f"/requests/content/{release_request.id}/notexist/")
    assert response.status_code == 404


def test_request_download_file(airlock_client):
    airlock_client.login(username="reviewer", output_checker=True)
    author = factories.create_user("author", ["workspace"])
    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(release_request, "default", "file.txt", contents="test")
    response = airlock_client.get(
        f"/requests/content/{release_request.id}/default/file.txt?download"
    )
    assert response.status_code == 200
    assert response.as_attachment
    assert list(response.streaming_content) == [b"test"]

    audit_log = bll.get_request_audit_log(
        user=airlock_client.user,
        request=release_request,
    )
    assert audit_log[0].type == AuditEventType.REQUEST_FILE_DOWNLOAD
    assert audit_log[0].path == UrlPath("default/file.txt")
    assert audit_log[0].extra["group"] == "default"


@pytest.mark.parametrize(
    "request_author,user,can_download",
    [
        (  # Different non-output-checker author and output-checker user
            {
                "username": "output-checker",
                "workspaces": ["workspace"],
                "output_checker": False,
            },
            {
                "username": "author",
                "workspaces": ["workspace"],
                "output_checker": True,
            },
            True,
        ),
        (  # Different output-checker author and output-checker user
            {
                "username": "output-checker",
                "workspaces": ["workspace"],
                "output_checker": True,
            },
            {
                "username": "author",
                "workspaces": ["workspace"],
                "output_checker": True,
            },
            True,
        ),
        (  # Different non-output-checker author and non-output-checker user
            {
                "username": "researcher",
                "workspaces": ["workspace"],
                "output_checker": False,
            },
            {
                "username": "author",
                "workspaces": ["workspace"],
                "output_checker": False,
            },
            False,
        ),
        (  # Same output-checker author and user
            {
                "username": "output-checker",
                "workspaces": ["workspace"],
                "output_checker": True,
            },
            {
                "username": "output-checker",
                "workspaces": ["workspace"],
                "output_checker": True,
            },
            False,
        ),
    ],
)
def test_request_download_file_permissions(
    airlock_client, request_author, user, can_download
):
    airlock_client.login(**user)
    author = factories.create_user(**request_author)
    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(
        release_request, "default", "file.txt", contents="test", user=author
    )
    response = airlock_client.get(
        f"/requests/content/{release_request.id}/default/file.txt?download"
    )
    if can_download:
        assert response.status_code == 200
        assert response.as_attachment
        assert list(response.streaming_content) == [b"test"]

        audit_log = bll.get_request_audit_log(
            user=airlock_client.user,
            request=release_request,
        )
        assert audit_log[0].type == AuditEventType.REQUEST_FILE_DOWNLOAD
    else:
        assert response.status_code == 403


def test_request_index_user_permitted_requests(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_release_request("test1", airlock_client.user)
    response = airlock_client.get("/requests/researcher")
    authored_ids = {r.id for r in response.context["authored_requests"]}

    assert authored_ids == {release_request.id}


# reviews page
def test_review_user_output_checker(airlock_client):
    airlock_client.login(workspaces=["test_workspace"], output_checker=True)
    other = factories.create_user(
        "other",
        workspaces=[
            "test_workspace",
            "other_workspace",
            "other_other_workspace",
            "other_other1_workspace",
        ],
    )
    r1 = factories.create_request_at_status(
        "other_workspace",
        author=other,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file()],
    )
    r2 = factories.create_request_at_status(
        "other_other_workspace",
        author=other,
        status=RequestStatus.RETURNED,
        files=[factories.request_file(changes_requested=True)],
    )
    r3 = factories.create_request_at_status(
        "other_other1_workspace",
        author=other,
        status=RequestStatus.APPROVED,
        files=[factories.request_file(approved=True)],
    )
    response = airlock_client.get("/requests/output_checker")

    outstanding_ids = {r[0].id for r in response.context["outstanding_requests"]}
    returned_ids = {r.id for r in response.context["returned_requests"]}
    approved_ids = {r.id for r in response.context["approved_requests"]}

    assert outstanding_ids == {r1.id}
    assert returned_ids == {r2.id}
    assert approved_ids == {r3.id}


# To confirm that the request page displays for an output checker
def test_request_index_user_output_checker(airlock_client):
    airlock_client.login(workspaces=["test_workspace"], output_checker=True)
    r1 = factories.create_request_at_status(
        "test_workspace",
        author=airlock_client.user,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file()],
    )

    response = airlock_client.get("/requests/researcher")

    authored_ids = {r.id for r in response.context["authored_requests"]}

    assert authored_ids == {r1.id}


def test_no_outstanding_request_output_checker(airlock_client):
    airlock_client.login(workspaces=["test_workspace"], output_checker=True)
    response = airlock_client.get("/requests/output_checker")
    outstanding_ids = {r[0].id for r in response.context["outstanding_requests"]}
    assert len(outstanding_ids) == 0


def test_request_index_user_request_progress(airlock_client):
    airlock_client.login(workspaces=["test_workspace"], output_checker=True)
    other = factories.create_user(
        "other",
        workspaces=[
            "other_workspace",
            "other1_workspace",
            "other2_workspace",
            "other3_workspace",
            "other4_workspace",
        ],
    )
    default_checkers = factories.get_default_output_checkers()

    def generate_files(reviewed=False, checkers_a=None, checkers_b=None):
        if checkers_a is None:
            checkers_a = default_checkers
        if checkers_b is None:
            checkers_b = default_checkers
        return [
            factories.request_file(
                path="afile.txt", contents="a", approved=reviewed, checkers=checkers_a
            ),
            factories.request_file(
                path="bfile.txt", contents="b", approved=reviewed, checkers=checkers_b
            ),
        ]

    # submitted, no-one has reviewed
    r0 = factories.create_request_at_status(
        "test_workspace",
        status=RequestStatus.SUBMITTED,
        files=generate_files(),
    )
    # submitted, all files reviewed (but review not submitted)
    r1 = factories.create_request_at_status(
        "test_workspace1",
        status=RequestStatus.SUBMITTED,
        files=generate_files(
            reviewed=True,
            checkers_a=[airlock_client.user],
            checkers_b=[airlock_client.user],
        ),
    )
    # submitted review by other checker, making the request partially reviewed.
    # no files reviewed by the user.
    r2 = factories.create_request_at_status(
        "other_workspace",
        status=RequestStatus.PARTIALLY_REVIEWED,
        files=generate_files(reviewed=True),
    )
    # submitted review by other checker, making the request partially reviewed.
    # some files reviewed by the user.
    r3 = factories.create_request_at_status(
        "other1_workspace",
        status=RequestStatus.PARTIALLY_REVIEWED,
        files=generate_files(
            reviewed=True,
            checkers_a=[default_checkers[0]],
            checkers_b=[airlock_client.user, default_checkers[0]],
        ),
    )
    # review submitted by the user, making the request partially reviewed
    r4 = factories.create_request_at_status(
        "other2_workspace",
        author=other,
        status=RequestStatus.PARTIALLY_REVIEWED,
        files=generate_files(
            reviewed=True,
            checkers_a=[airlock_client.user],
            checkers_b=[airlock_client.user, default_checkers[0]],
        ),
    )
    # fully reviewed by other checkers
    r5 = factories.create_request_at_status(
        "other3_workspace",
        author=other,
        status=RequestStatus.REVIEWED,
        files=generate_files(reviewed=True),
    )
    # fully reviewed by user & one other checker
    r6_checkers = [airlock_client.user, default_checkers[0]]
    r6 = factories.create_request_at_status(
        "other4_workspace",
        author=other,
        status=RequestStatus.REVIEWED,
        files=generate_files(
            reviewed=True, checkers_a=r6_checkers, checkers_b=r6_checkers
        ),
    )

    response = airlock_client.get("/requests/output_checker")
    assert response.context["outstanding_requests"] == [
        (r0, "Your review: 0/2 files (incomplete)"),
        (r1, "Your review: 2/2 files (incomplete)"),
        (r2, "Your review: 0/2 files (incomplete)"),
        (r3, "Your review: 1/2 files (incomplete)"),
        (r4, "Your review: 2/2 files"),
        (r5, "Your review: 0/2 files (incomplete)"),
        (r6, "Your review: 2/2 files"),
    ]


def test_request_submit_author(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_release_request(
        "test1", user=airlock_client.user
    )
    factories.add_request_file(release_request, "group", "path/test.txt")
    bll.group_edit(
        release_request, "group", "my context", "my controls", airlock_client.user
    )

    response = airlock_client.post(f"/requests/submit/{release_request.id}")

    assert response.status_code == 302
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.SUBMITTED


def test_request_submit_not_author(airlock_client):
    airlock_client.login(workspaces=["test1"])
    other_author = factories.create_user("other", ["test1"], False)
    release_request = factories.create_release_request(
        "test1", user=other_author, status=RequestStatus.PENDING
    )
    factories.add_request_file(release_request, "group", "path/test.txt")
    bll.group_edit(release_request, "group", "my context", "my controls", other_author)

    response = airlock_client.post(f"/requests/submit/{release_request.id}")

    assert response.status_code == 403
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.PENDING


def test_request_submit_missing_context_controls(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_release_request(
        "test1", user=airlock_client.user
    )
    factories.add_request_file(release_request, "group", "path/test.txt")

    response = airlock_client.post(f"/requests/submit/{release_request.id}")

    assert response.status_code == 302
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    # request has not been submitted
    assert persisted_request.status == RequestStatus.PENDING


def test_request_withdraw_author(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_release_request(
        "test1", user=airlock_client.user
    )
    factories.add_request_file(release_request, "group", "path/test.txt")

    response = airlock_client.post(f"/requests/withdraw/{release_request.id}")

    assert response.status_code == 302
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.WITHDRAWN


def test_request_withdraw_not_author(airlock_client):
    airlock_client.login(workspaces=["test1"])
    other_author = factories.create_user("other", ["test1"], False)
    release_request = factories.create_release_request(
        "test1", user=other_author, status=RequestStatus.PENDING
    )
    factories.add_request_file(release_request, "group", "path/test.txt")

    response = airlock_client.post(f"/requests/withdraw/{release_request.id}")

    assert response.status_code == 403
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.PENDING


def test_request_return_author(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_request_at_status(
        "test1",
        author=airlock_client.user,
        status=RequestStatus.REVIEWED,
        files=[
            factories.request_file("group", "path/test.txt", approved=True),
            factories.request_file("group", "path/test1.txt", changes_requested=True),
        ],
    )

    response = airlock_client.post(f"/requests/return/{release_request.id}")

    assert response.status_code == 403
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.REVIEWED


def test_request_return_output_checker(airlock_client):
    airlock_client.login(workspaces=["test1"], output_checker=True)
    other_author = factories.create_user("other", ["test1"], False)
    release_request = factories.create_request_at_status(
        "test1",
        author=other_author,
        status=RequestStatus.REVIEWED,
        files=[
            factories.request_file("group", "path/test.txt", approved=True),
            factories.request_file("group", "path/test1.txt", changes_requested=True),
        ],
    )
    response = airlock_client.post(f"/requests/return/{release_request.id}")

    assert response.status_code == 302
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.RETURNED


def test_request_review_author(airlock_client):
    airlock_client.login(workspaces=["test1"], output_checker=True)
    release_request = factories.create_request_at_status(
        "test1",
        author=airlock_client.user,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(approved=True)],
    )
    response = airlock_client.post(f"/requests/review/{release_request.id}")

    assert response.status_code == 403
    persisted_request = factories.refresh_release_request(release_request)
    assert persisted_request.status == RequestStatus.SUBMITTED


def test_request_review_output_checker(airlock_client):
    airlock_client.login(username="checker", workspaces=["test1"], output_checker=True)
    release_request = factories.create_request_at_status(
        "test1",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(approved=True, checkers=[airlock_client.user])],
    )

    response = airlock_client.get(release_request.get_url())
    # Files have been reviewed but review has not been submitted yet
    assert "You can now submit your review" in response.rendered_content

    response = airlock_client.post(
        f"/requests/review/{release_request.id}", follow=True
    )

    assert response.status_code == 200
    persisted_request = factories.refresh_release_request(release_request)
    assert persisted_request.status == RequestStatus.PARTIALLY_REVIEWED
    assert (
        "Your review has been submitted"
        in list(response.context["messages"])[0].message
    )

    response = airlock_client.get(release_request.get_url())
    # Reminder message no longer shown now that review is submitted
    assert "You can now submit your review" not in response.rendered_content


def test_request_review_non_output_checker(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_request_at_status(
        "test1",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(approved=True)],
    )
    response = airlock_client.post(f"/requests/review/{release_request.id}")

    assert response.status_code == 403
    persisted_request = factories.refresh_release_request(release_request)
    assert persisted_request.status == RequestStatus.SUBMITTED


def test_request_review_not_all_files_reviewed(airlock_client):
    airlock_client.login(username="checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "test1",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(approved=True, checkers=[airlock_client.user]),
            factories.request_file(path="foo.txt"),
        ],
    )
    response = airlock_client.post(
        f"/requests/review/{release_request.id}", follow=True
    )

    assert response.status_code == 200
    persisted_request = factories.refresh_release_request(release_request)
    assert persisted_request.status == RequestStatus.SUBMITTED
    assert (
        "You must review all files to submit your review" in response.rendered_content
    )


def test_empty_requests_for_workspace(airlock_client):
    airlock_client.login(workspaces=["test1"])

    response = airlock_client.get("/requests/workspace/test1")

    response.render()
    assert response.status_code == 200
    assert "There are no requests in this workspace" in response.rendered_content


def test_requests_for_workspace(airlock_client):
    airlock_client.login(workspaces=["test1"])
    author1 = factories.create_user("author1", ["test1"], False)
    author2 = factories.create_user("author2", ["test1"], False)

    release_request1 = factories.create_release_request(
        "test1", user=author1, status=RequestStatus.PENDING
    )
    factories.add_request_file(release_request1, "group", "path/test.txt")

    release_request2 = factories.create_release_request(
        "test1", user=author2, status=RequestStatus.PENDING
    )
    factories.add_request_file(release_request2, "group", "path/test2.txt")

    response = airlock_client.post("/requests/workspace/test1")

    response.render()
    assert response.status_code == 200
    assert "All requests in workspace test1" in response.rendered_content
    assert "PENDING" in response.rendered_content
    assert author1.username in response.rendered_content
    assert author2.username in response.rendered_content


@pytest.mark.parametrize("review", [("approve"), ("request_changes"), ("reset_review")])
def test_file_review_bad_user(airlock_client, review):
    workspace = "test1"
    airlock_client.login(workspaces=[workspace], output_checker=False)
    author = factories.create_user("author", [workspace], False)
    path = "path/test.txt"
    release_request = factories.create_request_at_status(
        "test1",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", path),
        ],
    )

    response = airlock_client.post(
        f"/requests/{review}/{release_request.id}/group/{path}"
    )
    assert response.status_code == 403
    relpath = UrlPath(path)
    assert (
        len(
            bll.get_release_request(release_request.id, author)
            .filegroups["group"]
            .files[relpath]
            .reviews
        )
        == 0
    )


@pytest.mark.parametrize("review", [("approve"), ("request_changes"), ("reset_review")])
def test_file_review_bad_file(airlock_client, review):
    airlock_client.login(output_checker=True)
    author = factories.create_user("author", ["test1"], False)
    path = "path/test.txt"
    release_request = factories.create_request_at_status(
        "test1",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", path),
        ],
    )

    bad_path = "path/bad.txt"
    response = airlock_client.post(
        f"/requests/{review}/{release_request.id}/group/{bad_path}"
    )
    assert response.status_code == 404
    relpath = UrlPath(path)
    assert (
        len(
            bll.get_release_request(release_request.id, author)
            .filegroups["group"]
            .files[relpath]
            .reviews
        )
        == 0
    )


def test_file_approve(airlock_client):
    airlock_client.login(output_checker=True)
    author = factories.create_user("author", ["test1"], False)
    path = "path/test.txt"
    release_request = factories.create_request_at_status(
        "test1",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", path),
        ],
    )

    response = airlock_client.post(
        f"/requests/approve/{release_request.id}/group/{path}"
    )
    assert response.status_code == 302
    relpath = UrlPath(path)
    review = (
        bll.get_release_request(release_request.id, author)
        .get_request_file_from_output_path(relpath)
        .reviews[airlock_client.user.username]
    )
    assert review.status == RequestFileVote.APPROVED
    assert review.reviewer == "testuser"


def test_file_request_changes(airlock_client):
    airlock_client.login(output_checker=True)
    author = factories.create_user("author", ["test1"], False)
    path = "path/test.txt"
    release_request = factories.create_request_at_status(
        "test1",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", path),
        ],
    )

    response = airlock_client.post(
        f"/requests/request_changes/{release_request.id}/group/{path}"
    )
    assert response.status_code == 302
    relpath = UrlPath(path)
    review = (
        bll.get_release_request(release_request.id, author)
        .get_request_file_from_output_path(relpath)
        .reviews[airlock_client.user.username]
    )
    assert review.status == RequestFileVote.CHANGES_REQUESTED
    assert review.reviewer == "testuser"


def test_file_reset_review(airlock_client):
    airlock_client.login(output_checker=True)
    author = factories.create_user("author", ["test1"], False)
    path = "path/test.txt"
    release_request = factories.create_request_at_status(
        "test1",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", path),
        ],
    )
    # first request changes to a file
    response = airlock_client.post(
        f"/requests/request_changes/{release_request.id}/group/{path}"
    )
    assert response.status_code == 302
    relpath = UrlPath(path)
    release_request = factories.refresh_release_request(release_request)
    review = release_request.get_request_file_from_output_path(relpath).reviews[
        airlock_client.user.username
    ]
    assert review.status == RequestFileVote.CHANGES_REQUESTED
    assert review.reviewer == "testuser"

    # then reset it to have no review
    response = airlock_client.post(
        f"/requests/reset_review/{release_request.id}/group/{path}"
    )
    assert response.status_code == 302
    relpath = UrlPath(path)
    release_request = factories.refresh_release_request(release_request)
    reviews = release_request.filegroups["group"].files[relpath].reviews
    assert len(reviews) == 0

    # verify a re-request
    response = airlock_client.post(
        f"/requests/reset_review/{release_request.id}/group/{path}"
    )
    assert response.status_code == 404


def test_request_reject_output_checker(airlock_client):
    airlock_client.login(output_checker=True)
    author = factories.create_user("author", ["test1"], False)
    release_request = factories.create_request_at_status(
        "test1",
        author=author,
        status=RequestStatus.REVIEWED,
        files=[
            factories.request_file(changes_requested=True),
        ],
    )
    response = airlock_client.post(f"/requests/reject/{release_request.id}")

    assert response.status_code == 302
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.REJECTED


def test_request_reject_not_output_checker(airlock_client):
    release_request = factories.create_request_at_status(
        "test1",
        author=factories.create_user("author1", workspaces=["test1"]),
        status=RequestStatus.REVIEWED,
        files=[
            factories.request_file(changes_requested=True),
        ],
    )
    airlock_client.login(workspaces=[release_request.workspace], output_checker=False)
    response = airlock_client.post(f"/requests/reject/{release_request.id}")

    assert response.status_code == 403
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.REVIEWED


def test_file_withdraw_file_pending(airlock_client):
    airlock_client.login(username="author", workspaces=["test1"], output_checker=False)

    release_request = factories.create_request_at_status(
        "test1",
        author=airlock_client.user,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file("group", "path/test.txt"),
        ],
    )

    # ensure it does exist
    release_request.get_request_file_from_urlpath("group/path/test.txt")

    response = airlock_client.post(
        f"/requests/withdraw/{release_request.id}/group/path/test.txt",
    )
    assert response.status_code == 302
    assert response.headers["location"] == release_request.get_url("group")

    persisted_request = factories.refresh_release_request(release_request)

    with pytest.raises(exceptions.FileNotFound):
        persisted_request.get_request_file_from_urlpath("group/path/test.txt")


def test_file_withdraw_file_submitted(airlock_client):
    airlock_client.login(username="author", workspaces=["test1"], output_checker=False)
    release_request = factories.create_request_at_status(
        "test1",
        author=airlock_client.user,
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", "path/test.txt", changes_requested=True),
        ],
    )
    # ensure it does exist
    release_request.get_request_file_from_urlpath("group/path/test.txt")

    response = airlock_client.post(
        f"/requests/withdraw/{release_request.id}/group/path/test.txt",
        follow=True,
    )
    assert response.status_code == 403


def test_file_withdraw_file_returned(airlock_client):
    airlock_client.login(username="author", workspaces=["test1"], output_checker=False)
    release_request = factories.create_request_at_status(
        "test1",
        author=airlock_client.user,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file("group", "path/test.txt", changes_requested=True),
        ],
    )
    # ensure it does exist
    release_request.get_request_file_from_urlpath("group/path/test.txt")

    response = airlock_client.post(
        f"/requests/withdraw/{release_request.id}/group/path/test.txt",
        follow=True,
    )
    # ensure template is rendered to force template coverage
    response.render()
    assert response.status_code == 200
    assert "This file has been withdrawn" in response.rendered_content

    persisted_request = factories.refresh_release_request(release_request)
    request_file = persisted_request.get_request_file_from_urlpath(
        "group/path/test.txt"
    )
    assert request_file.filetype == RequestFileType.WITHDRAWN


def test_file_withdraw_file_bad_file(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_release_request(
        "test1",
        user=airlock_client.user,
    )

    response = airlock_client.post(
        f"/requests/withdraw/{release_request.id}/group/bad/path.txt",
    )
    assert response.status_code == 404


def test_file_withdraw_file_not_author(airlock_client):
    release_request = factories.create_release_request(
        "test1",
    )
    factories.add_request_file(release_request, "group", "path/test.txt")

    airlock_client.login(workspaces=["test1"])
    response = airlock_client.post(
        f"/requests/withdraw/{release_request.id}/group/path/test.txt",
    )
    assert response.status_code == 403


def test_file_withdraw_file_bad_request(airlock_client):
    airlock_client.login(workspaces=["test1"])

    response = airlock_client.post(
        "/requests/withdraw/bad_id/group/path/test.txt",
    )
    assert response.status_code == 404


def test_request_multiselect_withdraw_files(airlock_client):
    user = factories.create_user(workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        list(user.workspaces)[0],
        author=user,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(group="group", path="file1.txt", approved=True),
            factories.request_file(group="group", path="file2.txt", approved=True),
        ],
    )

    airlock_client.login_with_user(user)
    response = airlock_client.post(
        f"/requests/multiselect/{release_request.id}",
        data={
            "action": "withdraw_files",
            "selected": [
                "group/file1.txt",
                "group/file2.txt",
            ],
            "next_url": release_request.get_url(),
        },
    )
    persisted_request = factories.refresh_release_request(release_request)
    messages = get_messages_text(response)

    for path in ["group/file1.txt", "group/file2.txt"]:
        request_file = persisted_request.get_request_file_from_urlpath(path)
        assert request_file.filetype == RequestFileType.WITHDRAWN
        assert f"{path} has been withdrawn from the request" in messages


def test_request_multiselect_withdraw_files_not_permitted(airlock_client):
    user = factories.create_user(workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        list(user.workspaces)[0],
        author=user,
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(group="group", path="file1.txt", approved=True),
            factories.request_file(group="group", path="file2.txt", approved=True),
        ],
    )

    airlock_client.login_with_user(user)
    response = airlock_client.post(
        f"/requests/multiselect/{release_request.id}",
        data={
            "action": "withdraw_files",
            "selected": [
                "group/file1.txt",
                "group/file2.txt",
            ],
            "next_url": release_request.get_url(),
        },
    )
    persisted_request = factories.refresh_release_request(release_request)
    messages = get_messages_text(response)
    assert "Cannot withdraw file" in messages

    for path in ["group/file1.txt", "group/file2.txt"]:
        request_file = persisted_request.get_request_file_from_urlpath(path)
        assert request_file.filetype != RequestFileType.WITHDRAWN


def test_request_multiselect_withdraw_files_none_selected(airlock_client):
    user = factories.create_user(workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        list(user.workspaces)[0],
        author=user,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(group="group", path="file1.txt", approved=True),
        ],
    )

    airlock_client.login_with_user(user)
    response = airlock_client.post(
        f"/requests/multiselect/{release_request.id}",
        data={
            "action": "withdraw_files",
            "selected": [],
            "next_url": release_request.get_url(),
        },
    )
    assert "You must select at least one file" in get_messages_text(response)


def test_request_multiselect_withdraw_files_invalid_action(airlock_client):
    user = factories.create_user(workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        list(user.workspaces)[0],
        author=user,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(group="group", path="file1.txt", approved=True),
        ],
    )

    airlock_client.login_with_user(user)
    response = airlock_client.post(
        f"/requests/multiselect/{release_request.id}",
        data={
            "action": "prorogate_files",
            "selected": ["group/file1.txt"],
            "next_url": release_request.get_url(),
        },
    )
    assert response.status_code == 404


@pytest.mark.parametrize("status", [RequestStatus.REVIEWED, RequestStatus.APPROVED])
def test_request_release_files_success(airlock_client, release_files_stubber, status):
    airlock_client.login(username="checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=status,
        files=[
            factories.request_file(path="file.txt", contents="test1", approved=True),
            factories.request_file(path="file1.txt", contents="test2", approved=True),
        ],
    )

    release_files_stubber(release_request)

    response = airlock_client.post(f"/requests/release/{release_request.id}")
    assert response.url == f"/requests/view/{release_request.id}/"
    assert response.status_code == 302


@pytest.mark.parametrize("status", [RequestStatus.REVIEWED, RequestStatus.APPROVED])
def test_request_release_files_success_htmx(
    airlock_client, release_files_stubber, status
):
    airlock_client.login(username="checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=status,
        files=[
            factories.request_file(path="file.txt", contents="test1", approved=True),
            factories.request_file(path="file1.txt", contents="test2", approved=True),
        ],
    )
    release_files_stubber(release_request)
    response = airlock_client.post(
        f"/requests/release/{release_request.id}",
        headers={"HX-Request": "true"},
    )

    assert response.headers["HX-Redirect"] == f"/requests/view/{release_request.id}/"
    assert response.status_code == 200


def test_requests_release_workspace_403(airlock_client):
    airlock_client.login(username="checker", workspaces=[], output_checker=False)

    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", "path/test.txt", approved=True),
        ],
    )

    response = airlock_client.post(f"/requests/release/{release_request.id}")
    assert response.status_code == 403


def test_requests_release_author_403(airlock_client):
    airlock_client.login(workspaces=["workspace"], output_checker=True)

    release_request = factories.create_request_at_status(
        "workspace",
        author=airlock_client.user,
        status=RequestStatus.REVIEWED,
        files=[factories.request_file(approved=True)],
    )
    response = airlock_client.post(
        f"/requests/release/{release_request.id}", follow=True
    )
    assert response.status_code == 200
    assert (
        list(response.context["messages"])[0].message
        == "Error releasing files: Can not set your own request to APPROVED"
    )


def test_requests_release_invalid_state_transition_403(airlock_client):
    airlock_client.login(username="checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.RETURNED,
        files=[factories.request_file(approved=True)],
    )
    response = airlock_client.post(
        f"/requests/release/{release_request.id}", follow=True
    )
    assert response.status_code == 200
    assert (
        list(response.context["messages"])[0].message
        == "Error releasing files: cannot change status from RETURNED to APPROVED"
    )


def test_requests_release_jobserver_403(airlock_client, release_files_stubber):
    airlock_client.login(username="checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.REVIEWED,
        files=[factories.request_file(approved=True)],
    )

    response = requests.Response()
    response.status_code = 403
    api403 = requests.HTTPError(response=response)
    release_files_stubber(release_request, body=api403)

    # test 403 is handled
    response = airlock_client.post(
        f"/requests/release/{release_request.id}", follow=True
    )
    assert response.status_code == 200
    assert isinstance(response, TemplateResponse)
    assert (
        list(response.context["messages"])[0].message
        == "Error releasing files: Permission denied"
    )


@pytest.mark.parametrize(
    "content_type,content",
    [
        ("text/plain", b"An error from job-server"),
        ("text/html", b"<p>An error from job-server</p>"),
        ("application/json", b'{"detail": "An error from job-server"}'),
    ],
)
def test_requests_release_jobserver_403_with_debug(
    airlock_client,
    release_files_stubber,
    settings,
    content_type,
    content,
):
    airlock_client.login(username="checker", output_checker=True)
    settings.DEBUG = True
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.REVIEWED,
        files=[factories.request_file(approved=True)],
    )

    response = requests.Response()
    response.status_code = 403
    response.headers = requests.structures.CaseInsensitiveDict()
    response.headers["Content-Type"] = content_type
    response.raw = BytesIO(content)
    api403 = requests.HTTPError(response=response)
    release_files_stubber(release_request, body=api403)

    # test 403 is handled
    response = airlock_client.post(
        f"/requests/release/{release_request.id}", follow=True
    )
    # DEBUG is on, so we return the job-server error
    assert response.status_code == 200
    assert isinstance(response, TemplateResponse)
    error_message = list(response.context["messages"])[0].message
    assert "An error from job-server" in error_message
    assert f"Type: {content_type}" in error_message


def test_requests_release_files_404(airlock_client, release_files_stubber):
    airlock_client.login(username="checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.REVIEWED,
        files=[factories.request_file(approved=True)],
    )

    response = requests.Response()
    response.status_code = 404
    api404 = requests.HTTPError(response=response)
    release_files_stubber(release_request, body=api404)

    response = airlock_client.post(
        f"/requests/release/{release_request.id}", follow=True
    )
    assert response.status_code == 200
    assert isinstance(response, TemplateResponse)
    assert (
        list(response.context["messages"])[0].message
        == "Error releasing files; please contact tech-support."
    )


@pytest.mark.parametrize(
    "urlpath,post_data,login_as,status,stub",
    [
        (
            "/requests/view/{request.id}/default/",
            None,
            "output_checker",
            RequestStatus.PENDING,
            False,
        ),
        (
            "/requests/view/{request.id}/default/file.txt",
            None,
            "output_checker",
            RequestStatus.PENDING,
            False,
        ),
        (
            "/requests/content/{request.id}/default/file.txt",
            None,
            "output_checker",
            RequestStatus.PENDING,
            False,
        ),
        ("/requests/submit/{request.id}", {}, "author", RequestStatus.PENDING, False),
        (
            "/requests/reject/{request.id}",
            {},
            "output_checker",
            RequestStatus.REVIEWED,
            False,
        ),
        (
            "/requests/release/{request.id}",
            {},
            "output_checker",
            RequestStatus.REVIEWED,
            True,
        ),
    ],
)
def test_request_view_tracing_with_request_attribute(
    airlock_client, release_files_stubber, urlpath, post_data, login_as, status, stub
):
    author = factories.create_user("author", ["test-workspace"])
    checker = factories.create_user("output_checker", output_checker=True)
    airlock_client.login(username=login_as, output_checker=True)

    release_request = factories.create_request_at_status(
        "test-workspace",
        status=status,
        author=author,
        files=[
            factories.request_file(
                "default",
                "file.txt",
                approved=status in [RequestStatus.REVIEWED, RequestStatus.RELEASED],
                checkers=[checker, factories.create_user(output_checker=True)],
            ),
        ],
    )

    # inject request id
    url = urlpath.format(request=release_request)

    if stub:
        release_files_stubber(release_request)

    if post_data is not None:
        airlock_client.post(url, post_data)
    else:
        airlock_client.get(url)
    traces = get_trace()
    last_trace = traces[-1]
    assert last_trace.attributes == {
        "release_request": release_request.id,
        "user": login_as,
    }


def test_group_edit_success(airlock_client):
    airlock_client.login(
        username="author", workspaces=["workspace"], output_checker=False
    )

    release_request = factories.create_release_request(
        "workspace", user=airlock_client.user
    )
    factories.add_request_file(release_request, "group", "file.txt")

    response = airlock_client.post(
        f"/requests/edit/{release_request.id}/group",
        data={
            "context": "foo",
            "controls": "bar",
        },
        follow=True,
    )

    assert response.status_code == 200
    messages = list(response.context.get("messages", []))
    assert messages[0].message == "Updated group group"

    release_request = factories.refresh_release_request(release_request)

    assert release_request.filegroups["group"].context == "foo"
    assert release_request.filegroups["group"].controls == "bar"


def test_group_edit_no_change(airlock_client, bll):
    airlock_client.login(
        username="author", workspaces=["workspace"], output_checker=False
    )

    release_request = factories.create_release_request(
        "workspace", user=airlock_client.user
    )
    factories.add_request_file(release_request, "group", "file.txt")
    bll.group_edit(
        release_request,
        "group",
        context="foo",
        controls="bar",
        user=airlock_client.user,
    )

    response = airlock_client.post(
        f"/requests/edit/{release_request.id}/group",
        data={
            "context": "foo",
            "controls": "bar",
        },
        follow=True,
    )

    assert response.status_code == 200
    messages = list(response.context.get("messages", []))
    assert messages[0].message == "No changes made to group group"

    release_request = factories.refresh_release_request(release_request)

    assert release_request.filegroups["group"].context == "foo"
    assert release_request.filegroups["group"].controls == "bar"


def test_group_edit_bad_user(airlock_client):
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(other)

    response = airlock_client.post(
        f"/requests/edit/{release_request.id}/group",
        data={
            "context": "foo",
            "controls": "bar",
        },
        follow=True,
    )

    assert response.status_code == 403


def test_group_edit_bad_group(airlock_client):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(author)

    response = airlock_client.post(
        f"/requests/edit/{release_request.id}/badgroup",
        data={
            "context": "foo",
            "controls": "bar",
        },
        follow=True,
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "output_checker,visibility,allowed",
    [
        (False, Visibility.PUBLIC, True),
        (False, Visibility.PRIVATE, False),
        (True, Visibility.PUBLIC, True),
        (True, Visibility.PRIVATE, True),
    ],
)
def test_group_comment_create_success(
    airlock_client, output_checker, visibility, allowed
):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(release_request, "group", "file.txt")

    user = factories.create_user(
        output_checker=output_checker, workspaces=["workspace"]
    )
    airlock_client.login_with_user(user)

    response = airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={"comment": "opinion", "visibility": visibility.name},
        follow=True,
    )
    # ensure templates covered
    assert response.rendered_content

    assert response.status_code == 200
    messages = list(response.context.get("messages", []))

    if allowed:
        assert "Comment added" in messages[0].message
        release_request = bll.get_release_request(release_request.id, author)
        assert release_request.filegroups["group"].comments[0].comment == "opinion"
        assert release_request.filegroups["group"].comments[0].author == user.username
    else:
        assert "visibility: Select a valid choice" in messages[0].message


def test_group_comment_create_bad_user(airlock_client):
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["other"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(other)

    response = airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={"comment": "comment", "visibility": "PUBLIC"},
        follow=True,
    )

    assert response.status_code == 403


def test_group_comment_create_bad_form(airlock_client):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(author)

    response = airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={},
        follow=True,
    )

    assert response.status_code == 200
    messages = list(response.context.get("messages", []))
    assert "comment: This field is required" in messages[0].message
    assert "visibility: This field is required" in messages[0].message


def test_group_comment_create_bad_group(airlock_client):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(author)

    response = airlock_client.post(
        f"/requests/comment/create/{release_request.id}/badgroup",
        data={"comment": "comment", "visibility": "PUBLIC"},
        follow=True,
    )

    assert response.status_code == 404


def test_group_comment_delete(airlock_client):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(author)
    airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={"comment": "typo comment", "visibility": "PUBLIC"},
        follow=True,
    )

    airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={"comment": "not-a-typo comment", "visibility": "PUBLIC"},
        follow=True,
    )

    release_request = bll.get_release_request(release_request.id, author)
    assert len(release_request.filegroups["group"].comments) == 2
    bad_comment = release_request.filegroups["group"].comments[0]
    good_comment = release_request.filegroups["group"].comments[1]

    response = airlock_client.post(
        f"/requests/comment/delete/{release_request.id}/group",
        data={"comment_id": bad_comment.id},
        follow=True,
    )

    assert response.status_code == 200
    release_request = bll.get_release_request(release_request.id, author)
    assert len(release_request.filegroups["group"].comments) == 1
    assert release_request.filegroups["group"].comments[0].id == good_comment.id


def test_group_comment_visibility_public(airlock_client):
    checker = factories.create_user("checker", [], True)

    release_request = factories.create_request_at_status(
        "workspace",
        files=[factories.request_file(group="group")],
        status=RequestStatus.SUBMITTED,
        checker=checker,
        checker_comments=[("group", "checker comment", Visibility.PRIVATE)],
    )

    airlock_client.login_with_user(checker)

    release_request = bll.get_release_request(release_request.id, checker)
    assert len(release_request.filegroups["group"].comments) == 1
    comment = release_request.filegroups["group"].comments[0]
    assert (
        release_request.filegroups["group"].comments[0].visibility == Visibility.PRIVATE
    )

    response = airlock_client.post(
        f"/requests/comment/visibility_public/{release_request.id}/group",
        data={"comment_id": comment.id},
        follow=True,
    )

    assert response.status_code == 200
    release_request = bll.get_release_request(release_request.id, checker)
    assert len(release_request.filegroups["group"].comments) == 1
    assert release_request.filegroups["group"].comments[0].id == comment.id
    assert (
        release_request.filegroups["group"].comments[0].visibility == Visibility.PUBLIC
    )


@pytest.mark.parametrize("endpoint,", ["delete", "visibility_public"])
def test_group_comment_modify_bad_form(airlock_client, endpoint):
    checker = factories.create_user("checker", [], True)

    release_request = factories.create_request_at_status(
        "workspace",
        files=[factories.request_file(group="group")],
        status=RequestStatus.SUBMITTED,
        checker=checker,
        checker_comments=[("group", "checker comment", Visibility.PRIVATE)],
    )

    airlock_client.login_with_user(checker)

    release_request = bll.get_release_request(release_request.id, checker)
    assert len(release_request.filegroups["group"].comments) == 1
    comment = release_request.filegroups["group"].comments[0]

    response = airlock_client.post(
        f"/requests/comment/{endpoint}/{release_request.id}/group",
        data={},
        follow=True,
    )

    assert response.status_code == 200
    messages = list(response.context.get("messages", []))
    assert messages[0].message == "comment_id: This field is required."

    release_request = bll.get_release_request(release_request.id, checker)
    assert len(release_request.filegroups["group"].comments) == 1
    assert release_request.filegroups["group"].comments[0].id == comment.id
    assert (
        release_request.filegroups["group"].comments[0].visibility == Visibility.PRIVATE
    )


@pytest.mark.parametrize("endpoint,", ["delete", "visibility_public"])
def test_group_comment_modify_bad_group(airlock_client, endpoint):
    checker = factories.create_user("checker", [], True)

    release_request = factories.create_request_at_status(
        "workspace",
        files=[factories.request_file(group="group")],
        status=RequestStatus.SUBMITTED,
        checker=checker,
        checker_comments=[("group", "checker comment", Visibility.PRIVATE)],
    )

    airlock_client.login_with_user(checker)

    release_request = bll.get_release_request(release_request.id, checker)
    assert len(release_request.filegroups["group"].comments) == 1
    comment = release_request.filegroups["group"].comments[0]

    response = airlock_client.post(
        f"/requests/comment/{endpoint}/{release_request.id}/badgroup",
        data={"comment_id": comment.id},
        follow=True,
    )

    assert response.status_code == 404
    release_request = bll.get_release_request(release_request.id, checker)
    assert len(release_request.filegroups["group"].comments) == 1
    assert release_request.filegroups["group"].comments[0].id == comment.id
    assert (
        release_request.filegroups["group"].comments[0].visibility == Visibility.PRIVATE
    )


@pytest.mark.parametrize("endpoint,", ["delete", "visibility_public"])
def test_group_comment_modify_missing_comment(airlock_client, endpoint):
    checker = factories.create_user("checker", [], True)

    release_request = factories.create_request_at_status(
        "workspace",
        files=[factories.request_file(group="group")],
        status=RequestStatus.SUBMITTED,
        checker=checker,
        checker_comments=[("group", "checker comment", Visibility.PRIVATE)],
    )

    airlock_client.login_with_user(checker)

    release_request = bll.get_release_request(release_request.id, checker)
    assert len(release_request.filegroups["group"].comments) == 1

    bad_comment_id = 50
    assert not release_request.filegroups["group"].comments[0].id == bad_comment_id

    response = airlock_client.post(
        f"/requests/comment/{endpoint}/{release_request.id}/group",
        data={"comment_id": bad_comment_id},
        follow=True,
    )

    assert response.status_code == 404
    release_request = bll.get_release_request(release_request.id, checker)
    assert len(release_request.filegroups["group"].comments) == 1
    assert (
        release_request.filegroups["group"].comments[0].visibility == Visibility.PRIVATE
    )
