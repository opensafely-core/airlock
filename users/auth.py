import logging

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

        try:
            api_data = login_api.get_user_authz(request.user)
        except login_api.LoginError:
            return None

        return User.from_api_data(api_data)

    def get_user(self, user_id: str) -> User | None:
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
