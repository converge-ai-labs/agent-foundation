from __future__ import annotations

import pytest
from a13n_service.agents.domain import AgentConfig, CreateAgentRequest, PluginRuntimeMode
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.resolution import AgentResolver
from a13n_service.agents.service import AgentService
from a13n_service.connectivity.outbound_policy import EndpointPolicy
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.models.domain import ModelCapabilities, WorkspaceSecretCredential
from a13n_service.models.models import ModelRecord, ModelRevisionRecord
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import transaction
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, ORG_ID, WORKSPACE_ID, actor
from .selection_helpers import CONNECTOR_CONNECTION_ID, MCP_CONNECTION_ID, seed_selection_sources

MODEL_ID = "mdl_selection12345678"
MODEL_REVISION_ID = "mdlr_selection1234567"
MODEL_SECRET_ID = "sec_modelselection123"
pytestmark = pytest.mark.anyio


async def test_agent_revision_and_invocation_use_connectivity_resolver(
    connectivity_sessions: async_sessionmaker[AsyncSession],
    connectivity_objects: LocalObjectStore,
) -> None:
    await seed_selection_sources(connectivity_sessions, connectivity_objects)
    await _seed_model(connectivity_sessions)
    connectivity = ConnectivitySelectionResolver(connectivity_sessions, connectivity_objects)
    models = AcceptedModelSelector(
        connectivity_sessions,
        built_in_provider_registry(),
        EndpointPolicy.from_operator_allowlist(private_domains=(), private_cidrs=()),
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
    service = AgentService(
        connectivity_sessions,
        revision_resolver,
        invocation_resolver,
        clock=lambda: NOW,
    )
    config = AgentConfig.model_validate(
        {
            "model": {"model_revision_id": MODEL_REVISION_ID},
            "input_adapter": {"adapter_key": "native"},
            "connector_tools": {
                "orders": {"connector_connection_id": CONNECTOR_CONNECTION_ID, "tools": ["find_order"]}
            },
            "mcp_tools": {"docs": {"mcp_connection_id": MCP_CONNECTION_ID, "exposure": "catalog"}},
            "protocol": {"public_name": "Selection test"},
        }
    )

    created = await service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-selection-agent",
        request=CreateAgentRequest(name="Selection agent", config=config),
    )
    retained = await invocation_resolver.prepare_retained_revision_graph(
        actor=actor(),
        agent_id=created.agent.id,
    )
    async with transaction(connectivity_sessions) as session:
        await invocation_resolver.freeze_retained_revision_graph(session, prepared=retained)

    prepared = await invocation_resolver.prepare(
        actor=actor(),
        agent_id=created.agent.id,
        run_id="run_1234567890abcdef",
    )
    async with transaction(connectivity_sessions) as session:
        frozen = await invocation_resolver.freeze_in_transaction(session, prepared=prepared)

    assert created.revision.connector_tools[0].connector_connection_id == CONNECTOR_CONNECTION_ID
    assert frozen.connector_connection_selections[0].allowed_tool_keys == ("find_order",)
    assert frozen.mcp_connection_selections[0].allowed_tool_keys == ("search_docs",)
    assert frozen.mcp_tool_snapshot is not None


async def _seed_model(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with transaction(sessions) as session:
        session.add(
            SecretRecord(
                id=MODEL_SECRET_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="workspace",
                owner_id=WORKSPACE_ID,
                key="selection_model_key",
                version=1,
                ciphertext=b"encrypted",
                nonce=b"123456789012",
                encryption_key_id="test-key",
                created_at=NOW,
                value_updated_at=NOW,
                deleted_at=None,
            )
        )
        session.add(
            ModelRecord(
                id=MODEL_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                version=1,
                name="Selection Model",
                normalized_name="selection model",
                description=None,
                current_revision_id=MODEL_REVISION_ID,
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
            ModelRevisionRecord(
                id=MODEL_REVISION_ID,
                model_id=MODEL_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                version=1,
                provider_type="openai",
                model_name="gpt-5.6-terra",
                base_url=None,
                credential=WorkspaceSecretCredential(secret_id=MODEL_SECRET_ID).model_dump(mode="json"),
                provider_config={},
                capabilities=ModelCapabilities(
                    input_modalities=("text",),
                    tool_calling=True,
                    structured_output=True,
                ).model_dump(mode="json"),
                capability_source="catalog",
                content_digest="0" * 64,
                created_by_type="user",
                created_by_id=actor().principal.principal_id,
                created_at=NOW,
            )
        )
