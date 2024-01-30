import pytest


def test_index(client):
    response = client.get("/")
    assert "Hello World" in response.rendered_content


workspace_name = "test"


@pytest.fixture
def tmp_workspace(tmp_path, settings):
    settings.WORKSPACE_DIR = tmp_path
    workspace_dir = tmp_path / workspace_name
    workspace_dir.mkdir()
    return workspace_dir


def test_workspace_view_index(client, tmp_workspace):
    (tmp_workspace / "file.txt").touch()
    response = client.get(f"/workspace/{workspace_name}/")
    assert "file.txt" in response.rendered_content


def test_workspace_does_not_exist(client):
    response = client.get("/workspace/bad/")
    assert response.status_code == 404


def test_workspace_view_with_directory(client, tmp_workspace):
    (tmp_workspace / "some_dir").mkdir()
    (tmp_workspace / "some_dir/file.txt").touch()
    response = client.get(f"/workspace/{workspace_name}/some_dir/")
    assert "file.txt" in response.rendered_content


def test_workspace_view_with_file(client, tmp_workspace):
    (tmp_workspace / "file.txt").write_text("foobar")
    response = client.get(f"/workspace/{workspace_name}/file.txt")
    assert "foobar" in response.rendered_content


def test_workspace_view_with_404(client, tmp_workspace):
    response = client.get(f"/workspace/{workspace_name}/no_such_file.txt")
    assert response.status_code == 404


def test_workspace_view_redirects_to_directory(client, tmp_workspace):
    (tmp_workspace / "some_dir").mkdir()
    response = client.get(f"/workspace/{workspace_name}/some_dir")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/workspace/{workspace_name}/some_dir/"


def test_workspace_view_redirects_to_file(client, tmp_workspace):
    (tmp_workspace / "file.txt").touch()
    response = client.get(f"/workspace/{workspace_name}/file.txt/")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/workspace/{workspace_name}/file.txt"
