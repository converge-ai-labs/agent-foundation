"""Typed process settings for Foundation Service."""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from a13n_logging import LogFormat
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from a13n_service.agents.domain import PluginRuntimeMode
from a13n_service.connectivity.bounds import (
    MAX_REDIRECTS,
    PROVIDER_REQUEST_MAX_BYTES,
    TOOL_RESULT_MAX_BYTES,
)
from a13n_service.database import MigrationConfig
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.observability import TraceContent
from a13n_service.secrets import SecretProtectionError, SecretProtector
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


class ProcessRole(StrEnum):
    """Process roles supported by the shared service artifact."""

    all = "all"
    control = "control"
    worker = "worker"
    connectivity = "connectivity"


class DatabaseBackend(StrEnum):
    postgresql = "postgresql"
    sqlite = "sqlite"


class RedisBackend(StrEnum):
    redis = "redis"
    memory = "memory"


class ObjectBackend(StrEnum):
    s3 = "s3"
    local = "local"


class Settings(BaseSettings):
    """Load executable configuration from ``FOUNDATION_*`` variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FOUNDATION_",
        extra="ignore",
        case_sensitive=False,
    )

    service_name: str = "foundation-service"
    role: ProcessRole = ProcessRole.all
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    build_version: str = "unknown"
    deployment_environment_name: str = Field(default="default", min_length=1, max_length=256)
    service_instance_id: str | None = Field(default=None, min_length=1, max_length=1024)
    plugin_runtime_mode: PluginRuntimeMode = PluginRuntimeMode.on_demand
    worker_concurrency: int = Field(default=8, ge=1, le=1024)
    worker_scan_limit: int = Field(default=32, ge=1, le=1024)
    worker_poll_interval_seconds: float = Field(default=0.5, gt=0, le=60)
    worker_lease_seconds: float = Field(default=30, gt=0, le=3600)
    worker_renewal_interval_seconds: float = Field(default=5, gt=0, le=300)
    worker_renewal_timeout_seconds: float = Field(default=5, gt=0, le=300)
    worker_reconciliation_timeout_seconds: float = Field(default=10, gt=0, le=300)
    worker_preparation_timeout_seconds: float = Field(default=120, gt=0, le=900)
    worker_cleanup_timeout_seconds: float = Field(default=15, gt=0, le=300)
    worker_drain_timeout_seconds: float = Field(default=30, gt=0, le=900)
    worker_handoff_preference_seconds: float | None = Field(default=None, gt=0, le=3600)
    plugin_max_wheel_bytes: int = Field(default=50 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    plugin_max_expanded_bytes: int = Field(default=200 * 1024 * 1024, ge=1, le=4 * 1024 * 1024 * 1024)
    plugin_max_archive_members: int = Field(default=20_000, ge=1, le=1_000_000)
    plugin_runtime_command_poll_interval_seconds: float = Field(default=1, gt=0, le=60)
    plugin_runtime_command_lease_seconds: float = Field(default=300, gt=3, le=3600)
    plugin_runtime_resolver_executable: str = Field(default="uv", min_length=1, max_length=1024)
    plugin_runtime_default_index_url: SecretStr = Field(
        default=SecretStr("https://pypi.org/simple"),
        min_length=1,
        max_length=4096,
        repr=False,
    )
    plugin_runtime_index_urls: tuple[SecretStr, ...] = Field(default=(), repr=False)
    plugin_runtime_resolver_timeout_seconds: float = Field(default=120, gt=0, le=900)
    plugin_runtime_resolver_max_packages: int = Field(default=512, ge=1, le=4096)
    plugin_runtime_max_materialized_bytes: int = Field(
        default=4 * 1024 * 1024 * 1024,
        ge=1,
        le=128 * 1024 * 1024 * 1024,
    )
    plugin_runner_ready_timeout_seconds: float = Field(default=60, gt=0, le=300)
    plugin_runner_command_timeout_seconds: float = Field(default=30, gt=0, le=300)
    plugin_runner_shutdown_timeout_seconds: float = Field(default=30, gt=0, le=300)
    plugin_runner_max_processes: int = Field(default=8, ge=1, le=256)
    environment_provider_builtins: tuple[str, ...] = (
        "a13n.direct-local",
        "a13n.local-envd",
        "a13n.docker",
        "a13n.e2b",
        "a13n.http-envd",
    )
    environment_provider_extensions: tuple[str, ...] = ()
    environment_maintenance_interval_seconds: float = Field(default=5, gt=0, le=300)
    environment_operation_timeout_seconds: float = Field(default=60, gt=0, le=3600)
    environment_maintenance_concurrency: int = Field(default=4, ge=1, le=128)
    pricing_auto_update: bool = True
    observability_tracing: bool = True
    observability_trace_content: TraceContent = TraceContent.none
    observability_query_provider: str = Field(default="none", pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    observability_query_langfuse_base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    observability_query_langfuse_public_key: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        repr=False,
    )
    observability_query_langfuse_secret_key: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        repr=False,
    )
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
    webhook_private_endpoint_domains: tuple[str, ...] = ()
    webhook_private_endpoint_cidrs: tuple[str, ...] = ()
    webhook_poll_interval_seconds: float = Field(default=1, gt=0, le=60)
    webhook_claim_lease_seconds: float = Field(default=30, gt=0, le=3600)
    webhook_claim_limit: int = Field(default=25, ge=1, le=100)
    webhook_max_attempts: int = Field(default=10, ge=1, le=1000)
    webhook_retry_base_seconds: float = Field(default=2, gt=0, le=3600)
    webhook_retry_max_seconds: float = Field(default=300, gt=0, le=86_400)
    webhook_request_timeout_seconds: float = Field(default=10, gt=0, le=300)
    webhook_max_response_bytes: int = Field(default=64 * 1024, ge=1, le=16 * 1024 * 1024)
    lifecycle_retention_days: int = Field(default=30, ge=1, le=3650)
    lifecycle_published_delivery_retention_days: int = Field(default=7, ge=1, le=3650)
    lifecycle_dead_letter_retention_days: int = Field(default=30, ge=1, le=3650)
    lifecycle_retention_poll_interval_seconds: float = Field(default=300, gt=0, le=86_400)
    lifecycle_retention_batch_limit: int = Field(default=200, ge=1, le=1000)
    lifecycle_projection_poll_interval_seconds: float = Field(default=1, gt=0, le=60)
    lifecycle_projection_lease_seconds: float = Field(default=60, gt=0, le=3600)
    lifecycle_projection_retry_seconds: float = Field(default=5, ge=0, le=3600)
    lifecycle_projection_max_attempts: int = Field(default=20, ge=1, le=1000)
    lifecycle_projection_claim_limit: int = Field(default=16, ge=1, le=200)
    run_stream_max_events: int = Field(default=4096, ge=1, le=100_000)
    run_stream_max_event_bytes: int = Field(default=320 * 1024, ge=1024, le=16 * 1024 * 1024)
    run_stream_closed_ttl_seconds: int = Field(default=24 * 60 * 60, ge=60, le=365 * 24 * 60 * 60)
    run_replay_max_events: int = Field(default=4096, ge=1, le=100_000)
    run_replay_max_items: int = Field(default=2048, ge=1, le=100_000)
    run_replay_max_bytes: int = Field(default=16 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    gateway_stream_page_size: int = Field(default=256, ge=1, le=1000)
    gateway_stream_poll_interval_seconds: float = Field(default=0.25, gt=0, le=10)
    gateway_stream_heartbeat_interval_seconds: float = Field(default=15, gt=0, le=300)
    gateway_stream_authorization_interval_seconds: float = Field(default=30, gt=0, le=300)
    gateway_stream_maximum_lifetime_seconds: float = Field(default=300, gt=0, le=3600)
    gateway_notification_poll_interval_seconds: float = Field(default=0.5, gt=0, le=30)
    gateway_notification_heartbeat_interval_seconds: float = Field(default=20, gt=0, le=300)
    gateway_notification_send_timeout_seconds: float = Field(default=10, gt=0, le=60)
    gateway_notification_max_frame_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    gateway_notification_max_subscriptions: int = Field(default=64, ge=1, le=1024)
    gateway_notification_max_topics: int = Field(default=128, ge=1, le=4096)
    gateway_notification_poll_limit: int = Field(default=100, ge=1, le=1000)
    gateway_notification_maximum_lifetime_seconds: float = Field(default=3600, gt=0, le=86_400)
    gateway_run_recovery_max_attempts: int = Field(default=3, ge=0, le=100)
    gateway_run_max_handoffs: int = Field(default=2, ge=0, le=100)
    gateway_run_queue_name: str = Field(default="default", pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
    gateway_run_priority: int = Field(default=0, ge=-1_000_000, le=1_000_000)
    a2a_enabled: bool = True
    a2a_public_origin: str | None = Field(default=None, min_length=1, max_length=2048)
    a2a_default_agent_id: str | None = Field(default=None, min_length=1, max_length=72)
    a2a_poll_interval_seconds: float = Field(default=0.5, gt=0, le=30)
    a2a_maximum_wait_seconds: float = Field(default=300, gt=0, le=3600)
    secret_master_key_base64: SecretStr | None = Field(default=None, repr=False)
    secret_encryption_key_id: str | None = Field(default=None, min_length=1, max_length=128, repr=False)

    connectivity_provider_request_max_bytes: int = Field(
        default=PROVIDER_REQUEST_MAX_BYTES,
        ge=1,
        le=PROVIDER_REQUEST_MAX_BYTES,
    )
    connectivity_workspace_pending_max_count: int = Field(default=10_000, ge=1, le=1_000_000)
    connectivity_workspace_pending_max_bytes: int = Field(default=1024 * 1024 * 1024, ge=1, le=2**63 - 1)
    connectivity_account_pending_max_count: int = Field(default=1_000, ge=1, le=100_000)
    connectivity_account_pending_max_bytes: int = Field(default=128 * 1024 * 1024, ge=1, le=2**63 - 1)
    connectivity_batch_max_events: int = Field(default=100, ge=1, le=1_000)
    connectivity_batch_max_bytes: int = Field(default=4 * 1024 * 1024, ge=1, le=64 * 1024 * 1024)
    connectivity_batch_max_wait_seconds: float = Field(default=300, gt=0, le=3600)
    connectivity_admission_poll_interval_seconds: float = Field(default=1, gt=0, le=60)
    connectivity_admission_lease_seconds: float = Field(default=30, gt=0, le=3600)
    connectivity_admission_max_attempts: int = Field(default=20, ge=1, le=1_000)
    connectivity_admission_max_backoff_seconds: float = Field(default=300, gt=0, le=3600)
    connectivity_dedup_horizon_seconds: int = Field(default=7 * 24 * 60 * 60, ge=60, le=30 * 24 * 60 * 60)
    connectivity_connect_timeout_seconds: float = Field(default=5, gt=0, le=60)
    connectivity_read_timeout_seconds: float = Field(default=30, gt=0, le=300)
    connectivity_total_timeout_seconds: float = Field(default=60, gt=0, le=600)
    connectivity_response_max_bytes: int = Field(default=1024 * 1024, ge=1, le=8 * 1024 * 1024)
    connectivity_tool_result_max_bytes: int = Field(
        default=TOOL_RESULT_MAX_BYTES,
        ge=1,
        le=TOOL_RESULT_MAX_BYTES,
    )
    connectivity_max_redirects: int = Field(default=MAX_REDIRECTS, ge=0, le=MAX_REDIRECTS)
    connectivity_oauth_setup_ttl_seconds: int = Field(default=600, ge=60, le=900)
    connectivity_public_origin: str | None = Field(default=None, min_length=1, max_length=2048)
    connectivity_oauth_client_name: str = Field(default="Agent Foundation", min_length=1, max_length=128)
    connectivity_private_endpoint_domains: tuple[str, ...] = ()
    connectivity_private_endpoint_cidrs: tuple[str, ...] = ()
    connectivity_http_origins: tuple[str, ...] = ()
    connectivity_provider_origins: tuple[str, ...] = ()
    connectivity_provider_token_expiry_skew_seconds: int = Field(default=60, ge=0, le=600)
    connectivity_setup_correlation_secret: SecretStr | None = Field(
        default=None,
        min_length=32,
        max_length=4096,
        repr=False,
    )
    connectivity_connector_reconcile_poll_interval_seconds: float = Field(default=2, gt=0, le=300)
    connectivity_connector_reconcile_lease_seconds: int = Field(default=60, ge=10, le=600)
    connectivity_retention_poll_interval_seconds: float = Field(default=60, gt=0, le=3600)
    connectivity_retention_batch_size: int = Field(default=25, ge=1, le=1000)

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

    asset_max_size_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=2**63 - 1)
    asset_cleanup_poll_interval_seconds: float = Field(default=5, gt=0, le=300)
    asset_cleanup_lease_seconds: float = Field(default=30, gt=0, le=3600)
    asset_cleanup_max_attempts: int = Field(default=10, ge=1, le=1000)

    filesystem_root: Path = Path("var/files")
    filesystem_worker_limit: int = Field(default=20, ge=1, le=256)

    auto_migrate: bool = False
    migration_advisory_lock_timeout_seconds: float = Field(default=900, gt=0, le=86_400)
    migration_lock_timeout_seconds: float = Field(default=3, gt=0, le=3600)
    migration_statement_timeout_seconds: float = Field(default=900, gt=0, le=86_400)
    migration_idle_transaction_timeout_seconds: float = Field(default=30, gt=0, le=3600)

    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.pretty

    @model_validator(mode="after")
    def validate_worker_bounds(self) -> Self:
        if self.worker_renewal_interval_seconds + self.worker_renewal_timeout_seconds >= self.worker_lease_seconds:
            raise ValueError("Worker lease must exceed the renewal interval plus renewal timeout")
        return self

    @model_validator(mode="after")
    def validate_connectivity_bounds(self) -> Self:
        if self.connectivity_account_pending_max_count > self.connectivity_workspace_pending_max_count:
            raise ValueError("Ingress pending count cannot exceed the Workspace pending count")
        if self.connectivity_account_pending_max_bytes > self.connectivity_workspace_pending_max_bytes:
            raise ValueError("Ingress pending bytes cannot exceed the Workspace pending bytes")
        if self.connectivity_batch_max_events > self.connectivity_account_pending_max_count:
            raise ValueError("batch event count cannot exceed the Ingress pending count")
        if self.connectivity_batch_max_bytes > self.connectivity_account_pending_max_bytes:
            raise ValueError("batch bytes cannot exceed the Ingress pending bytes")
        if self.connectivity_admission_lease_seconds <= self.connectivity_admission_poll_interval_seconds:
            raise ValueError("admission lease must exceed its poll interval")
        if self.connectivity_total_timeout_seconds < max(
            self.connectivity_connect_timeout_seconds,
            self.connectivity_read_timeout_seconds,
        ):
            raise ValueError("Connectivity total timeout cannot be shorter than a phase timeout")
        return self

    @model_validator(mode="after")
    def webhook_delivery_policy_is_coherent(self) -> Self:
        if self.webhook_claim_lease_seconds <= self.webhook_request_timeout_seconds:
            raise ValueError("Webhook claim lease must exceed the request timeout")
        if self.webhook_retry_max_seconds < self.webhook_retry_base_seconds:
            raise ValueError("Webhook maximum retry delay must cover the base delay")
        return self

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

    def connectivity_endpoint_policy(self) -> EndpointPolicy:
        """Build the strict endpoint policy shared by Connector and Remote MCP clients."""

        return EndpointPolicy.from_operator_allowlist(
            private_domains=self.connectivity_private_endpoint_domains,
            private_cidrs=self.connectivity_private_endpoint_cidrs,
            require_https=True,
            http_origins=self.connectivity_http_origins,
        )

    def validated_connectivity_public_origin(self) -> str:
        """Return the configured exact public origin without trusting forwarded headers."""

        if self.connectivity_public_origin is None:
            raise ValueError("FOUNDATION_CONNECTIVITY_PUBLIC_ORIGIN is required for control-capable roles")
        try:
            normalized, _, _ = self.connectivity_endpoint_policy().validate_syntax(self.connectivity_public_origin)
        except EndpointPolicyError as error:
            raise ValueError("FOUNDATION_CONNECTIVITY_PUBLIC_ORIGIN is invalid") from error
        parsed = urlsplit(normalized)
        if parsed.path or parsed.query:
            raise ValueError("FOUNDATION_CONNECTIVITY_PUBLIC_ORIGIN must be an exact origin")
        return normalized

    def secret_protector(self) -> SecretProtector:
        if self.secret_master_key_base64 is None or self.secret_encryption_key_id is None:
            raise SecretProtectionError(
                "FOUNDATION_SECRET_MASTER_KEY_BASE64 and FOUNDATION_SECRET_ENCRYPTION_KEY_ID are required"
            )
        return SecretProtector.from_base64(
            encoded_key=self.secret_master_key_base64.get_secret_value(),
            encryption_key_id=self.secret_encryption_key_id,
        )

    def validate_trace_query_configuration(
        self,
        *,
        registered_provider_keys: Collection[str] = ("langfuse",),
    ) -> None:
        """Validate provider selection without opening a control-plane client."""

        if self.observability_query_provider == "none":
            return
        if self.observability_query_provider not in registered_provider_keys:
            raise ValueError(f"Trace Query provider is not registered: {self.observability_query_provider}")
        if self.observability_query_provider != "langfuse":
            return
        if self.observability_query_langfuse_base_url is None:
            raise ValueError("FOUNDATION_OBSERVABILITY_QUERY_LANGFUSE_BASE_URL is required")
        if self.observability_query_langfuse_public_key is None:
            raise ValueError("FOUNDATION_OBSERVABILITY_QUERY_LANGFUSE_PUBLIC_KEY is required")
        if self.observability_query_langfuse_secret_key is None:
            raise ValueError("FOUNDATION_OBSERVABILITY_QUERY_LANGFUSE_SECRET_KEY is required")

        from a13n_service.trace_query.langfuse import validate_langfuse_base_url

        validate_langfuse_base_url(self.observability_query_langfuse_base_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process settings singleton without creating resources."""

    return Settings()
