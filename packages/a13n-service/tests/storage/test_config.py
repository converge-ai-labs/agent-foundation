from pathlib import Path

import pytest
from a13n_service.storage.config import PostgreSQLConfig, StorageSettings
from pydantic import ValidationError


def _settings(tmp_path: Path) -> dict[str, object]:
    return {
        "database": {"url": "postgresql://user:password@db.example/database"},
        "redis": {"backend": "memory"},
        "objects": {"backend": "local", "root": tmp_path / "objects"},
        "filesystem": {"root": tmp_path / "files"},
    }


def test_settings_select_explicit_backends(tmp_path: Path) -> None:
    settings = StorageSettings.model_validate(_settings(tmp_path))

    assert settings.database.url.get_secret_value() == "postgresql+psycopg://user:password@db.example/database"
    assert settings.redis.backend == "memory"
    assert settings.objects.backend == "local"


def test_settings_reject_unknown_fields(tmp_path: Path) -> None:
    value = _settings(tmp_path)
    value["unexpected"] = True

    with pytest.raises(ValidationError, match="unexpected"):
        StorageSettings.model_validate(value)


@pytest.mark.parametrize("object_root", ["files", "files/objects"])
def test_settings_reject_overlapping_local_roots(tmp_path: Path, object_root: str) -> None:
    value = _settings(tmp_path)
    value["objects"] = {"backend": "local", "root": tmp_path / object_root}

    with pytest.raises(ValidationError, match="must not overlap"):
        StorageSettings.model_validate(value)


def test_settings_normalizes_parent_components_when_comparing_roots(tmp_path: Path) -> None:
    value = _settings(tmp_path)
    value["objects"] = {"backend": "local", "root": tmp_path / "nested" / ".." / "files"}

    with pytest.raises(ValidationError, match="must not overlap"):
        StorageSettings.model_validate(value)


def test_settings_bound_resource_limits(tmp_path: Path) -> None:
    value = _settings(tmp_path)
    value["filesystem"] = {"root": tmp_path / "files", "worker_limit": 1000}

    with pytest.raises(ValidationError, match="worker_limit"):
        StorageSettings.model_validate(value)


def test_secret_url_is_not_rendered() -> None:
    settings = StorageSettings.model_validate(
        {
            "database": {"url": "postgresql://user:password@example/db"},
            "redis": {"backend": "redis", "url": "redis://:secret@example/0"},
            "objects": {"backend": "s3", "bucket": "objects"},
            "filesystem": {"root": "/data/files"},
        }
    )

    rendered = repr(settings)
    assert "password" not in rendered
    assert "secret" not in rendered


@pytest.mark.parametrize(
    ("value", "normalized"),
    [
        ("postgresql:///service", "postgresql+psycopg:///service"),
        (
            "postgresql+psycopg://user:password@/service?host=/var/run/postgresql",
            "postgresql+psycopg://user:password@/service?host=%2Fvar%2Frun%2Fpostgresql",
        ),
        ("postgresql:///?service=foundation", "postgresql+psycopg:///?service=foundation"),
    ],
)
def test_postgresql_config_accepts_and_normalizes_sqlalchemy_postgresql_urls(value: str, normalized: str) -> None:
    config = PostgreSQLConfig(url=value)

    assert config.url.get_secret_value() == normalized


@pytest.mark.parametrize("value", ["", "not-a-url", "mysql://user:private-password@db.example/service"])
def test_postgresql_config_rejects_invalid_or_non_postgresql_urls_without_revealing_them(value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        PostgreSQLConfig(url=value)

    if value:
        assert value not in str(caught.value)


def test_postgresql_config_sanitizes_malformed_port_errors() -> None:
    value = "postgresql://user:private-password@db.example:private-port/service"

    with pytest.raises(ValidationError) as caught:
        PostgreSQLConfig(url=value)

    assert "private-password" not in str(caught.value)
    assert "private-port" not in str(caught.value)
