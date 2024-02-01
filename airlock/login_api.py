import requests
from django.conf import settings


class LoginError(Exception):
    pass


def get_user_data(user: str, token: str):
    response = requests.post(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/auth",
        headers={"Authorization": settings.AIRLOCK_API_TOKEN},
        json={"user": user, "token": token},
    )
    # We don't currently get any more detail about failures than this
    if response.status_code == requests.codes.forbidden:
        raise LoginError("Invalid user or token")
    response.raise_for_status()
    return response.json()
