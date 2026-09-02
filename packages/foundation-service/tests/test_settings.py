import base64
from pathlib import Path

import pytest
from a13n_service.settings import (
    DatabaseBackend,
    ObjectBackend,
    RedisBackend,
    ServiceRole,
    ServiceSettings,
)
from a13n_service.storage.config import (
    LocalObjectConfig,
    PostgreSQLConfig,
    RedisMemoryConfig,
    S3ObjectConfig,
    SQLiteConfig,
)


def test_settings_preserve_service_defaults_without_exposing_secrets() -> None:
    settings = ServiceSettings(_env_file=None)

    assert settings.role is ServiceRole.all
    assert settings.port == 8000
    assert settings.database_backend is DatabaseBackend.postgresql
    assert settings.asset_max_size_bytes == 100 * 1024 * 1024
    assert settings.plugin_runtime_command_poll_interval_seconds == 1
    assert settings.plugin_runtime_command_lease_seconds == 300
    assert settings.plugin_runtime_resolver_executable == "uv"
    assert settings.plugin_runtime_default_index_url.get_secret_value() == "https://pypi.org/simple"
    assert settings.plugin_runtime_index_urls == ()
    assert settings.plugin_runtime_resolver_timeout_seconds == 120
    assert settings.plugin_runtime_resolver_max_packages == 512
    assert settings.observability_query_provider == "none"
    assert "foundation:foundation" not in repr(settings)


def test_asset_size_bound_must_be_positive_and_finite() -> None:
    with pytest.raises(ValueError):
        ServiceSettings(_env_file=None, asset_max_size_bytes=0)


@pytest.mark.parametrize(
    "values",
    [
        {"plugin_runtime_command_poll_interval_seconds": 0},
        {"plugin_runtime_command_poll_interval_seconds": 61},
        {"plugin_runtime_command_lease_seconds": 3},
        {"plugin_runtime_command_lease_seconds": 3601},
        {"plugin_runtime_resolver_timeout_seconds": 0},
        {"plugin_runtime_resolver_timeout_seconds": 901},
        {"plugin_runtime_resolver_max_packages": 0},
        {"plugin_runtime_resolver_max_packages": 4097},
    ],
)
def test_plugin_runtime_command_timing_is_bounded(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ServiceSettings(_env_file=None, **values)


def test_plugin_runtime_package_indexes_are_redacted() -> None:
    settings = ServiceSettings(
        _env_file=None,
        plugin_runtime_default_index_url="https://user:default-secret@packages.example/simple",
        plugin_runtime_index_urls=["https://user:extra-secret@private.example/simple"],
    )

    rendered = repr(settings)
    assert "default-secret" not in rendered
    assert "extra-secret" not in rendered


def test_local_profile_maps_to_typed_storage_settings(tmp_path: Path) -> None:
    settings = ServiceSettings(
        _env_file=None,
        database_backend="sqlite",
        database_sqlite_path=tmp_path / "database.sqlite3",
        redis_backend="memory",
        object_backend="local",
        object_local_root=tmp_path / "objects",
        filesystem_root=tmp_path / "files",
    )

    storage = settings.storage_settings()

    assert isinstance(storage.database, SQLiteConfig)
    assert isinstance(storage.redis, RedisMemoryConfig)
    assert isinstance(storage.objects, LocalObjectConfig)


def test_network_profile_maps_to_typed_storage_settings(tmp_path: Path) -> None:
    settings = ServiceSettings(
        _env_file=None,
        database_backend="postgresql",
        database_url="postgresql://user:secret@database/foundation",
        redis_backend="redis",
        redis_url="redis://:secret@redis/0",
        object_backend="s3",
        object_bucket="foundation",
        filesystem_root=tmp_path / "files",
    )

    storage = settings.storage_settings()

    assert isinstance(storage.database, PostgreSQLConfig)
    assert isinstance(storage.objects, S3ObjectConfig)
    assert "secret" not in repr(settings)
    assert "secret" not in repr(storage)


def test_langfuse_trace_query_configuration_is_complete_and_redacted() -> None:
    settings = ServiceSettings(
        _env_file=None,
        observability_query_provider="langfuse",
        observability_query_langfuse_base_url="https://langfuse.example.com/",
        observability_query_langfuse_public_key="pk-query-secret",
        observability_query_langfuse_secret_key="sk-query-secret",
    )

    settings.validate_trace_query_configuration()

    assert "pk-query-secret" not in repr(settings)
    assert "sk-query-secret" not in repr(settings)


def test_distribution_registered_trace_query_provider_is_accepted() -> None:
    settings = ServiceSettings(_env_file=None, observability_query_provider="custom")

    settings.validate_trace_query_configuration(registered_provider_keys=("custom", "langfuse"))


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"observability_query_provider": "tempo"}, "not registered"),
        ({"observability_query_provider": "langfuse"}, "LANGFUSE_BASE_URL"),
        (
            {
                "observability_query_provider": "langfuse",
                "observability_query_langfuse_base_url": "https://langfuse.example.com",
            },
            "LANGFUSE_PUBLIC_KEY",
        ),
        (
            {
                "observability_query_provider": "langfuse",
                "observability_query_langfuse_base_url": "https://user:password@langfuse.example.com",
                "observability_query_langfuse_public_key": "pk-test",
                "observability_query_langfuse_secret_key": "sk-test",
            },
            "base URL",
        ),
    ],
)
def test_invalid_trace_query_configuration_fails_static_validation(
    values: dict[str, object],
    message: str,
) -> None:
    settings = ServiceSettings(_env_file=None, **values)

    with pytest.raises(ValueError, match=message):
        settings.validate_trace_query_configuration()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"database_backend": DatabaseBackend.postgresql, "database_url": None}, "FOUNDATION_DATABASE_URL"),
        ({"redis_backend": RedisBackend.redis, "redis_url": None}, "FOUNDATION_REDIS_URL"),
        ({"object_backend": ObjectBackend.s3, "object_bucket": None}, "FOUNDATION_OBJECT_BUCKET"),
    ],
)
def test_selected_network_backend_requires_its_location(values: dict[str, object], message: str) -> None:
    settings = ServiceSettings(_env_file=None, **values)

    with pytest.raises(ValueError, match=message):
        settings.storage_settings()


def test_managed_secret_master_key_is_exact_and_redacted() -> None:
    settings = ServiceSettings(
        _env_file=None,
        secret_master_key_base64=base64.b64encode(b"k" * 32).decode(),
        secret_encryption_key_id="master-2026-08",
    )

    assert settings.secret_protector().encryption_key_id == "master-2026-08"
    assert base64.b64encode(b"k" * 32).decode() not in repr(settings)

    invalid = ServiceSettings(
        _env_file=None,
        secret_master_key_base64=base64.b64encode(b"short").decode(),
        secret_encryption_key_id="master-2026-08",
    )
    with pytest.raises(ValueError, match="256 bits"):
        invalid.secret_protector()
