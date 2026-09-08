import base64
from pathlib import Path

import pytest
from a13n_service.settings import (
    DatabaseBackend,
    ObjectBackend,
    ProcessRole,
    RedisBackend,
    Settings,
)
from a13n_service.storage.config import (
    LocalObjectConfig,
    PostgreSQLConfig,
    RedisMemoryConfig,
    S3ObjectConfig,
    SQLiteConfig,
)


def test_settings_preserve_service_defaults_without_exposing_secrets() -> None:
    settings = Settings(_env_file=None)

    assert settings.role is ProcessRole.all
    assert settings.port == 8000
    assert settings.database_backend is DatabaseBackend.postgresql
    assert settings.asset_max_size_bytes == 100 * 1024 * 1024
    assert settings.observability_query_provider == "none"
    assert settings.connectivity_retention_batch_size == 25
    assert settings.connectivity_max_redirects == 3
    assert "a13n_service:a13n_service" not in repr(settings)


def test_asset_size_bound_must_be_positive_and_finite() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, asset_max_size_bytes=0)


def test_local_profile_maps_to_typed_storage_settings(tmp_path: Path) -> None:
    settings = Settings(
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
    settings = Settings(
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
    settings = Settings(
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
    settings = Settings(_env_file=None, observability_query_provider="custom")

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
    settings = Settings(_env_file=None, **values)

    with pytest.raises(ValueError, match=message):
        settings.validate_trace_query_configuration()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"database_backend": DatabaseBackend.postgresql, "database_url": None}, "A13N_SERVICE_DATABASE_URL"),
        ({"redis_backend": RedisBackend.redis, "redis_url": None}, "A13N_SERVICE_REDIS_URL"),
        ({"object_backend": ObjectBackend.s3, "object_bucket": None}, "A13N_SERVICE_OBJECT_BUCKET"),
    ],
)
def test_selected_network_backend_requires_its_location(values: dict[str, object], message: str) -> None:
    settings = Settings(_env_file=None, **values)

    with pytest.raises(ValueError, match=message):
        settings.storage_settings()


def test_managed_secret_master_key_is_exact_and_redacted() -> None:
    settings = Settings(
        _env_file=None,
        secret_master_key_base64=base64.b64encode(b"k" * 32).decode(),
        secret_encryption_key_id="master-2026-08",
    )

    assert settings.secret_protector().encryption_key_id == "master-2026-08"
    assert base64.b64encode(b"k" * 32).decode() not in repr(settings)

    invalid = Settings(
        _env_file=None,
        secret_master_key_base64=base64.b64encode(b"short").decode(),
        secret_encryption_key_id="master-2026-08",
    )
    with pytest.raises(ValueError, match="256 bits"):
        invalid.secret_protector()


def test_connectivity_bounds_and_public_origin_fail_closed() -> None:
    with pytest.raises(ValueError, match="Ingress pending count"):
        Settings(
            _env_file=None,
            connectivity_workspace_pending_max_count=10,
            connectivity_ingress_pending_max_count=11,
        )
    with pytest.raises(ValueError, match="lease"):
        Settings(
            _env_file=None,
            connectivity_admission_poll_interval_seconds=10,
            connectivity_admission_lease_seconds=10,
        )
    with pytest.raises(ValueError, match="PUBLIC_ORIGIN is required"):
        Settings(_env_file=None).validated_connectivity_public_origin()
    with pytest.raises(ValueError, match="must be an exact origin"):
        Settings(
            _env_file=None,
            connectivity_public_origin="https://foundation.example.com/path",
        ).validated_connectivity_public_origin()

    settings = Settings(
        _env_file=None,
        connectivity_public_origin="http://foundation.internal:8080",
        connectivity_http_origins=("http://foundation.internal:8080",),
    )
    assert settings.validated_connectivity_public_origin() == "http://foundation.internal:8080"


def test_environment_capacity_configuration(monkeypatch):
    defaults = Settings(_env_file=None)
    assert defaults.environment_max_targets_per_workspace == 1000
    assert defaults.environment_max_active_per_workspace == 100
    assert defaults.environment_maintenance_batch_size == 64
    monkeypatch.setenv("A13N_SERVICE_ENVIRONMENT_MAX_TARGETS_PER_WORKSPACE", "2000")
    monkeypatch.setenv("A13N_SERVICE_ENVIRONMENT_MAX_ACTIVE_PER_WORKSPACE", "200")
    configured = Settings(_env_file=None)
    assert configured.environment_max_targets_per_workspace == 2000
    assert configured.environment_max_active_per_workspace == 200
    for field in (
        "environment_max_targets_per_workspace",
        "environment_max_active_per_workspace",
        "environment_maintenance_batch_size",
    ):
        with pytest.raises(ValueError):
            Settings(_env_file=None, **{field: 0})
