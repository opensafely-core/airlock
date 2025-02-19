import json
import time
from pathlib import Path

import requests
from django.conf import settings


session = requests.Session()


class LoginError(Exception):
    pass


def get_user_data(user: str, token: str):
    if settings.AIRLOCK_DEV_USERS_FILE and not settings.AIRLOCK_API_TOKEN:
        return get_user_data_dev(settings.AIRLOCK_DEV_USERS_FILE, user, token)
    else:
        return get_user_data_prod(user, token)


def get_user_data_prod(username: str, token: str):
    api_user = auth_api_call(
        "/releases/authenticate", {"user": username, "token": token}
    )
    api_user["last_refresh"] = time.time()
    return api_user


def get_user_authz(username):
    api_user = auth_api_call("/releases/authorise", {"user": username})
    api_user["last_refresh"] = time.time()
    return api_user


def get_user_data_dev(dev_users_file: Path, user: str, token: str):
    try:
        dev_users = json.loads(dev_users_file.read_text())
    except FileNotFoundError as e:  # pragma: no cover
        e.add_note(
            "You may want to run:\n\n    just load-example-data\n\nto create one."
        )
        raise e
    if user not in dev_users or dev_users[user]["token"] != token:
        raise LoginError("Invalid user or token")
    else:
        details = dev_users[user]["details"]
        # ensure that we never try refresh this user with job-server
        details["last_refresh"] = time.time() + (365 * 24 * 60 * 60)
        return details


def auth_api_call(path, json):
    try:
        response = session.post(
            f"{settings.AIRLOCK_API_ENDPOINT}{path}",
            headers={"Authorization": settings.AIRLOCK_API_TOKEN},
            json=json,
        )
        response.raise_for_status()
    except requests.ConnectionError:  # pragma: nocover
        raise LoginError("Could not connect to jobs.opensafely.org")
    except requests.HTTPError as exc:
        if exc.response.status_code == requests.codes.forbidden:
            # We don't currently get any more detail about failures than this
            raise LoginError("Invalid user or token")
        else:
            raise LoginError("Error when logging in")

    return response.json()
