import os

import opentelemetry.exporter.otlp.proto.http.trace_exporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import ConsoleSpanExporter

import services.tracing as tracing


def test_setup_default_tracing_empty_env(monkeypatch):
    env = {"PYTHONPATH": ""}
    monkeypatch.setattr(os, "environ", env)
    provider = tracing.setup_default_tracing(set_global=False)
    assert provider._active_span_processor._span_processors == ()


def test_setup_default_tracing_console(monkeypatch):
    env = {"PYTHONPATH": "", "OTEL_EXPORTER_CONSOLE": "1"}
    monkeypatch.setattr(os, "environ", env)
    provider = tracing.setup_default_tracing(set_global=False)

    processor = provider._active_span_processor._span_processors[0]
    assert isinstance(processor.span_exporter, ConsoleSpanExporter)


def test_setup_default_tracing_otlp_defaults(monkeypatch):
    # add single quotes to test quote stripping
    env = {"PYTHONPATH": "", "OTEL_EXPORTER_OTLP_HEADERS": "'foo=bar'"}
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(
        opentelemetry.exporter.otlp.proto.http.trace_exporter, "environ", env
    )
    provider = tracing.setup_default_tracing(set_global=False)
    assert provider.resource.attributes["service.name"] == "airlock"

    exporter = provider._active_span_processor._span_processors[0].span_exporter
    assert isinstance(exporter, OTLPSpanExporter)
    assert exporter._endpoint == "https://api.honeycomb.io/v1/traces"
    assert exporter._headers == {"foo": "bar"}
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://api.honeycomb.io"


def test_setup_default_tracing_otlp_with_env(monkeypatch):
    env = {
        "PYTHONPATH": "",
        "OTEL_EXPORTER_OTLP_HEADERS": "foo=bar",
        "OTEL_SERVICE_NAME": "service",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://endpoint",
    }
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(
        opentelemetry.exporter.otlp.proto.http.trace_exporter, "environ", env
    )
    provider = tracing.setup_default_tracing(set_global=False)
    assert provider.resource.attributes["service.name"] == "service"

    exporter = provider._active_span_processor._span_processors[0].span_exporter

    assert isinstance(exporter, OTLPSpanExporter)
    assert exporter._endpoint == "https://endpoint/v1/traces"
    assert exporter._headers == {"foo": "bar"}


def test_not_instrument_decorator():
    assert tracing.trace.get_current_span().is_recording() is False


@tracing.instrument
def test_instrument_decorator():
    current_span = tracing.trace.get_current_span()
    assert current_span.is_recording() is True
    assert current_span.name == "test_instrument_decorator"


@tracing.instrument(span_name="testing", attributes={"foo": "bar"})
def test_instrument_decorator_with_name_and_attributes():
    current_span = tracing.trace.get_current_span()
    assert current_span.is_recording() is True
    assert current_span.name == "testing"
    assert current_span.attributes == {"foo": "bar"}


@tracing.instrument(kwarg_attributes={"number": "num"})
def assert_function_attributes(*, num):
    current_span = tracing.trace.get_current_span()
    assert current_span.attributes == {"number": num}
    return num


def test_instrument_decorator_with_function_attributes():
    assert_function_attributes(num=1)
