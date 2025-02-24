import json
import os
import subprocess
import sys

import pytest
from django.conf import settings
from django.contrib import auth
from django.contrib.sessions.models import Session
from django.test import RequestFactory
from playwright.sync_api import expect

from tests import factories


expect.set_options(timeout=15_000)


@pytest.fixture(scope="session", autouse=True)
def set_env():
    # This is required for playwright tests with Django
    # See https://github.com/microsoft/playwright-pytest/issues/29
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "1"


@pytest.fixture(scope="session", autouse=True)
def playwright_install(request):  # pragma: no cover
    if os.environ.get("PLAYWRIGHT_BROWSER_EXECUTABLE_PATH"):
        # No need to install browsers if we're using a custom
        # executable path
        return
    # As this can potentially take a long time when it's first run (and as it is
    # subsequently a silent no-op) we disable output capturing so that progress gets
    # displayed to the user
    capmanager = request.config.pluginmanager.getplugin("capturemanager")
    command = [sys.executable, "-m", "playwright", "install", "chromium"]
    # Install with dependencies in CI (but not in docker, as they've already
    # been installed in the image)
    if os.environ.get("CI") and not os.environ.get("DOCKER"):
        command.extend(["--with-deps"])
    with capmanager.global_and_fixture_disabled():
        subprocess.run(command, check=True)


def login_as_user(live_server, context, user_dict):
    """
    Creates a session with relevant user data and
    sets the session cookie.
    """
    user = factories.create_airlock_user(**user_dict)

    # Create and attach session
    request = RequestFactory().get("/")
    request.session = Session.get_session_store_class()()  # type: ignore
    request.session.create()
    auth.login(request, user)
    request.session.save()

    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": request.session._session_key,
                "url": live_server.url,
            }
        ]
    )
    return user


@pytest.fixture
def output_checker_user(live_server, context):
    login_as_user(
        live_server,
        context,
        factories.create_api_user(
            username="test_output_checker",
            workspaces={
                "test-dir2": factories.create_api_workspace(project="Project 2")
            },
            output_checker=True,
        ),
    )


@pytest.fixture
def researcher_user(live_server, context):
    login_as_user(
        live_server,
        context,
        factories.create_api_user(
            username="test_researcher",
            workspaces={
                "test-dir1": factories.create_api_workspace(project="Test Project")
            },
            output_checker=False,
        ),
    )


@pytest.fixture
def dev_users(tmp_path, settings):
    settings.AIRLOCK_DEV_USERS_FILE = tmp_path / "dev_users.json"
    settings.AIRLOCK_DEV_USERS_FILE.write_text(
        json.dumps(
            {
                "output_checker": {
                    "token": "output_checker",
                    "details": factories.create_api_user(
                        username="output_checker",
                        output_checker=True,
                        workspaces={},
                    ),
                },
                "output_checker_1": {
                    "token": "output_checker_1",
                    "details": factories.create_api_user(
                        username="output_checker_1",
                        output_checker=True,
                        workspaces={},
                    ),
                },
                "researcher": {
                    "token": "researcher",
                    "details": factories.create_api_user(
                        username="researcher",
                        output_checker=False,
                        workspaces={
                            "test-workspace": factories.create_api_workspace(
                                project="Test Project"
                            )
                        },
                    ),
                },
            }
        )
    )
