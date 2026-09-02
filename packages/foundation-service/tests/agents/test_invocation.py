from __future__ import annotations

import pytest
from a13n_service.agents.domain import (
    AgentRunOverride,
    CreateAgentRequest,
    CreateAgentRevisionRequest,
)
from a13n_service.agents.errors import AgentError
from a13n_service.agents.invocation import merge_agent_run_override
from a13n_service.agents.invocation_resolution import (
    AgentInvocationResolver,
    AgentSelectorKind,
)
from a13n_service.agents.service import AgentService
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import WORKSPACE_ID, actor, agent_config


def test_absent_override_inherits_complete_agent_config() -> None:
    base = agent_config()

    merged = merge_agent_run_override(base, None)

    assert merged.config.model_dump(mode="json", by_alias=True) == base.model_dump(mode="json", by_alias=True)


def test_scalar_and_list_overrides_replace_and_clear() -> None:
    base = agent_config()
    override = AgentRunOverride.model_validate(
        {
            "model": {"settings": {}, "characteristics": {"context_window": 32000}},
            "instructions": "",
            "plugins": [],
            "skills": [],
            "client_tools": [],
            "output_spec": None,
            "environment": None,
            "retries": {"tools": 0},
        }
    )

    merged = merge_agent_run_override(base, override)

    assert merged.config.model.model_revision_id == base.model.model_revision_id
    assert merged.config.model.settings == {}
    assert merged.config.model.characteristics.context_window == 32000
    assert merged.config.instructions == ""
    assert merged.config.plugins == ()
    assert merged.config.skills == ()
    assert merged.config.client_tools == ()
    assert merged.config.output_spec is None
    assert merged.config.environment is None
    assert merged.config.retries is not None
    assert merged.config.retries.tools == 0
    assert merged.config.retries.output == 1


def test_empty_retry_patch_is_a_noop() -> None:
    base = agent_config().model_copy(update={"retries": None})

    merged = merge_agent_run_override(base, AgentRunOverride.model_validate({"retries": {}}))

    assert merged.config.retries is None


def test_duplicate_list_override_is_rejected() -> None:
    override = AgentRunOverride.model_validate(
        {
            "skills": [
                {"skill_revision_id": "skrev_1234567890abcdef"},
                {"skill_revision_id": "skrev_1234567890abcdef"},
            ]
        }
    )

    with pytest.raises(AgentError) as invalid:
        merge_agent_run_override(agent_config(), override)

    assert invalid.value.code == "validation_error"
    assert invalid.value.details == {"path": "skills", "reason": "duplicate_selection"}


def test_subagent_patch_is_name_keyed_and_supports_default_selection() -> None:
    base = agent_config(
        subagents={
            "researcher": {
                "agent_id": "ap_1234567890abcdef",
                "version": 3,
                "description": "Research",
                "environment": {"mode": "none"},
            },
            "legacy": {
                "agent_id": "ap_abcdef1234567890",
                "environment": {"mode": "none"},
            },
        }
    )
    override = AgentRunOverride.model_validate(
        {
            "subagents": {
                "researcher": {"version": None, "description": None},
                "legacy": None,
                "writer": {"agent_id": "ap_1111111111111111"},
            }
        }
    )

    merged = merge_agent_run_override(base, override)

    assert tuple(merged.config.subagents) == ("researcher", "writer")
    assert merged.config.subagents["researcher"].version is None
    assert merged.config.subagents["researcher"].description is None
    assert merged.config.subagents["writer"].context.history == "none"
    assert merged.config.subagents["writer"].environment.mode == "none"


@pytest.mark.parametrize(
    ("payload", "path", "reason"),
    [
        ({"model": None}, "model", "null_not_allowed"),
        ({"instructions": None}, "instructions", "null_not_allowed"),
        ({"skills": None}, "skills", "null_not_allowed"),
        ({"retries": None}, "retries", "null_not_allowed"),
        ({"model": {"settings": None}}, "model.settings", "null_not_allowed"),
        ({"retries": {"tools": None}}, "retries.tools", "null_not_allowed"),
        ({"subagents": {"new": {}}}, "subagents.new.agent_id", "required"),
    ],
)
def test_invalid_null_or_incomplete_overrides_are_bounded(payload: dict[str, object], path: str, reason: str) -> None:
    override = AgentRunOverride.model_validate(payload)

    with pytest.raises(AgentError) as invalid:
        merge_agent_run_override(agent_config(), override)

    assert invalid.value.code == "validation_error"
    assert invalid.value.details == {"path": path, "reason": reason}


@pytest.mark.anyio
async def test_current_invocation_freezes_complete_effective_config(
    agent_service: AgentService,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-current-invocation",
        request=CreateAgentRequest(name="Current Invocation", config=agent_config()),
    )

    prepared = await agent_invocation_resolver.prepare(
        actor=actor(),
        agent_id=created.agent.id,
        config_override=AgentRunOverride(instructions="One Run only.", retries={"tools": 0}),
    )
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freeze_in_transaction(session, prepared=prepared)

    assert frozen.selector_kind is AgentSelectorKind.current
    assert frozen.agent_revision_id == created.revision.id
    assert frozen.effective_config.instructions == "One Run only."
    assert frozen.effective_config.retries is not None
    assert frozen.effective_config.retries.tools == 0
    assert frozen.effective_config.resolved_model == created.revision.resolved_model


@pytest.mark.anyio
async def test_exact_historical_revision_never_follows_current(
    agent_service: AgentService,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-exact-invocation",
        request=CreateAgentRequest(name="Exact Invocation", config=agent_config(instructions="v1")),
    )
    second = await agent_service.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="create-exact-v2",
        request=CreateAgentRevisionRequest(
            expected_version=1,
            config=agent_config(instructions="v2"),
        ),
    )

    prepared = await agent_invocation_resolver.prepare(
        actor=actor(),
        agent_id=created.agent.id,
        agent_revision_id=created.revision.id,
    )
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freeze_in_transaction(session, prepared=prepared)

    assert frozen.selector_kind is AgentSelectorKind.exact
    assert frozen.agent_revision_id == created.revision.id
    assert frozen.agent_revision_id != second.revision.id
    assert frozen.effective_config.instructions == "v1"


@pytest.mark.anyio
async def test_current_selector_detects_revision_change_between_prepare_and_commit(
    agent_service: AgentService,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = await agent_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-current-race",
        request=CreateAgentRequest(name="Current Race", config=agent_config()),
    )
    prepared = await agent_invocation_resolver.prepare(actor=actor(), agent_id=created.agent.id)
    await agent_service.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="advance-current-race",
        request=CreateAgentRevisionRequest(
            expected_version=1,
            config=agent_config(instructions="advanced"),
        ),
    )

    with pytest.raises(AgentError) as conflict:
        async with transaction(agent_sessions) as session:
            await agent_invocation_resolver.freeze_in_transaction(session, prepared=prepared)
    assert conflict.value.code == "current_revision_conflict"
