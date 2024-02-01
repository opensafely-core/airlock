from unittest import mock

import pytest


pytestmark = pytest.mark.django_db


@mock.patch("airlock.login_api.requests.post", autospec=True)
def test_login(requests_post, client, settings):
    settings.AIRLOCK_API_TOKEN = "test_api_token"

    api_response = requests_post.return_value
    api_response.status_code = 200
    api_response.json.return_value = {
        "username": "test_user",
        "output_checker": False,
    }

    assert "user" not in client.session

    response = client.post(
        "/login/",
        {"user": "test_user", "token": "foo bar baz"},
    )

    requests_post.assert_called_with(
        "https://jobs.opensafely.org/api/v2/releases/auth",
        headers={"Authorization": "test_api_token"},
        json={"user": "test_user", "token": "foo bar baz"},
    )

    assert client.session["user"]["username"] == "test_user"
    assert client.session["user"]["output_checker"] is False

    assert response.url == "/workspaces/"


@mock.patch("airlock.login_api.requests.post")
def test_login_invalid_token(requests_post, client, settings):
    settings.AIRLOCK_API_TOKEN = "test_api_token"

    api_response = requests_post.return_value
    api_response.status_code = 403

    response = client.post(
        "/login/",
        {"user": "test_user", "token": "foo bar baz"},
    )

    requests_post.assert_called_with(
        "https://jobs.opensafely.org/api/v2/releases/auth",
        headers={"Authorization": "test_api_token"},
        json={"user": "test_user", "token": "foo bar baz"},
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


def test_logout(client):
    session_user = {"id": 1, "username": "test"}
    session = client.session
    session["user"] = session_user
    session.save()

    response = client.get("/logout/")
    assert response.url == "/"
    assert "user" not in client.session