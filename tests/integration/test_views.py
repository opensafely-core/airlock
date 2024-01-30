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
    response = client_with_permission.get(f"/workspace/{workspace_name}/")
    assert "file.txt" in response.rendered_content


def test_workspace_does_not_exist(client_with_permission):
    response = client_with_permission.get("/workspace/bad/")
    assert response.status_code == 404


def test_workspace_view_with_directory(client_with_permission, tmp_workspace):
    (tmp_workspace / "some_dir").mkdir()
    (tmp_workspace / "some_dir/file.txt").touch()
    response = client_with_permission.get(f"/workspace/{workspace_name}/some_dir/")
    assert "file.txt" in response.rendered_content


def test_workspace_view_with_file(client_with_permission, tmp_workspace):
    (tmp_workspace / "file.txt").write_text("foobar")
    response = client_with_permission.get(f"/workspace/{workspace_name}/file.txt")
    assert "foobar" in response.rendered_content


def test_workspace_view_with_404(client_with_permission, tmp_workspace):
    response = client_with_permission.get(
        f"/workspace/{workspace_name}/no_such_file.txt"
    )
    assert response.status_code == 404


def test_workspace_view_redirects_to_directory(client_with_permission, tmp_workspace):
    (tmp_workspace / "some_dir").mkdir()
    response = client_with_permission.get(f"/workspace/{workspace_name}/some_dir")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/workspace/{workspace_name}/some_dir/"


def test_workspace_view_redirects_to_file(client_with_permission, tmp_workspace):
    (tmp_workspace / "file.txt").touch()
    response = client_with_permission.get(f"/workspace/{workspace_name}/file.txt/")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/workspace/{workspace_name}/file.txt"


def test_workspace_view_index_no_user(client, tmp_workspace):
    response = client.get(f"/workspace/{workspace_name}/")
    assert response.status_code == 403


def test_workspace_view_with_directory_no_user(client, tmp_workspace):
    (tmp_workspace / "some_dir").mkdir()
    response = client.get(f"/workspace/{workspace_name}/some_dir/")
    assert response.status_code == 403


def test_workspace_view_index_no_permission(client_with_user, tmp_workspace):
    forbidden_client = client_with_user({"id": 1, "workspaces": ["another-workspace"]})
    response = forbidden_client.get(f"/workspace/{workspace_name}/")
    assert response.status_code == 403


def test_workspace_view_with_directory_no_permission(client_with_user, tmp_workspace):
    (tmp_workspace / "some_dir").mkdir()
    forbidden_client = client_with_user({"id": 1, "workspaces": ["another-workspace"]})
    response = forbidden_client.get(f"/workspace/{workspace_name}/some_dir/")
    assert response.status_code == 403
