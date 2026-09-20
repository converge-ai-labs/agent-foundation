import pytest
from a13n_logging import LogFormat
from a13n_service.log import SafeExceptionFilter, build_log_config
from a13n_service.settings import Settings
from rich.logging import RichHandler


def test_log_config_injects_service_context() -> None:
    settings = Settings(service={"role": "control", "build_version": "v1"}, logging={"format": LogFormat.json})

    config = build_log_config(settings)

    assert config["handlers"]["default"]["formatter"] == "json"
    assert config["handlers"]["default"]["filters"] == ["context", "trace_context", "safe_exception"]
    assert config["loggers"]["alembic"]["handlers"] == ["default"]
    assert config["loggers"]["a13n_harness"]["handlers"] == ["default"]
    assert config["loggers"]["a13n_harness"]["propagate"] is False
    assert config["filters"]["context"]["fields"] == {
        "service": "a13n-service",
        "role": "control",
        "build_version": "v1",
    }


def test_pretty_logging_is_the_local_default() -> None:
    config = build_log_config(Settings())

    assert config["handlers"]["default"]["formatter"] == "pretty"
    assert config["handlers"]["default"]["()"] is RichHandler


def test_access_logging_removes_callback_secrets_before_formatting() -> None:
    import logging

    from a13n_service.log import RequestTargetFilter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/connection-authorizations/browser?code=secret&state=state", "1.1", 303),
        None,
    )
    assert RequestTargetFilter().filter(record)
    assert record.getMessage() == '127.0.0.1 - "GET /connection-authorizations/browser HTTP/1.1" 303'
    config = build_log_config(Settings())
    assert config["loggers"]["uvicorn.access"]["filters"] == ["request_target"]


def test_trace_filter_adds_active_trace_and_span_ids() -> None:
    import logging

    from a13n_service.log import TraceContextFilter
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(shutdown_on_exit=False)
    with provider.get_tracer("test").start_as_current_span("operation") as span:
        record = logging.makeLogRecord({"msg": "observed"})
        assert TraceContextFilter().filter(record)
        assert record.trace_id == format(span.get_span_context().trace_id, "032x")
        assert record.span_id == format(span.get_span_context().span_id, "016x")
    provider.shutdown()


@pytest.mark.parametrize("logger_name", ["uvicorn.error", "a13n_service.environments"])
def test_service_exception_logging_removes_private_exception_message(logger_name: str) -> None:
    import json
    import logging

    from a13n_logging import JsonFormatter

    try:
        raise RuntimeError("private provider response")
    except RuntimeError as error:
        record = logging.LogRecord(
            logger_name,
            logging.ERROR,
            "",
            0,
            "Exception in ASGI application",
            (),
            (type(error), error, error.__traceback__),
        )

    assert SafeExceptionFilter().filter(record)
    rendered = JsonFormatter().format(record)
    assert "private provider response" not in rendered
    payload = json.loads(rendered)
    assert payload["exception_chain"][0]["type"] == "builtins.RuntimeError"
    assert "exception" not in payload
