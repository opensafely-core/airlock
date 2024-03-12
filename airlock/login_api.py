import json

import requests
from django.conf import settings


class LoginError(Exception):
    pass


def get_user_data(user: str, token: str):
    if settings.AIRLOCK_DEV_USERS_FILE and not settings.AIRLOCK_API_TOKEN:
        return get_user_data_dev(user, token)
    else:
        return get_user_data_prod(user, token)


def get_user_data_prod(user: str, token: str):
    response = requests.post(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/authenticate",
        headers={"Authorization": settings.AIRLOCK_API_TOKEN},
        json={"user": user, "token": token},
    )
    # We don't currently get any more detail about failures than this
    if response.status_code == requests.codes.forbidden:
        raise LoginError("Invalid user or token")
    response.raise_for_status()
    return response.json()


def get_user_data_dev(user: str, token: str):
    try:
        dev_users = json.loads(settings.AIRLOCK_DEV_USERS_FILE.read_text())
    except FileNotFoundError as e:  # pragma: no cover
        e.add_note(
            "You may want to run:\n\n    just load-example-data\n\nto create one."
        )
        raise e
    if user not in dev_users or dev_users[user]["token"] != token:
        raise LoginError("Invalid user or token")
    else:
        return dev_users[user]["details"]
