import logging
import time

from django.conf import settings
from django.contrib.auth.backends import BaseBackend
from django.http import HttpRequest

from users import login_api
from users.models import User


logger = logging.getLogger(__name__)


class Level4AuthenticationBackend(BaseBackend):
    def authenticate(
        self, request: HttpRequest | None, username=None, token=None, **kwargs
    ) -> User | None:
        """Standard backend authenticate API call.

        Returns the user if successfully authenticate, or None if not
        """
        if not username or not token:
            return None

        try:
            api_data = login_api.get_user_data(username, token)
        except login_api.LoginError:
            return None

        return User.from_api_data(api_data)

    def refresh(self, request: HttpRequest) -> User | None:
        """Refresh a user's data via the API.

        Mimics the authenticate API, and returns None if the refresh failed.
        """
        assert request.user.is_authenticated, "Can only refresh authenticated users"

        return self.update(request.user)

    def needs_refresh(self, user):
        time_since_authz = time.time() - user.last_refresh
        return time_since_authz > settings.AIRLOCK_AUTHZ_TIMEOUT

    def create_or_update(self, username: str) -> User | None:
        """
        Create a user and/or update their data via the API.
        Note: does not authenticate the user.
        """
        user = self.get_user(username)
        if user is None:
            # No user exists; create an initial user instance with the minimum required api
            # data that will allow us to refresh them. last_refresh is set to 0 to ensure that
            # we need to refresh in the next step
            user = User.from_api_data({"username": username}, last_refresh=0)

        # Only update the user if last refresh was longer ago than the allowed web timeout
        if self.needs_refresh(user):
            return self.update(user)

        return user

    def update(self, user) -> User | None:
        try:
            api_data = login_api.get_user_authz(user)
        except login_api.LoginError:
            return None
        return User.from_api_data(api_data)

    def get_user(self, user_id: str) -> User | None:
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
