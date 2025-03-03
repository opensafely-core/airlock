import time

import pytest
from opentelemetry import trace

from tests import factories
from tests.conftest import get_trace


@pytest.mark.django_db
def test_middleware_expired_user_prod(airlock_client, settings, auth_api_stubber):
    user = factories.create_airlock_user()
    airlock_client.login_with_user(user)
    factories.create_workspace("new_workspace")

    response = airlock_client.get("/workspaces/view/new_workspace/")
    assert response.status_code == 403
    refresh = user.last_refresh

    # skip some time
    user.last_refresh = time.time() - (2 * settings.AIRLOCK_AUTHZ_TIMEOUT)
    user.save()

    new_workspaces = user.workspaces.copy()
    new_workspaces["new_workspace"] = factories.create_api_workspace()

    auth_api_stubber(
        "authorise",
        json={
            "username": user.username,
            "output_checker": user.output_checker,
            "workspaces": new_workspaces,
        },
    )

    response = airlock_client.get("/workspaces/view/new_workspace/")
    assert response.status_code == 200
    # check last_refresh was updated
    user.refresh_from_db()
    assert user.last_refresh > refresh


@pytest.mark.django_db
def test_middleware_expired_user_dev(airlock_client, settings):
    # doesn't need to exist on disk, just be set in config
    settings.AIRLOCK_DEV_USERS_FILE = "path/to/file"
    airlock_client.login()
    user = airlock_client.user
    factories.create_workspace("workspace")

    response = airlock_client.get("/workspaces/view/workspace/")
    assert response.status_code == 200
    refresh = user.last_refresh

    # skip some time
    user.last_refresh = time.time() - (2 * settings.AIRLOCK_AUTHZ_TIMEOUT)
    user.save()

    response = airlock_client.get("/workspaces/view/workspace/")
    assert response.status_code == 200
    # check last_refresh was updated
    user.refresh_from_db()
    assert user.last_refresh > refresh


@pytest.mark.django_db
def test_middleware_expired_error(airlock_client, settings, auth_api_stubber):
    last_refresh = time.time() - (2 * settings.AIRLOCK_AUTHZ_TIMEOUT)
    user = factories.create_airlock_user(last_refresh=last_refresh)
    airlock_client.login_with_user(user)
    auth_api_stubber("authorise", status=500)
    response = airlock_client.get("/workspaces/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_middleware_user_trace(airlock_client):
    user = factories.create_airlock_user(workspaces=["workspace"])
    airlock_client.login_with_user(user)
    factories.create_workspace("workspace")

    # In tests the current span in the middleware is a NonRecordingSpan,
    # so call the endpoint inside another span so we can assert that the
    # user is added during the middleware
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("mock_django_span"):
        response = airlock_client.get("/workspaces/view/workspace/")

    assert response.status_code == 200

    traces = {span.name: span.attributes for span in get_trace()}
    assert traces["mock_django_span"] == {
        "workspace": "workspace",
        "username": user.username,
        "user_id": user.user_id,
    }
    assert traces["workspace_view"] == {
        "workspace": "workspace",
        "username": user.username,
        "user_id": user.user_id,
    }


@pytest.mark.django_db
def test_middleware_user_trace_with_no_user(airlock_client):
    # In tests the current span in the middleware is a NonRecordingSpan,
    # so call the endpoint inside another span so we can assert that the
    # user attribute is added during the middleware
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("mock_django_span"):
        response = airlock_client.get("/login/")

    assert response.status_code == 200
    traces = {span.name: span.attributes for span in get_trace()}
    assert traces["mock_django_span"] == {
        "user_id": "anonymous",
        "username": "anonymous",
    }
