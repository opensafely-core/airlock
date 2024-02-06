import pytest
import responses as _responses
from django.conf import settings


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


@pytest.fixture
def release_files_stubber(responses):
    def release_files(request, jobserver_id="jobserver-id", body=None):
        responses.post(
            f"{settings.AIRLOCK_API_ENDPOINT}/releases/workspace/{request.workspace.name}",
            status=201,
            headers={"Release-Id": jobserver_id},
            body=body,
        )

        if not isinstance(body, Exception):
            for path in request.filelist():
                responses.post(
                    f"{settings.AIRLOCK_API_ENDPOINT}/releases/release/{jobserver_id}",
                    status=201,
                )

        return responses

    return release_files
