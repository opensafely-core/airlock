import pytest

from airlock.users import User


@pytest.fixture
def client_with_user(client):
    def _client(session_user):
        session_user = {"id": 1, "username": "test", **session_user}
        session = client.session
        session["user"] = session_user
        session.save()
        client.user = User.from_session(session)
        return client

    return _client


@pytest.fixture
def client_with_permission(client_with_user):
    output_checker = {"output_checker": True}
    yield client_with_user(output_checker)
