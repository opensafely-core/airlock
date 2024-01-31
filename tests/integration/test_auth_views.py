import pytest


pytestmark = pytest.mark.django_db


def test_login(client):
    assert "user" not in client.session
    response = client.get("/login/")
    assert client.session["user"]["username"] == "temp_output_checker"
    assert response.url == "/workspaces/"
