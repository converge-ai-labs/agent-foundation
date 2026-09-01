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
    assert settings.connector_providers == ()
    assert settings.database_backend is DatabaseBackend.postgresql
    assert "foundation:foundation" not in repr(settings)


def test_connector_provider_trust_is_typed() -> None:
    settings = ServiceSettings(
        _env_file=None,
        connector_providers=[
            {
                "provider_key": "github",
                "distribution_name": "a13n-connector-github",
                "distribution_version": "1.2.3",
            }
        ],
    )

    assert settings.connector_providers[0].provider_key == "github"
    assert settings.connector_providers[0].distribution_name == "a13n-connector-github"


def test_connector_role_and_capability_signing_key_are_typed() -> None:
    encoded = base64.b64encode(b"c" * 32).decode()
    settings = ServiceSettings(
        _env_file=None,
        role="connector",
        connector_capability_signing_key_base64=encoded,
        connector_internal_auth_token="internal-token-0123456789abcdef0123456789",
    )

    assert settings.role is ServiceRole.connector
    assert settings.connector_capability_codec() is not None
    assert encoded not in repr(settings)
    assert "internal-token" not in repr(settings)


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
