import json
from pathlib import Path

import requests
from django.conf import settings
from opentelemetry import trace


session = requests.Session()


class LoginUpstreamError(Exception):
    """Indicates connection error or server error when logging in against the job server
    API."""


class LoginClientError(Exception):
    """Indicates a client error when logging in against the job server API."""


class NotAuthenticatedError(Exception):
    """Indicates an authentication failure."""


class NotAuthorizedError(Exception):
    """Indicates an authenticated user is not authorized."""


def get_user_data(user: str, token: str):
    if settings.AIRLOCK_DEV_USERS_FILE and not settings.AIRLOCK_API_TOKEN:
        return get_user_data_dev(settings.AIRLOCK_DEV_USERS_FILE, user, token)
    else:
        return get_user_data_prod(user, token)


def get_user_authz(user):
    if settings.AIRLOCK_DEV_USERS_FILE and not settings.AIRLOCK_API_TOKEN:
        # automatically valid
        return user.api_data
    else:
        return get_user_authz_prod(user.username)


def get_user_data_prod(username: str, token: str):
    try:
        return auth_api_call(
            "/releases/authenticate",
            {"user": username, "token": token},
        )
    except LoginClientError as exc:
        raise NotAuthenticatedError from exc


def get_user_authz_prod(username: str):
    try:
        return auth_api_call("/releases/authorise", json={"user": username})
    except LoginClientError as exc:
        raise NotAuthorizedError from exc


def get_user_data_dev(dev_users_file: Path, user: str, token: str):
    """Look up a user from local dev config instead of API.

    Optionally validate token if passed, otherwise return that users data.
    """
    try:
        dev_users = json.loads(dev_users_file.read_text())
    except FileNotFoundError as e:  # pragma: no cover
        e.add_note(
            "You may want to run:\n\n    just load-example-data\n\nto create one."
        )
        raise
    if user not in dev_users or dev_users[user]["token"] != token:
        raise NotAuthenticatedError("Invalid user or token")
    else:
        return dev_users[user]["details"]


def auth_api_call(path, json):
    span = trace.get_current_span()
    try:
        response = session.post(
            f"{settings.AIRLOCK_API_ENDPOINT}{path}",
            headers={"Authorization": settings.AIRLOCK_API_TOKEN},
            json=json,
        )
        response.raise_for_status()
    except requests.ConnectionError as exc:  # pragma: nocover
        span.record_exception(exc)
        raise LoginUpstreamError from exc
    except requests.HTTPError as exc:
        span.record_exception(exc)
        if exc.response.status_code < 500:
            raise LoginClientError from exc
        else:
            raise LoginUpstreamError from exc

    return response.json()
