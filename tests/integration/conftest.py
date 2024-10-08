import pytest
from django.test import Client

from tests import factories


class AirlockClient(Client):
    def login(self, **credentials):
        username = credentials.get("username", "testuser")
        workspaces = credentials.get("workspaces")
        output_checker = credentials.get("output_checker", False)
        user = factories.create_user(username, workspaces, output_checker)
        self.login_with_user(user)

    def login_with_user(self, user):
        session = self.session
        session["user"] = user.to_dict()
        session.save()
        self.user = user


@pytest.fixture
def airlock_client():
    return AirlockClient()
