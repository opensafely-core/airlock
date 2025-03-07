from io import BytesIO
from unittest.mock import patch

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
    audit_user = factories.create_airlock_user(
        username="audit_user", workspaces=["workspace"]
    )
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
    assert audit_user.username in response.rendered_content
    assert audit_user.fullname in response.rendered_content


def test_request_view_root_group(airlock_client):
    airlock_client.login(output_checker=True)
    audit_user = factories.create_airlock_user(
        username="audit_user", workspaces=["workspace"]
    )
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
    assert audit_user.username in response.rendered_content
    assert audit_user.fullname in response.rendered_content
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
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
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
    assert response.template_name == "file_browser/request/contents.html"
    assert '<ul id="tree"' not in response.rendered_content


def test_request_view_with_submitted_request(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace", status=RequestStatus.SUBMITTED, files=[factories.request_file()]
    )
    response = airlock_client.get(f"/requests/view/{release_request.id}", follow=True)
    assert "Rejecting a request is disabled" in response.rendered_content
    assert "Releasing to jobs.opensafely.org is disabled" in response.rendered_content
    assert "Return request before full review" in response.rendered_content
    assert "Submit review" in response.rendered_content


@pytest.mark.parametrize(
    "request_status,author,login_as,files,message,public_comment_user",
    [
        (
            # comments required on changes-requested file, alert not shown to checker
            RequestStatus.PENDING,
            "researcher",
            "researcher",
            [factories.request_file(group="group")],
            None,
            None,
        ),
        (
            # comments required on changes-requested file, alert not shown to checker
            RequestStatus.RETURNED,
            "researcher",
            "checker",
            [factories.request_file(group="group", changes_requested=True)],
            None,
            None,
        ),
        (
            # comments required on changes-requested file, alert shown to author
            RequestStatus.RETURNED,
            "researcher",
            "researcher",
            [factories.request_file(group="group", changes_requested=True)],
            "Please explain how you have updated the request",
            None,
        ),
        (
            # comments made on changes-requested file, no alert
            RequestStatus.RETURNED,
            "researcher",
            "researcher",
            [factories.request_file(group="group", changes_requested=True)],
            None,
            "researcher",
        ),
        (
            # comments made on changes-requested file by other user, no alert
            RequestStatus.RETURNED,
            "researcher",
            "researcher",
            [factories.request_file(group="group", changes_requested=True)],
            None,
            "researcher",
        ),
        (
            # comments not required on approved file, no alert
            RequestStatus.RETURNED,
            "researcher",
            "researcher",
            [factories.request_file(group="group", approved=True)],
            None,
            None,
        ),
    ],
)
def test_request_view_submit_request_alert(
    airlock_client,
    request_status,
    author,
    login_as,
    files,
    message,
    public_comment_user,
):
    """
    Alert message shown when a request is in returned status
    and does not yet have comments on all groups with changes requested
    """
    checkers = factories.get_default_output_checkers()
    users = {
        "researcher": factories.create_airlock_user(
            username="researcher", workspaces=["workspace"]
        ),
        "researcher1": factories.create_airlock_user(
            username="researcher1", output_checker=False
        ),
        "checker": checkers[0],
    }
    airlock_client.login(
        username=users[login_as].username, output_checker=users[login_as].output_checker
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=users[author],
        status=request_status,
        files=files,
        checker=users["checker"],
    )
    if public_comment_user:
        bll.group_comment_create(
            release_request,
            "group",
            "A public comment",
            Visibility.PUBLIC,
            users[public_comment_user],
        )

    response = airlock_client.get(f"/requests/view/{release_request.id}", follow=True)

    # The reminder message is only shown if the request in REVIEWED status
    # and the user is an output checker and not the author
    if message:
        assert message in response.context_data["request_action_required"]
    else:
        assert not response.context_data["request_action_required"]


@pytest.mark.parametrize(
    "author, login_as, files, message",
    [
        # not all files reviewed
        ("researcher", "checker", [factories.request_file()], None),
        # changes requested, has comments
        (
            "researcher",
            "checker",
            [factories.request_file(changes_requested=True, comment=True)],
            "submit your review now",
        ),
        # all approved
        (
            "researcher",
            "checker",
            [factories.request_file(changes_requested=True, comment=True)],
            "submit your review now",
        ),
        # approved, no comments
        (
            "researcher",
            "checker",
            [factories.request_file(approved=True, comment=False)],
            "submit your review now",
        ),
        # changes requested, needs comments
        (
            "researcher",
            "checker",
            [factories.request_file(changes_requested=True, comment=False)],
            "The following group(s) require comments",
        ),
        # changes requested, needs comments, author view
        (
            "researcher",
            "researcher",
            [factories.request_file(changes_requested=True, comment=False)],
            None,
        ),
        # changes requested, needs comments, author view, author is output-checker
        (
            "collaborator_checker",
            "collaborator_checker",
            [factories.request_file(changes_requested=True, comment=False)],
            None,
        ),
        # one file still needs reviewed, one approved
        (
            "researcher",
            "checker",
            [
                factories.request_file(approved=True),
                factories.request_file(path="unapproved.txt"),
            ],
            None,
        ),
        # one file still needs reviewed, one changes requested and missing comments
        (
            "researcher",
            "checker",
            [
                factories.request_file(changes_requested=True, comment=False),
                factories.request_file(path="unapproved.txt"),
            ],
            None,
        ),
        # one file with changes requested and comments, other supporting
        (
            "researcher",
            "checker",
            [
                factories.request_file(
                    group="group1", changes_requested=True, comment=True
                ),
                factories.request_file(
                    group="group2",
                    path="supporting.txt",
                    filetype=RequestFileType.SUPPORTING,
                    comment=False,
                ),
            ],
            "You have reviewed all files",
        ),
    ],
)
def test_request_view_submit_review_alert(
    airlock_client, author, login_as, files, message
):
    """
    Alert shown if:
    - this user has reviewed all files but has not yet submitted their review
    - this user has reviewed all files and needs to comment on groups with
      changes requested
    """
    checker = factories.get_default_output_checkers()[0]
    users = {
        "researcher": factories.create_airlock_user(
            username="researcher", workspaces=["workspace"]
        ),
        "collaborator_checker": factories.create_airlock_user(
            username="checker", output_checker=True, workspaces=["workspace"]
        ),
        "checker": checker,
    }
    airlock_client.login(
        username=users[login_as].username, output_checker=users[login_as].output_checker
    )

    release_request = factories.create_request_at_status(
        "workspace", author=users[author], status=RequestStatus.SUBMITTED, files=files
    )

    response = airlock_client.get(f"/requests/view/{release_request.id}", follow=True)

    # The all-files-reviewed reminder message is only shown if the request has
    # output files and all have been reviewed
    # If comments on groups are missing, the alert lists them
    # These alerts are never shown to an author
    if message:
        assert message in response.context_data["request_action_required"]
    else:
        assert not response.context_data["request_action_required"]


@pytest.mark.parametrize(
    "author,login_as,files,message,public_comment_user",
    [
        (
            # reviewed and all approved, output-checker can return/reject/release
            "researcher",
            "checker",
            [factories.request_file(group="group", approved=True)],
            "You can now return, reject or release this request",
            None,
        ),
        (
            # reviewed and not all approved, output-checker can return/reject
            "researcher",
            "checker",
            [
                factories.request_file(group="group", approved=True, path="foo.txt"),
                factories.request_file(
                    group="group", changes_requested=True, path="bar.txt"
                ),
            ],
            "You can now return or reject this request",
            "checker",
        ),
        (
            # reviewed and not all approved, comments missing
            "researcher",
            "checker",
            [
                factories.request_file(group="group", approved=True, path="foo.txt"),
                factories.request_file(
                    group="group", changes_requested=True, path="bar.txt"
                ),
            ],
            "The following group(s) require a public comment",
            None,
        ),
        (
            # reviewed and all approved, logged in as author who is also an output-checker
            "collaborator_checker",
            "collaborator_checker",
            [factories.request_file(group="group", approved=True)],
            None,
            None,
        ),
        (
            # reviewed and all approved, logged in as non output-checker
            "researcher",
            "researcher1",
            [factories.request_file(group="group", approved=True)],
            None,
            None,
        ),
        (
            # reviewed and all approved, logged in as author
            "researcher",
            "researcher",
            [factories.request_file(group="group", approved=True)],
            None,
            None,
        ),
    ],
)
def test_request_view_complete_turn_alert(
    airlock_client, author, login_as, files, message, public_comment_user
):
    """
    Alert message shown when a request has two submitted reviews and
    1) still requires public comments on any groups with changes requested OR
    2) has comments/is all approved and can now be progressed by returning/rejecting/releasing
    """
    checkers = factories.get_default_output_checkers()
    users = {
        "researcher": factories.create_airlock_user(
            username="researcher", workspaces=["workspace"]
        ),
        "researcher1": factories.create_airlock_user(
            username="researcher1", output_checker=False
        ),
        "collaborator_checker": factories.create_airlock_user(
            username="checker", output_checker=True, workspaces=["workspace"]
        ),
        "checker": checkers[0],
    }
    airlock_client.login(
        username=users[login_as].username, output_checker=users[login_as].output_checker
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=users[author],
        status=RequestStatus.REVIEWED,
        files=files,
        checker=users["checker"],
    )
    if public_comment_user:
        bll.group_comment_create(
            release_request,
            "group",
            "A public comment",
            Visibility.PUBLIC,
            users[public_comment_user],
        )

    response = airlock_client.get(f"/requests/view/{release_request.id}", follow=True)

    # The reminder message is only shown if the request in REVIEWED status
    # and the user is an output checker and not the author
    if message:
        assert message in response.context_data["request_action_required"]
    else:
        assert not response.context_data["request_action_required"]


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
        (
            "Reject request",
            "Rejecting a request is disabled",
            "Reject request as unsuitable for release",
        ),
        (
            "Release files",
            "Releasing to jobs.opensafely.org is disabled",
            "Release files to jobs.opensafely.org",
        ),
        (
            "Return request",
            "Returning a request is disabled",
            "Return request for changes/clarification",
        ),
    ]

    for button_text, disabled_tooltip, expected_tooltip in expected_buttons:
        assert button_text in response.rendered_content
        assert disabled_tooltip not in response.rendered_content
        assert expected_tooltip in response.rendered_content

    assert "author will need to resubmit" in response.rendered_content
    assert "Are you ready to return the request" not in response.rendered_content

    assert "Submit review" in response.rendered_content
    assert "You have already submitted your review" in response.rendered_content


def test_request_view_with_approved_request(
    mock_old_api,
    airlock_client,
):
    airlock_client.login(username="output-checker-0", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.APPROVED,
        files=[
            factories.request_file(approved=True, path="test1.txt", contents="1"),
            factories.request_file(
                approved=True, path="test2.txt", contents="2", uploaded=True
            ),
        ],
    )

    response = airlock_client.get(f"/requests/view/{release_request.id}/", follow=True)
    # Files are uploading, no action buttons shown
    for button_label in ["Reject request", "Release files", "Return request"]:
        assert button_label not in response.rendered_content


@pytest.mark.parametrize("status", list(RequestStatus))
def test_request_view_with_authored_request_file(mock_old_api, airlock_client, status):
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
    assert "Approved" in response.rendered_content
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
    assert "Changes requested" in response.rendered_content


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
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
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
    author = factories.create_airlock_user(**request_author)
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
def test_review_user_output_checker(airlock_client, mock_old_api):
    airlock_client.login(workspaces=["test_workspace"], output_checker=True)
    other = factories.create_airlock_user(
        username="other",
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
    other = factories.create_airlock_user(
        username="other",
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
    other_author = factories.create_airlock_user(
        username="other", workspaces=["test1"], output_checker=False
    )
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

    assert response.status_code == 403
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    # request has not been submitted
    assert persisted_request.status == RequestStatus.PENDING


def test_request_submit_missing_context_controls_for_empty_group(airlock_client):
    # Empty groups are not considered incomplete, even if they are missing context/controls
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_request_at_status(
        workspace="test1",
        status=RequestStatus.PENDING,
        author=airlock_client.user,
        files=[factories.request_file("group1", "path/test1.txt")],
    )
    factories.add_request_file(release_request, "group2", "path/test2.txt")

    release_request = factories.refresh_release_request(release_request)

    # group1 is OK, group2 is incomplete; we can't submit
    assert not release_request.filegroups["group1"].incomplete()
    assert release_request.filegroups["group2"].incomplete()
    response = airlock_client.post(f"/requests/submit/{release_request.id}")
    assert response.status_code == 403

    # withdraw the file from group2, group2 is now empty so we can submit
    bll.withdraw_file_from_request(
        release_request, UrlPath("group2/path/test2.txt"), airlock_client.user
    )
    release_request = factories.refresh_release_request(release_request)
    assert release_request.filegroups["group2"].empty()

    response = airlock_client.post(f"/requests/submit/{release_request.id}")
    assert response.status_code == 302

    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.SUBMITTED


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
    other_author = factories.create_airlock_user(
        username="other", workspaces=["test1"], output_checker=False
    )
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
    other_author = factories.create_airlock_user(
        username="other", workspaces=["test1"], output_checker=False
    )
    release_request = factories.create_request_at_status(
        "test1",
        author=other_author,
        status=RequestStatus.REVIEWED,
        files=[
            factories.request_file("group", "path/test.txt", approved=True),
            factories.request_file("group", "path/test1.txt", changes_requested=True),
        ],
    )
    # no public comment on group, 403
    response = airlock_client.post(f"/requests/return/{release_request.id}")
    assert response.status_code == 403

    # add comment and try again
    bll.group_comment_create(
        release_request, "group", "a comment", Visibility.PUBLIC, airlock_client.user
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
    assert "You have reviewed all files" in response.rendered_content

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
    assert "You have reviewed all files" not in response.rendered_content


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
    author1 = factories.create_airlock_user(
        username="author1", workspaces=["test1"], output_checker=False
    )
    author2 = factories.create_airlock_user(
        username="author2", workspaces=["test1"], output_checker=False
    )

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
    assert author1.fullname in response.rendered_content
    assert author2.username in response.rendered_content
    assert author2.fullname in response.rendered_content


@pytest.mark.parametrize("review", [("approve"), ("request_changes"), ("reset_review")])
def test_file_review_bad_user(airlock_client, review):
    workspace = "test1"
    airlock_client.login(workspaces=[workspace], output_checker=False)
    author = factories.create_airlock_user(
        username="author", workspaces=[workspace], output_checker=False
    )
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
    author = factories.create_airlock_user(
        username="author", workspaces=["test1"], output_checker=False
    )
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
    author = factories.create_airlock_user(
        username="author", workspaces=["test1"], output_checker=False
    )
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
    assert review.reviewer.username == "testuser"


def test_file_request_changes(airlock_client):
    airlock_client.login(output_checker=True)
    author = factories.create_airlock_user(
        username="author", workspaces=["test1"], output_checker=False
    )
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
    assert review.reviewer.username == "testuser"


def test_file_reset_review(airlock_client):
    airlock_client.login(output_checker=True)
    author = factories.create_airlock_user(
        username="author", workspaces=["test1"], output_checker=False
    )
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
    assert review.reviewer.username == "testuser"

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
    author = factories.create_airlock_user(
        username="author", workspaces=["test1"], output_checker=False
    )
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
        author=factories.create_airlock_user(username="author1", workspaces=["test1"]),
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


@pytest.mark.parametrize(
    "new_group,new_filetype",
    [
        # change group
        ("new-group", "OUTPUT"),
        # change filetype
        ("", "SUPPORTING"),
        # change both
        ("new-group", "SUPPORTING"),
    ],
)
def test_file_change_file_properties(airlock_client, new_group, new_filetype):
    airlock_client.login(username="author", workspaces=["test1"], output_checker=False)
    test_relpath = "path/test.txt"

    release_request = factories.create_request_at_status(
        "test1",
        author=airlock_client.user,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(
                "group", test_relpath, filetype=RequestFileType.OUTPUT
            ),
        ],
    )

    # ensure it does exist
    release_request.get_request_file_from_urlpath(f"group/{test_relpath}")
    if new_group:
        with pytest.raises(exceptions.FileNotFound):
            release_request.get_request_file_from_urlpath(f"{new_group}/{test_relpath}")

    response = airlock_client.post(
        f"/requests/change-properties/{release_request.id}",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": f"group/{test_relpath}",
            "form-0-filetype": new_filetype,
            "next_url": release_request.get_url(f"group/{test_relpath}"),
            "filegroup": "group",
            # new filegroup overrides a selected existing one (or the default)
            "new_filegroup": new_group,
        },
    )
    assert response.status_code == 302

    expected_group = new_group if new_group else "group"
    assert response.headers["location"] == release_request.get_url(
        f"{expected_group}/{test_relpath}"
    )

    persisted_request = factories.refresh_release_request(release_request)

    request_file = persisted_request.get_request_file_from_urlpath(
        f"{expected_group}/{test_relpath}"
    )
    if new_group:
        with pytest.raises(exceptions.FileNotFound):
            persisted_request.get_request_file_from_urlpath(f"group/{test_relpath}")
    assert request_file.filetype == RequestFileType[new_filetype]


def test_file_change_file_properties_multiple_files(airlock_client):
    airlock_client.login(username="author", workspaces=["test1"], output_checker=False)
    test_relpath = "path/test.txt"
    test_relpath1 = "path/test1.txt"

    release_request = factories.create_request_at_status(
        "test1",
        author=airlock_client.user,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(
                "group", test_relpath, contents="test", filetype=RequestFileType.OUTPUT
            ),
            factories.request_file(
                "group",
                test_relpath1,
                contents="test1",
                filetype=RequestFileType.SUPPORTING,
            ),
        ],
    )

    response = airlock_client.post(
        f"/requests/change-properties/{release_request.id}",
        data={
            "form-TOTAL_FORMS": "2",
            "form-INITIAL_FORMS": "2",
            "form-0-file": f"group/{test_relpath}",
            "form-0-filetype": "OUTPUT",
            "form-1-file": f"group/{test_relpath1}",
            "form-1-filetype": "OUTPUT",
            "next_url": release_request.get_url("group/path"),
            "filegroup": "group",
            # new filegroup overrides a selected existing one (or the default)
            "new_filegroup": "new_group",
        },
    )
    assert response.status_code == 302
    assert response.headers["location"] == release_request.get_url("new_group/path")

    persisted_request = factories.refresh_release_request(release_request)

    request_file = persisted_request.get_request_file_from_urlpath(
        f"new_group/{test_relpath}"
    )
    request_file1 = persisted_request.get_request_file_from_urlpath(
        f"new_group/{test_relpath1}"
    )
    assert request_file.filetype == RequestFileType.OUTPUT
    assert request_file1.filetype == RequestFileType.OUTPUT


def test_file_change_file_properties_no_changes(airlock_client):
    airlock_client.login(username="author", workspaces=["test1"], output_checker=False)
    test_relpath = "path/test.txt"

    release_request = factories.create_request_at_status(
        "test1",
        author=airlock_client.user,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(
                "group", test_relpath, filetype=RequestFileType.OUTPUT
            ),
        ],
    )
    audit_log_count = len(
        bll.get_request_audit_log(airlock_client.user, release_request, "group")
    )

    # ensure it does exist
    release_request.get_request_file_from_urlpath(f"group/{test_relpath}")
    with pytest.raises(exceptions.FileNotFound):
        release_request.get_request_file_from_urlpath(f"new_group/{test_relpath}")

    response = airlock_client.post(
        f"/requests/change-properties/{release_request.id}",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": f"group/{test_relpath}",
            "form-0-filetype": "OUTPUT",
            "next_url": release_request.get_url(f"group/{test_relpath}"),
            "filegroup": "group",
            "new_filegroup": "",
        },
    )
    assert response.status_code == 302
    assert response.headers["location"] == release_request.get_url(
        f"group/{test_relpath}"
    )

    persisted_request = factories.refresh_release_request(release_request)

    request_file = persisted_request.get_request_file_from_urlpath(
        f"group/{test_relpath}"
    )
    assert request_file.filetype == RequestFileType.OUTPUT
    # Nothing to do, no new audit logs
    assert (
        len(bll.get_request_audit_log(airlock_client.user, persisted_request, "group"))
        == audit_log_count
    )


def test_change_file_properties_bad_next_url(airlock_client):
    airlock_client.login(username="author", workspaces=["test1"], output_checker=False)
    test_relpath = "path/test.txt"

    release_request = factories.create_request_at_status(
        "test1",
        author=airlock_client.user,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file("group", test_relpath),
        ],
    )
    release_request.get_request_file_from_urlpath(f"group/{test_relpath}")
    with pytest.raises(exceptions.FileNotFound):
        release_request.get_request_file_from_urlpath(f"new_group/{test_relpath}")

    response = airlock_client.post(
        f"/requests/change-properties/{release_request.id}",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": f"group/{test_relpath}",
            "form-0-filetype": "OUTPUT",
            # missing value for "next_url" causes the form to fail validation
            "filegroup": "group",
            # new filegroup overrides a selected existing one (or the default)
            "new_filegroup": "new_group",
        },
    )

    assert response.status_code == 302
    assert response.headers["location"] == release_request.get_url()

    release_request.get_request_file_from_urlpath(f"group/{test_relpath}")
    with pytest.raises(exceptions.FileNotFound):
        release_request.get_request_file_from_urlpath(f"new_group/{test_relpath}")


def test_change_file_properties_permission_denied(airlock_client):
    airlock_client.login(username="author", workspaces=["test1"], output_checker=False)
    test_relpath = "path/test.txt"

    release_request = factories.create_request_at_status(
        "test1",
        author=airlock_client.user,
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", test_relpath),
        ],
    )
    release_request.get_request_file_from_urlpath(f"group/{test_relpath}")
    with pytest.raises(exceptions.FileNotFound):
        release_request.get_request_file_from_urlpath(f"new_group/{test_relpath}")

    response = airlock_client.post(
        f"/requests/change-properties/{release_request.id}",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": f"group/{test_relpath}",
            "form-0-filetype": "OUTPUT",
            "next_url": release_request.get_url(f"group/{test_relpath}"),
            "filegroup": "group",
            # new filegroup overrides a selected existing one (or the default)
            "new_filegroup": "new_group",
        },
    )

    assert response.status_code == 302
    # redirects to the original group path, not the new one
    assert response.headers["location"] == release_request.get_url(
        f"group/{test_relpath}"
    )

    release_request.get_request_file_from_urlpath(f"group/{test_relpath}")
    with pytest.raises(exceptions.FileNotFound):
        release_request.get_request_file_from_urlpath(f"new_group/{test_relpath}")


def test_request_multiselect_withdraw_files(airlock_client):
    user = factories.create_airlock_user(workspaces=["workspace"])
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


def test_request_multiselect_change_file_properties(airlock_client):
    user = factories.create_airlock_user(workspaces=["workspace"])
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
            "action": "update_files",
            "selected": [
                "group/file1.txt",
                "group/file2.txt",
            ],
            "next_url": release_request.get_url("group"),
        },
    )

    assert (
        f'<form action="/requests/change-properties/{release_request.id}" method="POST"'
        in response.rendered_content
    )
    assert "file1.txt" in response.rendered_content


def test_request_multiselect_withdraw_files_not_permitted(airlock_client):
    user = factories.create_airlock_user(workspaces=["workspace"])
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


def test_request_multiselect_change_file_properties_not_permitted(airlock_client):
    user = factories.create_airlock_user(workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        list(user.workspaces)[0],
        author=user,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(
                group="group",
                path="file2.txt",
                changes_requested=True,
            ),
        ],
    )

    airlock_client.login_with_user(user)

    bll.withdraw_file_from_request(release_request, UrlPath("group/file2.txt"), user)

    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.get_request_file_from_urlpath("group/file2.txt")
    assert request_file.filetype == RequestFileType.WITHDRAWN

    response = airlock_client.post(
        f"/requests/multiselect/{release_request.id}",
        data={
            "action": "update_files",
            "selected": [
                "group/file2.txt",
            ],
            "next_url": release_request.get_url(),
        },
    )

    assert "file2.txt" in response.rendered_content
    assert "cannot change file group or type" in response.rendered_content
    assert (
        f'<form action="/requests/change-properties/{release_request.id}" method="POST"'
        in response.rendered_content
    )


@pytest.mark.parametrize("action", ["withdraw_files", "update_files"])
def test_request_multiselect_none_selected(airlock_client, action):
    user = factories.create_airlock_user(workspaces=["workspace"])
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


def test_request_multiselect_invalid_action(airlock_client):
    user = factories.create_airlock_user(workspaces=["workspace"])
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
    # create request in REVIEWED status; we need to stub the job-server calls
    # before release_files is called (it will be called by
    # create_request_at_status if the status is APPROVED)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.REVIEWED,
        files=[
            factories.request_file(path="file.txt", contents="test1", approved=True),
            factories.request_file(path="file1.txt", contents="test2", approved=True),
        ],
    )

    release_files_stubber(release_request)

    if status == RequestStatus.APPROVED:
        bll.release_files(release_request, airlock_client.user)

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
        status=RequestStatus.REVIEWED,
        files=[
            factories.request_file(path="file.txt", contents="test1", approved=True),
            factories.request_file(path="file1.txt", contents="test2", approved=True),
        ],
    )
    release_files_stubber(release_request)

    if status == RequestStatus.APPROVED:
        bll.release_files(release_request, airlock_client.user)

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
    user = factories.create_airlock_user(output_checker=True)
    airlock_client.login_with_user(user)

    release_request = factories.create_request_at_status(
        "workspace",
        author=user,
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
    author = factories.create_airlock_user(
        username="author", workspaces=["test-workspace"]
    )
    checker = factories.create_airlock_user(
        username="output_checker", output_checker=True
    )
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
                checkers=[checker, factories.create_airlock_user(output_checker=True)],
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
        "username": login_as,
        "user_id": login_as,
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
    author = factories.create_airlock_user(
        username="author", workspaces=["workspace"], output_checker=False
    )
    other = factories.create_airlock_user(
        username="other", workspaces=["workspace"], output_checker=False
    )

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
    author = factories.create_airlock_user(
        username="author", workspaces=["workspace"], output_checker=False
    )

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
        (True, Visibility.PRIVATE, False),
    ],
)
def test_group_comment_create_success(
    airlock_client, output_checker, visibility, allowed
):
    author = factories.create_airlock_user(
        username="author", workspaces=["workspace"], output_checker=False
    )

    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(release_request, "group", "file.txt")

    # collaborator user - has access to the workspace but is not the author
    user = factories.create_airlock_user(
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
        assert release_request.filegroups["group"].comments[0].author == user
    else:
        assert "visibility: Select a valid choice" in messages[0].message


def test_group_comment_create_bad_user(airlock_client):
    author = factories.create_airlock_user(
        username="author", workspaces=["workspace"], output_checker=False
    )
    other = factories.create_airlock_user(
        username="other", workspaces=["other"], output_checker=False
    )

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
    author = factories.create_airlock_user(
        username="author", workspaces=["workspace"], output_checker=False
    )

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
    author = factories.create_airlock_user(
        username="author", workspaces=["workspace"], output_checker=False
    )

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
    author = factories.create_airlock_user(
        username="author", workspaces=["workspace"], output_checker=False
    )

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
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )

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
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )

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
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )

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
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )

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


def test_group_request_changes(airlock_client):
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(),
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", path="file1.txt"),
            factories.request_file("group", path="file2.txt"),
            factories.request_file(
                "group", path="file3.txt", filetype=RequestFileType.SUPPORTING
            ),
            factories.request_file("group1", path="file4.txt"),
        ],
    )
    airlock_client.login_with_user(checker)

    response = airlock_client.post(
        f"/requests/request-changes/{release_request.id}/group",
        follow=True,
    )
    assert "Changes have been requested for 2 files" in response.rendered_content

    release_request = factories.refresh_release_request(release_request)

    for filename in ["file1.txt", "file2.txt"]:
        rfile = release_request.get_request_file_from_urlpath(
            UrlPath(f"group/{filename}")
        )
        assert (
            rfile.get_file_vote_for_user(checker) == RequestFileVote.CHANGES_REQUESTED
        )

    supporting_file = release_request.get_request_file_from_urlpath(
        UrlPath("group/file3.txt")
    )
    assert supporting_file.get_file_vote_for_user(checker) is None

    other_group_file = release_request.get_request_file_from_urlpath(
        UrlPath("group1/file4.txt")
    )
    assert other_group_file.get_file_vote_for_user(checker) is None


def test_group_request_changes_with_existing_votes(airlock_client):
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )
    other_checker = factories.create_airlock_user(
        username="other", workspaces=[], output_checker=True
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(),
        status=RequestStatus.SUBMITTED,
        files=[
            # approved by this user
            factories.request_file(
                "group", path="file1.txt", approved=True, checkers=[checker]
            ),
            # approved by other user
            factories.request_file(
                "group", path="file2.txt", approved=True, checkers=[other_checker]
            ),
            # no vote
            factories.request_file("group", path="file3.txt"),
            # changes already requested by thi user
            factories.request_file(
                "group", path="file4.txt", changes_requested=True, checkers=[checker]
            ),
            factories.request_file(
                "group", path="file5.txt", changes_requested=True, checkers=[checker]
            ),
        ],
    )
    audit_logs_count = len(bll.get_request_audit_log(checker, release_request))
    airlock_client.login_with_user(checker)

    response = airlock_client.post(
        f"/requests/request-changes/{release_request.id}/group",
        follow=True,
    )
    # changes requested for the 2 files this user hasn't voted on
    assert "Changes have been requested for 2 files" in response.rendered_content
    # no changes for the file this user has already approved
    assert (
        "You have already approved 1 file which has not been updated"
        in response.rendered_content
    )
    # no changes for the file this user has already requeseted changes for
    assert "You have already requested changes for 2 files" in response.rendered_content

    release_request = factories.refresh_release_request(release_request)

    for filename in ["file2.txt", "file3.txt", "file4.txt", "file5.txt"]:
        rfile = release_request.get_request_file_from_urlpath(
            UrlPath(f"group/{filename}")
        )
        assert (
            rfile.get_file_vote_for_user(checker) == RequestFileVote.CHANGES_REQUESTED
        )

    approved_file = release_request.get_request_file_from_urlpath(
        UrlPath("group/file1.txt")
    )
    assert approved_file.get_file_vote_for_user(checker) == RequestFileVote.APPROVED

    # audit logs for 2 new file votes only
    assert (
        len(bll.get_request_audit_log(checker, release_request)) == audit_logs_count + 2
    )


def test_group_request_changes_with_existing_votes_nothing_to_do(airlock_client):
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(),
        status=RequestStatus.SUBMITTED,
        files=[
            # approved by this user
            factories.request_file(
                "group", path="file1.txt", approved=True, checkers=[checker]
            ),
            # approved by other user
            factories.request_file(
                "group", path="file2.txt", approved=True, checkers=[checker]
            ),
        ],
    )
    audit_logs_count = len(bll.get_request_audit_log(checker, release_request))

    airlock_client.login_with_user(checker)

    response = airlock_client.post(
        f"/requests/request-changes/{release_request.id}/group",
        follow=True,
    )
    assert (
        "You have already approved 2 files which have not been updated"
        in response.rendered_content
    )
    # no changes for the file this user has already requeseted changes for
    assert "You have already requested changes" not in response.rendered_content
    assert "Changes have been requested" not in response.rendered_content

    release_request = factories.refresh_release_request(release_request)

    for filename in ["file1.txt", "file2.txt"]:
        rfile = release_request.get_request_file_from_urlpath(
            UrlPath(f"group/{filename}")
        )
        assert rfile.get_file_vote_for_user(checker) == RequestFileVote.APPROVED

    # no new audit logs
    assert audit_logs_count == len(bll.get_request_audit_log(checker, release_request))


def test_group_request_changes_not_allowed(airlock_client):
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(),
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file("group", path="file1.txt", changes_requested=True),
        ],
    )
    airlock_client.login_with_user(checker)

    response = airlock_client.post(
        f"/requests/request-changes/{release_request.id}/group",
    )
    assert response.status_code == 403


def test_group_request_changes_file_review_not_allowed(airlock_client):
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(),
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", path="file1.txt"),
            factories.request_file("group", path="file2.txt"),
            factories.request_file("group", path="file3.txt"),
        ],
    )
    airlock_client.login_with_user(checker)

    with patch(
        "airlock.permissions.check_user_can_review_file",
        side_effect=[None, exceptions.RequestReviewDenied("review not allowed"), None],
    ):
        response = airlock_client.post(
            f"/requests/request-changes/{release_request.id}/group",
            follow=True,
        )

    resp_content = str(response.content)
    # changes requested for the 2 files this user hasn't voted on
    assert "Changes have been requested for 2 files" in resp_content
    # no changes for the file this user has already approved
    assert "Error requesting changes for file2.txt" in resp_content

    release_request = factories.refresh_release_request(release_request)
    for filename in ["file1.txt", "file3.txt"]:
        rfile = release_request.get_request_file_from_urlpath(
            UrlPath(f"group/{filename}")
        )
        assert (
            rfile.get_file_vote_for_user(checker) == RequestFileVote.CHANGES_REQUESTED
        )

    rfile = release_request.get_request_file_from_urlpath(UrlPath("group/file2.txt"))
    assert rfile.get_file_vote_for_user(checker) is None


def test_group_request_changes_bad_group(airlock_client):
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(),
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", path="file1.txt"),
        ],
    )
    airlock_client.login_with_user(checker)

    response = airlock_client.post(
        f"/requests/request-changes/{release_request.id}/group1",
    )
    assert response.status_code == 404


def test_group_reset_votes(airlock_client):
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )
    other_checker = factories.create_airlock_user(
        username="other", workspaces=[], output_checker=True
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(),
        status=RequestStatus.SUBMITTED,
        files=[
            # approved by this user
            factories.request_file(
                "group", path="file1.txt", approved=True, checkers=[checker]
            ),
            # approved by other user
            factories.request_file(
                "group", path="file2.txt", approved=True, checkers=[other_checker]
            ),
            # no vote
            factories.request_file("group", path="file3.txt"),
            # changes already requested by this user
            factories.request_file(
                "group", path="file4.txt", changes_requested=True, checkers=[checker]
            ),
        ],
    )
    audit_logs_count = len(bll.get_request_audit_log(checker, release_request))
    for filename in ["file1.txt", "file4.txt"]:
        rfile = release_request.get_request_file_from_urlpath(
            UrlPath(f"group/{filename}")
        )
        assert rfile.get_file_vote_for_user(checker) is not None

    airlock_client.login_with_user(checker)

    response = airlock_client.post(
        f"/requests/reset-votes/{release_request.id}/group",
        follow=True,
    )
    assert "Votes on 2 files have been reset" in response.rendered_content

    release_request = factories.refresh_release_request(release_request)

    for filename in ["file1.txt", "file2.txt", "file3.txt", "file4.txt"]:
        rfile = release_request.get_request_file_from_urlpath(
            UrlPath(f"group/{filename}")
        )
        assert rfile.get_file_vote_for_user(checker) is None

    rfile = release_request.get_request_file_from_urlpath(UrlPath("group/file2.txt"))
    assert rfile.get_file_vote_for_user(other_checker) == RequestFileVote.APPROVED

    # audit logs for 2 reset file votes only (2 files were un-voted)
    assert (
        len(bll.get_request_audit_log(checker, release_request)) == audit_logs_count + 2
    )


def test_group_reset_votes_nothing_to_do(airlock_client):
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(),
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file("group", path="file1.txt")],
    )
    airlock_client.login_with_user(checker)

    response = airlock_client.post(
        f"/requests/reset-votes/{release_request.id}/group",
        follow=True,
    )
    assert "No votes to reset" in response.rendered_content

    release_request = factories.refresh_release_request(release_request)

    rfile = release_request.get_request_file_from_urlpath(UrlPath("group/file1.txt"))
    assert rfile.get_file_vote_for_user(checker) is None


def test_group_reset_votes_not_allowed(airlock_client):
    # cna't reset vote after review has been submitted
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(),
        status=RequestStatus.PARTIALLY_REVIEWED,
        files=[
            factories.request_file(
                "group", path="file1.txt", changes_requested=True, checkers=[checker]
            ),
        ],
        checker=checker,
    )
    airlock_client.login_with_user(checker)

    response = airlock_client.post(
        f"/requests/reset-votes/{release_request.id}/group",
    )
    assert response.status_code == 403


def test_group_reset_votes_bad_group(airlock_client):
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(),
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file("group", path="file1.txt"),
        ],
    )
    airlock_client.login_with_user(checker)

    response = airlock_client.post(
        f"/requests/reset-votes/{release_request.id}/group1",
    )
    assert response.status_code == 404


def test_group_reset_votes_file_reset_not_allowed(airlock_client):
    checker = factories.create_airlock_user(
        username="checker", workspaces=[], output_checker=True
    )
    release_request = factories.create_request_at_status(
        "workspace",
        author=factories.create_airlock_user(),
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(
                "group", path="file1.txt", approved=True, checkers=[checker]
            ),
            factories.request_file(
                "group", path="file2.txt", changes_requested=True, checkers=[checker]
            ),
            factories.request_file(
                "group", path="file3.txt", approved=True, checkers=[checker]
            ),
        ],
    )
    airlock_client.login_with_user(checker)

    with patch(
        "airlock.permissions.check_user_can_reset_file_review",
        side_effect=[None, None, exceptions.RequestReviewDenied("reset not allowed")],
    ):
        response = airlock_client.post(
            f"/requests/reset-votes/{release_request.id}/group",
            follow=True,
        )
    resp_content = str(response.content)
    assert "Votes on 2 files have been reset" in response.rendered_content
    assert "Error resetting vote for file3.txt" in resp_content

    release_request = factories.refresh_release_request(release_request)
    for filename in ["file1.txt", "file2.txt"]:
        rfile = release_request.get_request_file_from_urlpath(
            UrlPath(f"group/{filename}")
        )
        assert rfile.get_file_vote_for_user(checker) is None

    rfile = release_request.get_request_file_from_urlpath(UrlPath("group/file3.txt"))
    assert rfile.get_file_vote_for_user(checker) == RequestFileVote.APPROVED


@pytest.mark.parametrize(
    "status,author,login_as,reviewed_by,can_vote,can_reset",
    [
        # No voting buttons for pending requests
        (RequestStatus.PENDING, "researcher", "researcher", None, False, False),
        (RequestStatus.PENDING, "researcher", "checker", None, False, False),
        # Both buttons for non-author output-checkers for submitted requests
        (RequestStatus.SUBMITTED, "researcher", "checker", None, True, True),
        (RequestStatus.SUBMITTED, "researcher", "checker", "checker", True, True),
        (RequestStatus.SUBMITTED, "researcher", "researcher", None, False, False),
        (RequestStatus.SUBMITTED, "other_checker", "checker", None, True, True),
        (RequestStatus.SUBMITTED, "other_checker", "other_checker", None, False, False),
        # Both buttons for non-author output-checkers for pariatlly reviewed/reviewed requests
        # if logged in user has not submitted review
        (
            RequestStatus.PARTIALLY_REVIEWED,
            "researcher",
            "checker",
            "other_checker",
            True,
            True,
        ),
        (RequestStatus.REVIEWED, "researcher", "checker", "other_checker", True, True),
        # No reset buttons for non-author output-checkers who have submitted review
        (
            RequestStatus.PARTIALLY_REVIEWED,
            "researcher",
            "checker",
            "checker",
            True,
            False,
        ),
        (RequestStatus.REVIEWED, "researcher", "checker", "checker", True, False),
    ],
)
def test_request_view_group_vote_buttons(
    airlock_client, status, author, login_as, reviewed_by, can_vote, can_reset
):
    users = {
        "researcher": factories.create_airlock_user(
            username="researcher", workspaces=["workspace"]
        ),
        "checker": factories.create_airlock_user(
            username="checker", workspaces=[], output_checker=True
        ),
        "other_checker": factories.create_airlock_user(
            username="other_checker", workspaces=["workspace"], output_checker=True
        ),
    }

    reviewer = users.get(reviewed_by)
    release_request = factories.create_request_at_status(
        "workspace",
        author=users[author],
        status=status,
        files=[
            factories.request_file(
                "group1",
                "some_dir/file1.txt",
                changes_requested=reviewer is not None,
                checkers=[reviewer] if reviewer else [],
            ),
            factories.request_file(
                "group1",
                "some_dir/file2.txt",
                filetype=RequestFileType.SUPPORTING,
            ),
        ],
        checker=reviewer,
    )
    airlock_client.login_with_user(users[login_as])
    response = airlock_client.get(f"/requests/view/{release_request.id}/group1/")

    assert response.context_data["group"]["request_changes_button"].show == can_vote
    assert response.context_data["group"]["reset_votes_button"].show == can_reset
