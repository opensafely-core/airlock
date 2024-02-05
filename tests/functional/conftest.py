import os
import subprocess
import sys

import pytest
from django.conf import settings
from django.contrib.sessions.models import Session


@pytest.fixture(scope="session", autouse=True)
def set_env():
    # This is required for playwright tests with Django
    # See https://github.com/microsoft/playwright-pytest/issues/29
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "1"


@pytest.fixture(scope="session", autouse=True)
def playwright_install(request):
    # As this can potentially take a long time when it's first run (and as it is
    # subsequently a silent no-op) we disable output capturing so that progress gets
    # displayed to the user
    capmanager = request.config.pluginmanager.getplugin("capturemanager")
    command = [sys.executable, "-m", "playwright", "install", "chromium"]
    # Install with dependencies in CI (but not in docker, as they've already
    # been installed in the image)
    if os.environ.get("CI") and not os.environ.get("DOCKER"):  # pragma: no cover
        command.extend(["--with-deps"])
    with capmanager.global_and_fixture_disabled():
        subprocess.run(command, check=True)


def login_as_user(live_server, context, user_dict):
    """
    Creates a session with relevant user data and
    sets the session cookie.
    """
    session_store = Session.get_session_store_class()()
    session_store["user"] = user_dict
    session_store.save()
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": session_store._session_key,
                "url": live_server.url,
            }
        ]
    )


@pytest.fixture
def output_checker_user(live_server, context):
    login_as_user(
        live_server,
        context,
        {
            "id": "test_output_checker",
            "username": "test_output_checker",
            "workspaces": [],
            "output_checker": True,
        },
    )


@pytest.fixture
def researcher_user(live_server, context):
    login_as_user(
        live_server,
        context,
        {
            "id": "test_researcher",
            "username": "test_researcher",
            "workspaces": ["test-dir1"],
            "output_checker": False,
        },
    )
