"""Connector Service operations used by the Control and event boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from jsonschema import Draft202012Validator
from pydantic import JsonValue

from .errors import ConnectorError
from .provider import (
    ConnectorConnectionProvider,
    ConnectorEventProvider,
    ConnectorPollingProvider,
    ConnectorProviderConnection,
    ConnectorProviderConnectionResult,
    ConnectorProviderContext,
    ConnectorProviderEvent,
    ConnectorProviderEventSourceResult,
    ConnectorProviderEventType,
    ConnectorProviderMetadata,
    ConnectorProviderSetupResult,
    ConnectorProviderTool,
    ConnectorToolProvider,
    ConnectorWebhookProvider,
    invoke_provider,
)
from .registry import ConnectorProviderCatalog, ConnectorProviderRegistration


class ConnectorProviderOperations(Protocol):
    """Authenticated Connector Service capability used outside Provider processes."""

    async def registrations(self) -> tuple[ConnectorProviderRegistration, ...]: ...

    async def metadata(self, provider_key: str) -> ConnectorProviderMetadata: ...

    async def validate_config(
        self,
        provider_key: str,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
    ) -> None: ...

    async def connection_spec(
        self,
        provider_key: str,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
    ) -> Mapping[str, JsonValue]: ...

    async def list_tools(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection | None,
    ) -> tuple[ConnectorProviderTool, ...]: ...

    async def list_events(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
    ) -> tuple[ConnectorProviderEventType, ...]: ...

    async def start_connection(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        setup_mode: str,
        input: Mapping[str, JsonValue],
        callback_url: str | None,
        callback_state: str | None,
    ) -> ConnectorProviderSetupResult: ...

    async def complete_connection(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        continuation_state: Mapping[str, JsonValue],
        input: Mapping[str, JsonValue],
    ) -> ConnectorProviderConnectionResult: ...

    async def refresh_connection(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
    ) -> ConnectorProviderConnectionResult: ...

    async def revoke_connection(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
    ) -> None: ...

    async def validate_connection(
        self,
        provider_key: str,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
    ) -> None: ...

    async def validate_event_config(
        self,
        provider_key: str,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
        event_type: str,
        provider_event_config_version: str,
        event_config: Mapping[str, JsonValue],
    ) -> None: ...

    async def start_event_source(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
        event_type: str,
        provider_event_config_version: str,
        event_config: Mapping[str, JsonValue],
        callback_url: str | None,
    ) -> ConnectorProviderEventSourceResult: ...

    async def stop_event_source(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
    ) -> None: ...

    async def renew_event_source(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
    ) -> ConnectorProviderEventSourceResult: ...

    async def reconcile_event_source(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
        event_type: str,
        provider_event_config_version: str,
        event_config: Mapping[str, JsonValue],
        source: ConnectorProviderEventSourceResult | None,
    ) -> ConnectorProviderEventSourceResult: ...

    async def receive_webhook(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[ConnectorProviderEvent, ...]: ...

    async def poll_events(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
        cursor: str | None,
    ) -> tuple[tuple[ConnectorProviderEvent, ...], str | None]: ...


class LocalConnectorProviderOperations:
    """Connector-role adapter that is the only layer importing Provider code."""

    def __init__(self, providers: ConnectorProviderCatalog) -> None:
        self._providers = providers

    async def registrations(self) -> tuple[ConnectorProviderRegistration, ...]:
        return self._providers.registrations

    async def metadata(self, provider_key: str) -> ConnectorProviderMetadata:
        return self._providers.registration(provider_key).metadata

    async def validate_config(
        self,
        provider_key: str,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
    ) -> None:
        provider = self._providers.require(provider_key)
        try:
            schema = provider.metadata.provider_config_schemas[provider_config_version]
            Draft202012Validator(schema).validate(config)
            provider.validate_config(provider_config_version, config)
        except ConnectorError:
            raise
        except Exception:
            raise ConnectorError("Connector configuration is invalid.", code="provider_config_incompatible") from None

    async def connection_spec(
        self,
        provider_key: str,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
    ) -> Mapping[str, JsonValue]:
        return self._connection_provider(provider_key).connection_spec(provider_config_version, config)

    async def list_tools(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection | None,
    ) -> tuple[ConnectorProviderTool, ...]:
        provider = self._providers.require(provider_key)
        if not isinstance(provider, ConnectorToolProvider):
            raise ConnectorError("Connector Provider exposes no tools.", code="tool_not_found")
        return await invoke_provider(
            context,
            provider.list_tools(
                context,
                provider_config_version=provider_config_version,
                config=config,
                connection=connection,
            ),
        )

    async def list_events(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
    ) -> tuple[ConnectorProviderEventType, ...]:
        provider = self._event_provider(provider_key)
        return await invoke_provider(
            context,
            provider.list_events(
                context,
                provider_config_version=provider_config_version,
                config=config,
                connection=connection,
            ),
        )

    async def start_connection(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        setup_mode: str,
        input: Mapping[str, JsonValue],
        callback_url: str | None,
        callback_state: str | None,
    ) -> ConnectorProviderSetupResult:
        provider = self._connection_provider(provider_key)
        return await invoke_provider(
            context,
            provider.start_connection(
                context,
                provider_config_version=provider_config_version,
                config=config,
                setup_mode=setup_mode,
                input=input,
                callback_url=callback_url,
                callback_state=callback_state,
            ),
        )

    async def complete_connection(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        continuation_state: Mapping[str, JsonValue],
        input: Mapping[str, JsonValue],
    ) -> ConnectorProviderConnectionResult:
        provider = self._connection_provider(provider_key)
        return await invoke_provider(
            context,
            provider.complete_connection(
                context,
                provider_config_version=provider_config_version,
                config=config,
                continuation_state=continuation_state,
                input=input,
            ),
        )

    async def refresh_connection(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
    ) -> ConnectorProviderConnectionResult:
        provider = self._connection_provider(provider_key)
        return await invoke_provider(
            context,
            provider.refresh_connection(
                context,
                provider_config_version=provider_config_version,
                config=config,
                connection=connection,
            ),
        )

    async def revoke_connection(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
    ) -> None:
        provider = self._connection_provider(provider_key)
        await invoke_provider(
            context,
            provider.revoke_connection(
                context,
                provider_config_version=provider_config_version,
                config=config,
                connection=connection,
            ),
        )

    async def validate_connection(
        self,
        provider_key: str,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
    ) -> None:
        try:
            self._connection_provider(provider_key).validate_connection(
                provider_config_version=provider_config_version,
                config=config,
                connection=connection,
            )
        except ConnectorError:
            raise
        except Exception:
            raise ConnectorError(
                "Connector Provider rejected the Connection state.",
                code="connection_incompatible",
            ) from None

    async def validate_event_config(
        self,
        provider_key: str,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
        event_type: str,
        provider_event_config_version: str,
        event_config: Mapping[str, JsonValue],
    ) -> None:
        try:
            self._event_provider(provider_key).validate_event_config(
                provider_config_version=provider_config_version,
                config=config,
                connection=connection,
                event_type=event_type,
                provider_event_config_version=provider_event_config_version,
                event_config=event_config,
            )
        except ConnectorError:
            raise
        except Exception:
            raise ConnectorError(
                "Connector Provider rejected the event configuration.",
                code="trigger_source_incompatible",
            ) from None

    async def start_event_source(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
        event_type: str,
        provider_event_config_version: str,
        event_config: Mapping[str, JsonValue],
        callback_url: str | None,
    ) -> ConnectorProviderEventSourceResult:
        provider = self._event_provider(provider_key)
        return await invoke_provider(
            context,
            provider.start_event_source(
                context,
                provider_config_version=provider_config_version,
                config=config,
                connection=connection,
                event_type=event_type,
                provider_event_config_version=provider_event_config_version,
                event_config=event_config,
                callback_url=callback_url,
            ),
        )

    async def stop_event_source(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
    ) -> None:
        provider = self._event_provider(provider_key)
        await invoke_provider(context, provider.stop_event_source(context, source=source))

    async def renew_event_source(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
    ) -> ConnectorProviderEventSourceResult:
        provider = self._event_provider(provider_key)
        return await invoke_provider(context, provider.renew_event_source(context, source=source))

    async def reconcile_event_source(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        provider_config_version: str,
        config: Mapping[str, JsonValue],
        connection: ConnectorProviderConnection,
        event_type: str,
        provider_event_config_version: str,
        event_config: Mapping[str, JsonValue],
        source: ConnectorProviderEventSourceResult | None,
    ) -> ConnectorProviderEventSourceResult:
        provider = self._event_provider(provider_key)
        return await invoke_provider(
            context,
            provider.reconcile_event_source(
                context,
                provider_config_version=provider_config_version,
                config=config,
                connection=connection,
                event_type=event_type,
                provider_event_config_version=provider_event_config_version,
                event_config=event_config,
                source=source,
            ),
        )

    async def receive_webhook(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[ConnectorProviderEvent, ...]:
        provider = self._providers.require(provider_key)
        if not isinstance(provider, ConnectorWebhookProvider):
            raise ConnectorError("Connector Provider exposes no webhook events.", code="trigger_source_incompatible")
        return await invoke_provider(
            context,
            provider.receive_webhook(context, source=source, headers=headers, body=body),
        )

    async def poll_events(
        self,
        provider_key: str,
        context: ConnectorProviderContext,
        *,
        source: ConnectorProviderEventSourceResult,
        cursor: str | None,
    ) -> tuple[tuple[ConnectorProviderEvent, ...], str | None]:
        provider = self._providers.require(provider_key)
        if not isinstance(provider, ConnectorPollingProvider):
            raise ConnectorError("Connector Provider exposes no polling events.", code="trigger_source_incompatible")
        return await invoke_provider(context, provider.poll_events(context, source=source, cursor=cursor))

    def _connection_provider(self, provider_key: str) -> ConnectorConnectionProvider:
        provider = self._providers.require(provider_key)
        if not isinstance(provider, ConnectorConnectionProvider):
            raise ConnectorError("Connector Provider exposes no Connections.", code="connection_incompatible")
        return provider

    def _event_provider(self, provider_key: str) -> ConnectorEventProvider:
        provider = self._providers.require(provider_key)
        if not isinstance(provider, ConnectorEventProvider):
            raise ConnectorError("Connector Provider exposes no events.", code="trigger_source_incompatible")
        return provider


def provider_operations(
    value: ConnectorProviderOperations | ConnectorProviderCatalog,
) -> ConnectorProviderOperations:
    if isinstance(value, ConnectorProviderCatalog):
        return LocalConnectorProviderOperations(value)
    return value


__all__ = [
    "ConnectorProviderOperations",
    "LocalConnectorProviderOperations",
    "provider_operations",
]
