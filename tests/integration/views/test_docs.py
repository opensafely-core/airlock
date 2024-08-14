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
