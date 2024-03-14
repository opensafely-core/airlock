import pytest

from tests import factories


@pytest.fixture
def client_with_user(client):
    def _client(session_user):
        session_user.setdefault("username", "test")
        user = factories.create_user(**session_user)
        session = client.session
        session["user"] = user.to_dict()
        session.save()
        client.user = user
        return client

    return _client


@pytest.fixture
def client_with_permission(client_with_user):
    output_checker = {"output_checker": True}
    yield client_with_user(output_checker)
