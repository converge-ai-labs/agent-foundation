from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from a13n_harness.capabilities import SubagentOperator
from a13n_harness.environment import DynamicEnvironmentCapability
from a13n_harness.plugin_factories import (
    HarnessPluginFactory,
    HarnessPluginFactoryContext,
    build_harness_plugin_factory_catalog,
)
from a13n_harness.plugins import AbstractHarnessPlugin
from a13n_ui.capability_runtime import production_portable_capabilities
from a13n_ui.composition import (
    IMPLICIT_NATIVE_PROFILE,
    PACKAGE_SYSTEM_PROMPT,
    AgentCompositionResolver,
    AgentReconstructor,
    CompositionAcceptanceService,
)
from a13n_ui.configuration import load_agent_ui_configuration
from a13n_ui.environment_runtime import WorkspaceBinding
from a13n_ui.errors import CompositionError
from a13n_ui.session_capability import AgentUiSessionCapability
from a13n_ui.settings import StorageSettings
from a13n_ui.storage import ObjectKind, open_local_store
from pydantic import BaseModel, ConfigDict, JsonValue
from pydantic_ai.capabilities import AbstractCapability

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


class _PortableCapability(AbstractCapability[Any]):
    def __init__(self, capability_id: str) -> None:
        self.id = capability_id


class _UnusedOperator(SubagentOperator):
    async def delegate(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not invoked during reconstruction")

    async def info(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not invoked during reconstruction")

    async def wait(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not invoked during reconstruction")

    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not invoked during reconstruction")

    async def cancel(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not invoked during reconstruction")

    async def resume(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("not invoked during reconstruction")


class _MemoryFactory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls) -> str:
        return "vendor.memory"

    def validate_configuration(self, configuration: Mapping[str, JsonValue]) -> BaseModel:
        return _MemoryConfiguration.model_validate(dict(configuration), strict=True)

    def create_plugin(self, context: HarnessPluginFactoryContext) -> AbstractHarnessPlugin:
        return _MemoryPlugin(context.plugin_id)


def _plugin_catalog():
    return build_harness_plugin_factory_catalog(explicit_factories=(_MemoryFactory(),))


async def _write_complete_source(tmp_path: Path) -> Path:
    config = tmp_path / "a13n-ui.yaml"
    config.write_text(
        """
schema_version: "1"
defaults:
  agent: assistant
models:
  primary:
    model: openai:gpt-5
    api_key: {env: OPENAI_API_KEY}
    settings:
      temperature: 0
plugins:
  memory:
    plugin: vendor.memory
    configuration:
      capacity: 8
mcp_servers:
  docs:
    transport:
      url: https://example.test/mcp
      headers:
        Authorization: {env: MCP_TOKEN}
agents:
  assistant:
    model: primary
    instructions: Root authored instructions.
    subagents:
      - markdown: explorer
      - agent: reviewer
  reviewer:
    model: primary
    instructions: Review independently.
    plugins: []
    mcp_servers: []
environments:
  local:
    kind: native
""".lstrip()
    )
    subagents = tmp_path / "subagents"
    subagents.mkdir()
    (subagents / "explorer.md").write_text(
        """---
name: explorer
description: Inspect relevant code.
instruction: Use for repository exploration.
model: inherit
model_settings:
  max_tokens: 2048
tools: [search, files]
optional_tools: [shell]
---

Report evidence with file paths.
"""
    )
    return config


async def test_resolves_complete_agent_graph_and_trusted_locks(tmp_path: Path) -> None:
    source = await load_agent_ui_configuration(await _write_complete_source(tmp_path))
    resolved = AgentCompositionResolver(plugin_catalog=_plugin_catalog()).resolve(source)

    snapshot = resolved.agents["assistant"]
    assert snapshot.harness_release
    assert snapshot.root.instructions == (PACKAGE_SYSTEM_PROMPT, "Root authored instructions.")
    assert snapshot.root.model.route == "openai:gpt-5"
    assert snapshot.root.model.api_key is not None
    assert snapshot.root.model.api_key.env == "OPENAI_API_KEY"
    assert snapshot.root.model.settings == {"temperature": 0.0}
    assert snapshot.root.plugins[0].configuration == {"capacity": 8}
    assert snapshot.root.mcp_servers[0].transport.headers["Authorization"].env == "MCP_TOKEN"

    explorer = snapshot.root.children[0]
    assert explorer.name == "explorer"
    assert explorer.definition.instructions == (
        PACKAGE_SYSTEM_PROMPT,
        "Root authored instructions.",
        "Report evidence with file paths.",
    )
    assert explorer.definition.model.settings == {"temperature": 0.0, "max_tokens": 2048}
    assert explorer.definition.plugins == snapshot.root.plugins
    assert explorer.definition.mcp_servers == snapshot.root.mcp_servers
    assert explorer.definition.tools == ("search", "files")

    reviewer = snapshot.root.children[1].definition
    assert reviewer.instructions == (PACKAGE_SYSTEM_PROMPT, "Review independently.")
    assert reviewer.plugins == ()
    assert reviewer.mcp_servers == ()
    assert tuple(lock.dependency_kind for lock in snapshot.dependencies) == (
        "mcp-adapter",
        "model-adapter",
        "plugin",
    )

    native = resolved.environments["local"]
    assert native.provider_key == "a13n.direct-local"
    assert native.binder_key == "a13n.native-workspace"
    assert resolved.default_environment == IMPLICIT_NATIVE_PROFILE
    assert IMPLICIT_NATIVE_PROFILE in resolved.environments


@pytest.mark.parametrize(
    ("settings", "model_cfg", "code"),
    [
        ({"extra_headers": {"X-Test": "value"}}, {}, "model_settings_invalid"),
        ({"temperature": 0}, {"base_url": "https://example.test"}, "model_configuration_unsupported"),
    ],
)
async def test_rejects_unsupported_model_behavior(
    tmp_path: Path,
    settings: dict[str, JsonValue],
    model_cfg: dict[str, JsonValue],
    code: str,
) -> None:
    config = tmp_path / "a13n-ui.yaml"
    config.write_text(
        """
schema_version: "1"
models:
  primary:
    model: openai:gpt-5
    settings: SETTINGS
    model_cfg: MODEL_CFG
agents:
  assistant:
    model: primary
""".replace("SETTINGS", _inline_json(settings)).replace("MODEL_CFG", _inline_json(model_cfg))
    )
    source = await load_agent_ui_configuration(config)

    with pytest.raises(CompositionError) as invalid:
        AgentCompositionResolver().resolve(source)

    assert invalid.value.code == code


async def test_plugin_package_schema_rejects_unknown_configuration(tmp_path: Path) -> None:
    config = tmp_path / "a13n-ui.yaml"
    config.write_text(
        """
schema_version: "1"
plugins:
  memory:
    plugin: vendor.memory
    configuration: {capacity: 8, unknown: true}
""".lstrip()
    )
    source = await load_agent_ui_configuration(config)

    with pytest.raises(CompositionError) as invalid:
        AgentCompositionResolver(plugin_catalog=_plugin_catalog()).resolve(source)

    assert invalid.value.code == "plugin_configuration_invalid"


async def test_reconstructs_exact_graph_without_resolving_runtime_credentials(tmp_path: Path) -> None:
    source = await load_agent_ui_configuration(await _write_complete_source(tmp_path))
    snapshot = AgentCompositionResolver(plugin_catalog=_plugin_catalog()).resolve(source).agents["assistant"]
    portable = {
        name: (lambda selected=name: _PortableCapability(f"test.{selected}")) for name in ("search", "files", "shell")
    }

    reconstructed = AgentReconstructor(
        plugin_catalog=_plugin_catalog(),
        portable_capabilities=portable,
    ).reconstruct(
        snapshot,
        subagent_operator=_UnusedOperator(),
        root_capabilities=(
            AgentUiSessionCapability(
                service=cast(Any, object()),
                source_session_id="session-root",
                binding=WorkspaceBinding(folders=(tmp_path,)),
            ),
        ),
    )

    assert reconstructed.executable.definition.definition_id == f"agent-ui:{snapshot.root.definition_digest}"
    assert reconstructed.executable.definition.agent.model.startswith("agent-ui:model-")
    assert tuple(reconstructed.executable.subagents) == ("explorer", "reviewer")
    assert "a13n.agent-ui.sessions" in {
        capability.id for capability in reconstructed.executable.definition.capabilities
    }
    for child in reconstructed.executable.subagents.values():
        assert "a13n.agent-ui.sessions" not in {capability.id for capability in child.definition.capabilities}


async def test_markdown_children_narrow_environment_tool_families_exactly(tmp_path: Path) -> None:
    config = tmp_path / "a13n-ui.yaml"
    config.write_text(
        """
schema_version: "1"
models:
  primary:
    model: openai:gpt-5
agents:
  assistant:
    model: primary
    subagents:
      - markdown: files-only
      - markdown: shell-only
""".lstrip()
    )
    subagents = tmp_path / "subagents"
    subagents.mkdir()
    (subagents / "files-only.md").write_text(
        """---
name: files-only
description: Read and write files.
tools: [files]
---

Use only file tools.
"""
    )
    (subagents / "shell-only.md").write_text(
        """---
name: shell-only
description: Run shell commands.
tools: [shell]
---

Use only shell tools.
"""
    )
    source = await load_agent_ui_configuration(config)
    snapshot = AgentCompositionResolver().resolve(source).agents["assistant"]

    reconstructed = AgentReconstructor(
        portable_capabilities=production_portable_capabilities(),
    ).reconstruct(snapshot, subagent_operator=_UnusedOperator())

    files = _dynamic_environment(reconstructed.executable.subagents["files-only"].definition.capabilities)
    shell = _dynamic_environment(reconstructed.executable.subagents["shell-only"].definition.capabilities)
    assert files.configuration.files_enabled
    assert not files.configuration.shell_enabled
    assert not shell.configuration.files_enabled
    assert shell.configuration.shell_enabled


async def test_acceptance_publishes_all_snapshots_before_atomic_selection(tmp_path: Path) -> None:
    source = await load_agent_ui_configuration(await _write_complete_source(tmp_path))
    settings = StorageSettings(data_root=tmp_path / "state")

    async with open_local_store(settings) as store:
        service = CompositionAcceptanceService(
            store,
            AgentCompositionResolver(plugin_catalog=_plugin_catalog()),
        )
        first = await service.accept(source, expected_current_digest=None)
        second = await service.accept(source, expected_current_digest=source.source_digest)

        assert first.snapshots == second.snapshots
        assert await store.configurations.current_digest() == source.source_digest
        selected = await store.configurations.snapshots(source.source_digest)
        assert selected == dict(first.snapshots)
        assert selected[("agent", "assistant")].object_kind is ObjectKind.agent_snapshot
        assert selected[("environment", "local")].object_kind is ObjectKind.environment_snapshot
        assert selected[("environment", IMPLICIT_NATIVE_PROFILE)].object_kind is ObjectKind.environment_snapshot
        agent = await store.objects.read_model(
            selected[("agent", "assistant")],
            type(AgentCompositionResolver(plugin_catalog=_plugin_catalog()).resolve(source).agents["assistant"]),
        )
        assert agent.root.source_name == "assistant"


def _dynamic_environment(capabilities: tuple[AbstractCapability[Any], ...]) -> DynamicEnvironmentCapability:
    matches = [item for item in capabilities if isinstance(item, DynamicEnvironmentCapability)]
    assert len(matches) == 1
    return matches[0]


def _inline_json(value: object) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))
