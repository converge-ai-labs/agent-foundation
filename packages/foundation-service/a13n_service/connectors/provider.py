"""Typed in-process contract implemented by trusted Connector Providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from anyio import fail_after
from pydantic import JsonValue, SecretStr

from .errors import ConnectorError, ConnectorProviderCapabilityError

type JsonObject = Mapping[str, JsonValue]
type EventDelivery = Literal["webhook", "polling"]


async def invoke_provider[T](context: ConnectorProviderContext, operation: Awaitable[T]) -> T:
    """Enforce one Foundation-owned absolute deadline around Provider I/O."""

    timeout_seconds = (context.deadline.astimezone(UTC) - datetime.now(UTC)).total_seconds()
    if timeout_seconds <= 0:
        if hasattr(operation, "close"):
            operation.close()  # type: ignore[attr-defined]
        raise ConnectorError("Connector Provider operation timed out.", code="provider_timeout")
    try:
        with fail_after(timeout_seconds):
            return await operation
    except TimeoutError:
        raise ConnectorError("Connector Provider operation timed out.", code="provider_timeout") from None


@dataclass(frozen=True, slots=True)
class ConnectorProviderCapabilities:
    """Capability groups implemented by one Provider."""

    tools: bool = False
    connections: bool = False
    events: bool = False
    event_delivery: EventDelivery | None = None

    def __post_init__(self) -> None:
        if self.events != (self.event_delivery is not None):
            raise ValueError("event_delivery must be set exactly when events are supported")


@dataclass(frozen=True, slots=True)
class ConnectorProviderMetadata:
    """Bounded deterministic metadata safe for the Provider catalog."""

    display_name: str
    description: str
    provider_config_schemas: Mapping[str, JsonObject]
    capabilities: ConnectorProviderCapabilities
    connection_setup_modes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConnectorProviderContext:
    """Bounded execution context supplied to one effectful Provider operation."""

    operation_id: str
    deadline: datetime

    def __post_init__(self) -> None:
        if not self.operation_id.strip() or len(self.operation_id) > 200:
            raise ValueError("operation_id must be a bounded non-empty value")
        if self.deadline.tzinfo is None or self.deadline.utcoffset() is None:
            raise ValueError("deadline must include a timezone")


@dataclass(frozen=True, slots=True)
class ConnectorProviderSecret:
    """One ephemeral credential value returned by or supplied to a Provider."""

    key: str
    value: SecretStr = field(repr=False)

    def __post_init__(self) -> None:
        if not self.key.strip() or len(self.key) > 200 or not self.value.get_secret_value():
            raise ValueError("Provider Secret must have a bounded key and non-empty value")


@dataclass(frozen=True, slots=True)
class ConnectorProviderConnection:
    """Private non-secret state and ephemeral credentials for one Connection."""

    provider_state_version: str
    provider_state: JsonObject
    secrets: tuple[ConnectorProviderSecret, ...] = ()


@dataclass(frozen=True, slots=True)
class ConnectorProviderAccount:
    """Safe external-account identity returned after setup or refresh."""

    external_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class ConnectorProviderConnectionResult:
    """Complete Provider result used to atomically create or refresh a Connection."""

    provider_state_version: str
    provider_state: JsonObject
    account: ConnectorProviderAccount
    secrets: tuple[ConnectorProviderSecret, ...]
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_provider_state(self.provider_state_version, self.provider_state)
        if not 1 <= len(self.account.external_id) <= 500 or not 1 <= len(self.account.display_name) <= 500:
            raise ValueError("Provider account identity must be bounded")
        _validate_secrets(self.secrets)
        if self.expires_at is not None and (self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None):
            raise ValueError("Connection expiry must include a timezone")


@dataclass(frozen=True, slots=True)
class ConnectorProviderSetupResult:
    """One setup step; a complete step contains a Connection result."""

    completed: bool
    next_action: JsonObject | None = None
    continuation_state: JsonObject | None = None
    connection: ConnectorProviderConnectionResult | None = None

    def __post_init__(self) -> None:
        if self.completed != (self.connection is not None):
            raise ValueError("a completed setup result must contain exactly one connection result")


@dataclass(frozen=True, slots=True)
class ConnectorProviderTool:
    """Complete managed tool definition returned during Agent authoring."""

    name: str
    tool_id: str
    description: str
    parameters_json_schema: JsonObject
    effects: tuple[str, ...] = ()
    credential_audiences: tuple[str, ...] = ()
    idempotency: str = "none"
    output_policy: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConnectorProviderToolResult:
    """Bounded native Provider result before Harness result-policy processing."""

    value: JsonValue


@dataclass(frozen=True, slots=True)
class ConnectorProviderEventType:
    """One event definition exposed during Trigger authoring."""

    name: str
    description: str
    config_schema: JsonObject


@dataclass(frozen=True, slots=True)
class ConnectorProviderEventSourceResult:
    """Safe state and credentials for an activated external event source."""

    provider_state_version: str
    provider_state: JsonObject
    secrets: tuple[ConnectorProviderSecret, ...] = ()

    def __post_init__(self) -> None:
        _validate_provider_state(self.provider_state_version, self.provider_state)
        _validate_secrets(self.secrets)


@dataclass(frozen=True, slots=True)
class ConnectorProviderEvent:
    """Normalized untrusted event accepted from webhook or polling delivery."""

    event_id: str
    event_type: str
    data: JsonObject
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        if not 1 <= len(self.event_id) <= 500 or not 1 <= len(self.event_type) <= 200:
            raise ValueError("Provider event identity must be bounded")
        if self.occurred_at is not None and (self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None):
            raise ValueError("Provider event time must include a timezone")


def _validate_provider_state(version: str, state: JsonObject) -> None:
    if not version.strip() or len(version) > 200 or not isinstance(state, Mapping):
        raise ValueError("Provider state must have a bounded version and object value")


def _validate_secrets(secrets: tuple[ConnectorProviderSecret, ...]) -> None:
    if len(secrets) > 32 or len({secret.key for secret in secrets}) != len(secrets):
        raise ValueError("Provider Secrets must have unique bounded keys")


class ConnectorProvider(ABC):
    """Trusted deployment code behind one Connector Provider entry point.

    The entry-point name is the Provider key. Implementations deliberately do
    not repeat it in this object.
    """

    @property
    @abstractmethod
    def metadata(self) -> ConnectorProviderMetadata:
        """Return deterministic non-secret metadata without external I/O."""

    @abstractmethod
    def validate_config(self, provider_config_version: str, config: JsonObject) -> None:
        """Validate one exact Connector configuration locally."""

    def supports_dependency_lock(
        self,
        *,
        distribution_name: str,
        distribution_version: str,
        class_module: str,
        class_qualname: str,
    ) -> bool:
        """Explicitly accept reconstruction from one older Provider artifact lock."""

        del distribution_name, distribution_version, class_module, class_qualname
        return False

    async def list_tools(
        self,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: JsonObject,
        connection: ConnectorProviderConnection | None,
    ) -> tuple[ConnectorProviderTool, ...]:
        del context, provider_config_version, config, connection
        raise ConnectorProviderCapabilityError("tools")

    async def call_tool(
        self,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: JsonObject,
        connection: ConnectorProviderConnection | None,
        tool_name: str,
        arguments: JsonObject,
    ) -> ConnectorProviderToolResult:
        del context, provider_config_version, config, connection, tool_name, arguments
        raise ConnectorProviderCapabilityError("tools")

    def connection_spec(self, provider_config_version: str, config: JsonObject) -> JsonObject:
        del provider_config_version, config
        raise ConnectorProviderCapabilityError("connections")

    async def start_connection(
        self,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: JsonObject,
        setup_mode: str,
        input: JsonObject,
        callback_url: str | None,
        callback_state: str | None,
    ) -> ConnectorProviderSetupResult:
        del context, provider_config_version, config, setup_mode, input, callback_url, callback_state
        raise ConnectorProviderCapabilityError("connections")

    async def complete_connection(
        self,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: JsonObject,
        continuation_state: JsonObject,
        input: JsonObject,
    ) -> ConnectorProviderConnectionResult:
        del context, provider_config_version, config, continuation_state, input
        raise ConnectorProviderCapabilityError("connections")

    async def refresh_connection(
        self,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: JsonObject,
        connection: ConnectorProviderConnection,
    ) -> ConnectorProviderConnectionResult:
        del context, provider_config_version, config, connection
        raise ConnectorProviderCapabilityError("connections")

    async def revoke_connection(
        self,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: JsonObject,
        connection: ConnectorProviderConnection,
    ) -> None:
        del context, provider_config_version, config, connection
        raise ConnectorProviderCapabilityError("connections")

    def validate_connection(
        self,
        *,
        provider_config_version: str,
        config: JsonObject,
        connection: ConnectorProviderConnection,
    ) -> None:
        del provider_config_version, config, connection
        raise ConnectorProviderCapabilityError("connections")

    async def list_events(
        self,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: JsonObject,
        connection: ConnectorProviderConnection,
    ) -> tuple[ConnectorProviderEventType, ...]:
        del context, provider_config_version, config, connection
        raise ConnectorProviderCapabilityError("events")

    def validate_event_config(
        self,
        *,
        provider_config_version: str,
        config: JsonObject,
        connection: ConnectorProviderConnection,
        event_type: str,
        provider_event_config_version: str,
        event_config: JsonObject,
    ) -> None:
        del (
            provider_config_version,
            config,
            connection,
            event_type,
            provider_event_config_version,
            event_config,
        )
        raise ConnectorProviderCapabilityError("events")

    async def start_event_source(
        self,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: JsonObject,
        connection: ConnectorProviderConnection,
        event_type: str,
        provider_event_config_version: str,
        event_config: JsonObject,
        callback_url: str | None,
    ) -> ConnectorProviderEventSourceResult:
        del (
            context,
            provider_config_version,
            config,
            connection,
            event_type,
            provider_event_config_version,
            event_config,
            callback_url,
        )
        raise ConnectorProviderCapabilityError("events")

    async def stop_event_source(
        self,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
    ) -> None:
        del context, source
        raise ConnectorProviderCapabilityError("events")

    async def renew_event_source(
        self,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
    ) -> ConnectorProviderEventSourceResult:
        del context, source
        raise ConnectorProviderCapabilityError("events")

    async def reconcile_event_source(
        self,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: JsonObject,
        connection: ConnectorProviderConnection,
        event_type: str,
        provider_event_config_version: str,
        event_config: JsonObject,
        source: ConnectorProviderEventSourceResult | None,
    ) -> ConnectorProviderEventSourceResult:
        del (
            context,
            provider_config_version,
            config,
            connection,
            event_type,
            provider_event_config_version,
            event_config,
            source,
        )
        raise ConnectorProviderCapabilityError("events")

    async def receive_webhook(
        self,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[ConnectorProviderEvent, ...]:
        del context, source, headers, body
        raise ConnectorProviderCapabilityError("events")

    async def poll_events(
        self,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
        cursor: str | None,
    ) -> tuple[tuple[ConnectorProviderEvent, ...], str | None]:
        del context, source, cursor
        raise ConnectorProviderCapabilityError("events")
