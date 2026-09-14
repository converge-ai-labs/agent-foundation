import pytest
from a13n_service.agents.domain import AgentRunOverride, CreateAgentRequest
from a13n_service.agents.invocation import merge_agent_run_override
from a13n_service.memory.domain import MemorySelection
from a13n_service.memory.runtime import graph_uses_memory
from a13n_service.storage import transaction

from .conftest import WORKSPACE_ID, actor, agent_config
from .test_reconstruction import _edge, _effective, _reconstruct, _rehash, _revision, _with_children


def test_memory_override_is_whole_value_and_defaults_keep_auto_recall():
    selection = MemorySelection(scope="agent", recall_limit=9, toolset=False)
    base = agent_config().model_copy(update={"memory": selection})
    assert merge_agent_run_override(base, AgentRunOverride()).memory == selection
    assert merge_agent_run_override(base, AgentRunOverride(memory=None)).memory is None
    replacement = merge_agent_run_override(base, AgentRunOverride(memory=MemorySelection())).memory
    assert replacement.auto_recall and replacement.toolset and replacement.recall_limit == 5
    assert "memory" not in AgentRunOverride().model_dump(exclude_unset=True)
    assert AgentRunOverride(memory=None).model_dump(exclude_unset=True) == {"memory": None}


@pytest.mark.anyio
async def test_memory_selection_survives_authoring_and_freezing(
    agent_sessions, agent_management, agent_invocation_resolver
):
    selection = MemorySelection(scope="thread", recall_required=True)
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="memory-agent",
        request=CreateAgentRequest(name="Memory", config=agent_config().model_copy(update={"memory": selection})),
    )
    prepared = await agent_invocation_resolver.preparation.prepare(actor=actor(), agent_id=created.agent.id)
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)
    assert created.revision.config.memory == frozen.effective_config.memory == selection


def test_reconstruction_retains_separate_root_and_child_selections():
    child = _revision()
    effective = _with_children(_effective(agent_config(), subagents=(_edge("reviewer"),)), {child.id: child})
    child_selection = MemorySelection(scope="agent", recall_limit=3)
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
