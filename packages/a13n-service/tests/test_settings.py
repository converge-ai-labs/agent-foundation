import base64
from pathlib import Path

import pytest
from a13n_service.configuration.sources import load_settings
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
    settings = Settings()

    assert settings.service.role is ProcessRole.all
    assert settings.service.port == 8000
    assert settings.database.backend is DatabaseBackend.postgresql
    assert settings.assets.max_size_bytes == 100 * 1024 * 1024
    assert settings.observability.query.provider == "none"
    assert settings.connectivity.retention_batch_size == 25
    assert settings.connectivity.max_redirects == 3
    assert "a13n_service:a13n_service" not in repr(settings)


def test_asset_size_bound_must_be_positive_and_finite() -> None:
    with pytest.raises(ValueError):
        Settings(assets={"max_size_bytes": 0})


def test_local_profile_maps_to_typed_storage_settings(tmp_path: Path) -> None:
    settings = Settings(
        database={"backend": "sqlite", "sqlite_path": tmp_path / "database.sqlite3"},
        redis={"backend": "memory"},
        objects={"backend": "local", "local_root": tmp_path / "objects"},
        filesystem={"root": tmp_path / "files"},
    )

    storage = settings.storage_settings()

    assert isinstance(storage.database, SQLiteConfig)
    assert isinstance(storage.redis, RedisMemoryConfig)
    assert isinstance(storage.objects, LocalObjectConfig)


def test_network_profile_maps_to_typed_storage_settings(tmp_path: Path) -> None:
    settings = Settings(
        database={"backend": "postgresql", "url": "postgresql://user:private-database-password@database/foundation"},
        redis={"backend": "redis", "url": "redis://:private-redis-password@redis/0"},
        objects={"backend": "s3", "bucket": "foundation"},
        filesystem={"root": tmp_path / "files"},
    )

    storage = settings.storage_settings()

    assert isinstance(storage.database, PostgreSQLConfig)
    assert isinstance(storage.objects, S3ObjectConfig)
    assert "private-database-password" not in repr(settings)
    assert "private-redis-password" not in repr(settings)
    assert "secret" not in repr(storage)


def test_langfuse_trace_query_configuration_is_complete_and_redacted() -> None:
    settings = Settings(
        observability={
            "query": {
                "provider": "langfuse",
                "langfuse_base_url": "https://langfuse.example.com/",
                "langfuse_public_key": "pk-query-secret",
                "langfuse_secret_key": "sk-query-secret",
            }
        }
    )

    settings.validate_trace_query_configuration()

    assert "pk-query-secret" not in repr(settings)
    assert "sk-query-secret" not in repr(settings)


def test_distribution_registered_trace_query_provider_is_accepted() -> None:
    settings = Settings(observability={"query": {"provider": "custom"}})

    settings.validate_trace_query_configuration(registered_provider_keys=("custom", "langfuse"))


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"observability": {"query": {"provider": "tempo"}}}, "not registered"),
        ({"observability": {"query": {"provider": "langfuse"}}}, "LANGFUSE_BASE_URL"),
        (
            {"observability": {"query": {"provider": "langfuse", "langfuse_base_url": "https://langfuse.example.com"}}},
            "LANGFUSE_PUBLIC_KEY",
        ),
        (
            {
                "observability": {
                    "query": {
                        "provider": "langfuse",
                        "langfuse_base_url": "https://user:password@langfuse.example.com",
                        "langfuse_public_key": "pk-test",
                        "langfuse_secret_key": "sk-test",
                    }
                }
            },
            "base URL",
        ),
    ],
)
def test_invalid_trace_query_configuration_fails_static_validation(
    values: dict[str, object],
    message: str,
) -> None:
    settings = Settings.model_validate(values)

    with pytest.raises(ValueError, match=message):
        settings.validate_trace_query_configuration()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"database": {"backend": DatabaseBackend.postgresql, "url": None}}, "A13N_SERVICE_DATABASE_URL"),
        ({"redis": {"backend": RedisBackend.redis, "url": None}}, "A13N_SERVICE_REDIS_URL"),
        ({"objects": {"backend": ObjectBackend.s3, "bucket": None}}, "A13N_SERVICE_OBJECT_BUCKET"),
    ],
)
def test_selected_network_backend_requires_its_location(values: dict[str, object], message: str) -> None:
    settings = Settings.model_validate(values)

    with pytest.raises(ValueError, match=message):
        settings.storage_settings()


def test_managed_secret_master_key_is_exact_and_redacted() -> None:
    settings = Settings(
        secrets={"master_key_base64": base64.b64encode(b"k" * 32).decode(), "encryption_key_id": "master-2026-08"}
    )

    assert settings.secret_protector().encryption_key_id == "master-2026-08"
    assert base64.b64encode(b"k" * 32).decode() not in repr(settings)

    invalid = Settings(
        secrets={"master_key_base64": base64.b64encode(b"short").decode(), "encryption_key_id": "master-2026-08"}
    )
    with pytest.raises(ValueError, match="256 bits"):
        invalid.secret_protector()


def test_connectivity_bounds_and_public_origin_fail_closed() -> None:
    with pytest.raises(ValueError, match="Ingress pending count"):
        Settings(connectivity={"workspace_pending_max_count": 10, "account_pending_max_count": 11})
    with pytest.raises(ValueError, match="lease"):
        Settings(connectivity={"admission_poll_interval_seconds": 10, "admission_lease_seconds": 10})
    with pytest.raises(ValueError, match="PUBLIC_ORIGIN is required"):
        Settings().validated_connectivity_public_origin()
    with pytest.raises(ValueError, match="must be an exact origin"):
        Settings(
            connectivity={"public_origin": "https://foundation.example.com/path"}
        ).validated_connectivity_public_origin()

    settings = Settings(
        connectivity={
            "public_origin": "http://foundation.internal:8080",
            "http_origins": ("http://foundation.internal:8080",),
        }
    )
    assert settings.validated_connectivity_public_origin() == "http://foundation.internal:8080"


def test_environment_capacity_configuration(monkeypatch):
    defaults = Settings()
    assert defaults.environments.max_targets_per_workspace == 1000
    assert defaults.environments.max_active_per_workspace == 100
    assert defaults.environments.maintenance_batch_size == 64
    monkeypatch.setenv("A13N_SERVICE_ENVIRONMENT_MAX_TARGETS_PER_WORKSPACE", "2000")
    monkeypatch.setenv("A13N_SERVICE_ENVIRONMENT_MAX_ACTIVE_PER_WORKSPACE", "200")
    configured = load_settings()
    assert configured.environments.max_targets_per_workspace == 2000
    assert configured.environments.max_active_per_workspace == 200
    for field in (
        "environment_max_targets_per_workspace",
        "environment_max_active_per_workspace",
        "environment_maintenance_batch_size",
    ):
        with pytest.raises(ValueError):
            Settings(environments={field.removeprefix("environment_"): 0})
