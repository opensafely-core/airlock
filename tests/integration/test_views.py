import pytest


def test_index(client):
    response = client.get("/")
    assert "Hello World" in response.rendered_content


@pytest.fixture
def tmp_workspace(tmp_path, settings):
    settings.WORKSPACE_DIR = tmp_path
    return tmp_path


def test_file_browser_index(client, tmp_workspace):
    (tmp_workspace / "file.txt").touch()
    response = client.get("/file-browser/")
    assert "file.txt" in response.rendered_content


def test_file_browser_with_directory(client, tmp_workspace):
    (tmp_workspace / "some_dir").mkdir()
    (tmp_workspace / "some_dir/file.txt").touch()
    response = client.get("/file-browser/some_dir/")
    assert "file.txt" in response.rendered_content


def test_file_browser_with_file(client, tmp_workspace):
    (tmp_workspace / "file.txt").write_text("foobar")
    response = client.get("/file-browser/file.txt")
    assert "foobar" in response.rendered_content


def test_file_browser_with_404(client, tmp_workspace):
    response = client.get("/file-browser/no_such_file.txt")
    assert response.status_code == 404


def test_file_browser_redirects_to_directory(client, tmp_workspace):
    (tmp_workspace / "some_dir").mkdir()
    response = client.get("/file-browser/some_dir")
    assert response.status_code == 302
    assert response.headers["Location"] == "/file-browser/some_dir/"


def test_file_browser_redirects_to_file(client, tmp_workspace):
    (tmp_workspace / "file.txt").touch()
    response = client.get("/file-browser/file.txt/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/file-browser/file.txt"
