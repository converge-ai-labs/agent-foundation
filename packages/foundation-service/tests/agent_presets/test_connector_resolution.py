from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from a13n_service.agent_presets.connector_resolution import AgentConnectorSelectionResolver
from a13n_service.agent_presets.domain import (
    AgentPresetCommandRequest,
    AgentRunOverride,
    CreateAgentPresetRequest,
    PluginRuntimeMode,
)
from a13n_service.agent_presets.errors import AgentPresetError
from a13n_service.agent_presets.invocation_resolution import AgentPresetInvocationResolver
from a13n_service.agent_presets.references import AgentPresetConnectorReferenceChecker
from a13n_service.agent_presets.resolution import AgentPresetResolver
from a13n_service.agent_presets.service import AgentPresetService
from a13n_service.connectors import (
    ConnectorProvider,
    ConnectorProviderCapabilities,
    ConnectorProviderCatalog,
    ConnectorProviderMetadata,
    ConnectorProviderRegistration,
    ConnectorProviderTool,
    LocalConnectorProviderOperations,
)
from a13n_service.connectors.models import ConnectionRecord, ConnectorRecord, ConnectorRevisionRecord
from a13n_service.model_configs.endpoint_policy import EndpointPolicy
from a13n_service.model_configs.providers import built_in_provider_registry
from a13n_service.model_configs.runtime import AcceptedModelSelector
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, ORG_ID, USER_ID, WORKSPACE_ID, actor, preset_config

CONNECTOR_ID = "con_1234567890abcdef"
CONNECTOR_REVISION_ID = "conrev_1234567890abcdef"
CONNECTION_ID = "conn_1234567890abcdef"


class _Provider(ConnectorProvider):
    def __init__(self) -> None:
        self.list_calls = 0
        self.connected_list_calls = 0
        self.search_description = "Search issues"

    @property
    def metadata(self) -> ConnectorProviderMetadata:
        return ConnectorProviderMetadata(
            display_name="Test",
            description="Test Connector Provider",
            contract_version="1",
            provider_config_schemas={"1": {"type": "object"}},
            capabilities=ConnectorProviderCapabilities(tools=True, connections=True),
        )

    def validate_config(self, provider_config_version: str, config) -> None:
        if provider_config_version != "1" or config:
            raise ValueError("invalid test config")

    async def list_tools(self, context, **kwargs):
        del context
        self.list_calls += 1
        if kwargs["connection"] is not None:
            self.connected_list_calls += 1
        return (
            ConnectorProviderTool(
                name="search",
                tool_id="test.search",
                description=self.search_description,
                parameters_json_schema={"type": "object"},
                effects=("read",),
                idempotency="read_only",
                output_policy={
                    "max_inline_bytes": 1024,
                    "max_output_bytes": 4096,
                    "overflow": "spill",
                    "redact": True,
                },
            ),
            ConnectorProviderTool(
                name="private",
                tool_id="test.private",
                description="Read private issues",
                parameters_json_schema={"type": "object"},
                effects=("read",),
                credential_audiences=("test_api",),
                idempotency="read_only",
                output_policy={
                    "max_inline_bytes": 1024,
                    "max_output_bytes": 4096,
                    "overflow": "spill",
                    "redact": True,
                },
            ),
            ConnectorProviderTool(
                name="create",
                tool_id="test.create",
                description="Create issue",
                parameters_json_schema={"type": "object"},
                effects=("write",),
                output_policy={
                    "max_inline_bytes": 1024,
                    "max_output_bytes": 4096,
                    "overflow": "spill",
                    "redact": True,
                },
            ),
        )

    async def call_tool(self, context, **kwargs):
        raise NotImplementedError


class _Secrets:
    async def replace_connection_secrets(self, session, **kwargs):
        raise NotImplementedError

    async def read_connection_secrets(self, **kwargs):
        return ()

    async def delete_connection_secrets(self, session, **kwargs):
        raise NotImplementedError


@pytest.fixture
async def connector_agent_services(
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[AgentPresetService, AgentPresetInvocationResolver, _Provider]]:
    async with transaction(agent_preset_sessions) as session:
        session.add(
            ConnectorRecord(
                id=CONNECTOR_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                name="Issues",
                description=None,
                enabled=True,
                version=1,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    async with transaction(agent_preset_sessions) as session:
        session.add_all(
            (
                ConnectorRevisionRecord(
                    id=CONNECTOR_REVISION_ID,
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    connector_id=CONNECTOR_ID,
                    version=1,
                    provider_key="test",
                    provider_config_version="1",
                    config={},
                    created_by_type="user",
                    created_by_id=USER_ID,
                    created_at=NOW,
                ),
                ConnectionRecord(
                    id=CONNECTION_ID,
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    connector_id=CONNECTOR_ID,
                    principal_type=None,
                    principal_id=None,
                    name="Shared Test",
                    provider_key="test",
                    provider_state_version="1",
                    provider_state={},
                    account_external_id="shared",
                    account_display_name="Shared",
                    status="active",
                    expires_at=NOW + timedelta(days=1),
                    lifecycle_operation_id=None,
                    cleanup_pending=False,
                    version=1,
                    created_by_type="user",
                    created_by_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )
    provider = _Provider()
    catalog = ConnectorProviderCatalog(
        (
            (
                ConnectorProviderRegistration(
                    provider_key="test",
                    class_module=__name__,
                    class_qualname="_Provider",
                    import_target=f"{__name__}:_Provider",
                    distribution_name="a13n-connector-test",
                    distribution_version="1.0.0",
                    metadata=provider.metadata,
                ),
                provider,
            ),
        )
    )
    connector_resolver = AgentConnectorSelectionResolver(
        agent_preset_sessions,
        LocalConnectorProviderOperations(catalog),
        _Secrets(),
        clock=lambda: NOW,
    )
    model_selector = AcceptedModelSelector(
        agent_preset_sessions,
        built_in_provider_registry(),
        EndpointPolicy.from_operator_allowlist(private_domains=(), private_cidrs=()),
    )
    publication = AgentPresetResolver(
        agent_preset_sessions,
        model_selector,
        plugin_runtime_mode=PluginRuntimeMode.on_demand,
        connector_resolver=connector_resolver,
    )
    yield (
        AgentPresetService(agent_preset_sessions, publication, clock=lambda: NOW),
        AgentPresetInvocationResolver(
            agent_preset_sessions,
            model_selector,
            plugin_runtime_mode=PluginRuntimeMode.on_demand,
            connector_resolver=connector_resolver,
        ),
        provider,
    )


async def _publish_connector_preset(service: AgentPresetService):
    preset = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-connector-preset",
        request=CreateAgentPresetRequest(
            name="Connector Preset",
            config=preset_config(
                connectors={
                    "issues": {
                        "connector_revision_id": CONNECTOR_REVISION_ID,
                        "tools": ["search"],
                    }
                }
            ),
        ),
    )
    return await service.publish(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="publish-connector-preset",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )


@pytest.mark.anyio
async def test_publish_freezes_connector_tools_and_provider_contract(
    connector_agent_services: tuple[AgentPresetService, AgentPresetInvocationResolver, _Provider],
) -> None:
    service, _invocations, provider = connector_agent_services

    published = await _publish_connector_preset(service)

    resolved = published.revision.resolved_connectors[0]
    assert resolved.name == "issues"
    assert resolved.connector_revision_id == CONNECTOR_REVISION_ID
    assert tuple(tool.name for tool in resolved.tools) == ("search",)
    assert resolved.tools[0].description == "Search issues"
    assert resolved.provider_lock.provider_key == "test"
    assert resolved.provider_lock.contract_version == "1"
    assert provider.list_calls == 1


@pytest.mark.anyio
async def test_published_revision_protects_referenced_connector_from_deletion(
    connector_agent_services: tuple[AgentPresetService, AgentPresetInvocationResolver, _Provider],
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service, _invocations, _provider = connector_agent_services
    checker = AgentPresetConnectorReferenceChecker()
    async with transaction(agent_preset_sessions) as session:
        assert not await checker.has_agent_preset_reference(
            session,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_id=CONNECTOR_ID,
        )

    await _publish_connector_preset(service)

    async with transaction(agent_preset_sessions) as session:
        assert await checker.has_agent_preset_reference(
            session,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connector_id=CONNECTOR_ID,
        )


@pytest.mark.anyio
async def test_inherited_connector_reuses_frozen_contract_but_override_rediscovers(
    connector_agent_services: tuple[AgentPresetService, AgentPresetInvocationResolver, _Provider],
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service, invocations, provider = connector_agent_services
    published = await _publish_connector_preset(service)
    provider.search_description = "Changed current catalog"

    inherited = await invocations.prepare(actor=actor(), agent_preset_id=published.preset.id)
    async with transaction(agent_preset_sessions) as session:
        inherited_frozen = await invocations.freeze_in_transaction(session, prepared=inherited)

    assert inherited_frozen.effective_config.resolved_connectors[0].tools[0].description == "Search issues"
    assert provider.list_calls == 1

    changed = await invocations.prepare(
        actor=actor(),
        agent_preset_id=published.preset.id,
        config_override=AgentRunOverride.model_validate({"connectors": {"issues": {"tools": ["create"]}}}),
    )
    async with transaction(agent_preset_sessions) as session:
        changed_frozen = await invocations.freeze_in_transaction(session, prepared=changed)

    assert tuple(tool.name for tool in changed_frozen.effective_config.resolved_connectors[0].tools) == ("create",)
    assert provider.list_calls == 2


@pytest.mark.anyio
async def test_runtime_headers_fail_closed_without_provider_schema(
    connector_agent_services: tuple[AgentPresetService, AgentPresetInvocationResolver, _Provider],
) -> None:
    service, invocations, _provider = connector_agent_services
    published = await _publish_connector_preset(service)

    with pytest.raises(AgentPresetError) as rejected:
        await invocations.prepare(
            actor=actor(),
            agent_preset_id=published.preset.id,
            config_override=AgentRunOverride.model_validate(
                {"connectors": {"issues": {"headers": {"X-Tenant": "private"}}}}
            ),
        )

    assert rejected.value.code == "preset_revision_not_executable"
    assert rejected.value.details == {"reason": "connector_runtime_headers_unsupported"}


@pytest.mark.anyio
async def test_invocation_resolves_shared_connection_for_credentialed_override(
    connector_agent_services: tuple[AgentPresetService, AgentPresetInvocationResolver, _Provider],
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service, invocations, provider = connector_agent_services
    published = await _publish_connector_preset(service)

    prepared = await invocations.prepare(
        actor=actor(),
        agent_preset_id=published.preset.id,
        config_override=AgentRunOverride.model_validate({"connectors": {"issues": {"tools": ["private"]}}}),
    )
    async with transaction(agent_preset_sessions) as session:
        frozen = await invocations.freeze_in_transaction(session, prepared=prepared)

    connector = frozen.effective_config.resolved_connectors[0]
    assert connector.connection_id == CONNECTION_ID
    assert tuple(tool.name for tool in connector.tools) == ("private",)
    assert provider.connected_list_calls == 1


@pytest.mark.anyio
async def test_connector_is_rechecked_in_final_acceptance_transaction(
    connector_agent_services: tuple[AgentPresetService, AgentPresetInvocationResolver, _Provider],
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    service, invocations, _provider = connector_agent_services
    published = await _publish_connector_preset(service)
    prepared = await invocations.prepare(actor=actor(), agent_preset_id=published.preset.id)
    async with transaction(agent_preset_sessions) as session:
        connector = await session.get(ConnectorRecord, CONNECTOR_ID)
        assert connector is not None
        connector.enabled = False
        connector.version += 1

    with pytest.raises(AgentPresetError) as rejected:
        async with transaction(agent_preset_sessions) as session:
            await invocations.freeze_in_transaction(session, prepared=prepared)

    assert rejected.value.code == "preset_revision_not_executable"
    assert rejected.value.details == {"reason": "connector_changed"}
