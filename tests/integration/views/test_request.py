from io import BytesIO

import pytest
import requests

from airlock.business_logic import (
    AuditEventType,
    RequestFileType,
    RequestStatus,
    UserFileReviewStatus,
    bll,
)
from airlock.types import UrlPath
from tests import factories
from tests.conftest import get_trace


pytestmark = pytest.mark.django_db


def test_request_index_no_user(airlock_client):
    release_request = factories.create_release_request("workspace")
    response = airlock_client.get(f"/requests/view/{release_request.id}/")
    assert response.status_code == 302


def test_request_view_index(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace", airlock_client.user)
    factories.write_request_file(release_request, "group", "file.txt")
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
    audit_user = factories.create_user("audit_user")
    release_request = factories.create_release_request("workspace", user=audit_user)
    factories.write_request_file(release_request, "group1", "some_dir/file1.txt")
    factories.write_request_file(
        release_request,
        "group1",
        "some_dir/file2.txt",
        filetype=RequestFileType.SUPPORTING,
    )
    factories.write_request_file(release_request, "group2", "some_dir/file3.txt")
    factories.write_request_file(
        release_request,
        "group2",
        "some_dir/file4.txt",
        filetype=RequestFileType.WITHDRAWN,
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
    assert "Created request" in response.rendered_content


def test_request_view_root_group(airlock_client):
    airlock_client.login(output_checker=True)
    audit_user = factories.create_user("audit_user")
    release_request = factories.create_release_request("workspace", user=audit_user)
    factories.write_request_file(release_request, "group1", "some_dir/file1.txt")
    factories.write_request_file(
        release_request,
        "group1",
        "some_dir/file2.txt",
        filetype=RequestFileType.SUPPORTING,
    )

    response = airlock_client.get(f"/requests/view/{release_request.id}/group1/")
    assert response.status_code == 200
    assert "Recent activity" in response.rendered_content
    assert "audit_user" in response.rendered_content
    assert "Added file" in response.rendered_content


def test_request_view_with_directory(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    factories.write_request_file(release_request, "group", "some_dir/file.txt")
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/some_dir/"
    )
    assert response.status_code == 200
    assert "file.txt" in response.rendered_content


@pytest.mark.parametrize(
    "filetype", [RequestFileType.OUTPUT, RequestFileType.SUPPORTING]
)
def test_request_view_with_file(airlock_client, filetype):
    release_request = factories.create_release_request("workspace")
    airlock_client.login(output_checker=True)
    factories.write_request_file(
        release_request, "group", "file.txt", "foobar", filetype=filetype
    )
    response = airlock_client.get(f"/requests/view/{release_request.id}/group/file.txt")
    assert response.status_code == 200
    assert (
        release_request.get_contents_url(UrlPath("group/file.txt"))
        in response.rendered_content
    )
    assert response.template_name == "file_browser/index.html"


def test_request_view_with_file_htmx(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    factories.write_request_file(release_request, "group", "file.txt", "foobar")
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
    release_request = factories.create_release_request(
        "workspace", status=RequestStatus.SUBMITTED
    )
    response = airlock_client.get(f"/requests/view/{release_request.id}", follow=True)
    assert "Rejecting a request is disabled" in response.rendered_content
    assert "Releasing to jobs.opensafely.org is disabled" in response.rendered_content
    assert "Returning a request is disabled" in response.rendered_content
    assert "Complete review" in response.rendered_content


def test_request_view_with_reviewed_request(airlock_client):
    # Login as 1st default output-checker
    airlock_client.login("output-checker-0", output_checker=True)
    release_request = factories.create_request_at_state(
        "workspace", status=RequestStatus.REVIEWED
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

    assert "Complete review" in response.rendered_content
    assert "You have already completed your review" in response.rendered_content


def test_request_view_with_withdrawn_request(airlock_client):
    # Login as 1st default output-checker
    airlock_client.login("output-checker-0", output_checker=True)
    release_request = factories.create_request_at_state(
        "workspace",
        status=RequestStatus.WITHDRAWN,
        withdrawn_after=RequestStatus.RETURNED,
    )
    response = airlock_client.get(f"/requests/view/{release_request.id}", follow=True)

    expected_buttons = [
        ("Reject request", "Rejecting a request is disabled"),
        ("Release files", "Releasing to jobs.opensafely.org is disabled"),
        ("Return request", "Returning a request is disabled"),
    ]

    for button_text, diabled_tooltip in expected_buttons:
        assert button_text in response.rendered_content
        assert diabled_tooltip in response.rendered_content


def test_request_view_with_authored_request_file(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        user=airlock_client.user,
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "file.txt", "foobar")
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/file.txt", follow=True
    )
    assert "Withdraw this file" in response.rendered_content


def test_request_view_with_submitted_file(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "file.txt", "foobar")
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/file.txt", follow=True
    )
    assert "Remove this file" not in response.rendered_content
    assert "Approve file" in response.rendered_content
    assert "Request changes" in response.rendered_content


def test_request_view_with_submitted_supporting_file(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(
        release_request,
        "group",
        "supporting_file.txt",
        filetype=RequestFileType.SUPPORTING,
    )
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/supporting_file.txt", follow=True
    )
    assert "Remove this file" not in response.rendered_content
    # these buttons currently exist but are both disabled
    assert "Approve file" not in response.rendered_content
    assert "Request changes" not in response.rendered_content


def test_request_view_with_submitted_file_approved(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "file.txt", "foobar")
    airlock_client.post(f"/requests/approve/{release_request.id}/group/file.txt")
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/file.txt", follow=True
    )
    assert "Approve file" in response.rendered_content
    assert "Request changes" in response.rendered_content


def test_request_view_with_submitted_file_rejected(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "file.txt", "foobar")
    airlock_client.post(f"/requests/reject/{release_request.id}/group/file.txt")
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
    release_request = factories.create_release_request("workspace")
    # write a file and a supporting file to the group
    factories.write_request_file(release_request, "group", "file.txt")
    factories.write_request_file(
        release_request,
        "group",
        "supporting_file.txt",
        filetype=RequestFileType.SUPPORTING,
    )
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/no_such_file.txt"
    )
    assert response.status_code == 404


def test_request_view_redirects_to_directory(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    factories.write_request_file(
        release_request, "group", "some_dir/file.txt", "foobar"
    )

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
    factories.write_request_file(release_request, "group", "file.txt")
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
    release_request = factories.create_release_request("workspace", id="id")
    factories.write_request_file(
        release_request, "default", "file.txt", contents="test"
    )
    response = airlock_client.get("/requests/content/id/default/file.txt")
    assert response.status_code == 200
    assert response.content == b'<pre class="txt">\ntest\n</pre>\n'
    audit_log = bll.get_audit_log(
        user=airlock_client.user.username,
        request=release_request.id,
    )
    assert audit_log[0].type == AuditEventType.REQUEST_FILE_VIEW
    assert audit_log[0].path == UrlPath("default/file.txt")
    assert audit_log[0].extra["group"] == "default"


def test_request_contents_dir(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace", id="id")
    factories.write_request_file(
        release_request, "default", "foo/file.txt", contents="test"
    )
    response = airlock_client.get("/requests/content/id/default/foo")
    assert response.status_code == 404


def test_request_contents_file_not_exists(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace", id="id")
    factories.write_request_file(
        release_request, "default", "foo/file.txt", contents="test"
    )
    response = airlock_client.get("/requests/content/id/default/notexists.txt")
    assert response.status_code == 404


def test_request_contents_group_not_exists(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace", id="id")
    factories.write_request_file(
        release_request, "default", "foo/file.txt", contents="test"
    )
    response = airlock_client.get("/requests/content/id/notexist/")
    assert response.status_code == 404


def test_request_download_file(airlock_client):
    airlock_client.login("reviewer", output_checker=True)
    author = factories.create_user("author", ["workspace"])
    release_request = factories.create_release_request(
        "workspace", id="id", user=author
    )
    factories.write_request_file(
        release_request, "default", "file.txt", contents="test"
    )
    response = airlock_client.get("/requests/content/id/default/file.txt?download")
    assert response.status_code == 200
    assert response.as_attachment
    assert list(response.streaming_content) == [b"test"]

    audit_log = bll.get_audit_log(
        user=airlock_client.user.username,
        request=release_request.id,
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
    release_request = factories.create_release_request(
        "workspace", id="id", user=author
    )
    factories.write_request_file(
        release_request, "default", "file.txt", contents="test", user=author
    )
    response = airlock_client.get("/requests/content/id/default/file.txt?download")
    if can_download:
        assert response.status_code == 200
        assert response.as_attachment
        assert list(response.streaming_content) == [b"test"]

        audit_log = bll.get_audit_log(
            user=airlock_client.user.username,
            request=release_request.id,
        )
        assert audit_log[0].type == AuditEventType.REQUEST_FILE_DOWNLOAD
    else:
        assert response.status_code == 403


def test_request_index_user_permitted_requests(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_release_request("test1", airlock_client.user)
    response = airlock_client.get("/requests/")
    authored_ids = {r.id for r in response.context["authored_requests"]}
    outstanding_ids = {r.id for r in response.context["outstanding_requests"]}
    returned_ids = {r.id for r in response.context["returned_requests"]}
    approved_ids = {r.id for r in response.context["approved_requests"]}
    assert authored_ids == {release_request.id}
    assert outstanding_ids == set()
    assert returned_ids == set()
    assert approved_ids == set()


def test_request_index_user_output_checker(airlock_client):
    airlock_client.login(workspaces=["test_workspace"], output_checker=True)
    other = factories.create_user("other")
    r1 = factories.create_release_request(
        "test_workspace", user=airlock_client.user, status=RequestStatus.SUBMITTED
    )
    r2 = factories.create_release_request(
        "other_workspace", user=other, status=RequestStatus.SUBMITTED
    )
    r3 = factories.create_release_request(
        "other_other_workspace", user=other, status=RequestStatus.RETURNED
    )
    r4 = factories.create_release_request(
        "other_other1_workspace", user=other, status=RequestStatus.APPROVED
    )
    response = airlock_client.get("/requests/")

    authored_ids = {r.id for r in response.context["authored_requests"]}
    outstanding_ids = {r.id for r in response.context["outstanding_requests"]}
    returned_ids = {r.id for r in response.context["returned_requests"]}
    approved_ids = {r.id for r in response.context["approved_requests"]}

    assert authored_ids == {r1.id}
    assert outstanding_ids == {r2.id}
    assert returned_ids == {r3.id}
    assert approved_ids == {r4.id}


def test_request_submit_author(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_release_request(
        "test1", user=airlock_client.user
    )
    factories.write_request_file(release_request, "group", "path/test.txt")

    response = airlock_client.post(f"/requests/submit/{release_request.id}")

    assert response.status_code == 302
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.SUBMITTED


def test_request_submit_not_author(airlock_client):
    airlock_client.login(workspaces=["test1"])
    other_author = factories.create_user("other", [], False)
    release_request = factories.create_release_request(
        "test1", user=other_author, status=RequestStatus.PENDING
    )
    factories.write_request_file(release_request, "group", "path/test.txt")

    response = airlock_client.post(f"/requests/submit/{release_request.id}")

    assert response.status_code == 403
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.PENDING


def test_request_withdraw_author(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_release_request(
        "test1", user=airlock_client.user
    )
    factories.write_request_file(release_request, "group", "path/test.txt")

    response = airlock_client.post(f"/requests/withdraw/{release_request.id}")

    assert response.status_code == 302
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.WITHDRAWN


def test_request_withdraw_not_author(airlock_client):
    airlock_client.login(workspaces=["test1"])
    other_author = factories.create_user("other", [], False)
    release_request = factories.create_release_request(
        "test1", user=other_author, status=RequestStatus.PENDING
    )
    factories.write_request_file(release_request, "group", "path/test.txt")

    response = airlock_client.post(f"/requests/withdraw/{release_request.id}")

    assert response.status_code == 403
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.PENDING


def test_request_return_author(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_release_request(
        "test1", user=airlock_client.user, status=RequestStatus.SUBMITTED
    )
    factories.write_request_file(
        release_request, "group", "path/test.txt", approved=True
    )
    factories.write_request_file(
        release_request, "group", "path/test1.txt", rejected=True
    )
    factories.complete_independent_review(release_request)

    response = airlock_client.post(f"/requests/return/{release_request.id}")

    assert response.status_code == 403
    persisted_request = factories.bll.get_release_request(
        release_request.id, airlock_client.user
    )
    assert persisted_request.status == RequestStatus.REVIEWED


def test_request_return_output_checker(airlock_client):
    airlock_client.login(workspaces=["test1"], output_checker=True)
    other_author = factories.create_user("other", [], False)
    release_request = factories.create_release_request("test1", user=other_author)
    factories.write_request_file(
        release_request, "group", "path/test.txt", approved=True
    )
    factories.write_request_file(
        release_request, "group", "path/test1.txt", rejected=True
    )
    factories.bll.set_status(release_request, RequestStatus.SUBMITTED, other_author)
    factories.complete_independent_review(release_request)
    response = airlock_client.post(f"/requests/return/{release_request.id}")

    assert response.status_code == 302
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.RETURNED


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
    factories.write_request_file(release_request1, "group", "path/test.txt")

    release_request2 = factories.create_release_request(
        "test1", user=author2, status=RequestStatus.PENDING
    )
    factories.write_request_file(release_request2, "group", "path/test2.txt")

    response = airlock_client.post("/requests/workspace/test1")

    response.render()
    assert response.status_code == 200
    assert "All requests in workspace test1" in response.rendered_content
    assert "PENDING" in response.rendered_content
    assert author1.username in response.rendered_content
    assert author2.username in response.rendered_content


@pytest.mark.parametrize("review", [("approve"), ("reject"), ("reset_review")])
def test_file_review_bad_user(airlock_client, review):
    workspace = "test1"
    airlock_client.login(workspaces=[workspace], output_checker=False)
    author = factories.create_user("author", [workspace], False)
    release_request = factories.create_release_request(
        workspace,
        user=author,
        status=RequestStatus.SUBMITTED,
    )
    path = "path/test.txt"
    factories.write_request_file(release_request, "group", path, contents="test")

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


@pytest.mark.parametrize("review", [("approve"), ("reject"), ("reset_review")])
def test_file_review_bad_file(airlock_client, review):
    airlock_client.login(output_checker=True)
    author = factories.create_user("author", ["test1"], False)
    release_request = factories.create_release_request(
        "test1",
        user=author,
        status=RequestStatus.SUBMITTED,
    )
    path = "path/test.txt"
    factories.write_request_file(release_request, "group", path, contents="test")

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
    release_request = factories.create_release_request(
        "test1",
        user=author,
        status=RequestStatus.SUBMITTED,
    )
    path = "path/test.txt"
    factories.write_request_file(release_request, "group", path, contents="test")

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
    assert review.status == UserFileReviewStatus.APPROVED
    assert review.reviewer == "testuser"


def test_file_reject(airlock_client):
    airlock_client.login(output_checker=True)
    author = factories.create_user("author", ["test1"], False)
    release_request = factories.create_release_request(
        "test1",
        user=author,
        status=RequestStatus.SUBMITTED,
    )
    path = "path/test.txt"
    factories.write_request_file(release_request, "group", path, contents="test")

    response = airlock_client.post(
        f"/requests/reject/{release_request.id}/group/{path}"
    )
    assert response.status_code == 302
    relpath = UrlPath(path)
    review = (
        bll.get_release_request(release_request.id, author)
        .get_request_file_from_output_path(relpath)
        .reviews[airlock_client.user.username]
    )
    assert review.status == UserFileReviewStatus.REJECTED
    assert review.reviewer == "testuser"


def test_file_reset_review(airlock_client):
    airlock_client.login(output_checker=True)
    author = factories.create_user("author", ["test1"], False)
    release_request = factories.create_release_request(
        "test1",
        user=author,
        status=RequestStatus.SUBMITTED,
    )
    path = "path/test.txt"
    factories.write_request_file(release_request, "group", path, contents="test")

    # first reject a file
    response = airlock_client.post(
        f"/requests/reject/{release_request.id}/group/{path}"
    )
    assert response.status_code == 302
    relpath = UrlPath(path)
    review = (
        bll.get_release_request(release_request.id, author)
        .get_request_file_from_output_path(relpath)
        .reviews[airlock_client.user.username]
    )
    assert review.status == UserFileReviewStatus.REJECTED
    assert review.reviewer == "testuser"

    # then reset it to have no review
    response = airlock_client.post(
        f"/requests/reset_review/{release_request.id}/group/{path}"
    )
    assert response.status_code == 302
    relpath = UrlPath(path)
    reviews = (
        bll.get_release_request(release_request.id, author)
        .filegroups["group"]
        .files[relpath]
        .reviews
    )
    assert len(reviews) == 0

    # verify a re-request
    response = airlock_client.post(
        f"/requests/reset_review/{release_request.id}/group/{path}"
    )
    assert response.status_code == 404


def test_request_reject_output_checker(airlock_client):
    airlock_client.login(output_checker=True)
    author = factories.create_user("author", ["test1"], False)
    release_request = factories.create_release_request(
        "test1",
        user=author,
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(
        release_request, "group", "path/test.txt", rejected=True
    )
    factories.complete_independent_review(release_request)

    response = airlock_client.post(f"/requests/reject/{release_request.id}")

    assert response.status_code == 302
    persisted_request = bll.get_release_request(release_request.id, airlock_client.user)
    assert persisted_request.status == RequestStatus.REJECTED


def test_request_reject_not_output_checker(airlock_client):
    release_request = factories.create_release_request(
        "test1",
        status=RequestStatus.SUBMITTED,
    )
    airlock_client.login(workspaces=[release_request.workspace], output_checker=False)
    factories.write_request_file(
        release_request, "group", "path/test.txt", rejected=True
    )
    factories.complete_independent_review(release_request)

    response = airlock_client.post(f"/requests/reject/{release_request.id}")

    assert response.status_code == 403
    persisted_request = factories.bll.get_release_request(
        release_request.id, airlock_client.user
    )
    assert persisted_request.status == RequestStatus.REVIEWED


def test_file_withdraw_file_pending(airlock_client):
    author = factories.create_user("author", ["test1"], False)
    airlock_client.login_with_user(author)
    release_request = factories.create_release_request(
        "test1",
        user=author,
        status=RequestStatus.PENDING,
    )
    factories.write_request_file(release_request, "group", "path/test.txt")
    release_request = factories.refresh_release_request(release_request)

    # ensure it does exist
    release_request.get_request_file_from_urlpath("group/path/test.txt")

    response = airlock_client.post(
        f"/requests/withdraw/{release_request.id}/group/path/test.txt",
    )
    assert response.status_code == 302
    assert response.headers["location"] == release_request.get_url("group")

    persisted_request = bll.get_release_request(release_request.id, author)

    with pytest.raises(bll.FileNotFound):
        persisted_request.get_request_file_from_urlpath("group/path/test.txt")


def test_file_withdraw_file_submitted(airlock_client):
    author = factories.create_user("author", ["test1"], False)
    airlock_client.login_with_user(author)
    release_request = factories.create_release_request(
        "test1",
        user=author,
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "path/test.txt")
    release_request = factories.refresh_release_request(release_request)

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

    persisted_request = bll.get_release_request(release_request.id, author)
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
    factories.write_request_file(release_request, "group", "path/test.txt")

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


@pytest.mark.parametrize("status", [RequestStatus.SUBMITTED, RequestStatus.APPROVED])
def test_request_release_files_success(airlock_client, release_files_stubber, status):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(
        release_request, "group", "test/file1.txt", "test1", approved=True
    )
    factories.write_request_file(
        release_request, "group", "test/file2.txt", "test2", approved=True
    )
    factories.complete_independent_review(release_request)

    if status == RequestStatus.APPROVED:
        release_request = factories.refresh_release_request(release_request)
        bll.set_status(release_request, status, airlock_client.user)

    api_responses = release_files_stubber(release_request)
    response = airlock_client.post("/requests/release/request_id")

    assert response.url == "/requests/view/request_id/"
    assert response.status_code == 302

    assert api_responses.calls[1].request.body.read() == b"test1"
    assert api_responses.calls[2].request.body.read() == b"test2"


@pytest.mark.parametrize("status", [RequestStatus.SUBMITTED, RequestStatus.APPROVED])
def test_request_release_files_success_htmx(
    airlock_client, release_files_stubber, status
):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(
        release_request, "group", "test/file1.txt", "test1", approved=True
    )
    factories.write_request_file(
        release_request, "group", "test/file2.txt", "test2", approved=True
    )
    if status == RequestStatus.APPROVED:
        release_request = factories.refresh_release_request(release_request)
        factories.complete_independent_review(release_request)
        release_request = factories.refresh_release_request(release_request)
        factories.bll.set_status(release_request, status, airlock_client.user)

    api_responses = release_files_stubber(release_request)
    response = airlock_client.post(
        "/requests/release/request_id",
        headers={"HX-Request": "true"},
    )

    assert response.headers["HX-Redirect"] == "/requests/view/request_id/"
    assert response.status_code == 200

    assert api_responses.calls[1].request.body.read() == b"test1"
    assert api_responses.calls[2].request.body.read() == b"test2"


def test_requests_release_workspace_403(airlock_client):
    airlock_client.login(output_checker=False)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(
        release_request, "group", "test/file1.txt", "test1", approved=True
    )
    response = airlock_client.post("/requests/release/request_id")
    assert response.status_code == 403


def test_requests_release_author_403(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        user=airlock_client.user,
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(
        release_request, "group", "test/file1.txt", "test1", approved=True
    )
    factories.complete_independent_review(release_request)
    response = airlock_client.post("/requests/release/request_id", follow=True)
    assert response.status_code == 200
    assert (
        list(response.context["messages"])[0].message
        == "Error releasing files: Can not set your own request to APPROVED"
    )


def test_requests_release_jobserver_403(airlock_client, release_files_stubber):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(
        release_request, "group", "test/file.txt", "test", approved=True
    )
    factories.complete_independent_review(release_request)

    response = requests.Response()
    response.status_code = 403
    api403 = requests.HTTPError(response=response)
    release_files_stubber(release_request, body=api403)

    # test 403 is handled
    response = airlock_client.post("/requests/release/request_id", follow=True)
    assert response.status_code == 200
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
    airlock_client.login(output_checker=True)
    settings.DEBUG = True
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(
        release_request, "default", "test/file.txt", "test", approved=True
    )
    factories.complete_independent_review(release_request)

    response = requests.Response()
    response.status_code = 403
    response.headers = {"Content-Type": content_type}
    response.raw = BytesIO(content)
    api403 = requests.HTTPError(response=response)
    release_files_stubber(release_request, body=api403)

    # test 403 is handled
    response = airlock_client.post("/requests/release/request_id", follow=True)
    # DEBUG is on, so we return the job-server error
    assert response.status_code == 200
    error_message = list(response.context["messages"])[0].message
    assert "An error from job-server" in error_message
    assert f"Type: {content_type}" in error_message


def test_requests_release_unapproved_files(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(
        release_request, "group", "test/file1.txt", "test1", approved=True
    )

    response = airlock_client.post("/requests/release/request_id", follow=True)
    assert response.status_code == 200
    assert (
        "request has unapproved files" in list(response.context["messages"])[0].message
    )


def test_requests_release_files_404(airlock_client, release_files_stubber):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )
    factories.write_request_file(
        release_request, "group", "test/file.txt", "test", approved=True
    )
    factories.complete_independent_review(release_request)

    response = requests.Response()
    response.status_code = 404
    api404 = requests.HTTPError(response=response)
    release_files_stubber(release_request, body=api404)

    response = airlock_client.post("/requests/release/request_id", follow=True)
    assert response.status_code == 200
    assert (
        list(response.context["messages"])[0].message
        == "Error releasing files; please contact tech-support."
    )


@pytest.mark.parametrize(
    "urlpath,post_data,login_as,status,stub",
    [
        (
            "/requests/view/request-id/default/",
            None,
            "output_checker",
            RequestStatus.PENDING,
            False,
        ),
        (
            "/requests/view/request-id/default/file.txt",
            None,
            "output_checker",
            RequestStatus.PENDING,
            False,
        ),
        (
            "/requests/content/request-id/default/file.txt",
            None,
            "output_checker",
            RequestStatus.PENDING,
            False,
        ),
        ("/requests/submit/request-id", {}, "author", RequestStatus.PENDING, False),
        (
            "/requests/reject/request-id",
            {},
            "output_checker",
            RequestStatus.REVIEWED,
            False,
        ),
        (
            "/requests/release/request-id",
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
    factories.create_user("output_checker", output_checker=True)
    airlock_client.login(username=login_as, output_checker=True)

    initial_status = (
        RequestStatus.SUBMITTED if status == RequestStatus.REVIEWED else status
    )
    release_request = factories.create_release_request(
        "test-workspace", id="request-id", user=author, status=initial_status
    )

    factories.write_request_file(
        release_request,
        "default",
        "file.txt",
        contents="test",
        approved=status == RequestStatus.REVIEWED,
    )
    if status == RequestStatus.REVIEWED:
        factories.complete_independent_review(release_request)
    if stub:
        release_files_stubber(release_request)

    if post_data is not None:
        airlock_client.post(urlpath, post_data)
    else:
        airlock_client.get(urlpath)
    traces = get_trace()
    last_trace = traces[-1]
    assert last_trace.attributes == {"release_request": "request-id"}


def test_group_edit_success(airlock_client):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(author)

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

    release_request = bll.get_release_request(release_request.id, author)

    assert release_request.filegroups["group"].context == "foo"
    assert release_request.filegroups["group"].controls == "bar"


def test_group_edit_no_change(airlock_client, bll):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(release_request, "group", "file.txt")
    bll.group_edit(release_request, "group", context="foo", controls="bar", user=author)

    airlock_client.login_with_user(author)

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

    release_request = bll.get_release_request(release_request.id, author)

    assert release_request.filegroups["group"].context == "foo"
    assert release_request.filegroups["group"].controls == "bar"


def test_group_edit_bad_user(airlock_client):
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(release_request, "group", "file.txt")

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
    factories.write_request_file(release_request, "group", "file.txt")

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


def test_group_comment_create_success(airlock_client):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(author)

    response = airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={"comment": "opinion"},
        follow=True,
    )

    assert response.status_code == 200
    messages = list(response.context.get("messages", []))
    assert messages[0].message == "Comment added"

    release_request = bll.get_release_request(release_request.id, author)

    assert release_request.filegroups["group"].comments[0].comment == "opinion"
    assert release_request.filegroups["group"].comments[0].author == "author"


def test_group_comment_create_bad_user(airlock_client):
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["other"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(other)

    response = airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={"comment": "comment"},
        follow=True,
    )

    assert response.status_code == 403


def test_group_comment_create_bad_form(airlock_client):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(author)

    response = airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={},
        follow=True,
    )

    assert response.status_code == 200
    messages = list(response.context.get("messages", []))
    assert messages[0].message == "comment: This field is required."


def test_group_comment_create_bad_group(airlock_client):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(author)

    response = airlock_client.post(
        f"/requests/comment/create/{release_request.id}/badgroup",
        data={"comment": "comment"},
        follow=True,
    )

    assert response.status_code == 404


def test_group_comment_delete(airlock_client):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(author)
    airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={"comment": "typo comment"},
        follow=True,
    )

    airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={"comment": "not-a-typo comment"},
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


def test_group_comment_delete_bad_form(airlock_client):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(author)
    airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={"comment": "typo comment"},
        follow=True,
    )

    release_request = bll.get_release_request(release_request.id, author)
    assert len(release_request.filegroups["group"].comments) == 1
    comment = release_request.filegroups["group"].comments[0]

    response = airlock_client.post(
        f"/requests/comment/delete/{release_request.id}/group",
        data={},
        follow=True,
    )

    assert response.status_code == 200
    messages = list(response.context.get("messages", []))
    assert messages[0].message == "comment_id: This field is required."

    release_request = bll.get_release_request(release_request.id, author)
    assert len(release_request.filegroups["group"].comments) == 1
    assert release_request.filegroups["group"].comments[0].id == comment.id


def test_group_comment_delete_bad_group(airlock_client):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(author)
    airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={"comment": "comment A"},
        follow=True,
    )

    release_request = bll.get_release_request(release_request.id, author)
    assert len(release_request.filegroups["group"].comments) == 1
    comment = release_request.filegroups["group"].comments[0]

    response = airlock_client.post(
        f"/requests/comment/delete/{release_request.id}/badgroup",
        data={"comment_id": comment.id},
        follow=True,
    )

    assert response.status_code == 404
    release_request = bll.get_release_request(release_request.id, author)
    assert len(release_request.filegroups["group"].comments) == 1
    assert release_request.filegroups["group"].comments[0].id == comment.id


def test_group_comment_delete_missing_comment(airlock_client):
    author = factories.create_user("author", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(release_request, "group", "file.txt")

    airlock_client.login_with_user(author)
    airlock_client.post(
        f"/requests/comment/create/{release_request.id}/group",
        data={"comment": "comment A"},
        follow=True,
    )

    release_request = bll.get_release_request(release_request.id, author)
    assert len(release_request.filegroups["group"].comments) == 1

    bad_comment_id = 50
    assert not release_request.filegroups["group"].comments[0].id == bad_comment_id

    response = airlock_client.post(
        f"/requests/comment/delete/{release_request.id}/group",
        data={"comment_id": bad_comment_id},
        follow=True,
    )

    assert response.status_code == 404
    release_request = bll.get_release_request(release_request.id, author)
    assert len(release_request.filegroups["group"].comments) == 1
