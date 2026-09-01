"""Typed process settings for Foundation Service."""

from __future__ import annotations

import base64
import binascii
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from a13n_logging import LogFormat
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from a13n_service.connectors.capability import ConnectorCapabilityCodec
from a13n_service.connectors.registry import ConnectorProviderTrust
from a13n_service.database import MigrationConfig
from a13n_service.secret_management import SecretProtectionError, SecretProtector
from a13n_service.storage.config import (
    FilesystemConfig,
    LocalObjectConfig,
    PostgreSQLConfig,
    RedisMemoryConfig,
    RedisServerConfig,
    S3ObjectConfig,
    SQLiteConfig,
    StorageSettings,
)


class ServiceRole(StrEnum):
    """Process roles supported by the shared service artifact."""

    all = "all"
    control = "control"
    worker = "worker"
    connector = "connector"


class DatabaseBackend(StrEnum):
    postgresql = "postgresql"
    sqlite = "sqlite"


class RedisBackend(StrEnum):
    redis = "redis"
    memory = "memory"


class ObjectBackend(StrEnum):
    s3 = "s3"
    local = "local"


class ServiceSettings(BaseSettings):
    """Load executable configuration from ``FOUNDATION_*`` variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FOUNDATION_",
        extra="ignore",
        case_sensitive=False,
    )

    service_name: str = "foundation-service"
    role: ServiceRole = ServiceRole.all
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    build_version: str = "unknown"
    web_dist_dir: Path | None = None
    connector_providers: tuple[ConnectorProviderTrust, ...] = ()
    connector_trigger_min_interval_seconds: int = Field(default=60, ge=1, le=86_400)
    connector_capability_signing_key_base64: SecretStr | None = Field(default=None, repr=False)
    connector_internal_auth_token: SecretStr | None = Field(
        default=None,
        min_length=32,
        max_length=4_096,
        repr=False,
    )
    connector_mcp_operation_timeout_seconds: float = Field(default=30, gt=0, le=300)
    connector_service_base_url: str = "http://127.0.0.1:8000"

    database_backend: DatabaseBackend = DatabaseBackend.postgresql
    database_url: SecretStr | None = Field(
        default=SecretStr("postgresql+psycopg://foundation:foundation@127.0.0.1:5432/foundation"),
        repr=False,
    )
    database_sqlite_path: Path = Path("var/foundation.sqlite3")
    database_pool_size: int = Field(default=10, ge=1, le=1000)
    database_max_overflow: int = Field(default=20, ge=0, le=1000)
    database_pool_timeout_seconds: float = Field(default=30, gt=0, le=300)
    database_pool_recycle_seconds: int = Field(default=3600, ge=0)
    database_connect_timeout_seconds: int = Field(default=10, ge=1, le=300)
    database_statement_timeout_seconds: float = Field(default=30, gt=0, le=3600)
    database_sqlite_busy_timeout_seconds: float = Field(default=5, gt=0, le=300)
    database_cleanup_timeout_seconds: float = Field(default=5, gt=0, le=60)
    database_readiness_timeout_seconds: float = Field(default=3, gt=0, le=300)

    model_private_endpoint_domains: tuple[str, ...] = ()
    model_private_endpoint_cidrs: tuple[str, ...] = ()
    model_resolve_dns_on_save: bool = True
    model_connection_test_timeout_seconds: float = Field(default=15, gt=0, le=120)
    secret_master_key_base64: SecretStr | None = Field(default=None, repr=False)
    secret_encryption_key_id: str | None = Field(default=None, min_length=1, max_length=128, repr=False)

    redis_backend: RedisBackend = RedisBackend.redis
    redis_url: SecretStr | None = Field(default=SecretStr("redis://127.0.0.1:6379/0"), repr=False)
    redis_max_connections: int = Field(default=20, ge=1, le=1000)
    redis_connect_timeout_seconds: float = Field(default=5, gt=0, le=300)
    redis_command_timeout_seconds: float = Field(default=10, gt=0, le=300)
    redis_health_check_interval_seconds: float = Field(default=30, gt=0, le=3600)
    redis_cleanup_timeout_seconds: float = Field(default=5, gt=0, le=60)

    object_backend: ObjectBackend = ObjectBackend.local
    object_local_root: Path = Path("var/objects")
    object_bucket: str | None = None
    object_region: str = Field(default="us-east-1", min_length=1)
    object_endpoint_url: str | None = None
    object_force_path_style: bool = False
    object_connect_timeout_seconds: float = Field(default=5, gt=0, le=300)
    object_read_timeout_seconds: float = Field(default=30, gt=0, le=300)
    object_compatibility_timeout_seconds: float = Field(default=120, gt=0, le=600)
    object_max_pool_connections: int = Field(default=20, ge=1, le=1000)
    object_multipart_part_size: int = Field(default=8 * 1024 * 1024, ge=5 * 1024 * 1024, le=64 * 1024 * 1024)
    object_local_chunk_size: int = Field(default=256 * 1024, ge=4096, le=8 * 1024 * 1024)

    filesystem_root: Path = Path("var/files")
    filesystem_worker_limit: int = Field(default=20, ge=1, le=256)

    auto_migrate: bool = False
    migration_advisory_lock_timeout_seconds: float = Field(default=900, gt=0, le=86_400)
    migration_lock_timeout_seconds: float = Field(default=3, gt=0, le=3600)
    migration_statement_timeout_seconds: float = Field(default=900, gt=0, le=86_400)
    migration_idle_transaction_timeout_seconds: float = Field(default=30, gt=0, le=3600)

    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.pretty

    def database_config(self) -> PostgreSQLConfig | SQLiteConfig:
        if self.database_backend is DatabaseBackend.sqlite:
            return SQLiteConfig(
                path=self.database_sqlite_path,
                busy_timeout_seconds=self.database_sqlite_busy_timeout_seconds,
                cleanup_timeout_seconds=self.database_cleanup_timeout_seconds,
            )
        if self.database_url is None:
            raise ValueError("FOUNDATION_DATABASE_URL is required for the PostgreSQL backend")
        return PostgreSQLConfig(
            url=self.database_url,
            pool_size=self.database_pool_size,
            max_overflow=self.database_max_overflow,
            pool_timeout_seconds=self.database_pool_timeout_seconds,
            pool_recycle_seconds=self.database_pool_recycle_seconds,
            connect_timeout_seconds=self.database_connect_timeout_seconds,
            statement_timeout_seconds=self.database_statement_timeout_seconds,
            cleanup_timeout_seconds=self.database_cleanup_timeout_seconds,
        )

    def redis_config(self) -> RedisServerConfig | RedisMemoryConfig:
        if self.redis_backend is RedisBackend.memory:
            return RedisMemoryConfig(cleanup_timeout_seconds=self.redis_cleanup_timeout_seconds)
        if self.redis_url is None:
            raise ValueError("FOUNDATION_REDIS_URL is required for the Redis backend")
        return RedisServerConfig(
            url=self.redis_url,
            max_connections=self.redis_max_connections,
            connect_timeout_seconds=self.redis_connect_timeout_seconds,
            command_timeout_seconds=self.redis_command_timeout_seconds,
            health_check_interval_seconds=self.redis_health_check_interval_seconds,
            cleanup_timeout_seconds=self.redis_cleanup_timeout_seconds,
        )

    def object_config(self) -> S3ObjectConfig | LocalObjectConfig:
        if self.object_backend is ObjectBackend.local:
            return LocalObjectConfig(root=self.object_local_root, chunk_size=self.object_local_chunk_size)
        if not self.object_bucket:
            raise ValueError("FOUNDATION_OBJECT_BUCKET is required for the S3 backend")
        return S3ObjectConfig(
            bucket=self.object_bucket,
            region=self.object_region,
            endpoint_url=self.object_endpoint_url,
            force_path_style=self.object_force_path_style,
            connect_timeout_seconds=self.object_connect_timeout_seconds,
            read_timeout_seconds=self.object_read_timeout_seconds,
            compatibility_timeout_seconds=self.object_compatibility_timeout_seconds,
            max_pool_connections=self.object_max_pool_connections,
            multipart_part_size=self.object_multipart_part_size,
        )

    def storage_settings(self) -> StorageSettings:
        return StorageSettings(
            database=self.database_config(),
            redis=self.redis_config(),
            objects=self.object_config(),
            filesystem=FilesystemConfig(root=self.filesystem_root, worker_limit=self.filesystem_worker_limit),
        )

    def migration_config(self) -> MigrationConfig:
        return MigrationConfig(
            advisory_lock_timeout_seconds=self.migration_advisory_lock_timeout_seconds,
            lock_timeout_seconds=self.migration_lock_timeout_seconds,
            statement_timeout_seconds=self.migration_statement_timeout_seconds,
            idle_transaction_timeout_seconds=self.migration_idle_transaction_timeout_seconds,
        )

    def secret_protector(self) -> SecretProtector:
        if self.secret_master_key_base64 is None or self.secret_encryption_key_id is None:
            raise SecretProtectionError(
                "FOUNDATION_SECRET_MASTER_KEY_BASE64 and FOUNDATION_SECRET_ENCRYPTION_KEY_ID are required"
            )
        return SecretProtector.from_base64(
            encoded_key=self.secret_master_key_base64.get_secret_value(),
            encryption_key_id=self.secret_encryption_key_id,
        )

    def connector_capability_codec(self) -> ConnectorCapabilityCodec:
        if self.connector_capability_signing_key_base64 is None:
            raise ValueError("FOUNDATION_CONNECTOR_CAPABILITY_SIGNING_KEY_BASE64 is required")
        try:
            key = base64.b64decode(
                self.connector_capability_signing_key_base64.get_secret_value(),
                validate=True,
            )
        except (binascii.Error, ValueError):
            raise ValueError("FOUNDATION_CONNECTOR_CAPABILITY_SIGNING_KEY_BASE64 is invalid") from None
        return ConnectorCapabilityCodec(key)


@lru_cache(maxsize=1)
def get_settings() -> ServiceSettings:
    """Return the process settings singleton without creating resources."""

    return ServiceSettings()
