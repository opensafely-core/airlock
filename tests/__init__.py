import os
import sys

import pytest
import pytest_playwright  # type: ignore
from pytest_playwright.pytest_playwright import (  #type: ignore
    VSCODE_PYTHON_EXTENSION_ID,
    _is_debugger_attached,
)


@pytest.fixture(scope="session")
def browser_type_launch_args_with_browser_executable(pytestconfig):  # pragma: no cover
    launch_options = {}
    # If present, add the executable path from the environment variable
    executable_path = os.environ.get("PLAYWRIGHT_BROWSER_EXECUTABLE_PATH")
    if executable_path:
        print(
            f"Running playwright tests with custom browser executable: {executable_path}"
        )
        launch_options["executable_path"] = executable_path

    # The rest of this fixture is taken directly from pytest-playwright
    # https://github.com/microsoft/playwright-pytest/blob/v0.4.4/pytest_playwright/pytest_playwright.py#L128
    headed_option = pytestconfig.getoption("--headed")
    if headed_option:
        launch_options["headless"] = False  # type: ignore
    elif VSCODE_PYTHON_EXTENSION_ID in sys.argv[0] and _is_debugger_attached():
        # When the VSCode debugger is attached, then launch the browser headed by default
        launch_options["headless"] = False  # type: ignore

    browser_channel_option = pytestconfig.getoption("--browser-channel")
    if browser_channel_option:
        launch_options["channel"] = browser_channel_option
    slowmo_option = pytestconfig.getoption("--slowmo")
    if slowmo_option:
        launch_options["slow_mo"] = slowmo_option
    return launch_options


pytest_playwright.pytest_playwright.browser_type_launch_args = (
    browser_type_launch_args_with_browser_executable
)
