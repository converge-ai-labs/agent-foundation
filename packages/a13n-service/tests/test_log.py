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
