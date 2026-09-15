from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from a13n_harness import AbstractHarnessPlugin, HarnessBuilder, ModelRecoveryPolicy
from a13n_harness.capabilities import SubagentCapability
from a13n_harness.plugin_factories import (
    HarnessPluginFactory,
    HarnessPluginFactoryCatalog,
    HarnessPluginFactoryContext,
    HarnessPluginFactoryRegistration,
)
from a13n_harness.tools.client import ClientToolsCapability
from a13n_service.agents.domain import (
    AgentConfig,
    AgentRevision,
    ChildAgentExecution,
    ConnectionToolSelection,
    EffectiveAgentConfig,
    EffectiveAgentModel,
    PluginSelection,
    PreparedAgentPlugins,
    ResolvedAgentModel,
    ResolvedRevisionContent,
    ResolvedSubagentEdge,
)
from a13n_service.agents.reconstruction import (
    AgentDefinitionReconstructionContext,
    AgentDefinitionReconstructionError,
    AgentReconstructor,
)
from a13n_service.digests import digest_request
from a13n_service.iam import PrincipalRef
from a13n_service.models.domain import ModelExecutionSnapshot
from pydantic import JsonValue, RootModel, TypeAdapter, ValidationError
from pydantic_ai.capabilities import Capability
from pydantic_ai.usage import UsageLimits

from .conftest import MODEL_ID, MODEL_KEY, agent_config

NOW = datetime(2026, 9, 2, tzinfo=UTC)
ROOT_AGENT_ID = "ap_1234567890abcdef"
ROOT_REVISION_ID = "apr_1234567890abcdef"
CHILD_AGENT_ID = "ap_child12345678901"
CHILD_REVISION_ID = "apr_child12345678901"


class _Configuration(RootModel[dict[str, JsonValue]]):
    pass


class _Plugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str, label: str) -> None:
        self._plugin_id = plugin_id
        self.label = label

    @property
    def plugin_id(self) -> str:
        return self._plugin_id


class _Factory(HarnessPluginFactory):
    def __init__(self) -> None:
        self.created: list[_Plugin] = []

    @classmethod
    def plugin_key(cls) -> str:
        return "test.plugin"

    def validate_configuration(self, configuration: Mapping[str, JsonValue]) -> _Configuration:
        return _Configuration(dict(configuration))

    def create_plugin(self, context: HarnessPluginFactoryContext) -> _Plugin:
        plugin = _Plugin(context.plugin_id, str(context.configuration.get("label", "default")))
        self.created.append(plugin)
        return plugin


def _catalog(*, distribution_name: str = "test-plugin") -> tuple[HarnessPluginFactoryCatalog, _Factory]:
    factory = _Factory()
    registration = HarnessPluginFactoryRegistration(
        plugin_key="test.plugin",
        class_module="test_plugin.factory",
        class_qualname="Factory",
        import_target="test_plugin.factory:Factory",
        distribution_name=distribution_name,
        distribution_version="1.2.3",
    )
    return HarnessPluginFactoryCatalog(((registration, factory),)), factory


def _resolved_model(config: AgentConfig) -> ResolvedAgentModel:
    return ResolvedAgentModel(
        model_id=MODEL_ID,
        model_key=MODEL_KEY,
        settings=config.model.settings,
        characteristics=config.model.characteristics,
    )


def _effective_model(config: AgentConfig) -> EffectiveAgentModel:
    return EffectiveAgentModel(
        execution=ModelExecutionSnapshot(
            model_id=MODEL_ID,
            model_key=MODEL_KEY,
            upstream_model="gpt-5.6-terra",
            model_api="openai.responses",
        ),
        settings=config.model.settings,
        characteristics=config.model.characteristics,
    )


def _plugin_selection(*, instance_name: str = "audit") -> PluginSelection:
    return PluginSelection(
        instance_name=instance_name,
        plugin_key="test.plugin",
        config={"label": instance_name},
    )


def _config(**updates: object) -> AgentConfig:
    payload = agent_config().model_dump(mode="python", by_alias=True)
    payload.update(updates)
    return AgentConfig.model_validate(payload)


def _effective(
    config: AgentConfig,
    *,
    plugins: tuple[PluginSelection, ...] = (),
    connection_tools: tuple[ConnectionToolSelection, ...] = (),
    subagents: tuple[ResolvedSubagentEdge, ...] = (),
) -> EffectiveAgentConfig:
    candidate = EffectiveAgentConfig(
        resolved_model=_effective_model(config),
        plugins=plugins,
        skills=(),
        connection_tools=connection_tools,
        resolved_subagents=subagents,
        toolsets=config.toolsets,
        reviewer=config.reviewer,
        instructions=config.instructions,
        input_adapter=config.input_adapter,
        client_tools=config.client_tools,
        output_spec=config.output_spec,
        retries=config.retries,
        secret_requirements=config.secret_requirements,
        protocol=config.protocol,
        content_digest="0" * 64,
    )
    payload = candidate.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
    return candidate.model_copy(update={"content_digest": digest_request(payload)})


def _revision(
    *,
    revision_id: str = CHILD_REVISION_ID,
    agent_id: str = CHILD_AGENT_ID,
    config: AgentConfig | None = None,
    plugins: tuple[PluginSelection, ...] = (),
    connection_tools: tuple[ConnectionToolSelection, ...] = (),
    subagents: tuple[ResolvedSubagentEdge, ...] = (),
) -> AgentRevision:
    selected_config = (config or agent_config(instructions="Handle delegated work.")).model_copy(
        update={"plugins": plugins}
    )
    resolved = ResolvedRevisionContent(
        resolved_model=_resolved_model(selected_config),
        resolved_skills=(),
        connection_tools=connection_tools,
        resolved_subagents=subagents,
    )
    digest = digest_request(
        {
            "config": selected_config.model_dump(mode="json", by_alias=True),
            "resolved": resolved.model_dump(mode="json", by_alias=True),
        }
    )
    return AgentRevision(
        id=revision_id,
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        agent_id=agent_id,
        version=1,
        config=selected_config,
        config_digest=digest_request(selected_config),
        resolved_model=resolved.resolved_model,
        resolved_skills=resolved.resolved_skills,
        connection_tools=resolved.connection_tools,
        resolved_subagents=resolved.resolved_subagents,
        content_digest=digest,
        source_revision_id=None,
        created_by=PrincipalRef(principal_type="user", principal_id="usr_1234567890abcdef"),
        created_at=NOW,
    )


def _edge(
    name: str,
    *,
    child_agent_id: str = CHILD_AGENT_ID,
    child_revision_id: str = CHILD_REVISION_ID,
    description: str | None = None,
) -> ResolvedSubagentEdge:
    return ResolvedSubagentEdge.model_validate(
        {
            "name": name,
            "child_agent_id": child_agent_id,
            "child_agent_revision_id": child_revision_id,
            "description": description,
            "context": {"include_task": False, "history": "summary", "task_state": "isolated"},
            "usage_limits": UsageLimits(request_limit=3),
            "environment": {"mode": "none"},
        }
    )


def _with_children(effective: EffectiveAgentConfig, children: Mapping[str, AgentRevision]) -> EffectiveAgentConfig:
    configurations = {
        revision_id: ChildAgentExecution(
            agent_id=revision.agent_id,
            revision_content_digest=revision.content_digest,
            effective_config=_effective(
                revision.config,
                plugins=revision.config.plugins,
                connection_tools=revision.connection_tools,
                subagents=revision.resolved_subagents,
            ),
        )
        for revision_id, revision in children.items()
    }
    return _rehash(effective.model_copy(update={"child_configs": configurations}))


def _rehash(effective: EffectiveAgentConfig) -> EffectiveAgentConfig:
    return effective.model_copy(
        update={
            "content_digest": digest_request(
                effective.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
            )
        }
    )


def _prepared(config: EffectiveAgentConfig) -> PreparedAgentPlugins:
    return PreparedAgentPlugins(
        plugins=config.plugins,
        children={key: _prepared(child.effective_config) for key, child in config.child_configs.items()},
    )


def _reconstruct(
    effective: EffectiveAgentConfig,
    *,
    catalog: HarnessPluginFactoryCatalog | None = None,
    children: Mapping[str, AgentRevision] | None = None,
    capability_provider: Any = None,
):
    reconstructor = AgentReconstructor(
        catalog or HarnessPluginFactoryCatalog(()),
        capability_provider=capability_provider,
    )
    if children is not None:
        effective = _with_children(effective, children)
    return reconstructor.reconstruct(
        agent_id=ROOT_AGENT_ID,
        agent_revision_id=ROOT_REVISION_ID,
        effective_config=effective,
        prepared_plugins=_prepared(effective),
        subagent_capability=SubagentCapability(),
    )


def test_reconstructs_root_model_client_tools_output_and_fresh_capabilities() -> None:
    client_tool = {
        "name": "lookup_order",
        "description": "Look up one order.",
        "parameters_json_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    }
    base_protocol = agent_config().protocol
    protocol = type(base_protocol).model_validate(
        {
            **base_protocol.model_dump(mode="python"),
            "client_tools": [{"name": "lookup_order", "required": True}],
        }
    )
    config = _config(
        instructions="Always verify the order.",
        client_tools=[client_tool],
        output_spec={
            "name": "OrderResult",
            "schema": {
                "type": "object",
                "properties": {"order": {"$ref": "order"}},
                "required": ["order"],
            },
            "resources": {
                "order": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                }
            },
        },
        protocol=protocol,
    )
    contexts: list[AgentDefinitionReconstructionContext] = []

    def capabilities(context: AgentDefinitionReconstructionContext):
        contexts.append(context)
        return (Capability(id="test.root"),)

    definition = _reconstruct(_effective(config), capability_provider=capabilities)

    assert definition.agent.model == MODEL_ID
    assert definition.agent.system_prompt == "Always verify the order."
    assert definition.agent.model_settings == {"temperature": 0.2}
    assert definition.agent.retries == {"tools": 2, "output": 1}
    assert definition.agent.model_characteristics == config.model.characteristics
    assert definition.agent.name == "Support"
    assert contexts[0].is_root is True
    assert contexts[0].agent_revision_id == ROOT_REVISION_ID
    client_capability = next(item for item in definition.capabilities if isinstance(item, ClientToolsCapability))
    assert client_capability.spec.default_toolsets[0].tools[0].name == "lookup_order"
    schema = TypeAdapter(definition.output_type).json_schema()
    assert schema["properties"]["order"]["properties"]["id"]["type"] == "string"
    assert "$ref" not in str(schema)
    adapter = TypeAdapter(definition.output_type)
    assert adapter.validate_python({"order": {"id": "order-1"}}) == {"order": {"id": "order-1"}}
    for invalid in ({}, {"order": {}}, {"order": {"id": 123}}):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)
    HarnessBuilder(configured_plugins_enabled=False).build(definition)


def test_reconstruction_preserves_root_and_child_connectivity_selections() -> None:
    connector = ConnectionToolSelection(
        connection_id="conn_1234567890abcdef",
        tools=("lookup_order",),
        defer_loading=False,
    )
    mcp = ConnectionToolSelection(
        connection_id="conn_fedcba0987654321",
        tools=("search", "fetch"),
        defer_loading=True,
    )
    child_config = _config(
        connection_tools=(connector, mcp),
    )
    child = _revision(
        config=child_config,
        connection_tools=(connector, mcp),
    )
    root_config = _config(
        connection_tools=(connector, mcp),
    )
    effective = _effective(
        root_config,
        connection_tools=(connector, mcp),
        subagents=(_edge("reviewer"),),
    )
    contexts: list[AgentDefinitionReconstructionContext] = []

    def capabilities(context: AgentDefinitionReconstructionContext):
        contexts.append(context)
        return ()

    _reconstruct(
        effective,
        children={child.id: child},
        capability_provider=capabilities,
    )

    assert [context.is_root for context in contexts] == [False, True]
    assert all(context.config.connection_tools == (connector, mcp) for context in contexts)


def test_reconstructs_variants_as_distinct_structured_outputs() -> None:
    config = _config(
        output_spec={
            "variants": [
                {
                    "name": "accepted",
                    "description": "Accepted result.",
                    "schema": {"type": "object", "properties": {"id": {"type": "string"}}},
                },
                {
                    "name": "rejected",
                    "description": "Rejected result.",
                    "schema": {"type": "object", "properties": {"reason": {"type": "string"}}},
                },
            ]
        }
    )

    definition = _reconstruct(_effective(config))

    assert isinstance(definition.output_type, tuple)
    assert [TypeAdapter(item).json_schema()["title"] for item in definition.output_type] == [
        "accepted",
        "rejected",
    ]
    for output_type, field in zip(definition.output_type, ("id", "reason"), strict=True):
        adapter = TypeAdapter(output_type)
        assert adapter.validate_python({field: "value"}) == {field: "value"}
        with pytest.raises(ValidationError):
            adapter.validate_python({field: 123})
    HarnessBuilder(configured_plugins_enabled=False).build(definition)


def test_reconstructs_each_subagent_occurrence_with_fresh_plugin_instances() -> None:
    catalog, factory = _catalog()
    child_config = _config(
        instructions="Handle the child task.",
        plugins=[
            {
                "instance_name": "audit",
                "plugin_key": "test.plugin",
                "config": {"label": "audit"},
            }
        ],
    )
    child = _revision(config=child_config, plugins=(_plugin_selection(),))
    effective = _effective(
        agent_config(),
        subagents=(_edge("reviewer", description="Review the answer."), _edge("checker")),
    )
    contexts: list[AgentDefinitionReconstructionContext] = []

    def capabilities(context: AgentDefinitionReconstructionContext):
        contexts.append(context)
        return (Capability(id=f"test.node-{len(contexts)}"),)

    definition = _reconstruct(
        effective,
        catalog=catalog,
        children={child.id: child},
        capability_provider=capabilities,
    )

    assert definition.model_recovery == ModelRecoveryPolicy(enabled=True)
    assert all(child.agent.model_recovery == definition.model_recovery for child in definition.subagents)
    assert [item.name for item in definition.subagents] == ["reviewer", "checker"]
    assert definition.subagents[0].description == "Review the answer."
    assert definition.subagents[1].description == "Support"
    assert definition.subagents[0].context.history == "summary"
    assert definition.subagents[0].usage_limits == UsageLimits(request_limit=3)
    assert definition.subagents[0].agent.plugins[0] is not definition.subagents[1].agent.plugins[0]
    assert [item.plugin_id for item in factory.created] == ["audit", "audit"]
    assert [context.is_root for context in contexts] == [False, False, True]
    subagent_capability = next(item for item in definition.capabilities if isinstance(item, SubagentCapability))
    assert subagent_capability.async_enabled is False
    HarnessBuilder(configured_plugins_enabled=False).build(definition)


def test_reconstruction_rejects_tampered_effective_config() -> None:
    effective = _effective(agent_config()).model_copy(update={"content_digest": "0" * 64})

    with pytest.raises(AgentDefinitionReconstructionError, match="effective_config_digest_mismatch"):
        _reconstruct(effective)


def test_reconstruction_rejects_missing_or_mismatched_child_revision() -> None:
    effective = _effective(agent_config(), subagents=(_edge("reviewer"),))

    with pytest.raises(AgentDefinitionReconstructionError) as missing:
        _reconstruct(effective)
    assert missing.value.reason == "subagent_snapshot_mismatch"

    child = _revision(agent_id="ap_other12345678901")
    with pytest.raises(AgentDefinitionReconstructionError) as mismatch:
        _reconstruct(effective, children={child.id: child})
    assert mismatch.value.reason == "subagent_agent_mismatch"


def test_reconstruction_rejects_tampered_child_and_missing_plugin() -> None:
    effective = _effective(agent_config(), subagents=(_edge("reviewer"),))
    child = _revision()
    effective = _with_children(effective, {child.id: child})
    child_snapshot = effective.child_configs[child.id]
    tampered = child_snapshot.model_copy(
        update={"effective_config": child_snapshot.effective_config.model_copy(update={"instructions": "tampered"})}
    )
    effective = _rehash(effective.model_copy(update={"child_configs": {child.id: tampered}}))
    with pytest.raises(AgentDefinitionReconstructionError, match="effective_config_digest_mismatch"):
        _reconstruct(effective)

    config = agent_config()
    plugin_effective = _effective(config, plugins=(_plugin_selection(),))
    wrong_catalog = HarnessPluginFactoryCatalog(())
    with pytest.raises(AgentDefinitionReconstructionError) as provenance:
        _reconstruct(plugin_effective, catalog=wrong_catalog)
    assert provenance.value.reason == "plugin_factory_missing"


def test_reconstruction_rejects_unknown_and_recursive_output_resources() -> None:
    unknown = _config(
        output_spec={
            "schema": {"type": "object", "properties": {"value": {"$ref": "missing"}}},
        }
    )
    with pytest.raises(AgentDefinitionReconstructionError) as unresolved:
        _reconstruct(_effective(unknown))
    assert unresolved.value.reason == "output_schema_reference_invalid"

    recursive = _config(
        output_spec={
            "schema": {"type": "object", "properties": {"node": {"$ref": "node"}}},
            "resources": {
                "node": {
                    "type": "object",
                    "properties": {"child": {"$ref": "node"}},
                }
            },
        }
    )
    with pytest.raises(AgentDefinitionReconstructionError) as recursion:
        _reconstruct(_effective(recursive))
    assert recursion.value.reason == "output_schema_recursive"


@pytest.mark.parametrize("distribution", ["test-plugin", "wrong-plugin"])
def test_recovery_accepts_compatible_repackaged_factory_without_constructing_plugins(distribution):
    catalog, factory = _catalog(distribution_name=distribution)
    effective = _effective(_config(), plugins=(_plugin_selection(),))
    reconstructor = AgentReconstructor(catalog)
    arguments = dict(
        agent_id=ROOT_AGENT_ID,
        agent_revision_id=ROOT_REVISION_ID,
        effective_config=effective,
        prepared_plugins=_prepared(effective),
        subagent_capability=SubagentCapability(),
    )
    reconstructor.validate(**arguments)
    assert factory.created == []
