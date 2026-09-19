import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.memory.builtins import MEM0_OSS
from a13n_service.agents.application import AgentManagement
from a13n_service.agents.domain import AgentConfig, AgentRunOverride, CreateAgentRequest
from a13n_service.agents.errors import AgentError
from a13n_service.agents.invocation import merge_agent_run_override
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.resolution import AgentResolver
from a13n_service.etags import resource_etag
from a13n_service.memory.domain import CreateMemoryProviderRequest, MemorySelection, UpdateMemoryProviderRequest
from a13n_service.memory.providers import MemoryProviderService
from a13n_service.memory.resources import MemoryProviderError
from a13n_service.memory.runtime import graph_uses_memory
from a13n_service.models.providers import built_in_model_provider_catalog
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.storage import transaction

from tests.models.conftest import protector

from .conftest import WORKSPACE_ID, actor, agent_config
from .test_reconstruction import _edge, _effective, _reconstruct, _rehash, _revision, _with_children

MEMORY_PROVIDER_ID = "memprov_1234567890abcdef"


@pytest.fixture
async def memory_agents(agent_sessions):
    catalog = ProviderCatalog((MEM0_OSS,))
    providers = MemoryProviderService(agent_sessions, protector(), catalog)
    provider = await providers.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateMemoryProviderRequest(
            type="mem0_oss",
            name="Memory",
            configuration={"base_url": "http://unused.invalid"},
            credential={"api_key": "test-key"},
        ),
    )
    models = AcceptedModelSelector(agent_sessions, built_in_model_provider_catalog())
    resolver = AgentResolver(agent_sessions, models, memory_provider_catalog=catalog)
    invocations = AgentInvocationResolver(agent_sessions, models, memory_provider_catalog=catalog)
    management = AgentManagement(agent_sessions, resolver, invocations, models)
    return management, invocations, providers, provider


def test_memory_override_is_whole_value_and_defaults_keep_auto_recall():
    selection = MemorySelection(provider_id=MEMORY_PROVIDER_ID, scope="agent", recall_limit=9, toolset=False)
    base = agent_config().model_copy(update={"memory": selection})
    assert merge_agent_run_override(base, AgentRunOverride()).memory == selection
    assert merge_agent_run_override(base, AgentRunOverride(memory=None)).memory is None
    replacement = merge_agent_run_override(
        base, AgentRunOverride(memory=MemorySelection(provider_id=MEMORY_PROVIDER_ID))
    ).memory
    assert replacement.auto_recall and replacement.toolset and replacement.recall_limit == 5
    assert "memory" not in AgentRunOverride().model_dump(exclude_unset=True)
    assert AgentRunOverride(memory=None).model_dump(exclude_unset=True) == {"memory": None}


@pytest.mark.anyio
async def test_memory_selection_survives_authoring_and_freezing(agent_sessions, memory_agents):
    agent_management, agent_invocation_resolver, _, provider = memory_agents
    selection = MemorySelection(provider_id=provider.id, scope="thread", recall_required=True)
    config_payload = agent_config().model_dump(mode="python", by_alias=True)
    config_payload["memory"] = selection
    config_payload["toolsets"]["web"]["enabled"] = True
    config_payload["toolsets"]["web"]["tools"]["fetch"]["enabled"] = True
    config = AgentConfig.model_validate(config_payload)
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="memory-agent",
        request=CreateAgentRequest(name="Memory", config=config),
    )
    prepared = await agent_invocation_resolver.preparation.prepare(actor=actor(), agent_id=created.agent.id)
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)
    assert created.revision.config.memory == frozen.effective_config.memory == selection
    assert frozen.effective_config.toolsets["web"] == config.toolsets["web"]
    _reconstruct(frozen.effective_config)


def test_reconstruction_retains_separate_root_and_child_selections():
    child = _revision()
    effective = _with_children(_effective(agent_config(), subagents=(_edge("reviewer"),)), {child.id: child})
    child_selection = MemorySelection(provider_id=MEMORY_PROVIDER_ID, scope="agent", recall_limit=3)
    child_execution = effective.child_configs[child.id]
    effective = _rehash(
        effective.model_copy(
            update={
                "child_configs": {
                    child.id: child_execution.model_copy(
                        update={
                            "effective_config": _rehash(
                                child_execution.effective_config.model_copy(update={"memory": child_selection})
                            )
                        }
                    )
                }
            }
        )
    )
    assert effective.memory is None and graph_uses_memory(effective)
    contexts = []

    def capabilities(context):
        contexts.append(context)
        return ()

    _reconstruct(effective, capability_provider=capabilities)
    assert [(context.is_root, context.config.memory) for context in contexts] == [
        (False, child_selection),
        (True, None),
    ]


@pytest.mark.anyio
async def test_provider_selection_is_frozen_but_eligibility_is_rechecked(agent_sessions, memory_agents):
    management, invocations, providers, provider = memory_agents
    config = agent_config().model_copy(update={"memory": MemorySelection(provider_id=provider.id)})
    created = await management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="frozen-memory",
        request=CreateAgentRequest(name="Frozen memory", config=config),
    )
    prepared = await invocations.preparation.prepare(actor=actor(), agent_id=created.agent.id)
    async with transaction(agent_sessions) as session:
        frozen = await invocations.freezing.freeze_in_transaction(session, prepared=prepared)
    updated = await providers.update(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        provider_id=provider.id,
        if_match=resource_etag(provider.id, provider.updated_at),
        request=UpdateMemoryProviderRequest(credential={"api_key": "rotated-secret"}, enabled=False),
    )
    assert frozen.effective_config.memory.provider_id == provider.id
    assert "rotated-secret" not in frozen.effective_config.model_dump_json()
    with pytest.raises(MemoryProviderError) as disabled:
        async with transaction(agent_sessions) as session:
            await invocations.freezing.freeze_in_transaction(session, prepared=prepared)
    assert disabled.value.code == "memory_provider_disabled"
    references = await providers.references(actor=actor(), workspace_id=WORKSPACE_ID, provider_id=updated.id)
    assert [item.agent_revision_id for item in references.items] == [created.revision.id]


@pytest.mark.anyio
async def test_unknown_provider_rejects_agent_authoring(memory_agents):
    management, _, _, _ = memory_agents
    config = agent_config().model_copy(update={"memory": MemorySelection(provider_id=MEMORY_PROVIDER_ID)})
    with pytest.raises(AgentError) as rejected:
        await management.commands.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="missing-memory",
            request=CreateAgentRequest(name="Missing memory", config=config),
        )
    assert rejected.value.code == "memory_provider_not_found"
