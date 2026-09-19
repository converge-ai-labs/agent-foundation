"""Installed plugin configuration survives compatible image replacements."""

from collections.abc import Mapping

import pytest
from a13n_harness import AbstractHarnessPlugin
from a13n_harness.capabilities import SubagentCapability
from a13n_harness.plugin_factories import (
    HarnessPluginFactory,
    HarnessPluginFactoryContext,
    build_harness_plugin_factory_catalog,
)
from a13n_service.agents.application import AgentManagement
from a13n_service.agents.domain import AgentRunOverride, CreateAgentRequest, PluginSelection
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.plugin_preparation import (
    PluginSelectionError,
    prepare_agent_plugins,
    validate_plugin_selections,
)
from a13n_service.agents.reconstruction import AgentReconstructor
from a13n_service.agents.resolution import AgentResolver
from a13n_service.models.providers import built_in_model_provider_catalog
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.storage import transaction
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .conftest import WORKSPACE_ID, actor, agent_config


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = "audit"
    limit: int = Field(default=5, ge=1)


class InstalledPlugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str, code_release: str) -> None:
        self._plugin_id = plugin_id
        self.code_release = code_release

    @property
    def plugin_id(self) -> str:
        return self._plugin_id


class InstalledFactory(HarnessPluginFactory):
    def __init__(self, code_release: str = "build-a", configuration_type: type[BaseModel] = Configuration) -> None:
        self.code_release = code_release
        self.configuration_type = configuration_type
        self.created: list[InstalledPlugin] = []

    @classmethod
    def plugin_key(cls) -> str:
        return "test.audit"

    def validate_configuration(self, configuration: Mapping[str, JsonValue]) -> BaseModel:
        return self.configuration_type.model_validate(configuration)

    def create_plugin(self, context: HarnessPluginFactoryContext) -> InstalledPlugin:
        plugin = InstalledPlugin(context.plugin_id, self.code_release)
        self.created.append(plugin)
        return plugin


def _management(sessions):
    models = AcceptedModelSelector(sessions, built_in_model_provider_catalog())
    invocations = AgentInvocationResolver(sessions, models)
    management = AgentManagement(sessions, AgentResolver(sessions, models), invocations, models)
    return management, invocations


@pytest.mark.anyio
async def test_control_preserves_authored_configuration_and_worker_prepares_it(agent_sessions):
    original_factory = InstalledFactory()
    original = build_harness_plugin_factory_catalog(explicit_factories=(original_factory,))
    management, _ = _management(agent_sessions)
    created = await management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="installed-plugin",
        request=CreateAgentRequest(
            name="Audit", config=agent_config(plugins=[{"instance_name": "audit", "plugin_key": "test.audit"}])
        ),
    )
    expected = PluginSelection(instance_name="audit", plugin_key="test.audit", config={"label": "audit", "limit": 5})
    assert created.revision.config.plugins[0].config == {}
    assert "resolved_plugins" not in created.revision.model_dump()
    assert original_factory.created == []

    replacement_factory = InstalledFactory("build-b")
    replacement = build_harness_plugin_factory_catalog(explicit_factories=(replacement_factory,))
    _, invocations = _management(agent_sessions)
    prepared = await invocations.preparation.prepare(actor=actor(), agent_id=created.agent.id)
    async with transaction(agent_sessions) as session:
        frozen = await invocations.freezing.freeze_in_transaction(session, prepared=prepared)
    assert frozen.effective_config.plugins == created.revision.config.plugins
    normalized = prepare_agent_plugins(original, frozen.effective_config)
    assert normalized.plugins == (expected,)
    definition = AgentReconstructor(replacement).reconstruct(
        agent_id=frozen.agent_id,
        agent_revision_id=frozen.agent_revision_id,
        effective_config=frozen.effective_config,
        prepared_plugins=normalized,
        subagent_capability=SubagentCapability(),
    )
    assert definition.plugins[0].code_release == "build-b"
    assert original_factory.created == []
    assert replacement_factory.created == [definition.plugins[0]]
    retained = await management.queries.get_revision(actor=actor(), revision_id=created.revision.id)
    assert retained == created.revision


@pytest.mark.parametrize("configuration", [{"limit": 0}, {"unknown": "value"}])
def test_invalid_configuration_fails_without_constructing_plugins(configuration):
    factory = InstalledFactory()
    catalog = build_harness_plugin_factory_catalog(explicit_factories=(factory,))
    with pytest.raises(PluginSelectionError, match="plugin_factory_configuration_invalid"):
        validate_plugin_selections(
            catalog, (PluginSelection(instance_name="audit", plugin_key="test.audit", config=configuration),)
        )
    assert factory.created == []


def test_recovery_rejects_silent_configuration_field_loss():
    class DropsLimit(BaseModel):
        label: str

    catalog = build_harness_plugin_factory_catalog(explicit_factories=(InstalledFactory("build-b", DropsLimit),))
    retained = PluginSelection(instance_name="audit", plugin_key="test.audit", config={"label": "audit", "limit": 5})
    with pytest.raises(PluginSelectionError, match="plugin_configuration_incompatible"):
        validate_plugin_selections(catalog, (retained,), retained=True)


@pytest.mark.anyio
@pytest.mark.parametrize("configuration", [{}, {"limit": 0}, {"unknown": "value"}])
async def test_control_accepts_plugin_selection_without_installed_code(agent_sessions, configuration):
    management, invocations = _management(agent_sessions)
    created = await management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="missing-installed-plugin",
        request=CreateAgentRequest(
            name="Missing",
            config=agent_config(
                plugins=[{"instance_name": "audit", "plugin_key": "missing.factory", "config": configuration}]
            ),
        ),
    )
    prepared = await invocations.preparation.prepare(actor=actor(), agent_id=created.agent.id)
    async with transaction(agent_sessions) as session:
        frozen = await invocations.freezing.freeze_in_transaction(session, prepared=prepared)
    assert frozen.effective_config.plugins == created.revision.config.plugins
    with pytest.raises(PluginSelectionError, match="plugin_factory_missing"):
        prepare_agent_plugins(build_harness_plugin_factory_catalog(), frozen.effective_config)


def test_recovery_rejects_json_type_changes_even_when_python_values_compare_equal():
    catalog = build_harness_plugin_factory_catalog(explicit_factories=(InstalledFactory(),))
    retained = PluginSelection(instance_name="audit", plugin_key="test.audit", config={"label": "audit", "limit": True})
    with pytest.raises(PluginSelectionError, match="plugin_configuration_incompatible"):
        validate_plugin_selections(catalog, (retained,), retained=True)


@pytest.mark.anyio
async def test_plugin_override_preserves_json_type_change(agent_sessions):
    management, invocations = _management(agent_sessions)
    created = await management.commands.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="plugin-value-type",
        request=CreateAgentRequest(
            name="Value",
            config=agent_config(
                plugins=[{"instance_name": "audit", "plugin_key": "test.audit", "config": {"value": True}}]
            ),
        ),
    )
    prepared = await invocations.preparation.prepare(
        actor=actor(),
        agent_id=created.agent.id,
        config_override=AgentRunOverride(
            plugins=(PluginSelection(instance_name="audit", plugin_key="test.audit", config={"value": 1}),)
        ),
    )
    assert type(prepared.merged.plugins[0].config["value"]) is int
    assert created.revision.config.plugins[0].config["value"] is True
