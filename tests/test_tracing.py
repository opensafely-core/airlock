import os
from dataclasses import dataclass

import opentelemetry.exporter.otlp.proto.http.trace_exporter
import pytest
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import ConsoleSpanExporter

from services.tracing import instrument, setup_default_tracing
from tests.conftest import get_trace


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
    assert current_span.name == "test_instrument_decorator"  # type: ignore


@instrument(span_name="testing", attributes={"foo": "bar"})
def test_instrument_decorator_with_name_and_attributes():
    current_span = trace.get_current_span()
    assert current_span.is_recording() is True
    assert current_span.name == "testing"  # type: ignore
    assert current_span.attributes == {"foo": "bar"}  # type: ignore


def test_instrument_decorator_parent_attributes(settings):
    @instrument(span_name="child", attributes={"foo": "bar"})
    def child(): ...

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("parent", attributes={"p_foo": "p_bar"}):
        child()

    spans = {span.name: span.attributes for span in get_trace()}

    assert spans == {
        "parent": {"p_foo": "p_bar", "foo": "bar"},
        "child": {"foo": "bar"},
    }


@pytest.mark.parametrize("set_status", [True, False])
def test_instrument_decorator_exception_status(set_status):
    @instrument(set_status_on_exception=set_status)
    def test_exception():
        raise Exception("test")

    with pytest.raises(Exception):
        test_exception()

    spans = get_trace()
    assert spans[0].status.is_ok is not set_status


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
        assert current_span.attributes == expected_attributes  # type: ignore
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
        assert current_span.attributes == {"foo": str(kwargs["bar"])}  # type: ignore

    if expect_ok:
        decorated_function(**func_kwargs)
    else:
        with pytest.raises(AttributeError, match="not found in function signature"):
            decorated_function(**func_kwargs)


@pytest.mark.parametrize(
    "instance_kwargs,function_arg,expected",
    [
        ({}, 1, {"foo": "1", "foo1": "default"}),
        ({"baz": "test"}, "string", {"foo": "string", "foo1": "test"}),
    ],
)
def test_instrument_decorator_on_class_method(instance_kwargs, function_arg, expected):
    @dataclass
    class Decorated:
        baz: str = "default"

        @instrument(func_attributes={"foo": "bar", "foo1": "baz"})
        def decorated_method(self, bar):
            current_span = trace.get_current_span()
            assert current_span.attributes == expected  # type: ignore

    instance = Decorated(**instance_kwargs)
    instance.decorated_method(function_arg)
