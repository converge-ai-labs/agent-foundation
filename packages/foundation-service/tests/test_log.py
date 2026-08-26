from converge_foundation_service.log import build_log_config
from converge_foundation_service.settings import ServiceSettings
from converge_logging import LogFormat
from rich.logging import RichHandler


def test_log_config_injects_service_context() -> None:
    settings = ServiceSettings(_env_file=None, role="control", build_version="v1", log_format=LogFormat.json)

    config = build_log_config(settings)

    assert config["handlers"]["default"]["formatter"] == "json"
    assert config["handlers"]["default"]["filters"] == ["context"]
    assert config["loggers"]["alembic"]["handlers"] == ["default"]
    assert config["filters"]["context"]["fields"] == {
        "service": "foundation-service",
        "role": "control",
        "build_version": "v1",
    }


def test_pretty_logging_is_the_local_default() -> None:
    config = build_log_config(ServiceSettings(_env_file=None))

    assert config["handlers"]["default"]["formatter"] == "pretty"
    assert config["handlers"]["default"]["()"] is RichHandler
