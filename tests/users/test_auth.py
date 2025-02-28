from tests import factories
from users import auth


def test_authenticate_no_credentials(rf):
    backend = auth.Level4AuthenticationBackend()
    assert backend.authenticate(rf.get("/")) is None


def test_authenticate_success(rf, auth_api_stubber):
    user = factories.create_airlock_user()
    auth_api_stubber("authenticate", json=user.api_data)
    backend = auth.Level4AuthenticationBackend()
    assert backend.authenticate(rf.get("/"), user.username, "TOKEN") == user


def test_authenticate_failure(rf, auth_api_stubber):
    auth_api_stubber("authenticate", status=403)
    backend = auth.Level4AuthenticationBackend()
    assert backend.authenticate(rf.get("/"), "username", "token") is None


def test_refresh_success(rf, auth_api_stubber):
    user = factories.create_airlock_user()
    auth_api_stubber("authorise", json=user.api_data)
    request = rf.get("/")
    request.user = user
    backend = auth.Level4AuthenticationBackend()
    assert backend.refresh(request) == user


def test_refresh_failure(rf, auth_api_stubber):
    user = factories.create_airlock_user()
    auth_api_stubber("authorise", status=403)
    request = rf.get("/")
    request.user = user
    backend = auth.Level4AuthenticationBackend()
    assert backend.refresh(request) is None


def test_get_user():
    backend = auth.Level4AuthenticationBackend()
    assert backend.get_user("foo") is None
    user = factories.create_airlock_user(username="foo")
    assert backend.get_user("foo") == user
