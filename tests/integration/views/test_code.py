import pytest

from airlock.types import UrlPath
from tests import factories


pytestmark = pytest.mark.django_db


def test_code_view_index(airlock_client):
    airlock_client.login(output_checker=True)
    repo = factories.create_repo("workspace")

    response = airlock_client.get(f"/code/view/workspace/{repo.commit}/")
    assert "project.yaml" in response.rendered_content


def test_code_view_index_return_url(airlock_client):
    airlock_client.login(output_checker=True)
    workspace = factories.create_workspace("workspace")
    repo = factories.create_repo(workspace)

    response = airlock_client.get(
        f"/code/view/workspace/{repo.commit}/?return_url={workspace.get_url()}"
    )
    assert "project.yaml" in response.rendered_content
    assert "return-button" in response.rendered_content


def test_code_view_index_request_author(airlock_client):
    airlock_client.login(output_checker=False, workspaces=["workspace"])
    workspace = factories.create_workspace("workspace")
    factories.create_release_request(workspace, user=airlock_client.user)
    repo = factories.create_repo(workspace)

    response = airlock_client.get(f"/code/view/workspace/{repo.commit}/")
    assert "project.yaml" in response.rendered_content
    assert "current-request-button" in response.rendered_content


def test_code_view_index_user_has_workspace_access(airlock_client):
    airlock_client.login(output_checker=False, workspaces=["workspace"])
    workspace = factories.create_workspace("workspace")
    repo = factories.create_repo(workspace)

    response = airlock_client.get(f"/code/view/workspace/{repo.commit}/")
    assert "project.yaml" in response.rendered_content
    assert "workspace-home-button" in response.rendered_content


def test_code_view_file(airlock_client):
    airlock_client.login(output_checker=True)
    repo = factories.create_repo("workspace")

    response = airlock_client.get(f"/code/view/workspace/{repo.commit}/project.yaml")
    assert repo.get_contents_url(UrlPath("project.yaml")) in response.rendered_content


def test_code_view_file_htmx(airlock_client):
    airlock_client.login(output_checker=True)
    repo = factories.create_repo("workspace")

    response = airlock_client.get(
        f"/code/view/workspace/{repo.commit}/project.yaml",
        headers={"HX-Request": "true"},
    )

    assert repo.get_contents_url(UrlPath("project.yaml")) in response.rendered_content
    assert response.template_name == "file_browser/repo/contents.html"
    assert '<ul id="tree"' not in response.rendered_content
    assert "HX-Request" in response.headers["Vary"]


def test_code_view_dir_redirects(airlock_client):
    airlock_client.login(output_checker=True)
    repo = factories.create_repo("workspace", files=[("somedir/foo.txt", "")])

    response = airlock_client.get(f"/code/view/workspace/{repo.commit}/somedir")
    assert response.status_code == 302
    assert (
        response.headers["Location"] == f"/code/view/workspace/{repo.commit}/somedir/"
    )


@pytest.mark.parametrize(
    "code_url,redirected_url",
    [
        ("/code/view/workspace/notexist/", "/workspaces/view/workspace/"),
        (
            "/code/view/workspace/notexist/?return_url=/workspaces/view/workspace/foo.txt",
            "/workspaces/view/workspace/foo.txt",
        ),
    ],
)
def test_code_view_no_repo(airlock_client, code_url, redirected_url):
    airlock_client.login(output_checker=True)
    factories.create_workspace("workspace")

    response = airlock_client.get(code_url)
    assert response.status_code == 302
    assert response.url == redirected_url


@pytest.mark.parametrize(
    "code_url,redirected_url",
    [
        ("/code/view/workspace/abcdefg/", "/workspaces/view/workspace/"),
        (
            "/code/view/workspace/abcdefg/?return_url=/workspaces/view/workspace/foo.txt",
            "/workspaces/view/workspace/foo.txt",
        ),
        (
            "/code/view/workspace/abcdefg/?return_url=http://example.com",
            "/workspaces/view/workspace/",
        ),
    ],
)
def test_code_view_no_commit(airlock_client, code_url, redirected_url):
    airlock_client.login(output_checker=True)
    factories.create_repo("workspace")

    response = airlock_client.get(code_url)
    assert response.status_code == 302
    assert response.url == redirected_url


def test_code_contents_file(airlock_client):
    airlock_client.login(output_checker=True)
    repo = factories.create_repo("workspace")
    response = airlock_client.get(
        f"/code/contents/workspace/{repo.commit}/project.yaml"
    )
    assert response.status_code == 200
    assert response.content == b'<pre class="yaml">\nyaml: true\n</pre>\n'


def test_code_contents_directory(airlock_client):
    airlock_client.login(output_checker=True)
    repo = factories.create_repo("workspace", files=[("somedir/foo.txt", "")])

    response = airlock_client.get(f"/code/view/workspace/{repo.commit}/somedir/")
    assert response.status_code == 200
    assert "foo.txt" in response.rendered_content


def test_code_contents_not_exists(airlock_client):
    airlock_client.login(output_checker=True)
    repo = factories.create_repo("workspace")
    response = airlock_client.get(f"/code/contents/workspace/{repo.commit}/notexist")
    assert response.status_code == 404


def test_code_contents_repo_not_exists(airlock_client):
    airlock_client.login(output_checker=True)
    factories.create_workspace("workspace")
    response = airlock_client.get("/code/contents/workspace/notexist/foo.txt")
    assert response.status_code == 404
