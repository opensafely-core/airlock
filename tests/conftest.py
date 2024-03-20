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


# We could parameterise this fixture to run tests over all Data Access Layer
# implementations in future
@pytest.fixture
def bll(monkeypatch):
    monkeypatch.setattr(tests.factories, "bll", airlock.business_logic.bll)
    return airlock.business_logic.bll


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
            for _ in request.get_output_file_paths():
                responses.post(
                    f"{settings.AIRLOCK_API_ENDPOINT}/releases/release/{jobserver_id}",
                    status=201,
                )

        return responses

    return release_files
