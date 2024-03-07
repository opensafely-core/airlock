import pytest
import responses as _responses
from django.conf import settings

import airlock.api
import old_api
import tests.factories


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


@pytest.fixture
def responses():
    with _responses.RequestsMock() as rsps:
        yield rsps


# we could parameterise this fixture to run tests over all api implementations in future
@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(tests.factories, "api", airlock.api.api)
    return airlock.api.api


@pytest.fixture
def release_files_stubber(responses):
    def release_files(request, jobserver_id="jobserver-id", body=None):
        responses.post(
            f"{settings.AIRLOCK_API_ENDPOINT}/releases/workspace/{request.workspace}",
            status=201,
            headers={"Release-Id": jobserver_id},
            body=body,
        )

        if not isinstance(body, Exception):
            for path in old_api.list_files(request.root()):
                responses.post(
                    f"{settings.AIRLOCK_API_ENDPOINT}/releases/release/{jobserver_id}",
                    status=201,
                )

        return responses

    return release_files
