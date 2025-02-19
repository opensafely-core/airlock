import pytest

from tests import factories


pytestmark = pytest.mark.django_db


def test_login_get(client):
    response = client.get("/login/")
    assert response.status_code == 200
    assert "token_login_form" in response.context


def test_login_already_logged_in(client):
    api_user = factories.create_api_user()
    session = client.session
    session["user"] = api_user
    session.save()
    response = client.get("/login/")
    assert response.status_code == 302
    assert response.url == ("/workspaces/")


def test_login(client, auth_api_stubber):
    auth_api_stubber("authenticate", json=factories.create_api_user())

    assert "user" not in client.session

    response = client.post(
        "/login/",
        {"user": "test_user", "token": "foo bar baz"},
    )

    assert client.session["user"]["username"] == "testuser"
    assert client.session["user"]["output_checker"] is False
    assert client.session["user"]["workspaces"] == {
        "workspace": {
            "project_details": {"name": "project", "ongoing": True},
            "archived": False,
        },
    }

    assert response.url == "/workspaces/"


def test_login_invalid_token(client, auth_api_stubber):
    auth_api_stubber("authenticate", status=403)

    response = client.post(
        "/login/",
        {"user": "test_user", "token": "foo bar baz"},
    )

    assert "user" not in client.session
    assert "Invalid user or token" in response.rendered_content


def test_login_invalid_form(client, settings):
    response = client.post(
        "/login/",
        {"user": "", "token": ""},
    )

    assert "user" not in client.session
    assert "This field is required" in response.rendered_content


def test_logout(airlock_client):
    airlock_client.login()

    response = airlock_client.get("/workspaces", follow=True)
    assert response.status_code == 200

    response = airlock_client.get("/logout/")
    assert response.url == "/login/"
    assert "user" not in airlock_client.session
