import os

import opentelemetry.exporter.otlp.proto.http.trace_exporter
import pytest
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import ConsoleSpanExporter

from services.tracing import instrument, setup_default_tracing


def test_setup_default_tracing_empty_env(monkeypatch):
    env = {"PYTHONPATH": ""}
    monkeypatch.setattr(os, "environ", env)
    provider = setup_default_tracing(set_global=False)
    assert provider._active_span_processor._span_processors == ()


def test_setup_default_tracing_console(monkeypatch):
    env = {"PYTHONPATH": "", "OTEL_EXPORTER_CONSOLE": "true"}
    monkeypatch.setattr(os, "environ", env)
    provider = setup_default_tracing(set_global=False)

    processor = provider._active_span_processor._span_processors[0]
    assert isinstance(processor.span_exporter, ConsoleSpanExporter)


def test_setup_default_tracing_otlp_defaults(monkeypatch):
    # add single quotes to test quote stripping
    env = {"PYTHONPATH": "", "OTEL_EXPORTER_OTLP_HEADERS": "'foo=bar'"}
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(
        opentelemetry.exporter.otlp.proto.http.trace_exporter, "environ", env
    )
    provider = setup_default_tracing(set_global=False)
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
    provider = setup_default_tracing(set_global=False)
    assert provider.resource.attributes["service.name"] == "service"

    exporter = provider._active_span_processor._span_processors[0].span_exporter

    assert isinstance(exporter, OTLPSpanExporter)
    assert exporter._endpoint == "https://endpoint/v1/traces"
    assert exporter._headers == {"foo": "bar"}


def test_not_instrument_decorator():
    assert trace.get_current_span().is_recording() is False


@instrument
def test_instrument_decorator():
    current_span = trace.get_current_span()
    assert current_span.is_recording() is True
    assert current_span.name == "test_instrument_decorator"


@instrument(span_name="testing", attributes={"foo": "bar"})
def test_instrument_decorator_with_name_and_attributes():
    current_span = trace.get_current_span()
    assert current_span.is_recording() is True
    assert current_span.name == "testing"
    assert current_span.attributes == {"foo": "bar"}


@pytest.mark.parametrize(
    "func_attributes,func_args,func_kwargs,expected_attributes",
    [
        # positional arg
        ({"func_attributes": {"number": "num"}}, (1,), {}, {"number": "1"}),
        # keyword arg
        (
            {"func_attributes": {"text": "string"}},
            (1,),
            {"string": "bar"},
            {"text": "bar"},
        ),
        # default keyword arg
        ({"func_attributes": {"text": "string"}}, (1,), {}, {"text": "Foo"}),
        # all args passed as keywords
        (
            {"func_attributes": {"number": "num"}},
            (),
            {"num": 1, "string": "bar"},
            {"number": "1"},
        ),
        # all args passed as positional
        ({"func_attributes": {"number": "num"}}, (1, "bar"), {}, {"number": "1"}),
        # multiple func attributes
        (
            {"func_attributes": {"number": "num", "text": "string"}},
            (1,),
            {},
            {"number": "1", "text": "Foo"},
        ),
    ],
)
def test_instrument_decorator_with_function_attributes(
    func_attributes, func_args, func_kwargs, expected_attributes
):
    @instrument(**func_attributes)
    def assert_function_kwarg_attributes(num, string="Foo"):
        current_span = trace.get_current_span()
        assert current_span.attributes == expected_attributes
        return num, string

    assert_function_kwarg_attributes(*func_args, **func_kwargs)


@pytest.mark.parametrize(
    "func_kwargs,expect_ok",
    [
        ({}, False),
        ({"foo": 1}, False),
        ({"bar": 1}, True),
    ],
)
def test_instrument_decorator_with_unnamed_kwargs(func_kwargs, expect_ok):
    @instrument(func_attributes={"foo": "bar"})
    def decorated_function(**kwargs):
        current_span = trace.get_current_span()
        assert current_span.attributes == {"foo": str(kwargs["bar"])}

    if expect_ok:
        decorated_function(**func_kwargs)
    else:
        with pytest.raises(AttributeError, match="not found in function signature"):
            decorated_function(**func_kwargs)
