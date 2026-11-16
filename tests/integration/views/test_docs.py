import pytest


pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "path",
    [
        "/docs/",
        "/docs/index.html",
        "/docs/how-tos/",
        "/docs/explanation/",
        "/docs/reference/",
        "/docs/img/favicon.svg",
    ],
)
def test_docs_index(airlock_client, path):
    response = airlock_client.get(path)
    assert response.status_code == 200


def test_invalid_docs_url_redirects(airlock_client):
    # an invalid docs URL redirects to the docs home page
    response = airlock_client.get("/docs/foo")
    assert response.status_code == 302
    assert response.url == "/docs/"


def test_docs_directory_without_trailing_slash_redirects(airlock_client):
    response = airlock_client.get("/docs/explanation")
    assert response.status_code == 302
    assert response.url == "/docs/explanation/"
