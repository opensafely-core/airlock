import time
from typing import Self

from django.contrib.auth.models import AbstractBaseUser
from django.db import models


class User(AbstractBaseUser):
    """This model is effectively a local cache of the job-server user information.

    Every time the user logs in, we should update the information here, and we
    should otherwise not modify it.
    """

    USERNAME_FIELD = "user_id"

    # surrogate id. For now, this will be a copy of username, as that's all we
    # have. But we anticipate that job-server API will return use a persistant
    # user id in future, to support users renaming their github accounts.
    user_id = models.TextField(primary_key=True)

    # The JSON api response
    api_data = models.JSONField(default=dict)

    # Time of last authentication refresh
    last_refresh = models.FloatField(default=time.time)

    @property
    def username(self):
        return self.api_data["username"]

    @property
    def workspaces(self):
        return self.api_data.get("workspaces", {})

    @property
    def copiloted_workspaces(self):
        return self.api_data.get("copiloted_workspaces", {})

    @property
    def output_checker(self):
        return self.api_data.get("output_checker", False)

    @classmethod
    def from_api_data(cls, api_data, last_refresh: float | None = None) -> Self:
        user, _ = cls.objects.get_or_create(user_id=api_data["username"])
        user.api_data = api_data
        if last_refresh is None:
            last_refresh = time.time()
        user.last_refresh = last_refresh
        user.save()
        return user
