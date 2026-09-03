from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from a13n_harness.capabilities import SubagentOperator
from a13n_harness.plugin_factories import HarnessPluginFactory, HarnessPluginFactoryContext
from a13n_harness.plugins import AbstractHarnessPlugin
from a13n_ui.composition import (
    IMPLICIT_NATIVE_PROFILE,
    PACKAGE_SYSTEM_PROMPT,
    AgentCompositionResolver,
    AgentReconstructor,
    CompositionAcceptanceService,
    ThreadCompositionSelection,
)
from a13n_ui.configuration import load_agent_ui_configuration
from a13n_ui.errors import CompositionError
from a13n_ui.extensions import AgentUiExtensionCatalog
from a13n_ui.settings import StorageSettings
from a13n_ui.storage import ObjectKind, open_local_store
from pydantic import BaseModel, ConfigDict, JsonValue

pytestmark = pytest.mark.anyio


class _MemoryConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    capacity: int


class _MemoryPlugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id

    @property
    def plugin_id(self) -> str:
        return self._plugin_id


class _MemoryFactory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls) -> str:
        return "vendor.memory"

    def validate_configuration(self, configuration: Mapping[str, JsonValue]) -> BaseModel:
        return _MemoryConfiguration.model_validate(dict(configuration), strict=True)

    def create_plugin(self, context: HarnessPluginFactoryContext) -> AbstractHarnessPlugin:
        return _MemoryPlugin(context.plugin_id)


class _UnusedOperator(SubagentOperator):
    async def delegate(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not invoked")

    async def info(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not invoked")

    async def wait(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not invoked")

    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not invoked")

    async def cancel(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not invoked")

    async def resume(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not invoked")


def _catalog() -> AgentUiExtensionCatalog:
    return AgentUiExtensionCatalog(host_plugin_factories=(_MemoryFactory(),))


def _write_source(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "a13n-ui.yaml"
    root.write_text(
        'schema_version: "2"\n'
        "defaults:\n"
        "  project: project-main\n"
        "  agent: agent-assistant\n"
        "  harness_plugins: [plugin-memory]\n"
        "  mcp_servers: [mcp-docs]\n"
    )
    files = {
        "models/primary.yaml": """
schema_version: "1"
kind: model
id: model-primary
name: Primary
route: openai:gpt-5
authentication: {kind: api_key, env: OPENAI_API_KEY}
settings: {temperature: 0}
model_configuration: {}
""",
        "extensions/memory.yaml": """
schema_version: "1"
kind: harness_plugin
id: plugin-memory
name: Memory
plugin_key: vendor.memory
configuration: {capacity: 8}
""",
        "mcp/docs.yaml": """
schema_version: "1"
kind: mcp_server
id: mcp-docs
name: Docs
transport:
  url: https://example.test/mcp
  headers:
    Authorization: {env: MCP_TOKEN}
""",
        "agents/assistant.yaml": """
schema_version: "1"
kind: agent
id: agent-assistant
name: Assistant
model: model-primary
instructions: Root authored instructions.
capabilities:
  - capability: dynamic_environment
    configuration: {files_enabled: true, shell_enabled: false}
harness_plugins: null
mcp_servers: null
tools: null
subagents:
  - markdown: subagent-explorer
  - agent: agent-reviewer
""",
        "agents/reviewer.yaml": """
schema_version: "1"
kind: agent
id: agent-reviewer
name: Reviewer
model: model-primary
instructions: Review independently.
harness_plugins: []
mcp_servers: []
subagents: []
""",
        "projects/main.yaml": f"""
schema_version: "1"
kind: project
id: project-main
name: Main
position: 0
roots:
  - path: {workspace.as_posix()}
""",
        "subagents/explorer.md": """---
name: explorer
description: Inspect relevant code.
instruction: Use for repository exploration.
model: inherit
tools: [glob, grep]
---

Report evidence with file paths.
""",
    }
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(exist_ok=True)
        target.write_text(content.lstrip())
    return root


def _selection(source_digest: str = "unused") -> ThreadCompositionSelection:
    del source_digest
    return ThreadCompositionSelection(
        thread_id="thread-1",
        version=1,
        project_id="project-main",
        agent_source_kind="agent",
        agent_source_id="agent-assistant",
        environment_profile_id=IMPLICIT_NATIVE_PROFILE,
        harness_plugin_ids=("plugin-memory",),
        environment_run_extension_ids=(),
        mcp_server_ids=("mcp-docs",),
    )


async def test_resolves_complete_credential_free_run_composition(tmp_path: Path) -> None:
    source = await load_agent_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())

    assert composition.generation_digest == source.source_digest
    assert composition.project_roots == (str((tmp_path / "workspace").resolve()),)
    assert composition.environment_profile.profile_id == IMPLICIT_NATIVE_PROFILE
    assert composition.root.instructions == (PACKAGE_SYSTEM_PROMPT, "Root authored instructions.")
    assert composition.root.model.route == "openai:gpt-5"
    assert composition.root.model.authentication.env == "OPENAI_API_KEY"
    assert composition.root.model.settings == {"temperature": 0.0}
    assert composition.root.harness_plugins[0].configuration == {"capacity": 8}
    assert composition.root.mcp_servers[0].transport.headers["Authorization"].env == "MCP_TOKEN"

    explorer = composition.root.children[0]
    assert explorer.name == "explorer"
    assert explorer.definition.instructions == (PACKAGE_SYSTEM_PROMPT, "Report evidence with file paths.")
    assert explorer.definition.model == composition.root.model
    assert explorer.definition.capabilities == composition.root.capabilities
    assert explorer.definition.tools == ("glob", "grep")

    reviewer = composition.root.children[1]
    assert reviewer.name == "agent-reviewer"
    assert reviewer.definition.harness_plugins == ()
    assert reviewer.definition.mcp_servers == ()
    assert {item.kind for item in composition.dependencies} >= {
        "capability",
        "harness_plugin",
        "environment_provider",
        "environment_adapter",
    }


async def test_generation_validation_rejects_selected_unknown_capability(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text().replace("dynamic_environment", "vendor.missing"))
    source = await load_agent_ui_configuration(path)

    with pytest.raises(CompositionError) as invalid:
        AgentCompositionResolver(_catalog()).validate_generation(source)

    assert invalid.value.code == "capability_catalog_invalid"


@pytest.mark.parametrize(
    ("route", "authentication_kind"),
    (
        ("openai-codex:gpt-5", "codex_subscription"),
        ("grok:grok-code-fast-1", "grok_subscription"),
    ),
)
async def test_subscription_route_reaches_composition_and_native_reconstruction(
    tmp_path: Path,
    route: str,
    authentication_kind: str,
) -> None:
    path = _write_source(tmp_path)
    model = tmp_path / "models/primary.yaml"
    model.write_text(
        model.read_text()
        .replace("route: openai:gpt-5", f"route: {route}")
        .replace(
            "authentication: {kind: api_key, env: OPENAI_API_KEY}",
            f"authentication: {{kind: {authentication_kind}}}",
        )
    )
    source = await load_agent_ui_configuration(path)
    catalog = _catalog()
    resolver = AgentCompositionResolver(catalog)

    resolver.validate_generation(source)
    composition = resolver.resolve_run(source, _selection())
    reconstructed = AgentReconstructor(catalog).reconstruct(
        composition,
        subagent_operator=_UnusedOperator(),
        subscription_sources={},
    )

    assert composition.root.model.route == route
    assert reconstructed.executable.definition.agent.model.startswith("agent-ui:model-")


async def test_reconstruction_builds_fresh_graph_and_keeps_root_capability_root_only(tmp_path: Path) -> None:
    source = await load_agent_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    reconstructed = AgentReconstructor(_catalog()).reconstruct(
        composition,
        subagent_operator=_UnusedOperator(),
    )

    assert reconstructed.executable.definition.definition_id == "agent-ui:agent:agent-assistant"
    assert tuple(reconstructed.executable.subagents) == ("explorer", "agent-reviewer")
    assert reconstructed.executable.definition.agent.model.startswith("agent-ui:model-")
    assert "a13n.dynamic-environment" in reconstructed.definition_capability_ids
    for child in reconstructed.executable.subagents.values():
        assert child.definition.definition_id != reconstructed.executable.definition.definition_id


async def test_acceptance_publishes_complete_generation_before_atomic_selection(tmp_path: Path) -> None:
    source = await load_agent_ui_configuration(_write_source(tmp_path))

    async with open_local_store(StorageSettings(data_root=tmp_path / "state")) as store:
        service = CompositionAcceptanceService(store, AgentCompositionResolver(_catalog()))
        accepted = await service.accept(source, expected_current_digest=None)

        assert accepted.source_digest == source.source_digest
        assert accepted.generation.object_kind is ObjectKind.configuration_generation
        assert await store.configurations.current_digest() == source.source_digest
        assert await service.current() == source
        resources = await store.configurations.resources(source.source_digest)
        assert {(item.resource_kind, item.resource_id) for item in resources} >= {
            ("agent", "agent-assistant"),
            ("model", "model-primary"),
            ("project", "project-main"),
            ("subagent", "subagent-explorer"),
        }
