from a13n_logging import LogFormat
from a13n_service.log import build_log_config
from a13n_service.settings import Settings
from rich.logging import RichHandler


def test_log_config_injects_service_context() -> None:
    settings = Settings(service={"role": "control", "build_version": "v1"}, logging={"format": LogFormat.json})

    config = build_log_config(settings)

    assert config["handlers"]["default"]["formatter"] == "json"
    assert config["handlers"]["default"]["filters"] == ["context"]
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
        ("127.0.0.1", "GET", "/api/v1/oauth/mcp/callback/key?code=secret&state=state", "1.1", 303),
        None,
    )
    assert RequestTargetFilter().filter(record)
    assert record.getMessage() == '127.0.0.1 - "GET /api/v1/oauth/mcp/callback/key HTTP/1.1" 303'
    config = build_log_config(Settings())
    assert config["loggers"]["uvicorn.access"]["filters"] == ["request_target"]
