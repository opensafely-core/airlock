import json
import os
import subprocess
import sys

import pytest


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


@pytest.fixture
def users(settings, tmp_path):
    settings.AIRLOCK_DEV_USERS_FILE = tmp_path / "users.json"
    user_data = {
        "test_output_checker": {
            "token": "test_output_checker",
            "details": {
                "username": "test_output_checker",
                "fullname": "Output Checker",
                "output_checker": True,
                "staff": True,
                "workspaces": [],
            },
        },
        "test_researcher": {
            "token": "test_researcher",
            "details": {
                "username": "test_researcher",
                "fullname": "Researcher",
                "output_checker": False,
                "staff": False,
                "workspaces": ["test-dir1"],
            },
        },
    }
    settings.AIRLOCK_DEV_USERS_FILE.write_text(json.dump(user_data))


@pytest.fixture
def logged_in_page(live_server, page, context, users):
    """
    Fixture that logs in a user and returns a page
    with relevant session user data already set.
    """

    def _login(username):
        # Go to the login page and retrieve the csrf token
        page.goto(live_server.url + "/login/")
        csrf_token = page.locator('[name="csrfmiddlewaretoken"]').input_value()
        # Use the browser context's request to post to the login view
        request = context.request
        params = {
            "ignore_https_errors": True,
            "headers": {"Referer": live_server.url, "X-CSRFToken": csrf_token},
            "fail_on_status_code": True,
        }
        request.post(page.url, form={"user": username, "token": username}, **params)
        return page

    return _login
