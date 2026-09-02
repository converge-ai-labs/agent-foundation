from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from a13n_harness import AbstractHarnessPlugin, HarnessBuilder
from a13n_harness.capabilities import SubagentCapability
from a13n_harness.plugin_factories import (
    HarnessPluginFactory,
    HarnessPluginFactoryCatalog,
    HarnessPluginFactoryContext,
    HarnessPluginFactoryRegistration,
)
from a13n_harness.tools.client import ClientToolsCapability
from a13n_service.agent_presets.domain import (
    AgentPresetConfig,
    AgentPresetRevision,
    EffectiveAgentConfig,
    PluginRuntimeMode,
    ResolvedAgentModelConfig,
    ResolvedPluginVersion,
    ResolvedRevisionContent,
    ResolvedSubagentEdge,
    canonical_digest,
)
from a13n_service.agent_presets.reconstruction import (
    AgentDefinitionReconstructionContext,
    AgentDefinitionReconstructionError,
    AgentPresetReconstructor,
)
from a13n_service.iam import PrincipalRef
from a13n_service.model_configs.domain import ModelExecutionSnapshot
from pydantic import JsonValue, RootModel, TypeAdapter
from pydantic_ai.capabilities import Capability
from pydantic_ai.usage import UsageLimits

from .conftest import MODEL_ID, preset_config

NOW = datetime(2026, 9, 2, tzinfo=UTC)
ROOT_PRESET_ID = "ap_1234567890abcdef"
ROOT_REVISION_ID = "apr_1234567890abcdef"
CHILD_PRESET_ID = "ap_child12345678901"
CHILD_REVISION_ID = "apr_child12345678901"
PLUGIN_ID = "plg_1234567890abcdef"
PLUGIN_VERSION_ID = "plgv_1234567890abcdef"


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


def _resolved_model(config: AgentPresetConfig) -> ResolvedAgentModelConfig:
    return ResolvedAgentModelConfig(
        execution=ModelExecutionSnapshot(
            model_id=MODEL_ID,
            provider_type="openai",
            model_name="gpt-5.6-terra",
            base_url=None,
            credential={"source": "none"},
            provider_config={},
            adapter_key="openai",
            adapter_version="1",
        ),
        settings=config.model.settings,
        characteristics=config.model.characteristics,
    )


def _plugin_selection(*, instance_name: str = "audit") -> ResolvedPluginVersion:
    return ResolvedPluginVersion(
        instance_name=instance_name,
        plugin_id=PLUGIN_ID,
        plugin_version_id=PLUGIN_VERSION_ID,
        plugin_key="test.plugin",
        distribution_name="test-plugin",
        distribution_version="1.2.3",
        top_level_package="test_plugin",
        wheel_digest="b" * 64,
        config={"label": instance_name},
    )


def _config(**updates: object) -> AgentPresetConfig:
    payload = preset_config().model_dump(mode="python", by_alias=True)
    payload.update(updates)
    return AgentPresetConfig.model_validate(payload)


def _effective(
    config: AgentPresetConfig,
    *,
    plugins: tuple[ResolvedPluginVersion, ...] = (),
    subagents: tuple[ResolvedSubagentEdge, ...] = (),
) -> EffectiveAgentConfig:
    candidate = EffectiveAgentConfig(
        resolved_model=_resolved_model(config),
        resolved_plugin_versions=plugins,
        runtime_lock_digest="a" * 64,
        resolved_skills=(),
        resolved_connectors=(),
        resolved_environment=None,
        resolved_subagents=subagents,
        instructions=config.instructions,
        input_adapter=config.input_adapter,
        client_tools=config.client_tools,
        output_spec=config.output_spec,
        retries=config.retries,
        secret_requirements=config.secret_requirements,
        asset_publication=config.asset_publication,
        protocol=config.protocol,
        content_digest="0" * 64,
    )
    payload = candidate.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
    return candidate.model_copy(update={"content_digest": canonical_digest(payload)})


def _revision(
    *,
    revision_id: str = CHILD_REVISION_ID,
    preset_id: str = CHILD_PRESET_ID,
    config: AgentPresetConfig | None = None,
    plugins: tuple[ResolvedPluginVersion, ...] = (),
    subagents: tuple[ResolvedSubagentEdge, ...] = (),
) -> AgentPresetRevision:
    selected_config = config or preset_config(instructions="Handle delegated work.")
    resolved = ResolvedRevisionContent(
        resolved_model=_resolved_model(selected_config),
        resolved_plugin_versions=plugins,
        runtime_lock_digest="c" * 64,
        resolved_skills=(),
        resolved_connectors=(),
        resolved_environment=None,
        resolved_subagents=subagents,
    )
    digest = canonical_digest(
        {
            "plugin_runtime_mode": PluginRuntimeMode.on_demand.value,
            "config": selected_config.model_dump(mode="json", by_alias=True),
            "resolved": resolved.model_dump(mode="json", by_alias=True),
        }
    )
    return AgentPresetRevision(
        id=revision_id,
        organization_id="org_1234567890abcdef",
        workspace_id="ws_1234567890abcdef",
        agent_preset_id=preset_id,
        revision_number=1,
        plugin_runtime_mode=PluginRuntimeMode.on_demand,
        config=selected_config,
        resolved_model=resolved.resolved_model,
        resolved_plugin_versions=resolved.resolved_plugin_versions,
        runtime_lock_digest=resolved.runtime_lock_digest,
        resolved_skills=resolved.resolved_skills,
        resolved_connectors=resolved.resolved_connectors,
        resolved_environment=resolved.resolved_environment,
        resolved_subagents=resolved.resolved_subagents,
        content_digest=digest,
        source_revision_id=None,
        created_by=PrincipalRef(principal_type="user", principal_id="usr_1234567890abcdef"),
        created_at=NOW,
    )


def _edge(
    name: str,
    *,
    child_preset_id: str = CHILD_PRESET_ID,
    child_revision_id: str = CHILD_REVISION_ID,
    description: str | None = None,
) -> ResolvedSubagentEdge:
    return ResolvedSubagentEdge.model_validate(
        {
            "name": name,
            "child_agent_preset_id": child_preset_id,
            "child_agent_preset_revision_id": child_revision_id,
            "description": description,
            "context": {"include_task": False, "history": "summary", "task_state": "isolated"},
            "usage_limits": UsageLimits(request_limit=3),
            "environment": {"mode": "none"},
        }
    )


def _reconstruct(
    effective: EffectiveAgentConfig,
    *,
    catalog: HarnessPluginFactoryCatalog | None = None,
    children: Mapping[str, AgentPresetRevision] | None = None,
    capability_provider: Any = None,
):
    reconstructor = AgentPresetReconstructor(
        catalog or HarnessPluginFactoryCatalog(()),
        capability_provider=capability_provider,
    )
    return reconstructor.reconstruct(
        agent_preset_id=ROOT_PRESET_ID,
        agent_preset_revision_id=ROOT_REVISION_ID,
        effective_config=effective,
        child_revisions=children or {},
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
    base_protocol = preset_config().protocol
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
    assert contexts[0].agent_preset_revision_id == ROOT_REVISION_ID
    client_capability = next(item for item in definition.capabilities if isinstance(item, ClientToolsCapability))
    assert client_capability.spec.default_toolsets[0].tools[0].name == "lookup_order"
    schema = TypeAdapter(definition.output_type).json_schema()
    assert schema["properties"]["order"]["properties"]["id"]["type"] == "string"
    assert "$ref" not in str(schema)
    HarnessBuilder(configured_plugins_enabled=False).build(definition)


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
    HarnessBuilder(configured_plugins_enabled=False).build(definition)


def test_reconstructs_each_subagent_occurrence_with_fresh_plugin_instances() -> None:
    catalog, factory = _catalog()
    child_config = _config(
        instructions="Handle the child task.",
        plugins=[
            {
                "mode": "on_demand",
                "instance_name": "audit",
                "plugin_version_id": PLUGIN_VERSION_ID,
                "config": {"label": "audit"},
            }
        ],
    )
    child = _revision(config=child_config, plugins=(_plugin_selection(),))
    effective = _effective(
        preset_config(),
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
    effective = _effective(preset_config()).model_copy(update={"content_digest": "0" * 64})

    with pytest.raises(AgentDefinitionReconstructionError, match="effective_config_digest_mismatch"):
        _reconstruct(effective)


def test_reconstruction_rejects_missing_or_mismatched_child_revision() -> None:
    effective = _effective(preset_config(), subagents=(_edge("reviewer"),))

    with pytest.raises(AgentDefinitionReconstructionError) as missing:
        _reconstruct(effective)
    assert (missing.value.reason, missing.value.path) == ("subagent_revision_missing", "subagents.reviewer")

    child = _revision(preset_id="ap_other12345678901")
    with pytest.raises(AgentDefinitionReconstructionError) as mismatch:
        _reconstruct(effective, children={child.id: child})
    assert mismatch.value.reason == "subagent_preset_mismatch"


def test_reconstruction_rejects_tampered_child_and_plugin_provenance() -> None:
    effective = _effective(preset_config(), subagents=(_edge("reviewer"),))
    child = _revision().model_copy(update={"content_digest": "0" * 64})

    with pytest.raises(AgentDefinitionReconstructionError, match="subagent_revision_digest_mismatch"):
        _reconstruct(effective, children={child.id: child})

    config = preset_config()
    plugin_effective = _effective(config, plugins=(_plugin_selection(),))
    wrong_catalog, _factory = _catalog(distribution_name="other-plugin")
    with pytest.raises(AgentDefinitionReconstructionError) as provenance:
        _reconstruct(plugin_effective, catalog=wrong_catalog)
    assert provenance.value.reason == "plugin_factory_provenance_mismatch"


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
