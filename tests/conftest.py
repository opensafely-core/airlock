import pytest
import responses as _responses
from django.conf import settings
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import airlock.business_logic
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


# mark every test iwth django_db
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
    "Ensure all tests have isolated file storage"
    settings.WORK_DIR = tmp_path
    settings.WORKSPACE_DIR = tmp_path / "workspaces"
    settings.REQUEST_DIR = tmp_path / "requests"
    settings.GIT_REPO_DIR = tmp_path / "repos"
    settings.WORKSPACE_DIR.mkdir(parents=True)
    settings.REQUEST_DIR.mkdir(parents=True)
    settings.GIT_REPO_DIR.mkdir(parents=True)
    "Ensure no tests attempt to call the real releases endpont"
    settings.AIRLOCK_API_ENDPOINT = "https://example.com/job-server"


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
