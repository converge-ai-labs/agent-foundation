from __future__ import annotations

import pytest
from a13n_service.agents.application import AgentManagement
from a13n_service.agents.domain import AgentConfig, CreateAgentRequest, PluginRuntimeMode
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.resolution import AgentResolver
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.models.domain import ModelApiConfig, ModelProfile
from a13n_service.models.models import ModelProviderRecord, ModelRecord
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.storage import transaction
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, ORG_ID, WORKSPACE_ID, actor
from .selection_helpers import CONNECTOR_CONNECTION_ID, MCP_CONNECTION_ID, seed_selection_sources

MODEL_ID = "mdl_selection12345678"
MODEL_KEY = "selection"
MODEL_PROVIDER_ID = "mprov_selection1234567"
pytestmark = pytest.mark.anyio


async def test_agent_revision_and_invocation_use_connectivity_resolver(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    await seed_selection_sources(connectivity_sessions)
    await _seed_model(connectivity_sessions)
    connectivity = ConnectivitySelectionResolver(connectivity_sessions)
    models = AcceptedModelSelector(
        connectivity_sessions,
        built_in_provider_registry(),
    )
    revision_resolver = AgentResolver(
        connectivity_sessions,
        models,
        plugin_runtime_mode=PluginRuntimeMode.on_demand,
        connectivity_resolver=connectivity,
    )
    invocation_resolver = AgentInvocationResolver(
        connectivity_sessions,
        models,
        plugin_runtime_mode=PluginRuntimeMode.on_demand,
        connectivity_resolver=connectivity,
    )
    service = AgentManagement(
        connectivity_sessions,
        revision_resolver,
        invocation_resolver,
        clock=lambda: NOW,
    )
    config = AgentConfig.model_validate(
        {
            "model": {
                "model_key": MODEL_KEY,
                "model_api": "openai.responses",
                "settings": {},
                "characteristics": {"context_window": 128000},
            },
            "input_adapter": {"adapter_key": "native"},
            "connector_tools": [{"connector_connection_id": CONNECTOR_CONNECTION_ID, "tools": ["find_order"]}],
            "mcp_tools": [{"mcp_connection_id": MCP_CONNECTION_ID, "defer_loading": True}],
            "protocol": {"public_name": "Selection test"},
        }
    )

    created = await service.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-selection-agent",
        request=CreateAgentRequest(name="Selection agent", config=config),
    )
    retained = await invocation_resolver.preparation.prepare_retained_revision_graph(
        actor=actor(),
        agent_id=created.agent.id,
    )
    async with transaction(connectivity_sessions) as session:
        await invocation_resolver.freezing.freeze_retained_revision_graph(session, prepared=retained)

    prepared = await invocation_resolver.preparation.prepare(
        actor=actor(),
        agent_id=created.agent.id,
        run_id="run_1234567890abcdef",
    )
    async with transaction(connectivity_sessions) as session:
        frozen = await invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)

    assert created.revision.connector_tools[0].connector_connection_id == CONNECTOR_CONNECTION_ID
    assert frozen.connector_connection_selections[0].tools == ("find_order",)
    assert frozen.mcp_connection_selections[0].tools is None


async def _seed_model(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(sessions) as session:
        session.add(
            ModelProviderRecord(
                id=MODEL_PROVIDER_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                type="openai",
                name="Selection Provider",
                normalized_name="selection provider",
                configuration={},
                credential_version=1,
                ciphertext=b"encrypted",
                nonce=b"123456789012",
                encryption_key_id="test-key",
                enabled=True,
                created_by_type="user",
                created_by_id=actor().principal.principal_id,
                updated_by_type="user",
                updated_by_id=actor().principal.principal_id,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            ModelRecord(
                id=MODEL_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                key=MODEL_KEY,
                normalized_key=MODEL_KEY,
                provider_id=MODEL_PROVIDER_ID,
                name="Selection Model",
                description=None,
                upstream_model="gpt-5.6-terra",
                model_apis=[
                    ModelApiConfig(
                        api="openai.responses",
                        profile=ModelProfile(input_modalities=("text",), supports_tools=True),
                    ).model_dump(mode="json")
                ],
                enabled=True,
                created_by_type="user",
                created_by_id=actor().principal.principal_id,
                updated_by_type="user",
                updated_by_id=actor().principal.principal_id,
                created_at=NOW,
                updated_at=NOW,
            )
        )
