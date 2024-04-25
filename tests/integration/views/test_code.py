import pytest

from airlock.types import UrlPath
from tests import factories


pytestmark = pytest.mark.django_db


def test_code_view_index(airlock_client):
    airlock_client.login(output_checker=True)
    repo = factories.create_repo("workspace")

    response = airlock_client.get(f"/code/view/workspace/{repo.commit}/")
    assert "project.yaml" in response.rendered_content


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
    assert response.template_name == "file_browser/contents.html"
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


def test_code_view_no_repo(airlock_client):
    airlock_client.login(output_checker=True)
    factories.create_workspace("workspace")

    response = airlock_client.get("/code/view/workspace/notexist/")
    assert response.status_code == 404


def test_code_view_no_commit(airlock_client):
    airlock_client.login(output_checker=True)
    factories.create_repo("workspace")

    response = airlock_client.get("/code/view/workspace/abcdefg/")
    assert response.status_code == 404


def test_code_contents_file(airlock_client):
    airlock_client.login(output_checker=True)
    repo = factories.create_repo("workspace")
    response = airlock_client.get(
        f"/code/contents/workspace/{repo.commit}/project.yaml"
    )
    assert response.status_code == 200
    assert response.content == b'<pre class="yaml">\nyaml: true\n</pre>\n'


def test_code_contents_not_exists(airlock_client):
    airlock_client.login(output_checker=True)
    repo = factories.create_repo("workspace")
    response = airlock_client.get(f"/code/contents/workspace/{repo.commit}/notexist")
    assert response.status_code == 404