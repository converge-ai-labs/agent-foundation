from __future__ import annotations

import pytest
from a13n_harness import ModelCapability
from a13n_harness.token_pricing import TokenPriceTier, TokenPricing, TokenRates
from a13n_service.agents.application import AgentManagement
from a13n_service.agents.domain import (
    AgentConfig,
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
from a13n_service.etags import resource_etag
from a13n_service.models.domain import CatalogRef
from a13n_service.models.models import ModelRecord
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import MODEL_ID, WORKSPACE_ID, actor, add_model, agent_config


def test_absent_override_inherits_complete_agent_config() -> None:
    base = agent_config()

    merged = merge_agent_run_override(base, None)

    assert merged.model_dump(mode="json", by_alias=True) == base.model_dump(
        mode="json", by_alias=True, exclude={"default_environment_template_id"}
    )


def test_scalar_and_list_overrides_replace_and_clear() -> None:
    base = agent_config()
    override = AgentRunOverride.model_validate(
        {
            "model": {"settings": {}, "characteristics": {"context_window_tokens": 32000}},
            "instructions": "",
            "plugins": [],
            "skills": [],
            "client_tools": [],
            "output_spec": None,
            "retries": {"tools": 0},
        }
    )

    merged = merge_agent_run_override(base, override)

    assert merged.model.model_key == base.model.model_key
    assert merged.model.settings == base.model.settings
    assert merged.model_settings_override == {}
    assert merged.model.characteristics.context_window_tokens == 32000
    assert merged.instructions == ""
    assert merged.plugins == ()
    assert merged.skills == ()
    assert merged.client_tools == ()
    assert merged.output_spec is None
    assert merged.retries is not None
    assert merged.retries.tools == 0
    assert merged.retries.output == 1


def test_model_settings_preserve_agent_and_run_layers_until_model_resolution() -> None:
    base = agent_config()
    override = AgentRunOverride.model_validate({"model": {"settings": {"thinking": "high"}}})

    merged = merge_agent_run_override(base, override)

    assert merged.model.settings == base.model.settings
    assert merged.model_settings_override == {"thinking": "high"}
    assert "model_settings_override" not in merged.model_dump(mode="json")


def test_client_tool_override_narrows_optional_protocol_surface() -> None:
    payload = agent_config().model_dump(mode="json", by_alias=True)
    payload["client_tools"] = [
        {"name": "required_tool", "description": "Required.", "parameters_json_schema": {"type": "object"}},
        {"name": "optional_tool", "description": "Optional.", "parameters_json_schema": {"type": "object"}},
    ]
    payload["protocol"]["client_tools"] = [
        {"name": "required_tool", "required": True},
        {"name": "optional_tool", "required": False},
    ]
    base = agent_config().__class__.model_validate(payload)

    merged = merge_agent_run_override(
        base,
        AgentRunOverride(client_tools=(base.client_tools[0],)),
    )

    assert [item.name for item in merged.client_tools] == ["required_tool"]
    assert [item.name for item in merged.protocol.client_tools] == ["required_tool"]


def test_client_tool_override_retains_missing_required_protocol_tool_for_validation() -> None:
    payload = agent_config().model_dump(mode="json", by_alias=True)
    payload["client_tools"] = [
        {"name": "required_tool", "description": "Required.", "parameters_json_schema": {"type": "object"}},
    ]
    payload["protocol"]["client_tools"] = [{"name": "required_tool", "required": True}]
    base = agent_config().__class__.model_validate(payload)

    merged = merge_agent_run_override(base, AgentRunOverride(client_tools=()))

    assert merged.client_tools == ()
    assert [item.name for item in merged.protocol.client_tools] == ["required_tool"]


def test_empty_retry_patch_is_a_noop() -> None:
    base = agent_config().model_copy(update={"retries": None})

    merged = merge_agent_run_override(base, AgentRunOverride.model_validate({"retries": {}}))

    assert merged.retries is None


def test_duplicate_list_override_is_rejected() -> None:
    override = AgentRunOverride.model_validate(
        {
            "skills": [
                {"skill_key": "deploy"},
                {"skill_key": "deploy", "version": 2},
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

    assert tuple(merged.subagents) == ("researcher", "writer")
    assert merged.subagents["researcher"].version is None
    assert merged.subagents["researcher"].description is None
    assert merged.subagents["writer"].context.history == "none"
    assert merged.subagents["writer"].environment.mode == "shared"


def test_connection_tool_overrides_replace_complete_lists() -> None:
    base = agent_config(
        connection_tools=(
            *({"connection_id": "cconn_1234567890abcdef", "tools": ["lookup"]},),
            *({"connection_id": "mcpc_1234567890abcdef"},),
        ),
    )
    override = AgentRunOverride.model_validate(
        {
            "connection_tools": [{"connection_id": "cconn_1111111111111111", "tools": [], "defer_loading": True}],
        }
    )
    merged = merge_agent_run_override(base, override)
    assert len(merged.connection_tools) == 1
    assert merged.connection_tools[0].connection_id == "cconn_1111111111111111"
    assert merged.connection_tools[0].tools == ()
    assert merged.connection_tools[0].defer_loading
    assert merge_agent_run_override(base, AgentRunOverride()).connection_tools == base.connection_tools


@pytest.mark.parametrize(
    ("payload", "path", "reason"),
    [
        ({"model": None}, "model", "null_not_allowed"),
        ({"instructions": None}, "instructions", "null_not_allowed"),
        ({"skills": None}, "skills", "null_not_allowed"),
        ({"retries": None}, "retries", "null_not_allowed"),
        ({"retries": {"tools": None}}, "retries.tools", "null_not_allowed"),
        ({"subagents": {"new": {}}}, "subagents.new.agent_id", "required"),
        ({"connection_tools": None}, "connection_tools", "null_not_allowed"),
        ({"connection_tools": None}, "connection_tools", "null_not_allowed"),
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
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-current-invocation",
        request=CreateAgentRequest(name="Current Invocation", config=agent_config()),
    )

    prepared = await agent_invocation_resolver.preparation.prepare(
        actor=actor(),
        agent_id=created.agent.id,
        config_override=AgentRunOverride(instructions="One Run only.", retries={"tools": 0}),
    )
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)

    assert frozen.selector_kind is AgentSelectorKind.current
    assert frozen.agent_revision_id == created.revision.id
    assert frozen.effective_config.instructions == "One Run only."
    assert frozen.effective_config.retries is not None
    assert frozen.effective_config.retries.tools == 0
    assert frozen.effective_config.resolved_model.execution.model_id == created.revision.resolved_model.model_id
    assert frozen.effective_config.resolved_model.execution.model_key == created.revision.resolved_model.model_key
    assert frozen.effective_config.resolved_model.execution.catalog_ref == CatalogRef(
        provider="openai", model="gpt-5.6-terra"
    )
    assert frozen.effective_config.resolved_model.settings == created.revision.resolved_model.settings
    assert frozen.effective_config.resolved_model.characteristics.context_window_tokens == 128000
    assert frozen.effective_config.resolved_model.characteristics.capabilities == {ModelCapability.IMAGE_UNDERSTANDING}


@pytest.mark.anyio
async def test_run_acceptance_uses_latest_model_without_revising_agent(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-live-model-selection",
        request=CreateAgentRequest(name="Live Model", config=agent_config()),
    )
    async with transaction(agent_sessions) as session:
        model = await session.get(ModelRecord, MODEL_ID)
        assert model is not None
        model.upstream_model = "gpt-new"
        model.catalog_ref = {"provider": "openai", "model": "gpt-5"}
        model.declarations = {
            "capabilities": ["audio_understanding"],
            "context_window_tokens": 192000,
            "pricing": {"tiers": [{"rates": {"input": "3", "output": "6"}}]},
        }

    prepared = await agent_invocation_resolver.preparation.prepare(actor=actor(), agent_id=created.agent.id)
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)

    assert frozen.agent_revision_id == created.revision.id
    assert frozen.effective_config.resolved_model.execution.upstream_model == "gpt-new"
    assert frozen.effective_config.resolved_model.execution.catalog_ref == CatalogRef(provider="openai", model="gpt-5")
    assert frozen.effective_config.resolved_model.execution.pricing is not None
    assert frozen.effective_config.resolved_model.execution.pricing.tiers[0].rates.input == 3
    assert frozen.effective_config.resolved_model.characteristics.capabilities == {ModelCapability.AUDIO_UNDERSTANDING}
    # The Agent's explicit window remains higher precedence than the updated Model declaration.
    assert frozen.effective_config.resolved_model.characteristics.context_window_tokens == 128000
    async with transaction(agent_sessions) as session:
        model = await session.get(ModelRecord, MODEL_ID)
        assert model is not None
        model.catalog_ref = None
        model.declarations = {"capabilities": ["video_understanding"], "context_window_tokens": 64000}
    assert frozen.effective_config.resolved_model.execution.catalog_ref == CatalogRef(provider="openai", model="gpt-5")
    assert frozen.effective_config.resolved_model.characteristics.capabilities == {ModelCapability.AUDIO_UNDERSTANDING}
    assert frozen.effective_config.resolved_model.execution.pricing == TokenPricing(
        tiers=(TokenPriceTier(rates=TokenRates(input=3, output=6)),)
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("override", "reason"),
    [
        (
            {"connection_tools": [{"connection_id": "cconn_1234567890abcdef", "permission": "allow"}]},
            "connection_unavailable",
        ),
        (
            {"connection_tools": [{"connection_id": "mcpc_1234567890abcdef", "permission": "allow"}]},
            "connection_unavailable",
        ),
    ],
)
async def test_invocation_rejects_unavailable_connections(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    override: dict[str, object],
    reason: str,
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key=f"create-invocation-{reason}",
        request=CreateAgentRequest(name=f"Invocation {reason}", config=agent_config()),
    )

    with pytest.raises(AgentError) as rejected:
        await agent_invocation_resolver.preparation.prepare(
            actor=actor(),
            agent_id=created.agent.id,
            agent_revision_id=created.revision.id,
            config_override=AgentRunOverride.model_validate(override),
        )

    assert rejected.value.code == "agent_revision_not_executable"
    assert rejected.value.details == {"reason": reason}


@pytest.mark.anyio
async def test_exact_historical_revision_never_follows_current(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-exact-invocation",
        request=CreateAgentRequest(name="Exact Invocation", config=agent_config(instructions="v1")),
    )
    second = await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="create-exact-v2",
        request=CreateAgentRevisionRequest(
            config=agent_config(instructions="v2"),
        ),
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
    )

    prepared = await agent_invocation_resolver.preparation.prepare(
        actor=actor(),
        agent_id=created.agent.id,
        agent_revision_id=created.revision.id,
    )
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)

    assert frozen.selector_kind is AgentSelectorKind.exact
    assert frozen.agent_revision_id == created.revision.id
    assert frozen.agent_revision_id != second.revision.id
    assert frozen.effective_config.instructions == "v1"


@pytest.mark.anyio
async def test_current_selector_detects_revision_change_between_prepare_and_commit(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-current-race",
        request=CreateAgentRequest(name="Current Race", config=agent_config()),
    )
    prepared = await agent_invocation_resolver.preparation.prepare(actor=actor(), agent_id=created.agent.id)
    await agent_management.revisions.create_revision(
        actor=actor(),
        agent_id=created.agent.id,
        idempotency_key="advance-current-race",
        request=CreateAgentRevisionRequest(
            config=agent_config(instructions="advanced"),
        ),
        if_match=resource_etag(created.agent.id, created.agent.updated_at),
    )

    with pytest.raises(AgentError) as conflict:
        async with transaction(agent_sessions) as session:
            await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)
    assert conflict.value.code == "default_revision_conflict"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("override", "temperature"),
    [({}, 0.2), ({"settings": {}}, 0.2), ({"settings": None}, 0.8), ({"settings": {"temperature": 0.5}}, 0.5)],
)
async def test_run_freezes_model_defaults_agent_settings_and_explicit_overrides(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
    override: dict,
    temperature: float,
) -> None:
    from a13n_service.models.models import ModelRecord

    from .conftest import MODEL_ID

    async with transaction(agent_sessions) as session:
        record = await session.get(ModelRecord, MODEL_ID)
        assert record is not None
        record.settings = {"temperature": 0.8, "max_tokens": 123}
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="defaults-create",
        request=CreateAgentRequest(name="Defaults", config=agent_config()),
    )
    assert created.revision.resolved_model.settings == {"temperature": 0.2}
    assert "model_api" not in created.revision.resolved_model.model_dump()
    prepared = await agent_invocation_resolver.preparation.prepare(
        actor=actor(), agent_id=created.agent.id, config_override=AgentRunOverride.model_validate({"model": override})
    )
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)
    assert frozen.effective_config.resolved_model.settings == {"temperature": temperature, "max_tokens": 123}
    async with transaction(agent_sessions) as session:
        record = await session.get(ModelRecord, MODEL_ID)
        assert record is not None
        record.settings = {"max_tokens": 456}
    assert frozen.effective_config.resolved_model.settings["max_tokens"] == 123


@pytest.mark.anyio
async def test_invalid_run_settings_preserve_the_parameter_error(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
) -> None:
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="invalid-settings-create",
        request=CreateAgentRequest(name="Settings", config=agent_config()),
    )
    with pytest.raises(AgentError) as invalid:
        await agent_invocation_resolver.preparation.prepare(
            actor=actor(),
            agent_id=created.agent.id,
            config_override=AgentRunOverride.model_validate({"model": {"settings": {"temperature": "secret"}}}),
        )
    assert invalid.value.code == "invalid_model_settings"
    assert invalid.value.details["path"] == ["settings", "temperature"]
    assert "secret" not in str(invalid.value.details)


@pytest.mark.anyio
async def test_run_reasoning_choice_replaces_agent_reasoning_choice(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    payload = agent_config().model_dump(mode="python")
    payload["model"]["settings"] = {
        "thinking": "low",
        "temperature": 0.2,
    }
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="reasoning-layer-create",
        request=CreateAgentRequest(
            name="Reasoning Layers",
            config=agent_config().__class__.model_validate(payload),
        ),
    )

    prepared = await agent_invocation_resolver.preparation.prepare(
        actor=actor(),
        agent_id=created.agent.id,
        config_override=AgentRunOverride.model_validate({"model": {"settings": {"thinking": "high"}}}),
    )
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)

    assert frozen.effective_config.resolved_model.settings == {"thinking": "high", "temperature": 0.2}


@pytest.mark.parametrize("field", ["account_tools", "native_tool_contexts"])
def test_native_authority_cannot_be_supplied_through_agent_config_or_override(field):
    from a13n_service.agents.domain import AgentRevision, EffectiveAgentConfig
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentConfig.model_validate({**agent_config().model_dump(), field: []})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentRunOverride.model_validate({field: []})
    assert field not in AgentRevision.model_fields
    assert field not in EffectiveAgentConfig.model_fields


@pytest.mark.parametrize("field", ["api_key", "credentials", "sensitive_values", "encrypted_config_payload"])
def test_run_override_rejects_direct_credentials(field: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AgentRunOverride.model_validate({field: "private-value"})


@pytest.mark.anyio
async def test_parent_acceptance_freezes_child_model_defaults_and_detects_child_revocation(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    from a13n_service.agents.models import AgentRecord

    child = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="child-snapshot",
        request=CreateAgentRequest(name="Child Snapshot", config=agent_config()),
    )
    parent = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="parent-snapshot",
        request=CreateAgentRequest(
            name="Parent Snapshot",
            config=agent_config(
                subagents={"researcher": {"agent_id": child.agent.id}},
            ),
        ),
    )
    async with transaction(agent_sessions) as session:
        model = await session.get(ModelRecord, MODEL_ID)
        assert model is not None
        model.settings = {"max_tokens": 321}
    prepared = await agent_invocation_resolver.preparation.prepare(actor=actor(), agent_id=parent.agent.id)
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)
    accepted_child = frozen.effective_config.child_configs[child.revision.id]
    assert accepted_child.effective_config.resolved_model.execution.pricing is not None
    assert accepted_child.effective_config.resolved_model.execution.pricing.tiers[0].rates.output == 2.0
    assert accepted_child.effective_config.resolved_model.execution.catalog_ref == CatalogRef(
        provider="openai", model="gpt-5.6-terra"
    )
    assert accepted_child.agent_id == child.agent.id
    assert accepted_child.revision_content_digest == child.revision.content_digest
    assert accepted_child.effective_config.resolved_model.settings == {"temperature": 0.2, "max_tokens": 321}
    assert accepted_child.effective_config.resolved_model.characteristics.capabilities == {
        ModelCapability.IMAGE_UNDERSTANDING
    }
    async with transaction(agent_sessions) as session:
        record = await session.get(AgentRecord, child.agent.id)
        assert record is not None
        record.enabled = False
    with pytest.raises(AgentError):
        async with transaction(agent_sessions) as session:
            await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)
    assert accepted_child.effective_config.resolved_model.settings["max_tokens"] == 321


def test_toolset_and_reviewer_overrides_inherit_replace_and_clear() -> None:
    from a13n_service.agents.domain import AgentConfig

    base = AgentConfig.model_validate(
        {
            **agent_config().model_dump(),
            "toolsets": {
                "shell": {
                    "enabled": True,
                    "tools": {"exec": {"permission": "review"}},
                }
            },
            "reviewer": {"model": MODEL_ID, "instruction": "Review writes."},
        }
    )
    inherited = merge_agent_run_override(base, AgentRunOverride(instructions="Changed task"))
    assert inherited.toolsets == base.toolsets and inherited.reviewer == base.reviewer
    assert inherited.reviewer.risk_threshold == "extra_high"
    assert inherited.reviewer.on_flagged == "deny"
    changed = merge_agent_run_override(
        base,
        AgentRunOverride.model_validate(
            {
                "reviewer": {
                    "model": MODEL_ID,
                    "on_flagged": "approval_required",
                    "rules": {"environment.shell_exec": {"risk_threshold": "high"}},
                },
            }
        ),
    )
    assert changed.reviewer.on_flagged == "approval_required"
    assert changed.reviewer.rules["environment.shell_exec"].risk_threshold == "high"
    assert changed.reviewer.instruction is None
    cleared = merge_agent_run_override(base, AgentRunOverride(reviewer=None))
    assert cleared.reviewer is None
    replaced = merge_agent_run_override(
        base,
        AgentRunOverride.model_validate(
            {"toolsets": {"shell": {"enabled": False, "tools": {"exec": {"permission": "deny"}}}}}
        ),
    )
    assert not replaced.toolsets["shell"].enabled
    assert replaced.toolsets["shell"].tools["exec"].permission == "deny"
    assert replaced.toolsets["shell"].tools["wait"].permission == "inherit"


def test_media_understanding_override_patches_each_kind() -> None:
    base = agent_config(media_understanding={"image": "agent-vision", "audio": "agent-vision"})

    inherited = merge_agent_run_override(base, AgentRunOverride.model_validate({"instructions": "Other."}))
    assert inherited.media_understanding == base.media_understanding
    patched = merge_agent_run_override(
        base, AgentRunOverride.model_validate({"media_understanding": {"image": "run-vision", "video": None}})
    )
    assert patched.media_understanding.image == "run-vision"
    assert patched.media_understanding.audio == "agent-vision"
    assert patched.media_understanding.video is None
    nulled = merge_agent_run_override(base, AgentRunOverride.model_validate({"media_understanding": None}))
    assert nulled.media_understanding == base.media_understanding
    assert AgentConfig.model_validate_json(base.model_dump_json()).media_understanding == base.media_understanding
    assert "media_understanding" not in merge_agent_run_override(agent_config(), None).model_dump(mode="json")


@pytest.mark.anyio
async def test_revision_creation_validates_selected_media_models(
    agent_management: AgentManagement,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await add_model(
        agent_sessions, key="vision", model_id="mdl_vision1234567890", capabilities=("image_understanding",)
    )
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-media-agent",
        request=CreateAgentRequest(name="Media Agent", config=agent_config(media_understanding={"image": "vision"})),
    )
    assert created.revision.config.media_understanding.image == "vision"
    for index, selection in enumerate(({"audio": "vision"}, {"image": "missing"})):
        with pytest.raises(AgentError) as failure:
            await agent_management.commands.create(
                actor=actor(),
                workspace_id=WORKSPACE_ID,
                idempotency_key=f"rejected-media-{index}",
                request=CreateAgentRequest(
                    name=f"Rejected {index}", config=agent_config(media_understanding=selection)
                ),
            )
        assert failure.value.details == {"reason": "managed_resource_unavailable"}


@pytest.mark.anyio
async def test_permissions_and_managed_reviewer_survive_acceptance_and_reconstruction(
    agent_management: AgentManagement,
    agent_invocation_resolver: AgentInvocationResolver,
    agent_sessions: async_sessionmaker[AsyncSession],
) -> None:
    from a13n_harness.capabilities import SubagentCapability
    from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
    from a13n_harness.tools import ToolPermissionsCapability
    from a13n_service.agents.domain import AgentConfig, EffectiveAgentConfig, PreparedAgentPlugins
    from a13n_service.agents.reconstruction import AgentReconstructor

    config = AgentConfig.model_validate(
        {
            **agent_config().model_dump(),
            "toolsets": {
                "files": {
                    "enabled": True,
                    "tools": {"view": {"permission": "review"}},
                }
            },
            "reviewer": {
                "model": MODEL_ID,
                "instruction": "Review writes.",
                "model_settings": {"temperature": 0.1},
                "on_flagged": "approval_required",
                "rules": {"environment.shell_exec": {"risk_threshold": "high"}},
            },
        }
    )
    created = await agent_management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="create-reviewed-agent",
        request=CreateAgentRequest(name="Reviewed Agent", config=config),
    )
    prepared = await agent_invocation_resolver.preparation.prepare(actor=actor(), agent_id=created.agent.id)
    assert prepared.reviewer_model is not None
    async with transaction(agent_sessions) as session:
        frozen = await agent_invocation_resolver.freezing.freeze_in_transaction(session, prepared=prepared)
    effective = EffectiveAgentConfig.model_validate_json(frozen.effective_config.model_dump_json())
    assert effective.toolsets == config.toolsets and effective.reviewer == config.reviewer
    assert effective.resolved_reviewer_model is not None
    assert effective.resolved_reviewer_model.execution.model_id == MODEL_ID
    assert effective.resolved_reviewer_model.execution.catalog_ref == CatalogRef(
        provider="openai", model="gpt-5.6-terra"
    )
    assert effective.resolved_reviewer_model.execution.pricing is not None
    assert effective.resolved_reviewer_model.execution.pricing.tiers[0].rates.output == 2.0
    assert effective.resolved_reviewer_model.settings["temperature"] == 0.1
    assert effective.resolved_reviewer_model.characteristics.context_window_tokens == 256000
    assert effective.resolved_reviewer_model.characteristics.capabilities == {ModelCapability.IMAGE_UNDERSTANDING}
    # Capture is sufficient; reconstruction does not reread the Model resource.
    definition = AgentReconstructor(plugin_catalog=HarnessPluginFactoryCatalog(())).reconstruct(
        agent_id=created.agent.id,
        agent_revision_id=created.revision.id,
        effective_config=effective,
        subagent_capability=SubagentCapability(),
        prepared_plugins=PreparedAgentPlugins(plugins=()),
    )
    permissions = next(
        capability for capability in definition.capabilities if isinstance(capability, ToolPermissionsCapability)
    )
    assert permissions.policy.risk_threshold == "extra_high"
    assert permissions.policy.on_flagged == "approval_required"
    assert permissions.policy.rules["environment.shell_exec"].risk_threshold == "high"
