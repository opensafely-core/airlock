import pytest

from tests import factories


pytestmark = pytest.mark.django_db


def test_login_get(client):
    response = client.get("/login/")
    assert response.status_code == 200
    assert "token_login_form" in response.context


def test_login_already_logged_in(airlock_client):
    airlock_client.login()
    response = airlock_client.get("/login/")
    assert response.status_code == 302
    assert response.url == ("/workspaces/")


def test_login(client, auth_api_stubber):
    auth_api_stubber("authenticate", json=factories.create_api_user())

    response = client.post(
        "/login/",
        {"user": "test_user", "token": "foo bar baz"},
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/workspaces/"

    user = response.wsgi_request.user
    assert user.is_authenticated
    assert user.username == "testuser"
    assert user.output_checker is False
    assert user.workspaces == {
        "workspace": {
            "project_details": {"name": "project", "ongoing": True},
            "archived": False,
        },
    }


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

    response = airlock_client.get("/workspaces/")
    assert response.status_code == 200

    response = airlock_client.get("/logout/")
    assert response.url == "/login/"
