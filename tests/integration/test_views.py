import pytest


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


workspace_name = "test"


@pytest.fixture
def tmp_workspace(tmp_path, settings):
    settings.WORKSPACE_DIR = tmp_path
    workspace_dir = tmp_path / workspace_name
    workspace_dir.mkdir()
    return workspace_dir


def test_workspace_view_index(client_with_permission, tmp_workspace):
    (tmp_workspace / "file.txt").touch()
    response = client_with_permission.get(f"/workspaces/{workspace_name}/")
    assert "file.txt" in response.rendered_content


def test_workspace_does_not_exist(client_with_permission):
    response = client_with_permission.get("/workspaces/bad/")
    assert response.status_code == 404


def test_workspace_view_with_directory(client_with_permission, tmp_workspace):
    (tmp_workspace / "some_dir").mkdir()
    (tmp_workspace / "some_dir/file.txt").touch()
    response = client_with_permission.get(f"/workspaces/{workspace_name}/some_dir/")
    assert "file.txt" in response.rendered_content


def test_workspace_view_with_file(client_with_permission, tmp_workspace):
    (tmp_workspace / "file.txt").write_text("foobar")
    response = client_with_permission.get(f"/workspaces/{workspace_name}/file.txt")
    assert "foobar" in response.rendered_content


def test_workspace_view_with_404(client_with_permission, tmp_workspace):
    response = client_with_permission.get(
        f"/workspaces/{workspace_name}/no_such_file.txt"
    )
    assert response.status_code == 404


def test_workspace_view_redirects_to_directory(client_with_permission, tmp_workspace):
    (tmp_workspace / "some_dir").mkdir()
    response = client_with_permission.get(f"/workspaces/{workspace_name}/some_dir")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/workspaces/{workspace_name}/some_dir/"


def test_workspace_view_redirects_to_file(client_with_permission, tmp_workspace):
    (tmp_workspace / "file.txt").touch()
    response = client_with_permission.get(f"/workspaces/{workspace_name}/file.txt/")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/workspaces/{workspace_name}/file.txt"


def test_workspace_view_index_no_user(client, tmp_workspace):
    response = client.get(f"/workspaces/{workspace_name}/")
    assert response.status_code == 403


def test_workspace_view_with_directory_no_user(client, tmp_workspace):
    (tmp_workspace / "some_dir").mkdir()
    response = client.get(f"/workspaces/{workspace_name}/some_dir/")
    assert response.status_code == 403


def test_workspace_view_index_no_permission(client_with_user, tmp_workspace):
    forbidden_client = client_with_user({"id": 1, "workspaces": ["another-workspace"]})
    response = forbidden_client.get(f"/workspaces/{workspace_name}/")
    assert response.status_code == 403


def test_workspace_view_with_directory_no_permission(client_with_user, tmp_workspace):
    (tmp_workspace / "some_dir").mkdir()
    forbidden_client = client_with_user({"id": 1, "workspaces": ["another-workspace"]})
    response = forbidden_client.get(f"/workspaces/{workspace_name}/some_dir/")
    assert response.status_code == 403


def test_workspaces_index_no_user(client, tmp_workspace):
    response = client.get("/workspaces/")
    assert response.status_code == 200
    assert list(response.context["container"].workspaces) == []


def test_workspaces_index_shows_workspace_dirs_only(
    client_with_permission, settings, tmp_workspace
):
    (settings.WORKSPACE_DIR / "test1").mkdir()
    (settings.WORKSPACE_DIR / "file.txt").touch()
    response = client_with_permission.get("/workspaces/")
    assert response.status_code == 200
    assert len(list(response.context["container"].workspaces)) == 2
    assert "file.txt" not in response.rendered_content


def test_workspaces_index_user_permitted_workspaces(
    client_with_user, settings, tmp_workspace
):
    permitted_client = client_with_user({"id": 1, "workspaces": ["test1"]})
    (settings.WORKSPACE_DIR / "test1").mkdir()
    (settings.WORKSPACE_DIR / "file.txt").touch()
    response = permitted_client.get("/workspaces/")
    workspace_names = {ws.name for ws in response.context["container"].workspaces}
    assert workspace_names == {"test1"}
    assert "file.txt" not in response.rendered_content
