import pytest


# Fail the test run if we see any warnings
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if terminalreporter.stats.get("warnings"):  # pragma: no cover
        print("\nWARNINGS DETECTED: Exiting with error")
        if terminalreporter._session.exitstatus == 0:
            terminalreporter._session.exitstatus = 13


@pytest.fixture(autouse=True)
def temp_storage(settings, tmp_path):
    "Ensure all tests have isolated file storage"
    settings.WORK_DIR = tmp_path
    settings.WORKSPACE_DIR = tmp_path / "workspaces"
    settings.REQUEST_DIR = tmp_path / "requests"
    settings.WORKSPACE_DIR.mkdir(parents=True)
    settings.REQUEST_DIR.mkdir(parents=True)
