from unittest.mock import MagicMock

import pytest
import responses as _responses
from django.conf import settings
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import airlock.business_logic
import old_api
import services.tracing as tracing
import tests.factories


# set up tracing for tests
provider = tracing.get_provider()
tracing.trace.set_tracer_provider(provider)
test_exporter = InMemorySpanExporter()
tracing.add_exporter(provider, test_exporter, SimpleSpanProcessor)


def get_trace():
    """Return all spans traced during this test."""
    return test_exporter.get_finished_spans()  # pragma: no cover


@pytest.fixture(autouse=True)
def clear_all_traces():
    test_exporter.clear()


# mark every test with django_db
def pytest_collection_modifyitems(config, items):
    for item in items:
        item.add_marker(pytest.mark.django_db)


# Fail the test run if we see any warnings
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if terminalreporter.stats.get("warnings"):  # pragma: no cover
        print("\nWARNINGS DETECTED: Exiting with error")
        if terminalreporter._session.exitstatus == 0:
            terminalreporter._session.exitstatus = 13


@pytest.fixture(autouse=True)
def temp_test_settings(settings, tmp_path):
    # Ensure all tests have isolated file storage
    settings.WORK_DIR = tmp_path
    settings.WORKSPACE_DIR = tmp_path / "workspaces"
    settings.REQUEST_DIR = tmp_path / "requests"
    settings.GIT_REPO_DIR = tmp_path / "repos"
    settings.WORKSPACE_DIR.mkdir(parents=True)
    settings.REQUEST_DIR.mkdir(parents=True)
    settings.GIT_REPO_DIR.mkdir(parents=True)
    # Ensure no tests attempt to call the real releases endpont
    settings.AIRLOCK_API_ENDPOINT = "https://example.com/job-server"
    # Ensure no tests attempt to call jobserver for notifications (tests for notifications
    # themselves explicitly override this setting)
    settings.AIRLOCK_API_TOKEN = ""


@pytest.fixture
def responses():
    with _responses.RequestsMock() as rsps:
        yield rsps


# We could parameterise this fixture to run tests over all Data Access Layer
# implementations in future
@pytest.fixture
def bll(monkeypatch):
    monkeypatch.setattr(tests.factories, "bll", airlock.business_logic.bll)
    return airlock.business_logic.bll


@pytest.fixture
def release_files_stubber(responses):
    def release_files(request, body=None):
        responses.post(
            f"{settings.AIRLOCK_API_ENDPOINT}/releases/workspace/{request.workspace}",
            status=201,
            headers={"Release-Id": request.id},
            body=body,
        )

        return responses

    return release_files


@pytest.fixture()
def upload_files_stubber(release_files_stubber):
    def upload_files(request, response_statuses=None):
        responses = release_files_stubber(request)

        if response_statuses is None:
            response_statuses = [201 for _ in request.get_output_file_paths()]

        for status in response_statuses:
            responses.post(
                f"{settings.AIRLOCK_API_ENDPOINT}/releases/release/{request.id}",
                status=status,
                json={"detail": "error" if status != 201 else "ok"},
            )

        return responses

    return upload_files


@pytest.fixture
def notifications_stubber(responses, settings):
    settings.AIRLOCK_API_TOKEN = "token"
    responses.assert_all_requests_are_fired = False

    def send_notification(json=None, exception=None):
        status_code = exception.response.status_code if exception else 201
        json = json or {"status": "ok"}
        responses.post(
            f"{settings.AIRLOCK_API_ENDPOINT}/airlock/events/",
            status=status_code,
            json=json,
        )

        return responses

    return send_notification


@pytest.fixture
def mock_notifications(notifications_stubber):
    return notifications_stubber()


@pytest.fixture
def auth_api_stubber(responses, settings):
    settings.AIRLOCK_API_TOKEN = "token"

    def stub_api_path(
        action="authenticate",
        status=200,
        json=None,
    ):
        assert action in ["authenticate", "authorise"], (
            "auth_api_stubber only supports authenticate and authorise actions"
        )
        responses.post(
            f"{settings.AIRLOCK_API_ENDPOINT}/releases/{action}",
            status=status,
            json=json,
        )

    return stub_api_path


@pytest.fixture
def mock_old_api(monkeypatch):
    monkeypatch.setattr(
        old_api,
        "get_or_create_release",
        MagicMock(autospec=old_api.get_or_create_release),
    )
    monkeypatch.setattr(old_api, "upload_file", MagicMock(autospec=old_api.upload_file))
