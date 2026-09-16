"""Typed process settings for a13n Service."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from a13n_logging import LogFormat
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator, model_validator

from a13n_service import __version__
from a13n_service.agents.domain import PluginKey
from a13n_service.connectivity.bounds import (
    MAX_REDIRECTS,
    PROVIDER_REQUEST_MAX_BYTES,
)
from a13n_service.environments.domain import LOCAL_PROVIDER_TYPES, JsonObject, LocalProviderType
from a13n_service.environments.policy import DEFAULT_BATCH_SIZE, DEFAULT_MAX_ACTIVE, DEFAULT_MAX_TARGETS
from a13n_service.observability import TraceContent
from a13n_service.storage.config import PostgreSQLConfig


class ProcessRole(StrEnum):
    """Process roles supported by the shared service artifact."""

    all = "all"
    control = "control"
    worker = "worker"
    connectivity = "connectivity"


class RedisBackend(StrEnum):
    redis = "redis"
    memory = "memory"


class ObjectBackend(StrEnum):
    s3 = "s3"
    local = "local"


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class MemorySettings(Section):
    timeout_seconds: float = Field(default=30, gt=0, le=300)


class ConfigurationAssistantSettings(Section):
    total_tokens_limit: int | None = Field(default=None, ge=1)


class ObservabilityQuerySettings(Section):
    logfire_base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    logfire_read_token: SecretStr | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    logfire_history_from: datetime | None = None
    provider: str = Field(default="none", pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    langfuse_base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    langfuse_public_key: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        repr=False,
    )
    langfuse_secret_key: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        repr=False,
    )


class ServiceSettings(Section):
    name: str = "a13n-service"
    role: ProcessRole = ProcessRole.all
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    build_version: str = __version__
    deployment_environment_name: str = Field(default="default", min_length=1, max_length=256)
    instance_id: str | None = Field(default=None, min_length=1, max_length=1024)


class IamSettings(Section):
    public_origin: str = "http://127.0.0.1:8000"
    session_cookie_name: str = Field(default="a13n_session", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    initial_admin_email: EmailStr | None = None
    session_days: int = Field(default=7, ge=1, le=90)
    invitation_days: int = Field(default=7, ge=1, le=30)
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, repr=False)
    smtp_password: SecretStr | None = None
    smtp_sender: EmailStr | None = None
    smtp_tls: Literal["starttls", "tls"] = "starttls"


class PluginsSettings(Section):
    keys: tuple[PluginKey, ...] = Field(default=(), max_length=128)


class ProviderPluginsSettings(Section):
    enabled: tuple[str, ...] = Field(default=(), max_length=128)


class WorkerSettings(Section):
    concurrency: int = Field(default=8, ge=1, le=1024)
    poll_interval_seconds: float = Field(default=1, gt=0, le=60)
    lease_seconds: float = Field(default=30, ge=12, le=3600)
    cleanup_seconds: float = Field(default=10, gt=0, le=300)
    drain_seconds: float = Field(default=30, gt=0, le=3600)


class SubagentsSettings(Section):
    reconcile_drain_seconds: float = Field(default=30, gt=0, le=3600)
    reconcile_poll_interval_seconds: float = Field(default=1, gt=0, le=60)


class EnvironmentsSettings(Section):
    provider_builtins: tuple[str, ...] = ("a13n.e2b", "a13n.http-envd")
    local_providers: dict[LocalProviderType, JsonObject] = Field(default_factory=dict)
    maintenance_interval_seconds: float = Field(default=5, gt=0, le=300)
    operation_timeout_seconds: float = Field(default=60, gt=0, le=3600)
    max_targets_per_workspace: int = Field(default=DEFAULT_MAX_TARGETS, ge=1)
    max_active_per_workspace: int = Field(default=DEFAULT_MAX_ACTIVE, ge=1)
    maintenance_batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=1, le=10_000)
    maintenance_concurrency: int = Field(default=4, ge=1, le=128)

    @model_validator(mode="after")
    def local_provider_configuration(self) -> Self:
        if LOCAL_PROVIDER_TYPES.intersection(self.provider_builtins):
            raise ValueError("Configure local backends through environments.local_providers, not provider_builtins")
        return self


class PricingSettings(Section):
    auto_update: bool = True


class ObservabilitySettings(Section):
    tracing: bool = True
    trace_content: TraceContent = TraceContent.none
    query: ObservabilityQuerySettings = Field(default_factory=ObservabilityQuerySettings)


class DatabaseSettings(PostgreSQLConfig):
    url: SecretStr = Field(
        default=SecretStr("postgresql+psycopg://a13n_service:a13n_service@127.0.0.1:5432/a13n_service"),
        repr=False,
    )
    readiness_timeout_seconds: float = Field(default=3, gt=0, le=300)


class ModelsSettings(Section):
    private_endpoint_domains: tuple[str, ...] = ()
    private_endpoint_cidrs: tuple[str, ...] = ()
    resolve_dns_on_save: bool = True
    connection_test_timeout_seconds: float = Field(default=15, gt=0, le=120)


class WebhooksSettings(Section):
    private_endpoint_domains: tuple[str, ...] = ()
    private_endpoint_cidrs: tuple[str, ...] = ()
    poll_interval_seconds: float = Field(default=1, gt=0, le=60)
    claim_lease_seconds: float = Field(default=30, gt=0, le=3600)
    claim_limit: int = Field(default=25, ge=1, le=100)
    max_attempts: int = Field(default=10, ge=1, le=1000)
    retry_base_seconds: float = Field(default=2, gt=0, le=3600)
    retry_max_seconds: float = Field(default=300, gt=0, le=86_400)
    request_timeout_seconds: float = Field(default=10, gt=0, le=300)
    max_response_bytes: int = Field(default=64 * 1024, ge=1, le=16 * 1024 * 1024)

    @model_validator(mode="after")
    def webhook_delivery_policy_is_coherent(self) -> Self:
        if self.claim_lease_seconds <= self.request_timeout_seconds:
            raise ValueError("Webhook claim lease must exceed the request timeout")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("Webhook maximum retry delay must cover the base delay")
        return self


class LifecycleSettings(Section):
    retention_days: int = Field(default=30, ge=1, le=3650)
    published_delivery_retention_days: int = Field(default=7, ge=1, le=3650)
    dead_letter_retention_days: int = Field(default=30, ge=1, le=3650)
    retention_poll_interval_seconds: float = Field(default=300, gt=0, le=86_400)
    retention_batch_limit: int = Field(default=200, ge=1, le=1000)
    projection_poll_interval_seconds: float = Field(default=1, gt=0, le=60)
    projection_lease_seconds: float = Field(default=60, gt=0, le=3600)
    projection_retry_seconds: float = Field(default=5, ge=0, le=3600)
    projection_max_attempts: int = Field(default=20, ge=1, le=1000)
    projection_claim_limit: int = Field(default=16, ge=1, le=200)


class ControlSettings(Section):
    recovery_poll_interval_seconds: float = Field(default=1, gt=0, le=300)
    recovery_batch_limit: int = Field(default=64, ge=1, le=1000)
    recovery_item_timeout_seconds: float = Field(default=30, gt=0, le=300)
    collection_poll_interval_seconds: float = Field(default=300, gt=0, le=86_400)
    collection_batch_limit: int = Field(default=64, ge=1, le=1000)
    collection_timeout_seconds: float = Field(default=30, gt=0, le=300)


class AssetsSettings(Section):
    tombstone_minimum_retention_days: int = Field(default=30, ge=1, le=36500)
    max_size_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=2**63 - 1)
    cleanup_poll_interval_seconds: float = Field(default=5, gt=0, le=300)
    cleanup_lease_seconds: float = Field(default=30, gt=0, le=3600)
    cleanup_max_attempts: int = Field(default=10, ge=1, le=1000)


class HooksSettings(Section):
    history_minimum_retention_days: int = Field(default=30, ge=1, le=3650)


class ObjectsSettings(Section):
    publication_timeout_seconds: float = Field(default=120, gt=0, le=3600)
    orphan_minimum_age_hours: int = Field(default=24, ge=1, le=87600)
    backend: ObjectBackend = ObjectBackend.local
    local_root: Path = Path("var/objects")
    bucket: str | None = None
    region: str = Field(default="us-east-1", min_length=1)
    endpoint_url: str | None = None
    force_path_style: bool = False
    connect_timeout_seconds: float = Field(default=5, gt=0, le=300)
    read_timeout_seconds: float = Field(default=30, gt=0, le=300)
    compatibility_timeout_seconds: float = Field(default=120, gt=0, le=600)
    max_pool_connections: int = Field(default=20, ge=1, le=1000)
    multipart_part_size: int = Field(default=8 * 1024 * 1024, ge=5 * 1024 * 1024, le=64 * 1024 * 1024)
    local_chunk_size: int = Field(default=256 * 1024, ge=4096, le=8 * 1024 * 1024)


class RunsSettings(Section):
    stream_max_events: int = Field(default=4096, ge=1, le=100_000)
    stream_max_event_bytes: int = Field(default=320 * 1024, ge=1024, le=16 * 1024 * 1024)
    stream_closed_ttl_seconds: int = Field(default=24 * 60 * 60, ge=60, le=365 * 24 * 60 * 60)
    replay_max_events: int = Field(default=4096, ge=1, le=100_000)
    replay_max_items: int = Field(default=2048, ge=1, le=100_000)
    replay_max_bytes: int = Field(default=16 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)


class GatewaySettings(Section):
    stream_page_size: int = Field(default=256, ge=1, le=1000)
    stream_poll_interval_seconds: float = Field(default=0.25, gt=0, le=10)
    stream_heartbeat_interval_seconds: float = Field(default=15, gt=0, le=300)
    stream_authorization_interval_seconds: float = Field(default=30, gt=0, le=300)
    stream_maximum_lifetime_seconds: float = Field(default=300, gt=0, le=3600)
    notification_poll_interval_seconds: float = Field(default=0.5, gt=0, le=30)
    notification_heartbeat_interval_seconds: float = Field(default=20, gt=0, le=300)
    notification_send_timeout_seconds: float = Field(default=10, gt=0, le=60)
    notification_max_frame_bytes: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    notification_max_subscriptions: int = Field(default=64, ge=1, le=1024)
    notification_max_topics: int = Field(default=128, ge=1, le=4096)
    notification_poll_limit: int = Field(default=100, ge=1, le=1000)
    notification_maximum_lifetime_seconds: float = Field(default=3600, gt=0, le=86_400)
    run_execution_max_attempts: int = Field(default=3, ge=0, le=100)
    run_max_handoffs: int = Field(default=2, ge=0, le=100)
    run_queue_name: str = Field(default="default", pattern=r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")
    run_priority: int = Field(default=0, ge=-1_000_000, le=1_000_000)
    a2a_enabled: bool = True
    a2a_public_origin: str | None = Field(default=None, min_length=1, max_length=2048)
    a2a_default_agent_id: str | None = Field(default=None, min_length=1, max_length=72)
    a2a_poll_interval_seconds: float = Field(default=0.5, gt=0, le=30)
    a2a_maximum_wait_seconds: float = Field(default=300, gt=0, le=3600)


class SecretsSettings(Section):
    master_key_base64: SecretStr | None = Field(default=None, repr=False)
    encryption_key_id: str | None = Field(default=None, min_length=1, max_length=128, repr=False)


class MCPServerSettings(Section):
    key: str = Field(pattern=r"^[a-z][a-z0-9-]{0,127}$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1024)
    endpoint_url: str = Field(min_length=1, max_length=2048)
    auth_mode: Literal["none", "bearer", "oauth", "static_headers"]
    documentation_url: str | None = Field(default=None, min_length=1, max_length=2048)
    logo_url: str | None = Field(default=None, min_length=1, max_length=2048)
    requirements: str = Field(default="", max_length=2048)
    static_header_names: tuple[str, ...] = Field(default=(), max_length=16)
    override_builtin: bool = False

    @model_validator(mode="after")
    def valid_static_headers(self) -> Self:
        names = tuple(name.casefold() for name in self.static_header_names)
        if len(set(names)) != len(names):
            raise ValueError("MCP server static header names must be unique")
        if (self.auth_mode == "static_headers") != bool(names):
            raise ValueError("Static-header catalog entries require header names only for static_headers auth")
        return self


class ConnectivitySettings(Section):
    provider_request_max_bytes: int = Field(
        default=PROVIDER_REQUEST_MAX_BYTES,
        ge=1,
        le=PROVIDER_REQUEST_MAX_BYTES,
    )
    workspace_pending_max_count: int = Field(default=10_000, ge=1, le=1_000_000)
    workspace_pending_max_bytes: int = Field(default=1024 * 1024 * 1024, ge=1, le=2**63 - 1)
    account_pending_max_count: int = Field(default=1_000, ge=1, le=100_000)
    account_pending_max_bytes: int = Field(default=128 * 1024 * 1024, ge=1, le=2**63 - 1)
    batch_max_events: int = Field(default=100, ge=1, le=1_000)
    batch_max_bytes: int = Field(default=4 * 1024 * 1024, ge=1, le=64 * 1024 * 1024)
    batch_max_wait_seconds: float = Field(default=300, gt=0, le=3600)
    admission_poll_interval_seconds: float = Field(default=1, gt=0, le=60)
    admission_lease_seconds: float = Field(default=30, gt=0, le=3600)
    admission_backoff_steps: int = Field(default=20, ge=1, le=1_000)
    admission_max_backoff_seconds: float = Field(default=300, gt=0, le=3600)
    dedup_horizon_seconds: int = Field(default=7 * 24 * 60 * 60, ge=60, le=30 * 24 * 60 * 60)
    connect_timeout_seconds: float = Field(default=5, gt=0, le=60)
    read_timeout_seconds: float = Field(default=30, gt=0, le=300)
    total_timeout_seconds: float = Field(default=60, gt=0, le=600)
    response_max_bytes: int = Field(default=1024 * 1024, ge=1, le=8 * 1024 * 1024)
    max_redirects: int = Field(default=MAX_REDIRECTS, ge=0, le=MAX_REDIRECTS)
    oauth_setup_ttl_seconds: int = Field(default=600, ge=60, le=900)
    public_origin: str | None = Field(default=None, min_length=1, max_length=2048)
    authorization_callback_urls: tuple[str, ...] = ()
    mcp_servers: tuple[MCPServerSettings, ...] = ()
    oauth_client_name: str = Field(default="Agent Foundation", min_length=1, max_length=128)
    private_endpoint_domains: tuple[str, ...] = ()
    private_endpoint_cidrs: tuple[str, ...] = ()
    http_origins: tuple[str, ...] = ()
    provider_origins: tuple[str, ...] = ()
    provider_token_expiry_skew_seconds: int = Field(default=60, ge=0, le=600)
    setup_correlation_secret: SecretStr | None = Field(
        default=None,
        min_length=32,
        max_length=4096,
        repr=False,
    )
    connector_reconcile_poll_interval_seconds: float = Field(default=2, gt=0, le=300)
    connector_reconcile_lease_seconds: int = Field(default=60, ge=10, le=600)
    retention_poll_interval_seconds: float = Field(default=60, gt=0, le=3600)
    retention_batch_size: int = Field(default=25, ge=1, le=1000)

    @field_validator("authorization_callback_urls")
    @classmethod
    def valid_authorization_callbacks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        from a13n_service.connectivity.browser_urls import split_browser_url

        if len(set(values)) != len(values):
            raise ValueError("Authorization callback URLs must be unique")
        for value in values:
            split_browser_url(value)
        return values

    @model_validator(mode="after")
    def validate_connectivity_bounds(self) -> Self:
        if self.account_pending_max_count > self.workspace_pending_max_count:
            raise ValueError("Ingress pending count cannot exceed the Workspace pending count")
        if self.account_pending_max_bytes > self.workspace_pending_max_bytes:
            raise ValueError("Ingress pending bytes cannot exceed the Workspace pending bytes")
        if self.batch_max_events > self.account_pending_max_count:
            raise ValueError("batch event count cannot exceed the Ingress pending count")
        if self.batch_max_bytes > self.account_pending_max_bytes:
            raise ValueError("batch bytes cannot exceed the Ingress pending bytes")
        if self.admission_lease_seconds <= self.admission_poll_interval_seconds:
            raise ValueError("admission lease must exceed its poll interval")
        if self.total_timeout_seconds < max(
            self.connect_timeout_seconds,
            self.read_timeout_seconds,
        ):
            raise ValueError("Connectivity total timeout cannot be shorter than a phase timeout")
        return self


class RedisSettings(Section):
    backend: RedisBackend = RedisBackend.redis
    url: SecretStr | None = Field(default=SecretStr("redis://127.0.0.1:6379/0"), repr=False)
    max_connections: int = Field(default=20, ge=1, le=1000)
    connect_timeout_seconds: float = Field(default=5, gt=0, le=300)
    command_timeout_seconds: float = Field(default=10, gt=0, le=300)
    health_check_interval_seconds: float = Field(default=30, gt=0, le=3600)
    cleanup_timeout_seconds: float = Field(default=5, gt=0, le=60)


class FilesystemSettings(Section):
    root: Path = Path("var/files")
    worker_limit: int = Field(default=20, ge=1, le=256)


class MigrationSettings(Section):
    auto_migrate: bool = False
    advisory_lock_timeout_seconds: float = Field(default=900, gt=0, le=86_400)
    lock_timeout_seconds: float = Field(default=3, gt=0, le=3600)
    statement_timeout_seconds: float = Field(default=900, gt=0, le=86_400)
    idle_transaction_timeout_seconds: float = Field(default=30, gt=0, le=3600)


class LoggingSettings(Section):
    level: str = "INFO"
    format: LogFormat = LogFormat.pretty
