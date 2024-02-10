import pytest
import requests

from airlock.users import User
from tests import factories


pytestmark = pytest.mark.django_db


def test_index(client):
    response = client.get("/")
    assert "Hello World" in response.rendered_content


@pytest.fixture
def client_with_user(client):
    def _client(session_user):
        session_user = {"id": 1, "username": "test", **session_user}
        session = client.session
        session["user"] = session_user
        session.save()
        client.user = User.from_session(session)
        return client

    return _client


@pytest.fixture
def client_with_permission(client_with_user):
    output_checker = {"output_checker": True}
    yield client_with_user(output_checker)


workspace_name = "test-workspace"
request_id = "test-request"


def test_workspace_view(client_with_permission):
    factories.write_workspace_file("workspace", "file.txt")

    response = client_with_permission.get("/workspaces/view/workspace/")
    assert "file.txt" in response.rendered_content


def test_workspace_does_not_exist(client_with_permission):
    response = client_with_permission.get("/workspaces/view/bad/")
    assert response.status_code == 404


def test_workspace_view_with_directory(client_with_permission):
    factories.write_workspace_file("workspace", "some_dir/file.txt")
    response = client_with_permission.get("/workspaces/view/workspace/some_dir/")
    assert "file.txt" in response.rendered_content


def test_workspace_view_with_file(client_with_permission):
    factories.write_workspace_file("workspace", "file.txt", "foobar")
    response = client_with_permission.get("/workspaces/view/workspace/file.txt")
    assert "foobar" in response.rendered_content


def test_workspace_view_with_404(client_with_permission):
    factories.create_workspace("workspace")
    response = client_with_permission.get("/workspaces/view/workspace/no_such_file.txt")
    assert response.status_code == 404


def test_workspace_view_redirects_to_directory(client_with_permission):
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir(parents=True)
    response = client_with_permission.get("/workspaces/view/workspace/some_dir")
    assert response.status_code == 302
    assert response.headers["Location"] == "/workspaces/view/workspace/some_dir/"


def test_workspace_view_redirects_to_file(client_with_permission):
    factories.write_workspace_file("workspace", "file.txt")
    response = client_with_permission.get("/workspaces/view/workspace/file.txt/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/workspaces/view/workspace/file.txt"


def test_workspace_view_index_no_user(client):
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir(parents=True)
    response = client.get("/workspaces/view/workspace/")
    assert response.status_code == 302


def test_workspace_view_with_directory_no_user(client):
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir(parents=True)
    response = client.get("/workspaces/view/workspace/some_dir/")
    assert response.status_code == 302


def test_workspace_view_index_no_permission(client_with_user):
    factories.create_workspace("workspace")
    forbidden_client = client_with_user({"workspaces": ["another-workspace"]})
    response = forbidden_client.get("/workspaces/view/workspace/")
    assert response.status_code == 403


def test_workspace_view_with_directory_no_permission(client_with_user):
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir(parents=True)
    forbidden_client = client_with_user({"workspaces": ["another-workspace"]})
    response = forbidden_client.get("/workspaces/view/workspace/some_dir/")
    assert response.status_code == 403


def test_workspaces_index_no_user(client):
    response = client.get("/workspaces/")
    assert response.status_code == 302


def test_workspaces_index_user_permitted_workspaces(client_with_user):
    permitted_client = client_with_user({"workspaces": ["test1"]})
    factories.create_workspace("test1")
    factories.create_workspace("test2")
    response = permitted_client.get("/workspaces/")
    workspace_names = {ws.name for ws in response.context["workspaces"]}
    assert workspace_names == {"test1"}
    assert "test2" not in response.rendered_content


def test_workspace_request_file_creates(client_with_user):
    client = client_with_user({"workspaces": ["test1"]})
    user = User.from_session(client.session)

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    assert workspace.get_current_request(user) is None

    response = client.post(
        "/workspaces/request-file/test1", data={"path": "test/path.txt"}
    )
    assert response.status_code == 302

    request = workspace.get_current_request(user)
    assert request.request_id.endswith(user.username)
    assert request.get_path("test/path.txt").exists()


def test_workspace_request_file_request_already_exists(client_with_user):
    client = client_with_user({"workspaces": ["test1"]})
    user = User.from_session(client.session)

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")
    request = factories.create_request(workspace, user)

    response = client.post(
        "/workspaces/request-file/test1", data={"path": "test/path.txt"}
    )
    assert response.status_code == 302
    assert workspace.get_current_request(user) == request
    assert request.get_path("test/path.txt").exists()


def test_workspace_request_file_request_path_does_not_exist(client_with_user):
    client = client_with_user({"workspaces": ["test1"]})
    factories.create_workspace("test1")

    response = client.post(
        "/workspaces/request-file/test1", data={"path": "test/path.txt"}
    )

    assert response.status_code == 404


def test_request_index_no_user(client):
    request = factories.create_request("workspace")
    response = client.get(f"/requests/view/{request.request_id}/")
    assert response.status_code == 302


def test_request_view_index(client_with_permission):
    request = factories.create_request("workspace", client_with_permission.user)
    factories.write_request_file(request, "file.txt")
    response = client_with_permission.get(f"/requests/view/{request.request_id}/")
    assert "file.txt" in response.rendered_content


def test_request_workspace_does_not_exist(client_with_permission):
    response = client_with_permission.get("/requests/view/bad/id/")
    assert response.status_code == 404


def test_request_id_does_not_exist(client_with_permission):
    response = client_with_permission.get("/requests/view/bad_id/")
    assert response.status_code == 404


def test_request_view_with_directory(client_with_permission):
    request = factories.create_request("workspace")
    factories.write_request_file(request, "some_dir/file.txt")
    response = client_with_permission.get(
        f"/requests/view/{request.request_id}/some_dir/"
    )
    assert "file.txt" in response.rendered_content


def test_request_view_with_file(client_with_permission):
    request = factories.create_request("workspace")
    factories.write_request_file(request, "file.txt", "foobar")
    response = client_with_permission.get(
        f"/requests/view/{request.request_id}/file.txt"
    )
    assert "foobar" in response.rendered_content


def test_request_view_with_404(client_with_permission):
    request = factories.create_request("workspace")
    response = client_with_permission.get(
        f"/requests/view/{request.request_id}/no_such_file.txt"
    )
    assert response.status_code == 404


def test_request_view_redirects_to_directory(client_with_permission):
    request = factories.create_request("workspace")
    (request.root() / "some_dir").mkdir(parents=True)

    response = client_with_permission.get(
        f"/requests/view/{request.request_id}/some_dir"
    )
    assert response.status_code == 302
    assert (
        response.headers["Location"] == f"/requests/view/{request.request_id}/some_dir/"
    )


def test_request_view_redirects_to_file(client_with_permission):
    request = factories.create_request("workspace")
    factories.write_request_file(request, "file.txt")
    response = client_with_permission.get(
        f"/requests/view/{request.request_id}/file.txt/"
    )
    assert response.status_code == 302
    assert (
        response.headers["Location"] == f"/requests/view/{request.request_id}/file.txt"
    )


def test_request_index_user_permitted_requests(client_with_user):
    permitted_client = client_with_user({"workspaces": ["test1"]})
    user = User.from_session(permitted_client.session)
    request = factories.create_request("test1", user)
    response = permitted_client.get("/requests/")
    request_ids = {r.request_id for r in response.context["requests"]}
    assert request_ids == {request.request_id}


def test_request_index_user_output_checker(client_with_user):
    r1 = factories.create_request("test1")
    r2 = factories.create_request("test2")
    permitted_client = client_with_user({"workspaces": [], "output_checker": True})
    response = permitted_client.get("/requests/")
    request_ids = {r.request_id for r in response.context["requests"]}
    assert request_ids == {r1.request_id, r2.request_id}


def test_requests_request_file_creates(client_with_user):
    client = client_with_user({"workspaces": ["test1"]})
    user = User.from_session(client.session)

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    assert workspace.get_current_request(user) is None

    response = client.post(
        "/workspaces/request-file/test1", data={"path": "test/path.txt"}
    )
    assert response.status_code == 302

    request = workspace.get_current_request(user)
    assert request.request_id.endswith(user.username)
    assert request.get_path("test/path.txt").exists()


def test_requests_request_file_request_already_exists(client_with_user):
    client = client_with_user({"workspaces": ["test1"]})
    user = User.from_session(client.session)

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")
    request = factories.create_request(workspace, user)

    response = client.post(
        "/workspaces/request-file/test1", data={"path": "test/path.txt"}
    )
    assert response.status_code == 302
    assert workspace.get_current_request(user) == request
    assert request.get_path("test/path.txt").exists()


def test_requests_request_file_request_path_does_not_exist(client_with_user):
    client = client_with_user({"workspaces": ["test1"]})
    factories.create_workspace("test1")

    response = client.post(
        "/workspaces/request-file/test1", data={"path": "test/path.txt"}
    )

    assert response.status_code == 404


def test_request_release_files_success(client_with_permission, release_files_stubber):
    request = factories.create_request("workspace", request_id="request_id")
    factories.write_request_file(request, "test/file1.txt", "test1")
    factories.write_request_file(request, "test/file2.txt", "test2")

    api_responses = release_files_stubber(request)
    response = client_with_permission.post("/requests/release/request_id")

    assert response.status_code == 302

    assert api_responses.calls[1].request.body.read() == b"test1"
    assert api_responses.calls[2].request.body.read() == b"test2"


def test_requests_release_files_403(client_with_permission, release_files_stubber):
    request = factories.create_request("workspace", request_id="request_id")
    factories.write_request_file(request, "test/file.txt", "test")

    response = requests.Response()
    response.status_code = 403
    api403 = requests.HTTPError(response=response)
    release_files_stubber(request, body=api403)

    # test 403 is handled
    response = client_with_permission.post("/requests/release/request_id")

    assert response.status_code == 403


def test_requests_release_files_404(client_with_permission, release_files_stubber):
    request = factories.create_request("workspace", request_id="request_id")
    factories.write_request_file(request, "test/file.txt", "test")

    # test 404 results in 500
    response = requests.Response()
    response.status_code = 404
    api403 = requests.HTTPError(response=response)
    release_files_stubber(request, body=api403)

    with pytest.raises(requests.HTTPError):
        client_with_permission.post("/requests/release/request_id")
