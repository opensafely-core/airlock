import time

import pytest
from django.conf import settings
from opentelemetry import trace

from tests import factories
from tests.conftest import get_trace


@pytest.mark.django_db
def test_middleware_expired_user(airlock_client, responses):
    user = factories.create_airlock_user()
    airlock_client.login_with_user(user)
    factories.create_workspace("new_workspace")

    response = airlock_client.get("/workspaces/view/new_workspace/")
    assert response.status_code == 403

    # skip some time
    session = airlock_client.session
    session["user"]["last_refresh"] = time.time() - (2 * settings.AIRLOCK_AUTHZ_TIMEOUT)
    session.save()

    new_workspaces = user.workspaces.copy()
    new_workspaces["new_workspace"] = {
        "project_details": {"name": "other_project", "ongoing": True},
        "archived": False,
    }

    responses.post(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/authorise",
        status=200,
        json={
            "username": user.username,
            "output_checker": user.output_checker,
            "workspaces": new_workspaces,
        },
    )

    response = airlock_client.get("/workspaces/view/new_workspace/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_middleware_expired_error(airlock_client, responses):
    last_refresh = time.time() - (2 * settings.AIRLOCK_AUTHZ_TIMEOUT)
    user = factories.create_airlock_user(last_refresh=last_refresh)
    airlock_client.login_with_user(user)
    factories.create_workspace("new_workspace")

    responses.post(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/authorise",
        status=500,
    )

    response = airlock_client.get("/workspaces/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_middleware_user_trace(airlock_client, responses):
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
        "user": user.username,
    }
    assert traces["workspace_view"] == {"workspace": "workspace", "user": user.username}


@pytest.mark.django_db
def test_middleware_user_trace_with_no_user(airlock_client, responses):
    # In tests the current span in the middleware is a NonRecordingSpan,
    # so call the endpoint inside another span so we can assert that the
    # user attribute is added during the middleware
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("mock_django_span"):
        response = airlock_client.get("/login/")

    assert response.status_code == 200
    traces = {span.name: span.attributes for span in get_trace()}
    assert traces["mock_django_span"] == {"user": ""}
