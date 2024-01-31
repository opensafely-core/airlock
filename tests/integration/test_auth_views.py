import pytest


pytestmark = pytest.mark.django_db


def test_login(client):
    assert "user" not in client.session
    response = client.get("/login/")
    assert client.session["user"]["username"] == "temp_output_checker"
    assert response.url == "/workspaces/"


def test_logout(client):
    session_user = {"id": 1, "username": "test"}
    session = client.session
    session["user"] = session_user
    session.save()

    response = client.get("/logout/")
    assert response.url == "/"
    assert "user" not in client.session
