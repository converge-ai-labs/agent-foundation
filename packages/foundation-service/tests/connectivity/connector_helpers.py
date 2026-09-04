from __future__ import annotations

from typing import Literal

from a13n_service.connectivity.connectors.contracts import (
    AdapterConnectionStatus,
    AdapterStatusReason,
    ConnectionBinding,
    ConnectionInspection,
    ConnectorProviderError,
    ConnectorTool,
    ConnectorToolOutcome,
    ConnectorToolPage,
    DiscoveredConnector,
    SetupContext,
    SetupStarted,
    StrictModel,
)
from a13n_service.connectivity.connectors.registry import ConnectorProviderImplementation, ConnectorProviderRegistry
from a13n_service.connectivity.domain import JsonObject


class FakeConfiguration(StrictModel):
    endpoint: Literal["https://connector.example"]
    tenant: Literal["tenant-1"]


class FakeCredentials(StrictModel):
    api_key: Literal["secret"]


class FakeSetup(StrictModel):
    scopes: tuple[Literal["read"], ...]


def validate_fake_setup(configuration: JsonObject, connector_key: str, value: object) -> JsonObject:
    FakeConfiguration.model_validate(configuration)
    if connector_key != "github":
        raise ValueError("invalid connector")
    return FakeSetup.model_validate(value).model_dump(mode="json")


class FakeConnectorBackend:
    def __init__(self) -> None:
        self.started = 0
        self.revoked: list[tuple[str, str]] = []
        self.inspection_status = AdapterConnectionStatus.ready
        self.fail_revoke = False
        self.supports_callback = True

    async def discover_tools(self, **kwargs) -> ConnectorToolPage:
        return ConnectorToolPage(
            items=(
                ConnectorTool(
                    provider_version="fake-1",
                    key="issues.create",
                    description="Create an issue",
                    input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
                    output_schema={"type": "object"},
                ),
            ),
            provider_version="fake-1",
        )


class FakeConnectorProvider:
    compatibility_profile = "fake_v1"

    def __init__(self, backend: FakeConnectorBackend) -> None:
        self.backend = backend

    async def aclose(self) -> None:
        pass

    def connect(self, binding: ConnectionBinding) -> FakeConnection:
        return FakeConnection(self.backend, binding)

    async def test(self) -> None:
        pass

    async def discover_connectors(self) -> tuple[DiscoveredConnector, ...]:
        return (
            DiscoveredConnector(
                key="github", name="GitHub", setup_schema={"type": "object"}, authentication_methods=("oauth2",)
            ),
        )

    async def start_setup(self, *, setup: JsonObject, context: SetupContext) -> SetupStarted:
        self.backend.started += 1
        return SetupStarted(
            external_ref="external-1",
            redirect_url="https://connector.example/authorize",
            external_handle=f"session://{context.attempt_id}",
            supports_verified_callback=self.backend.supports_callback,
        )

    async def complete_setup(
        self, *, session_uri: str, context: SetupContext, expected_external_ref: str
    ) -> ConnectionInspection:
        return ConnectionInspection(
            external_ref=expected_external_ref,
            connector_key=context.connector_key,
            external_user_correlation=context.external_user_correlation,
            status=AdapterConnectionStatus.ready,
            safe_metadata={"account": "safe"},
            provider_version="fake-1",
        )


class FakeConnection:
    def __init__(self, backend: FakeConnectorBackend, binding: ConnectionBinding) -> None:
        self.backend = backend
        self.binding = binding

    async def aclose(self) -> None:
        pass

    async def inspect(self) -> ConnectionInspection:
        reason = (
            AdapterStatusReason.reauthorization_required
            if self.backend.inspection_status is AdapterConnectionStatus.action_required
            else None
        )
        return ConnectionInspection(
            external_ref=self.binding.external_ref,
            connector_key=self.binding.connector_key,
            external_user_correlation=self.binding.external_user_correlation,
            status=self.backend.inspection_status,
            status_reason=reason,
            safe_metadata={"account": "safe"},
            provider_version="fake-1",
        )

    async def revoke(self, *, operation_id: str) -> None:
        self.backend.revoked.append((self.binding.external_ref, operation_id))
        if self.backend.fail_revoke:
            raise ConnectorProviderError("timeout", retryable=True, outcome_unknown=True)

    async def discover_tools(self, *, cursor: str | None) -> ConnectorToolPage:
        return await self.backend.discover_tools(cursor=cursor)

    async def execute_tool(
        self, *, tool_key: str, provider_version: str, arguments: JsonObject, request_id: str
    ) -> ConnectorToolOutcome:
        raise NotImplementedError


def fake_registry(backend: FakeConnectorBackend) -> ConnectorProviderRegistry:
    return ConnectorProviderRegistry(
        (
            ConnectorProviderImplementation(
                type="fake_connector",
                display_name="Fake",
                configuration_model=FakeConfiguration,
                credential_model=FakeCredentials,
                setup_validator=validate_fake_setup,
                factory=lambda configuration, credentials: FakeConnectorProvider(backend),
            ),
        )
    )
