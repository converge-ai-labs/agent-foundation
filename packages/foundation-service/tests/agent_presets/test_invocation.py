from __future__ import annotations

import pytest
from a13n_service.agent_presets.domain import (
    AgentPresetCommandRequest,
    AgentRunOverride,
    CreateAgentPresetRequest,
    ReplaceAgentPresetConfigRequest,
    SetDefaultAgentPresetRevisionRequest,
)
from a13n_service.agent_presets.errors import AgentPresetError
from a13n_service.agent_presets.invocation import merge_agent_run_override
from a13n_service.agent_presets.invocation_resolution import (
    AgentPresetInvocationResolver,
    AgentPresetSelectorKind,
)
from a13n_service.agent_presets.service import AgentPresetService
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import WORKSPACE_ID, actor, preset_config


def test_absent_override_inherits_complete_preset_config() -> None:
    base = preset_config()

    merged = merge_agent_run_override(base, None)

    assert merged.config.model_dump(mode="json", by_alias=True) == base.model_dump(mode="json", by_alias=True)


def test_scalar_and_list_overrides_replace_and_clear() -> None:
    base = preset_config()
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

    assert merged.config.model.model_config_id == base.model.model_config_id
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
    base = preset_config().model_copy(update={"retries": None})

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

    with pytest.raises(AgentPresetError) as invalid:
        merge_agent_run_override(preset_config(), override)

    assert invalid.value.code == "validation_error"
    assert invalid.value.details == {"path": "skills", "reason": "duplicate_selection"}


def test_subagent_patch_is_name_keyed_and_supports_default_selection() -> None:
    base = preset_config(
        subagents={
            "researcher": {
                "agent_preset_id": "ap_1234567890abcdef",
                "revision": 3,
                "description": "Research",
                "environment": {"mode": "none"},
            },
            "legacy": {
                "agent_preset_id": "ap_abcdef1234567890",
                "environment": {"mode": "none"},
            },
        }
    )
    override = AgentRunOverride.model_validate(
        {
            "subagents": {
                "researcher": {"revision": None, "description": None},
                "legacy": None,
                "writer": {"agent_preset_id": "ap_1111111111111111"},
            }
        }
    )

    merged = merge_agent_run_override(base, override)

    assert tuple(merged.config.subagents) == ("researcher", "writer")
    assert merged.config.subagents["researcher"].revision is None
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
        ({"subagents": {"new": {}}}, "subagents.new.agent_preset_id", "required"),
    ],
)
def test_invalid_null_or_incomplete_overrides_are_bounded(payload: dict[str, object], path: str, reason: str) -> None:
    override = AgentRunOverride.model_validate(payload)

    with pytest.raises(AgentPresetError) as invalid:
        merge_agent_run_override(preset_config(), override)

    assert invalid.value.code == "validation_error"
    assert invalid.value.details == {"path": path, "reason": reason}


@pytest.mark.anyio
async def test_default_invocation_freezes_complete_effective_config(
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-default-invocation",
        request=CreateAgentPresetRequest(name="Default Invocation", config=preset_config()),
    )
    revision_result = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="create-revision-default-invocation",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="set-default-invocation",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=revision_result.preset.resource_version,
            revision_id=revision_result.revision.id,
        ),
    )

    prepared = await agent_preset_invocation_resolver.prepare(
        actor=actor(),
        agent_preset_id=preset.id,
        config_override=AgentRunOverride(instructions="One Run only.", retries={"tools": 0}),
    )
    async with transaction(agent_preset_sessions) as session:
        frozen = await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=prepared)

    assert frozen.selector_kind is AgentPresetSelectorKind.default
    assert frozen.agent_preset_revision_id == revision_result.revision.id
    assert frozen.effective_config.instructions == "One Run only."
    assert frozen.effective_config.retries is not None
    assert frozen.effective_config.retries.tools == 0
    assert frozen.effective_config.retries.output == 1
    assert frozen.effective_config.resolved_model == revision_result.revision.resolved_model
    assert len(frozen.effective_config.content_digest) == 64
    assert len(frozen.sensitive_values_digest) == 64


@pytest.mark.anyio
async def test_exact_historical_revision_never_falls_back_to_default(
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-exact-invocation",
        request=CreateAgentPresetRequest(name="Exact Invocation", config=preset_config(instructions="v1")),
    )
    first = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="create_revision-exact-invocation-v1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    edited = await agent_preset_service.replace_config(
        actor=actor(),
        preset_id=preset.id,
        request=ReplaceAgentPresetConfigRequest(
            expected_resource_version=2,
            config=preset_config(instructions="v2"),
        ),
    )
    second = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="create_revision-exact-invocation-v2",
        request=AgentPresetCommandRequest(expected_resource_version=edited.resource_version),
    )

    prepared = await agent_preset_invocation_resolver.prepare(
        actor=actor(),
        agent_preset_id=preset.id,
        agent_preset_revision_id=first.revision.id,
    )
    async with transaction(agent_preset_sessions) as session:
        frozen = await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=prepared)

    assert frozen.selector_kind is AgentPresetSelectorKind.exact
    assert frozen.agent_preset_revision_id == first.revision.id
    assert frozen.agent_preset_revision_id != second.revision.id
    assert frozen.effective_config.instructions == "v1"


@pytest.mark.anyio
async def test_expected_default_revision_and_prepare_commit_races_conflict(
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    preset = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-default-race",
        request=CreateAgentPresetRequest(name="Default Race", config=preset_config(instructions="v1")),
    )
    first = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="create-revision-default-race-v1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    selected_first = await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="set-default-race-v1",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=first.preset.resource_version,
            revision_id=first.revision.id,
        ),
    )
    with pytest.raises(AgentPresetError) as stale_expectation:
        await agent_preset_invocation_resolver.prepare(
            actor=actor(),
            agent_preset_id=preset.id,
            expected_default_revision_id="apr_1111111111111111",
        )
    assert stale_expectation.value.code == "default_revision_conflict"

    prepared = await agent_preset_invocation_resolver.prepare(actor=actor(), agent_preset_id=preset.id)
    edited = await agent_preset_service.replace_config(
        actor=actor(),
        preset_id=preset.id,
        request=ReplaceAgentPresetConfigRequest(
            expected_resource_version=selected_first.resource_version,
            config=preset_config(instructions="v2"),
        ),
    )
    second = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="create-revision-default-race-v2",
        request=AgentPresetCommandRequest(expected_resource_version=edited.resource_version),
    )
    await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=preset.id,
        idempotency_key="set-default-race-v2",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=second.preset.resource_version,
            revision_id=second.revision.id,
        ),
    )

    with pytest.raises(AgentPresetError) as raced:
        async with transaction(agent_preset_sessions) as session:
            await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=prepared)
    assert raced.value.code == "default_revision_conflict"


@pytest.mark.anyio
async def test_missing_default_and_disabled_presets_cannot_start_new_root_work(
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
) -> None:
    without_default = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-without-default-invocation",
        request=CreateAgentPresetRequest(name="Without Default Invocation", config=preset_config()),
    )
    with pytest.raises(AgentPresetError) as missing_default:
        await agent_preset_invocation_resolver.prepare(actor=actor(), agent_preset_id=without_default.id)
    assert missing_default.value.code == "preset_default_revision_missing"

    revision_result = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=without_default.id,
        idempotency_key="create_revision-disabled-invocation",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    await agent_preset_service.change_lifecycle(
        actor=actor(),
        preset_id=without_default.id,
        action="disable",
        idempotency_key="disable-invocation",
        request=AgentPresetCommandRequest(expected_resource_version=revision_result.preset.resource_version),
    )
    with pytest.raises(AgentPresetError) as disabled:
        await agent_preset_invocation_resolver.prepare(actor=actor(), agent_preset_id=without_default.id)
    assert disabled.value.code == "preset_disabled"


@pytest.mark.anyio
async def test_inherited_subagent_keeps_exact_revision_when_child_default_changes(
    agent_preset_service: AgentPresetService,
    agent_preset_invocation_resolver: AgentPresetInvocationResolver,
    agent_preset_sessions: async_sessionmaker[AsyncSession],
) -> None:
    child = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-invocation-child",
        request=CreateAgentPresetRequest(name="Invocation Child", config=preset_config(instructions="child-v1")),
    )
    first_child = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=child.id,
        idempotency_key="create_revision-invocation-child-v1",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    selected_child = await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=child.id,
        idempotency_key="set-default-invocation-child-v1",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=first_child.preset.resource_version,
            revision_id=first_child.revision.id,
        ),
    )
    root = await agent_preset_service.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-invocation-root",
        request=CreateAgentPresetRequest(
            name="Invocation Root",
            config=preset_config(subagents={"child": {"agent_preset_id": child.id, "environment": {"mode": "none"}}}),
        ),
    )
    root_revision = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=root.id,
        idempotency_key="create_revision-invocation-root",
        request=AgentPresetCommandRequest(expected_resource_version=1),
    )
    await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=root.id,
        idempotency_key="set-default-invocation-root",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=root_revision.preset.resource_version,
            revision_id=root_revision.revision.id,
        ),
    )
    prepared = await agent_preset_invocation_resolver.prepare(actor=actor(), agent_preset_id=root.id)

    edited_child = await agent_preset_service.replace_config(
        actor=actor(),
        preset_id=child.id,
        request=ReplaceAgentPresetConfigRequest(
            expected_resource_version=selected_child.resource_version,
            config=preset_config(instructions="child-v2"),
        ),
    )
    second_child = await agent_preset_service.create_revision(
        actor=actor(),
        preset_id=child.id,
        idempotency_key="create_revision-invocation-child-v2",
        request=AgentPresetCommandRequest(expected_resource_version=edited_child.resource_version),
    )
    await agent_preset_service.set_default_revision(
        actor=actor(),
        preset_id=child.id,
        idempotency_key="set-default-invocation-child-v2",
        request=SetDefaultAgentPresetRevisionRequest(
            expected_resource_version=second_child.preset.resource_version,
            revision_id=second_child.revision.id,
        ),
    )
    async with transaction(agent_preset_sessions) as session:
        frozen = await agent_preset_invocation_resolver.freeze_in_transaction(session, prepared=prepared)

    selected_child = frozen.effective_config.resolved_subagents[0]
    assert selected_child.child_agent_preset_revision_id == first_child.revision.id
    assert selected_child.child_agent_preset_revision_id != second_child.revision.id
