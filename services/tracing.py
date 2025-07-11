import os
from functools import wraps
from inspect import Parameter, signature

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
            "rap.backend": os.environ.get("BACKEND", "unknown"),
            "rap.service": "airlock",
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
        # put back into env to be parsed properly
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = cleaned_headers

        if "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ:
            os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "https://api.honeycomb.io"

        add_exporter(provider, OTLPSpanExporter())

    if os.environ.get("OTEL_EXPORTER_CONSOLE", "").lower() == "true":
        add_exporter(provider, ConsoleSpanExporter())

    if set_global:  # pragma: nocover
        trace.set_tracer_provider(provider)

    # bug: this code requires some envvars to be set, so ensure they are
    os.environ.setdefault("PYTHONPATH", "")
    from opentelemetry.instrumentation.auto_instrumentation import (  # noqa: F401
        sitecustomize,
    )

    return provider


def instrument(
    _func=None,
    *,
    span_name: str = "",
    set_status_on_exception=False,
    record_exception: bool = True,
    attributes: dict[str, str] = None,
    func_attributes: dict[str, str] = None,
    existing_tracer: trace.Tracer = None,
):
    """
    A decorator to instrument a function with an OTEL tracing span.

    span_name: custom name for the span, defaults to name of decorated functionvv
    set_status_on_exception: passed to `start_as_current_span`. Whether to mark
      this span as failed if an exception is raise. Note: Django uses exceptions
      for control flow (e.g. Http404, PermissionDenied), so this is set to
      False by default.
    record_exception: passed to `start_as_current_span`; whether to record
      exceptions when they happen.
    attributes: custom attributes to set on the span
    func_attributes: k, v pairs of attribute name to function parameter
      name. Sets the span attribute k to the str representation of
      the function argument v (can be either positional or keyword argument).
      v must be either a string, or an object that can be passed to str().
      If the decorated function is a class method, the parameter will also be
      looked up on the class instance.
    existing_tracer: pass an optional existing tracer to use. Defaults to
      a tracer named with the value of the environment variable
      `OTEL_SERVICE_NAME` if available, or the name of the module containing
      the decoraated function.
    """

    def span_decorator(func):
        tracer = existing_tracer or trace.get_tracer(
            os.environ.get("OTEL_SERVICE_NAME", "airlock")
        )
        name = span_name or func.__qualname__
        attributes_dict = attributes or {}
        func_signature = signature(func)
        default_params = {
            param_name: param.default
            for param_name, param in func_signature.parameters.items()
            if param and param.default is not Parameter.empty
        }

        @wraps(func)
        def wrap_with_span(*args, **kwargs):
            bound_args = func_signature.bind(*args, **kwargs).arguments
            if "request" in bound_args:
                user = bound_args["request"].user
                if user and user.is_authenticated:
                    attributes_dict["user_id"] = user.user_id
                    attributes_dict["username"] = user.username
                else:  # pragma: nocover
                    attributes_dict["user_id"] = "anonymous"
                    attributes_dict["username"] = "anonymous"

            if func_attributes is not None:
                bound_args = func_signature.bind(*args, **kwargs).arguments
                for attribute, parameter_name in func_attributes.items():
                    # Find the value of this parameter by(in order):
                    # 1) the function kwargs directly; if a function signature takes a parameter
                    # like `**kwargs`, we can retrieve a named parameter from the keyword arguments
                    # there
                    # 2) the bound args retrieved from the function signature; this will find any
                    # explicity passed values when the function was called.
                    # 3) the parameter default value, if there is one
                    # 4) the attribute on the class instance, if there is one
                    # 5) Finally, raises an exception if we can't find a value for the expected parameter
                    if parameter_name in kwargs:
                        func_arg = kwargs[parameter_name]
                    elif parameter_name in bound_args:
                        func_arg = bound_args[parameter_name]
                    elif parameter_name in default_params:
                        func_arg = default_params[parameter_name]
                    elif "self" in bound_args and hasattr(
                        bound_args["self"], parameter_name
                    ):
                        func_arg = getattr(bound_args["self"], parameter_name)
                    else:
                        raise AttributeError(
                            f"Expected argument {parameter_name} not found in function signature"
                        )
                    attributes_dict[attribute] = str(func_arg)

            # Add attributes to the current (parent) span before we start the child span
            span = trace.get_current_span()
            span.set_attributes(attributes_dict)

            with tracer.start_as_current_span(
                name,
                set_status_on_exception=set_status_on_exception,
                record_exception=record_exception,
                attributes=attributes_dict,
            ):
                return func(*args, **kwargs)

        return wrap_with_span

    if _func is None:
        return span_decorator
    else:
        return span_decorator(_func)
