from io import BytesIO

import pytest
import requests

from airlock.business_logic import Status
from tests import factories


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


def test_request_view_with_directory(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    factories.write_request_file(release_request, "group", "some_dir/file.txt")
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/some_dir/"
    )
    assert response.status_code == 200
    assert "file.txt" in response.rendered_content


def test_request_view_with_file(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
    factories.write_request_file(release_request, "group", "file.txt", "foobar")
    response = airlock_client.get(f"/requests/view/{release_request.id}/group/file.txt")
    assert response.status_code == 200
    assert (
        release_request.get_contents_url("group/file.txt") in response.rendered_content
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
        release_request.get_contents_url("group/file.txt") in response.rendered_content
    )
    assert response.template_name == "file_browser/contents.html"
    assert '<ul id="tree"' not in response.rendered_content


def test_request_view_with_submitted_request(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace", status=Status.SUBMITTED
    )
    response = airlock_client.get(f"/requests/view/{release_request.id}", follow=True)
    assert "Reject Request" in response.rendered_content
    assert "Release Files" in response.rendered_content


def test_request_view_with_authored_request_file(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        user=airlock_client.user,
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "file.txt", "foobar")
    response = airlock_client.get(
        f"/requests/view/{release_request.id}/group/file.txt", follow=True
    )
    assert "Remove this file" in response.rendered_content


def test_request_view_with_404(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace")
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
    assert response.content == b"<pre>test</pre>"


def test_request_contents_dir(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace", id="id")
    factories.write_request_file(
        release_request, "default", "foo/file.txt", contents="test"
    )
    response = airlock_client.get("/requests/content/id/default/foo")
    assert response.status_code == 404


def test_request_contents_not_exists(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request("workspace", id="id")
    factories.write_request_file(
        release_request, "default", "foo/file.txt", contents="test"
    )
    response = airlock_client.get("/requests/content/id/default/notexists.txt")
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
    else:
        assert response.status_code == 403


def test_request_index_user_permitted_requests(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_release_request("test1", airlock_client.user)
    response = airlock_client.get("/requests/")
    authored_ids = {r.id for r in response.context["authored_requests"]}
    outstanding_ids = {r.id for r in response.context["outstanding_requests"]}
    assert authored_ids == {release_request.id}
    assert outstanding_ids == set()


def test_request_index_user_output_checker(airlock_client):
    airlock_client.login(workspaces=["test_workspace"], output_checker=True)
    other = factories.create_user("other")
    r1 = factories.create_release_request(
        "test_workspace", user=airlock_client.user, status=Status.SUBMITTED
    )
    r2 = factories.create_release_request(
        "other_workspace", user=other, status=Status.SUBMITTED
    )
    response = airlock_client.get("/requests/")

    authored_ids = {r.id for r in response.context["authored_requests"]}
    outstanding_ids = {r.id for r in response.context["outstanding_requests"]}
    assert authored_ids == {r1.id}
    assert outstanding_ids == {r2.id}


def test_request_submit_author(airlock_client):
    airlock_client.login(workspaces=["test1"])
    release_request = factories.create_release_request(
        "test1", user=airlock_client.user
    )
    factories.write_request_file(release_request, "group", "path/test.txt")

    response = airlock_client.post(f"/requests/submit/{release_request.id}")

    assert response.status_code == 302
    persisted_request = factories.bll.get_release_request(
        release_request.id, airlock_client.user
    )
    assert persisted_request.status == Status.SUBMITTED


def test_request_submit_not_author(airlock_client):
    airlock_client.login(workspaces=["test1"])
    other_user = factories.create_user("other", [], False)
    release_request = factories.create_release_request(
        "test1", user=other_user, status=Status.PENDING
    )
    factories.write_request_file(release_request, "group", "path/test.txt")

    response = airlock_client.post(f"/requests/submit/{release_request.id}")

    assert response.status_code == 403
    persisted_request = factories.bll.get_release_request(
        release_request.id, airlock_client.user
    )
    assert persisted_request.status == Status.PENDING


def test_request_reject_output_checker(airlock_client):
    airlock_client.login(output_checker=True)
    author = factories.create_user("author", ["test1"], False)
    release_request = factories.create_release_request(
        "test1",
        user=author,
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "path/test.txt")

    response = airlock_client.post(f"/requests/reject/{release_request.id}")

    assert response.status_code == 302
    persisted_request = factories.bll.get_release_request(
        release_request.id, airlock_client.user
    )
    assert persisted_request.status == Status.REJECTED


def test_request_reject_not_output_checker(airlock_client):
    release_request = factories.create_release_request(
        "test1",
        status=Status.SUBMITTED,
    )
    airlock_client.login(workspaces=[release_request.workspace], output_checker=False)
    factories.write_request_file(release_request, "group", "path/test.txt")

    response = airlock_client.post(f"/requests/reject/{release_request.id}")

    assert response.status_code == 403
    persisted_request = factories.bll.get_release_request(
        release_request.id, airlock_client.user
    )
    assert persisted_request.status == Status.SUBMITTED


def test_request_release_files_success(airlock_client, release_files_stubber):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "test/file1.txt", "test1")
    factories.write_request_file(release_request, "group", "test/file2.txt", "test2")

    api_responses = release_files_stubber(release_request)
    response = airlock_client.post("/requests/release/request_id")

    assert response.status_code == 302

    assert api_responses.calls[1].request.body.read() == b"test1"
    assert api_responses.calls[2].request.body.read() == b"test2"


def test_requests_release_workspace_403(airlock_client):
    airlock_client.login(output_checker=False)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "test/file1.txt", "test1")
    response = airlock_client.post("/requests/release/request_id")
    assert response.status_code == 403


def test_requests_release_author_403(airlock_client):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        user=airlock_client.user,
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "test/file1.txt", "test1")
    response = airlock_client.post("/requests/release/request_id")
    assert response.status_code == 403


def test_requests_release_jobserver_403(airlock_client, release_files_stubber):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "test/file.txt", "test")

    response = requests.Response()
    response.status_code = 403
    api403 = requests.HTTPError(response=response)
    release_files_stubber(release_request, body=api403)

    # test 403 is handled
    response = airlock_client.post("/requests/release/request_id")

    assert response.status_code == 403


@pytest.mark.parametrize(
    "content_type,content,should_contain_iframe",
    [
        ("text/plain", b"An error from job-server", False),
        ("text/html", b"<p>An error from job-server</p>", True),
    ],
)
def test_requests_release_jobserver_403_with_debug(
    airlock_client,
    release_files_stubber,
    settings,
    content_type,
    content,
    should_contain_iframe,
):
    airlock_client.login(output_checker=True)
    settings.DEBUG = True
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "default", "test/file.txt", "test")

    response = requests.Response()
    response.status_code = 403
    response.headers = {"Content-Type": content_type}
    response.raw = BytesIO(content)
    api403 = requests.HTTPError(response=response)
    release_files_stubber(release_request, body=api403)

    # test 403 is handled
    response = airlock_client.post("/requests/release/request_id")
    # DEBUG is on, so we return the job-server error
    assert response.status_code == 200
    assert "An error from job-server" in response.rendered_content
    contains_iframe = "<iframe" in response.rendered_content
    assert contains_iframe == should_contain_iframe


def test_requests_release_files_404(airlock_client, release_files_stubber):
    airlock_client.login(output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "test/file.txt", "test")

    # test 404 results in 500
    response = requests.Response()
    response.status_code = 404
    api403 = requests.HTTPError(response=response)
    release_files_stubber(release_request, body=api403)

    with pytest.raises(requests.HTTPError):
        airlock_client.post("/requests/release/request_id")
