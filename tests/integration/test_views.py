import pytest
from django.conf import settings

from tests.factories import WorkspaceFactory


pytestmark = pytest.mark.django_db


def test_index(client):
    response = client.get("/")
    assert "Hello World" in response.rendered_content


@pytest.fixture
def client_with_user(client):
    def _client(session_user=None):
        session = client.session
        session["user"] = session_user
        session.save()
        return client

    return _client


@pytest.fixture
def client_with_permission(client_with_user):
    output_checker = {"id": 1, "is_output_checker": True}
    yield client_with_user(output_checker)


workspace_name = "test-workspace"
request_id = "test-request"


@pytest.fixture
def tmp_workspace():
    return WorkspaceFactory(workspace_name)


@pytest.fixture
def tmp_request(tmp_workspace):
    return tmp_workspace.create_request(request_id)


def test_workspace_view_index(client_with_permission, tmp_workspace):
    tmp_workspace.write_file("file.txt")
    response = client_with_permission.get(f"/workspaces/{tmp_workspace.name}/")
    assert "file.txt" in response.rendered_content


def test_workspace_does_not_exist(client_with_permission):
    response = client_with_permission.get("/workspaces/bad/")
    assert response.status_code == 404


def test_workspace_view_with_directory(client_with_permission, tmp_workspace):
    tmp_workspace.write_file("some_dir/file.txt")
    response = client_with_permission.get(f"/workspaces/{tmp_workspace.name}/some_dir/")
    assert "file.txt" in response.rendered_content


def test_workspace_view_with_file(client_with_permission, tmp_workspace):
    tmp_workspace.write_file("file.txt", "foobar")
    response = client_with_permission.get(f"/workspaces/{tmp_workspace.name}/file.txt")
    assert "foobar" in response.rendered_content


def test_workspace_view_with_404(client_with_permission, tmp_workspace):
    response = client_with_permission.get(
        f"/workspaces/{tmp_workspace.name}/no_such_file.txt"
    )
    assert response.status_code == 404


def test_workspace_view_redirects_to_directory(client_with_permission, tmp_workspace):
    tmp_workspace.mkdir("some_dir")
    response = client_with_permission.get(f"/workspaces/{tmp_workspace.name}/some_dir")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/workspaces/{tmp_workspace.name}/some_dir/"


def test_workspace_view_redirects_to_file(client_with_permission, tmp_workspace):
    tmp_workspace.write_file("file.txt")
    response = client_with_permission.get(f"/workspaces/{tmp_workspace.name}/file.txt/")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/workspaces/{tmp_workspace.name}/file.txt"


def test_workspace_view_index_no_user(client, tmp_workspace):
    tmp_workspace.mkdir("some_dir")
    response = client.get(f"/workspaces/{tmp_workspace.name}/")
    assert response.status_code == 403


def test_workspace_view_with_directory_no_user(client, tmp_workspace):
    tmp_workspace.mkdir("some_dir")
    response = client.get(f"/workspaces/{tmp_workspace.name}/some_dir/")
    assert response.status_code == 403


def test_workspace_view_index_no_permission(client_with_user, tmp_workspace):
    forbidden_client = client_with_user({"id": 1, "workspaces": ["another-workspace"]})
    response = forbidden_client.get(f"/workspaces/{tmp_workspace.name}/")
    assert response.status_code == 403


def test_workspace_view_with_directory_no_permission(client_with_user, tmp_workspace):
    tmp_workspace.mkdir("some_dir")
    forbidden_client = client_with_user({"id": 1, "workspaces": ["another-workspace"]})
    response = forbidden_client.get(f"/workspaces/{tmp_workspace.name}/some_dir/")
    assert response.status_code == 403


def test_workspaces_index_no_user(client):
    response = client.get("/workspaces/")
    assert response.status_code == 403


def test_workspaces_index_shows_workspace_dirs_only(
    client_with_permission, tmp_workspace
):
    WorkspaceFactory("test1")
    (settings.WORKSPACE_DIR / "file.txt").touch()
    response = client_with_permission.get("/workspaces/")
    assert response.status_code == 200
    assert len(list(response.context["container"].workspaces)) == 2
    assert "file.txt" not in response.rendered_content


def test_workspaces_index_user_permitted_workspaces(client_with_user, tmp_workspace):
    permitted_client = client_with_user({"id": 1, "workspaces": ["test1"]})
    WorkspaceFactory("test1")
    WorkspaceFactory("test2")
    response = permitted_client.get("/workspaces/")
    workspace_names = {ws.name for ws in response.context["container"].workspaces}
    assert workspace_names == {"test1"}
    assert "test2" not in response.rendered_content


def test_request_view_index_no_user(client, tmp_request):
    response = client.get(
        f"/requests/{tmp_request.workspace}/{tmp_request.request_id}/"
    )
    assert response.status_code == 403


def test_request_view_index(client_with_permission, tmp_request):
    tmp_request.write_file("file.txt")
    response = client_with_permission.get(
        f"/requests/{tmp_request.workspace}/{tmp_request.request_id}/"
    )
    assert "file.txt" in response.rendered_content


def test_request_workspace_does_not_exist(client_with_permission):
    response = client_with_permission.get("/requests/bad/id/")
    assert response.status_code == 404


def test_request_id_does_not_exist(client_with_permission, tmp_workspace):
    response = client_with_permission.get(f"/requests/{tmp_workspace.name}/bad_id/")
    assert response.status_code == 404


def test_request_view_with_directory(client_with_permission, tmp_request):
    tmp_request.write_file("some_dir/file.txt")
    response = client_with_permission.get(
        f"/requests/{tmp_request.workspace}/{tmp_request.request_id}/some_dir/"
    )
    assert "file.txt" in response.rendered_content


def test_request_view_with_file(client_with_permission, tmp_request):
    tmp_request.write_file("file.txt", "foobar")
    response = client_with_permission.get(
        f"/requests/{tmp_request.workspace}/{tmp_request.request_id}/file.txt"
    )
    assert "foobar" in response.rendered_content


def test_request_view_with_404(client_with_permission, tmp_request):
    response = client_with_permission.get(
        f"/requests/{tmp_request.workspace}/{tmp_request.request_id}/no_such_file.txt"
    )
    assert response.status_code == 404


def test_request_view_redirects_to_directory(client_with_permission, tmp_request):
    tmp_request.mkdir("some_dir")
    response = client_with_permission.get(
        f"/requests/{tmp_request.workspace}/{tmp_request.request_id}/some_dir"
    )
    assert response.status_code == 302
    assert (
        response.headers["Location"]
        == f"/requests/{tmp_request.workspace}/{tmp_request.request_id}/some_dir/"
    )


def test_request_view_redirects_to_file(client_with_permission, tmp_request):
    tmp_request.write_file("file.txt")
    response = client_with_permission.get(
        f"/requests/{tmp_request.workspace}/{tmp_request.request_id}/file.txt/"
    )
    assert response.status_code == 302
    assert (
        response.headers["Location"]
        == f"/requests/{tmp_request.workspace}/{tmp_request.request_id}/file.txt"
    )
