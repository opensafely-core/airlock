import pytest
from django.test import Client

from tests import factories


class AirlockClient(Client):
    # Note: we do not use the normal Client.login api, which expects a username
    # and password, because we do not want to have to stub and api call for
    # every login. Instead, we just pass user details, and we will get or
    # create that user, and log them in.
    def login(self, **user_data):
        username = user_data.get("username", "testuser")
        workspaces = user_data.get("workspaces")
        output_checker = user_data.get("output_checker", False)
        user = factories.create_airlock_user(username, workspaces, output_checker)
        # bypass authentication, just set up the session
        self.force_login(user)
        self.user = user

    def login_with_user(self, user):
        self.force_login(user)
        self.user = user


@pytest.fixture
def airlock_client():
    return AirlockClient()
