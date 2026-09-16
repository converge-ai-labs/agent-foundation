"""Typed process settings for a13n Service."""

from __future__ import annotations

from collections.abc import Collection
from functools import lru_cache

from pydantic import Field

from a13n_service.connectivity.browser_urls import split_browser_url
from a13n_service.database import MigrationConfig
from a13n_service.endpoint_policy import EndpointPolicy, EndpointPolicyError
from a13n_service.iam.configuration import IdentityConfiguration
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.storage.config import (
    FilesystemConfig,
    LocalObjectConfig,
    PostgreSQLConfig,
    RedisMemoryConfig,
    RedisServerConfig,
    S3ObjectConfig,
    StorageSettings,
)

from .configuration.sections import (
    AssetsSettings,
    ConfigurationAssistantSettings,
    ConnectivitySettings,
    ControlSettings,
    DatabaseSettings,
    EnvironmentsSettings,
    FilesystemSettings,
    GatewaySettings,
    HooksSettings,
    IamSettings,
    LifecycleSettings,
    LoggingSettings,
    MemorySettings,
    MigrationSettings,
    ModelsSettings,
    ObjectsSettings,
    ObservabilitySettings,
    PluginsSettings,
    PricingSettings,
    ProviderPluginsSettings,
    RedisSettings,
    RunsSettings,
    SecretsSettings,
    Section,
    ServiceSettings,
    SubagentsSettings,
    WebhooksSettings,
    WorkerSettings,
)
from .configuration.sections import ObjectBackend as ObjectBackend
from .configuration.sections import ProcessRole as ProcessRole
from .configuration.sections import RedisBackend as RedisBackend


class Settings(Section):
    """Immutable effective configuration; input sources are resolved by load_settings."""

    service: ServiceSettings = Field(default_factory=ServiceSettings)
    iam: IamSettings = Field(default_factory=IamSettings)
    plugins: PluginsSettings = Field(default_factory=PluginsSettings)
    provider_plugins: ProviderPluginsSettings = Field(default_factory=ProviderPluginsSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    subagents: SubagentsSettings = Field(default_factory=SubagentsSettings)
    environments: EnvironmentsSettings = Field(default_factory=EnvironmentsSettings)
    pricing: PricingSettings = Field(default_factory=PricingSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    models: ModelsSettings = Field(default_factory=ModelsSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    configuration_assistant: ConfigurationAssistantSettings = Field(default_factory=ConfigurationAssistantSettings)
    webhooks: WebhooksSettings = Field(default_factory=WebhooksSettings)
    lifecycle: LifecycleSettings = Field(default_factory=LifecycleSettings)
    control: ControlSettings = Field(default_factory=ControlSettings)
    assets: AssetsSettings = Field(default_factory=AssetsSettings)
    hooks: HooksSettings = Field(default_factory=HooksSettings)
    objects: ObjectsSettings = Field(default_factory=ObjectsSettings)
    runs: RunsSettings = Field(default_factory=RunsSettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    secrets: SecretsSettings = Field(default_factory=SecretsSettings)
    connectivity: ConnectivitySettings = Field(default_factory=ConnectivitySettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    filesystem: FilesystemSettings = Field(default_factory=FilesystemSettings)
    migration: MigrationSettings = Field(default_factory=MigrationSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    def identity_configuration(self) -> IdentityConfiguration:
        return IdentityConfiguration(
            public_origin=self.iam.public_origin,
            session_cookie_name=self.iam.session_cookie_name,
            initial_admin_email=self.iam.initial_admin_email,
            session_days=self.iam.session_days,
            invitation_days=self.iam.invitation_days,
            smtp_host=self.iam.smtp_host,
            smtp_port=self.iam.smtp_port,
            smtp_username=self.iam.smtp_username,
            smtp_password=self.iam.smtp_password,
            smtp_sender=self.iam.smtp_sender,
            smtp_tls=self.iam.smtp_tls,
        )

    def database_config(self) -> PostgreSQLConfig:
        return PostgreSQLConfig.model_validate(self.database.model_dump(exclude={"readiness_timeout_seconds"}))

    def redis_config(self) -> RedisServerConfig | RedisMemoryConfig:
        if self.redis.backend is RedisBackend.memory:
            return RedisMemoryConfig(cleanup_timeout_seconds=self.redis.cleanup_timeout_seconds)
        if self.redis.url is None:
            raise ValueError("A13N_SERVICE_REDIS_URL is required for the Redis backend")
        return RedisServerConfig(
            url=self.redis.url,
            max_connections=self.redis.max_connections,
            connect_timeout_seconds=self.redis.connect_timeout_seconds,
            command_timeout_seconds=self.redis.command_timeout_seconds,
            health_check_interval_seconds=self.redis.health_check_interval_seconds,
            cleanup_timeout_seconds=self.redis.cleanup_timeout_seconds,
        )

    def object_config(self) -> S3ObjectConfig | LocalObjectConfig:
        if self.objects.backend is ObjectBackend.local:
            return LocalObjectConfig(root=self.objects.local_root, chunk_size=self.objects.local_chunk_size)
        if not self.objects.bucket:
            raise ValueError("A13N_SERVICE_OBJECT_BUCKET is required for the S3 backend")
        return S3ObjectConfig(
            bucket=self.objects.bucket,
            region=self.objects.region,
            endpoint_url=self.objects.endpoint_url,
            force_path_style=self.objects.force_path_style,
            connect_timeout_seconds=self.objects.connect_timeout_seconds,
            read_timeout_seconds=self.objects.read_timeout_seconds,
            compatibility_timeout_seconds=self.objects.compatibility_timeout_seconds,
            max_pool_connections=self.objects.max_pool_connections,
            multipart_part_size=self.objects.multipart_part_size,
        )

    def storage_settings(self) -> StorageSettings:
        return StorageSettings(
            database=self.database_config(),
            redis=self.redis_config(),
            objects=self.object_config(),
            filesystem=FilesystemConfig(root=self.filesystem.root, worker_limit=self.filesystem.worker_limit),
        )

    def migration_config(self) -> MigrationConfig:
        return MigrationConfig(
            advisory_lock_timeout_seconds=self.migration.advisory_lock_timeout_seconds,
            lock_timeout_seconds=self.migration.lock_timeout_seconds,
            statement_timeout_seconds=self.migration.statement_timeout_seconds,
            idle_transaction_timeout_seconds=self.migration.idle_transaction_timeout_seconds,
        )

    def connectivity_endpoint_policy(self) -> EndpointPolicy:
        """Build the strict endpoint policy shared by Connector and Remote MCP clients."""

        return EndpointPolicy.from_operator_allowlist(
            private_domains=self.connectivity.private_endpoint_domains,
            private_cidrs=self.connectivity.private_endpoint_cidrs,
            require_https=True,
            http_origins=self.connectivity.http_origins,
        )

    def validated_connectivity_public_origin(self) -> str:
        """Return the configured exact public origin without trusting forwarded headers."""

        if self.connectivity.public_origin is None:
            raise ValueError("A13N_SERVICE_CONNECTIVITY_PUBLIC_ORIGIN is required for control-capable roles")
        try:
            normalized, _, _ = self.connectivity_endpoint_policy().validate_syntax(self.connectivity.public_origin)
            parsed = split_browser_url(normalized)
        except EndpointPolicyError as error:
            raise ValueError("A13N_SERVICE_CONNECTIVITY_PUBLIC_ORIGIN is invalid") from error
        except ValueError as error:
            raise ValueError("A13N_SERVICE_CONNECTIVITY_PUBLIC_ORIGIN must use HTTPS or exact loopback HTTP") from error
        if parsed.path or parsed.query:
            raise ValueError("A13N_SERVICE_CONNECTIVITY_PUBLIC_ORIGIN must be an exact origin")
        return normalized

    def secret_protector(self) -> SecretProtector:
        if self.secrets.master_key_base64 is None or self.secrets.encryption_key_id is None:
            raise SecretProtectionError(
                "A13N_SERVICE_SECRET_MASTER_KEY_BASE64 and A13N_SERVICE_SECRET_ENCRYPTION_KEY_ID are required"
            )
        return SecretProtector.from_base64(
            encoded_key=self.secrets.master_key_base64.get_secret_value(),
            encryption_key_id=self.secrets.encryption_key_id,
        )

    def validate_trace_query_configuration(
        self,
        *,
        registered_provider_keys: Collection[str] = ("langfuse", "logfire"),
    ) -> None:
        """Validate provider selection without opening a control-plane client."""

        if self.observability.query.provider == "none":
            return
        if self.observability.query.provider not in registered_provider_keys:
            raise ValueError(f"Trace Query provider is not registered: {self.observability.query.provider}")
        if self.observability.query.provider == "logfire":
            query = self.observability.query
            if query.logfire_base_url is None:
                raise ValueError("A13N_SERVICE_OBSERVABILITY_QUERY_LOGFIRE_BASE_URL is required")
            if query.logfire_read_token is None:
                raise ValueError("A13N_SERVICE_OBSERVABILITY_QUERY_LOGFIRE_READ_TOKEN is required")
            if query.logfire_history_from is None or query.logfire_history_from.tzinfo is None:
                raise ValueError("A13N_SERVICE_OBSERVABILITY_QUERY_LOGFIRE_HISTORY_FROM must be timezone-aware")
            from a13n_service.trace_query.logfire import validate_logfire_base_url

            validate_logfire_base_url(query.logfire_base_url)
            return
        if self.observability.query.provider != "langfuse":
            return
        if self.observability.query.langfuse_base_url is None:
            raise ValueError("A13N_SERVICE_OBSERVABILITY_QUERY_LANGFUSE_BASE_URL is required")
        if self.observability.query.langfuse_public_key is None:
            raise ValueError("A13N_SERVICE_OBSERVABILITY_QUERY_LANGFUSE_PUBLIC_KEY is required")
        if self.observability.query.langfuse_secret_key is None:
            raise ValueError("A13N_SERVICE_OBSERVABILITY_QUERY_LANGFUSE_SECRET_KEY is required")

        from a13n_service.trace_query.langfuse import validate_langfuse_base_url

        validate_langfuse_base_url(self.observability.query.langfuse_base_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Default embedded application configuration, with no implicit file loading."""
    from .configuration.sources import load_settings

    return load_settings()
