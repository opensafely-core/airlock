import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter


def get_provider():
    # https://github.com/open-telemetry/semantic-conventions/tree/main/docs/resource#service
    resource = Resource.create(
        attributes={
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "airlock"),
            "service.namespace": os.environ.get("BACKEND", "unknown"),
        }
    )
    return TracerProvider(resource=resource)


def add_exporter(provider, exporter, processor=BatchSpanProcessor):
    """Utility method to add an exporter.

    We use the BatchSpanProcessor by default, which is the default for
    production. This is asynchronous, and queues and retries sending telemetry.

    In testing, we instead use SimpleSpanProcessor, which is synchronous and
    easy to inspect the output of within a test.
    """
    # Note: BatchSpanProcessor is configured via env vars:
    # https://opentelemetry-python.readthedocs.io/en/latest/sdk/trace.export.html#opentelemetry.sdk.trace.export.BatchSpanProcessor
    provider.add_span_processor(processor(exporter))


def setup_default_tracing(set_global=True):
    provider = get_provider()

    """Inspect environment variables and set up exporters accordingly."""
    if "OTEL_EXPORTER_OTLP_HEADERS" in os.environ:
        # workaround for env file parsing issues
        cleaned_headers = os.environ["OTEL_EXPORTER_OTLP_HEADERS"].strip("\"'")
        # put back into env to be parsed properlh
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = cleaned_headers

        if "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ:
            os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "https://api.honeycomb.io"

        add_exporter(provider, OTLPSpanExporter())

    if "OTEL_EXPORTER_CONSOLE" in os.environ:
        add_exporter(provider, ConsoleSpanExporter())

    if set_global:  # pragma: nocover
        trace.set_tracer_provider(provider)

    from opentelemetry.instrumentation.auto_instrumentation import (  # noqa: F401
        sitecustomize,
    )

    return provider
