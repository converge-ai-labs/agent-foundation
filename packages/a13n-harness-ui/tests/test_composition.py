from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest
from a13n_harness.capabilities import SubagentOperator
from a13n_harness.capabilities.skills import SkillsCapability
from a13n_harness.models import SelfHealingModelCapability
from a13n_harness.plugin_factories import HarnessPluginFactory, HarnessPluginFactoryContext
from a13n_harness.plugins import AbstractHarnessPlugin
from a13n_harness.providers.environment.local_envd.provider import LOCAL_ENVD
from a13n_harness_ui.composition import (
    IMPLICIT_NATIVE_PROFILE,
    PACKAGE_SYSTEM_PROMPT,
    AgentCompositionResolver,
    AgentReconstructor,
    CompositionAcceptanceService,
    ThreadCompositionSelection,
)
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.environment_paths import BUILTIN_SKILLS_PATH, EnvironmentPathLayout
from a13n_harness_ui.environment_profiles import SANDBOX_PROFILE_ID
from a13n_harness_ui.environment_runtime import EnvironmentSnapshotReconstructor
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.extensions import LOCAL_ENVD_ADAPTER_KEY, LOCAL_ENVD_PROVIDER_KEY, HarnessUiExtensionCatalog
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import ObjectKind, open_local_store
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


def _catalog() -> HarnessUiExtensionCatalog:
    return HarnessUiExtensionCatalog(host_plugin_factories=(_MemoryFactory(),))


def _write_source(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "a13n-harness-ui.yaml"
    root.write_text(
        'schema_version: "1"\n'
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
    source = await load_harness_ui_configuration(_write_source(tmp_path))
    selection = replace(_selection(), local_roots=(str(tmp_path / "workspace"),))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, selection)

    assert composition.generation_digest == source.source_digest
    assert composition.project_roots == (str((tmp_path / "workspace").resolve()),)
    assert composition.environment_profile.profile_id == IMPLICIT_NATIVE_PROFILE
    assert composition.root.system_prompt == (PACKAGE_SYSTEM_PROMPT,)
    assert composition.root.instructions == ("Root authored instructions.",)
    assert composition.root.model.route == "openai:gpt-5"
    assert composition.root.model.authentication.env == "OPENAI_API_KEY"
    assert composition.root.model.settings == {"temperature": 0.0}
    assert composition.root.harness_plugins[0].configuration == {"capacity": 8}
    assert composition.root.mcp_servers[0].transport.headers["Authorization"].env == "MCP_TOKEN"

    explorer = composition.root.children[0]
    assert explorer.name == "explorer"
    assert explorer.definition.system_prompt == (PACKAGE_SYSTEM_PROMPT,)
    assert explorer.definition.instructions == ("Report evidence with file paths.",)
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


async def test_reconstruction_disables_request_limit_for_root_and_children(tmp_path: Path) -> None:
    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    reconstructed = AgentReconstructor(_catalog()).reconstruct(composition, subagent_operator=_UnusedOperator())
    definition = reconstructed.executable.definition

    assert definition.agent.usage_limits.request_limit is None
    assert len(definition.subagents) == 2
    for child in definition.subagents:
        assert child.agent.agent.usage_limits.request_limit is None


async def test_resolves_release_owned_sandbox_profile_without_configuration_resource(tmp_path: Path) -> None:
    source = await load_harness_ui_configuration(_write_source(tmp_path))
    selection = replace(_selection(), environment_profile_id=SANDBOX_PROFILE_ID)

    composition = AgentCompositionResolver(_catalog()).resolve_run(source, selection)
    reconstructed = EnvironmentSnapshotReconstructor(catalog=_catalog()).reconstruct(composition.environment_profile)

    assert composition.environment_profile.profile_id == SANDBOX_PROFILE_ID
    assert composition.environment_profile.provider_key == LOCAL_ENVD_PROVIDER_KEY
    assert composition.environment_profile.adapter_key == LOCAL_ENVD_ADAPTER_KEY
    assert reconstructed.provider is LOCAL_ENVD
    assert reconstructed.adapter.preserves_host_paths


async def test_generation_validation_skips_selected_unknown_capability(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text().replace("dynamic_environment", "vendor.missing"))
    source = await load_harness_ui_configuration(path)

    resolver = AgentCompositionResolver(_catalog())
    warnings: list[str] = []
    resolver.validate_generation(source, warnings=warnings)
    assert len(warnings) == 1
    assert "agent-assistant" in warnings[0]
    assert "vendor.missing" in warnings[0]
    assert "capability_catalog_invalid" in warnings[0]
    assert source.agents["agent-assistant"].capabilities[0].capability == "vendor.missing"
    composition = resolver.resolve_run(source, _selection())
    assert all(item.capability != "vendor.missing" for item in composition.root.capabilities)
    assert all(item.key != "vendor.missing" for item in composition.dependencies)
    AgentReconstructor(_catalog()).reconstruct(composition, subagent_operator=_UnusedOperator())


async def test_invalid_capabilities_are_skipped_per_entry_without_default_fallback(tmp_path: Path) -> None:
    from a13n_harness_ui.configuration.models import CapabilitySelection

    source = await load_harness_ui_configuration(_write_source(tmp_path))
    agent = source.agents["agent-assistant"]
    selections = (
        CapabilitySelection(capability="NativeTool", configuration={"kind": "web_search"}),
        CapabilitySelection(capability="NativeTool", configuration={"kind": "missing"}),
        CapabilitySelection(capability="NativeTool", configuration={"kind": "code_execution"}),
        CapabilitySelection(capability="working_state", configuration={"unexpected": "private-value"}),
        CapabilitySelection(capability="codeact", configuration={"unexpected": True}),
    )
    source = source.model_copy(
        update={
            "agents": {
                **source.agents,
                agent.id: agent.model_copy(update={"capabilities": selections}),
                "agent-reviewer": source.agents["agent-reviewer"].model_copy(
                    update={
                        "capabilities": (CapabilitySelection(capability="vendor.missing"),),
                    }
                ),
            }
        }
    )
    resolver = AgentCompositionResolver(_catalog())
    warnings: list[str] = []
    resolver.validate_generation(source, warnings=warnings)
    assert len(warnings) == 4
    assert any("agent-reviewer" in warning for warning in warnings)
    assert all("private-value" not in warning for warning in warnings)
    composition = resolver.resolve_run(source, _selection())
    keys = [item.capability for item in composition.root.capabilities]
    assert keys.count("NativeTool") == 2
    assert "working_state" not in keys
    assert "codeact" not in keys
    assert composition.root.children[0].definition.capabilities == composition.root.capabilities
    assert "vendor.missing" not in {item.key for item in composition.dependencies}
    AgentReconstructor(_catalog()).reconstruct(composition, subagent_operator=_UnusedOperator())


async def test_capability_warnings_do_not_change_generation_identity_or_source(tmp_path: Path) -> None:
    from dataclasses import dataclass

    from pydantic_ai.capabilities import AbstractCapability

    @dataclass
    class OptionalCapability(AbstractCapability):
        pass

    path = _write_source(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(
        agent.read_text()
        .replace("dynamic_environment", "OptionalCapability")
        .replace("{files_enabled: true, shell_enabled: false}", "{}")
    )
    original = agent.read_bytes()
    source = await load_harness_ui_configuration(path)
    async with open_local_store(StorageSettings(data_root=tmp_path / "data")) as store:
        service = CompositionAcceptanceService(store, AgentCompositionResolver(_catalog()))
        await service.accept(source, expected_current_digest=None)
        assert len(service.capability_warnings) == 1
        assert await service.current() == source
        # A later process can install the implementation without changing source identity.
        catalog = HarnessUiExtensionCatalog(
            host_capabilities={"OptionalCapability": OptionalCapability}, host_plugin_factories=(_MemoryFactory(),)
        )
        restarted = CompositionAcceptanceService(store, AgentCompositionResolver(catalog))
        await restarted.accept(source, expected_current_digest=source.source_digest)
        assert restarted.capability_warnings == ()
        assert await restarted.current() == source
    assert agent.read_bytes() == original


async def test_unexpected_capability_constructor_failure_is_not_skipped(tmp_path: Path) -> None:
    from dataclasses import dataclass

    from a13n_harness_ui.configuration.models import CapabilitySelection
    from pydantic_ai.capabilities import AbstractCapability

    @dataclass
    class BrokenCapability(AbstractCapability):
        def __post_init__(self) -> None:
            raise RuntimeError("unexpected construction failure")

    source = await load_harness_ui_configuration(_write_source(tmp_path))
    agent = source.agents["agent-assistant"]
    source = source.model_copy(
        update={
            "agents": {
                **source.agents,
                agent.id: agent.model_copy(
                    update={
                        "capabilities": (CapabilitySelection(capability="BrokenCapability"),),
                    }
                ),
            }
        }
    )
    catalog = HarnessUiExtensionCatalog(
        host_capabilities={"BrokenCapability": BrokenCapability}, host_plugin_factories=(_MemoryFactory(),)
    )
    with pytest.raises(RuntimeError, match="unexpected construction failure"):
        AgentCompositionResolver(catalog).validate_generation(source)


async def test_captured_capability_reconstruction_remains_strict(tmp_path: Path) -> None:
    from a13n_harness_ui.composition.models import ResolvedCapabilityRecipe

    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    captured = composition.model_copy(
        update={
            "root": composition.root.model_copy(
                update={
                    "capabilities": (
                        *composition.root.capabilities,
                        ResolvedCapabilityRecipe(capability="vendor.missing", configuration={}),
                    ),
                }
            )
        }
    )
    with pytest.raises(CompositionError) as error:
        AgentReconstructor(_catalog()).reconstruct(captured, subagent_operator=_UnusedOperator())
    assert error.value.code == "capability_catalog_invalid"


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
    source = await load_harness_ui_configuration(path)
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
    assert reconstructed.executable.definition.agent.model.startswith("a13n-harness-ui:model-")
    from a13n_harness.filters import ColdStartFilterConfiguration

    assert reconstructed.executable.definition.agent.cold_start_filter == ColdStartFilterConfiguration()
    assert reconstructed.executable.definition.agent.cold_start_filter.idle_seconds == 3600


async def test_global_guidance_and_default_file_context_are_captured_for_root_and_children(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    (tmp_path / "AGENTS.md").write_text("Global instruction revision one")
    source = await load_harness_ui_configuration(path)
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    nodes = [composition.root, *(child.definition for child in composition.root.children)]
    for node in nodes:
        assert "Global instruction revision one" in node.global_guidance[0]
        for default in ("file_context", "working_state", "user_interaction", "codeact"):
            assert sum(item.capability == default for item in node.capabilities) == 1
    (tmp_path / "AGENTS.md").write_text("Global instruction revision two")
    refreshed = AgentCompositionResolver(_catalog()).resolve_run(
        await load_harness_ui_configuration(path), _selection()
    )
    assert "revision two" in refreshed.root.global_guidance[0]
    assert "revision one" in composition.root.global_guidance[0]
    assert refreshed.generation_digest != composition.generation_digest


@pytest.mark.parametrize("enable_ask_user_question", [False, True])
@pytest.mark.parametrize("enable_codeact", [False, True])
async def test_builtin_tool_switches_are_captured_and_reconstructed(
    tmp_path: Path, enable_ask_user_question: bool, enable_codeact: bool
) -> None:
    path = _write_source(tmp_path)
    path.write_text(
        path.read_text()
        + f"\ntools:\n  enable_ask_user_question: {str(enable_ask_user_question).lower()}\n"
        + f"  enable_codeact: {str(enable_codeact).lower()}\n  interaction_timeout_seconds: 30\n"
    )
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(
        agent.read_text().replace(
            "capabilities:\n",
            "capabilities:\n  - capability: user_interaction\n"
            "  - capability: codeact\n    configuration: {inline: false, max_state_bytes: 2048, max_state_entries: 4}\n",
        )
    )
    source = await load_harness_ui_configuration(path)
    catalog = _catalog()
    composition = AgentCompositionResolver(catalog).resolve_run(source, _selection())
    assert source.document.tools.interaction_timeout_seconds == 30
    for node in (composition.root, *(child.definition for child in composition.root.children)):
        names = [item.capability for item in node.capabilities]
        assert names.count("user_interaction") == int(enable_ask_user_question)
        assert names.count("codeact") == int(enable_codeact)
    rebuilt = AgentReconstructor(catalog).reconstruct(composition, subagent_operator=_UnusedOperator())
    assert ("a13n.user-interaction" in rebuilt.definition_capability_ids) is enable_ask_user_question
    assert ("a13n.codeact" in rebuilt.definition_capability_ids) is enable_codeact
    if enable_codeact:
        recipe = next(item for item in composition.root.capabilities if item.capability == "codeact")
        assert recipe.configuration == {"inline": False, "max_state_bytes": 2048, "max_state_entries": 4}


async def test_reconstruction_builds_fresh_graph_and_keeps_root_capability_root_only(tmp_path: Path) -> None:
    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    reconstructed = AgentReconstructor(_catalog()).reconstruct(
        composition,
        subagent_operator=_UnusedOperator(),
    )

    for executable in (
        reconstructed.executable,
        *(child.executable for child in reconstructed.executable.subagents.values()),
    ):
        leaves = []
        executable._agent.root_capability.apply(leaves.append)
        assert sum(isinstance(capability, SelfHealingModelCapability) for capability in leaves) == 1
    # Builder defaults are execution behavior, not authored capture selections.
    assert "a13n.model.self-healing" not in reconstructed.definition_capability_ids
    assert reconstructed.executable.definition.definition_id == "a13n-harness-ui:agent:agent-assistant"
    assert tuple(reconstructed.executable.subagents) == ("explorer", "agent-reviewer")
    assert reconstructed.executable.definition.agent.model.startswith("a13n-harness-ui:model-")
    assert "a13n.dynamic-environment" in reconstructed.definition_capability_ids
    assert reconstructed.executable.definition.model_recovery.enabled
    assert reconstructed.executable.definition.model_recovery.max_attempts == 5
    from a13n_harness.recovery import DEFAULT_RECOVERY_PROMPT
    from pydantic_ai.messages import TextContent

    prompt = reconstructed.executable.definition.model_recovery.continuation_prompt
    assert len(prompt) == 1
    assert isinstance(prompt[0], TextContent)
    assert prompt[0].content == DEFAULT_RECOVERY_PROMPT
    assert prompt[0].metadata == {"display": False, "source_id": "a13n-harness-ui.model-recovery"}
    for child in reconstructed.executable.subagents.values():
        assert child.definition.definition_id != reconstructed.executable.definition.definition_id
        assert child.definition.model_recovery == reconstructed.executable.definition.model_recovery


@pytest.mark.parametrize("profile", ["native", "sandbox", "custom"])
@pytest.mark.parametrize("tools", [None, (), ("mkdir", "shell_exec")])
async def test_native_default_tool_policy_respects_profile_and_per_node_selection(
    tmp_path: Path, profile: str, tools: tuple[str, ...] | None
) -> None:
    from a13n_harness_ui.composition.reconstruction import _NativeDefaultToolsCapability

    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    environment = composition.environment_profile
    if profile != "native":
        environment = environment.model_copy(
            update={"profile_id": SANDBOX_PROFILE_ID if profile == "sandbox" else "environment-custom"}
        )
    root = composition.root.model_copy(update={"tools": tools})
    composition = composition.model_copy(update={"root": root, "environment_profile": environment})
    rebuilt = AgentReconstructor(_catalog()).reconstruct(composition, subagent_operator=_UnusedOperator())
    nodes = [(composition.root, rebuilt.executable)] + [
        (child.definition, rebuilt.executable.subagents[child.name]) for child in composition.root.children
    ]
    for node, executable in nodes:
        assert any(isinstance(item, _NativeDefaultToolsCapability) for item in executable.definition.capabilities) == (
            profile == "native" and node.tools is None
        )


@pytest.mark.parametrize("shell", ["managed", "absent", "unmanaged", "unavailable"])
async def test_native_default_tools_use_prepared_managed_ids(shell: str) -> None:
    from collections.abc import AsyncIterator

    from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
    from a13n_harness.tools import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
    from a13n_harness_ui.composition.reconstruction import _NativeDefaultToolsCapability
    from pydantic_ai.capabilities import Capability
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo, FunctionModel
    from pydantic_ai.tools import Tool
    from pydantic_ai.toolsets import FunctionToolset

    observed: set[str] = set()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        observed.update(tool.name for tool in info.function_tools)
        yield "done"

    def implementation() -> str:
        return "unused"

    async def unavailable(ctx, tool_def):
        return None

    def managed(name: str, tool_id: str, **kwargs: Any) -> HarnessTool:
        return HarnessTool(
            implementation,
            name=name,
            harness_metadata=HarnessToolMetadata(
                tool_id=tool_id,
                effects=frozenset({"read"}),
                credential_audiences=(),
                idempotency="read_only",
                output_policy=ToolOutputPolicy(max_inline_bytes=512, max_output_bytes=1024),
            ),
            **kwargs,
        )

    mutations = {"make_directory", "remove_file", "copy_file", "move_file"}
    tools = [
        managed("make_directory", "filesystem.mkdir"),
        managed("remove_file", "filesystem.remove"),
        managed("copy_file", "filesystem.copy"),
        managed("move_file", "filesystem.move"),
        managed("view", "filesystem.read"),
        managed("mkdir", "plugin.mkdir"),
        Tool(implementation, name="delete"),
    ]
    if shell in {"managed", "unavailable"}:
        tools.append(
            managed(
                "run_command",
                "environment.shell_exec",
                prepare=unavailable if shell == "unavailable" else None,
            )
        )
    elif shell == "unmanaged":
        tools.append(Tool(implementation, name="shell_exec"))
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(toolsets=[FunctionToolset(tools)]), _NativeDefaultToolsCapability()),
    )
    result = await executable.run("inspect", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    assert {"view", "mkdir", "delete"} <= observed
    assert mutations.intersection(observed) == (set() if shell == "managed" else mutations)


async def test_reconstruction_propagates_all_project_mounts_to_skills(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    workspace_2 = tmp_path / "workspace-2"
    workspace_3 = tmp_path / "workspace-3"
    workspace_2.mkdir()
    workspace_3.mkdir()
    project = tmp_path / "projects" / "main.yaml"
    project.write_text(f"{project.read_text()}  - path: {workspace_2.as_posix()}\n  - path: {workspace_3.as_posix()}\n")
    agent = tmp_path / "agents" / "assistant.yaml"
    agent.write_text(
        agent.read_text().replace(
            "harness_plugins: null",
            "  - capability: skills\n    configuration: {}\nharness_plugins: null",
        )
    )
    source = await load_harness_ui_configuration(path)
    selection = replace(_selection(), local_roots=tuple(root.path for root in source.projects["project-main"].roots))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, selection)

    user_skills = tmp_path / "user-skills"
    reconstructed = AgentReconstructor(
        _catalog(),
        user_skills_root=user_skills,
    ).reconstruct(
        composition,
        subagent_operator=_UnusedOperator(),
    )

    skills = next(
        capability
        for capability in reconstructed.executable.definition.capabilities
        if isinstance(capability, SkillsCapability)
    )
    assert skills.manager.roots == (
        BUILTIN_SKILLS_PATH,
        user_skills.as_posix(),
        f"{workspace_3.as_posix()}/.agents/skills",
        f"{workspace_2.as_posix()}/.agents/skills",
        f"{(tmp_path / 'workspace').as_posix()}/.agents/skills",
    )


def test_environment_path_layout_keeps_virtual_mount_aliases() -> None:
    layout = EnvironmentPathLayout.resolve(
        canonical_host_paths=False,
        project_roots=("/project", "/shared"),
        user_skills_root=Path("/home/example/.agents/skills"),
    )

    assert layout.project_mounts == ("/workspace", "/environment/workspace-2")
    assert layout.user_skills == "/environment/user-skills"
    assert layout.content_plugin_skills == ()


def test_skills_capability_builds_deterministic_multi_mount_sources() -> None:
    selected = HarnessUiExtensionCatalog().capabilities(
        (
            (
                "skills",
                {
                    "roots": [
                        "/workspace/team-skills",
                        "/environment/workspace-2/product-skills",
                    ]
                },
            ),
        ),
        path_layout=EnvironmentPathLayout(
            project_mounts=(
                "/workspace",
                "/environment/workspace-2",
                "/environment/workspace-3",
            ),
            user_skills="/environment/user-skills",
            content_plugin_skills=(
                ("plugin-alpha", "/environment/content-plugin-1"),
                ("plugin-zeta", "/environment/content-plugin-2"),
            ),
        ),
    )[0]

    assert isinstance(selected.capability, SkillsCapability)
    assert selected.capability.manager.roots == (
        BUILTIN_SKILLS_PATH,
        "/environment/user-skills",
        "/environment/content-plugin-1",
        "/environment/content-plugin-2",
        "/environment/workspace-3/.agents/skills",
        "/environment/workspace-2/.agents/skills",
        "/workspace/.agents/skills",
        "/workspace/team-skills",
        "/environment/workspace-2/product-skills",
    )
    assert selected.capability.manager.policy.conflict == "prefer_later"


def test_skills_capability_accepts_windows_native_mount_paths() -> None:
    selected = HarnessUiExtensionCatalog().capabilities(
        (("skills", {}),),
        path_layout=EnvironmentPathLayout(
            project_mounts=("D:/work/project", "//server/share/shared"),
            user_skills="C:/Users/example/.agents/skills",
        ),
    )[0]

    assert isinstance(selected.capability, SkillsCapability)
    assert selected.capability.manager.roots == (
        BUILTIN_SKILLS_PATH,
        "C:/Users/example/.agents/skills",
        "//server/share/shared/.agents/skills",
        "D:/work/project/.agents/skills",
    )


def test_skills_capability_rejects_duplicate_explicit_roots() -> None:
    with pytest.raises(CompositionError) as invalid:
        HarnessUiExtensionCatalog().capabilities(
            (("skills", {"roots": ["/workspace/team-skills", "/workspace/team-skills"]}),)
        )

    assert invalid.value.code == "capability_configuration_invalid"


async def test_acceptance_publishes_complete_generation_before_atomic_selection(tmp_path: Path) -> None:
    source = await load_harness_ui_configuration(_write_source(tmp_path))

    async with open_local_store(StorageSettings(data_root=tmp_path / "state")) as store:
        service = CompositionAcceptanceService(store, AgentCompositionResolver(_catalog()))
        accepted = await service.accept(source, expected_current_digest=None)

        assert accepted.source_digest == source.source_digest
        assert accepted.generation.object_kind is ObjectKind.configuration_generation
        assert await store.configurations.current_digest() == source.source_digest
        assert await service.current() == source
        resources = await store.configurations.resources(source.source_digest)
        assert next(item.name for item in resources if item.resource_id == "project-main") == "Main"
        assert {(item.resource_kind, item.resource_id) for item in resources} >= {
            ("agent", "agent-assistant"),
            ("model", "model-primary"),
            ("project", "project-main"),
            ("subagent", "subagent-explorer"),
        }


@pytest.mark.parametrize(
    "capabilities", list(permutations(("image_understanding", "video_understanding", "audio_understanding")))
)
async def test_reacceptance_preserves_legacy_capability_order_without_rewriting_snapshot(tmp_path, capabilities):
    from a13n_harness import HarnessModelCharacteristics, ModelCapability
    from a13n_harness_ui.errors import StoreIntegrityError

    root = _write_source(tmp_path)
    model_path = tmp_path / "models/primary.yaml"
    model_path.write_text(
        model_path.read_text()
        + "model_characteristics:\n"
        + "  capabilities: [image_understanding, video_understanding, audio_understanding]\n"
    )
    source = await load_harness_ui_configuration(root)
    payload = source.model_dump(mode="json")
    payload["models"]["model-primary"]["model_characteristics"]["capabilities"] = list(capabilities)
    async with open_local_store(StorageSettings(data_root=tmp_path / "state")) as store:
        envelope = await store.objects.publish(
            object_kind=ObjectKind.configuration_generation, object_schema_version="1", payload=payload
        )
        await store.configurations.accept(
            generation_digest=source.source_digest,
            generation=envelope.ref,
            sources=(),
            resources=(),
            expected_current_digest=None,
        )
        service = CompositionAcceptanceService(store, AgentCompositionResolver(_catalog()))
        for _ in range(2):
            accepted = await service.accept(source, expected_current_digest=source.source_digest)
            assert accepted.generation == envelope.ref
            assert await service.current() == source
        assert await store.objects.read(envelope.ref) == envelope
        assert len(await store.objects.references()) == 1

        model = source.models["model-primary"].model_copy(
            update={
                "model_characteristics": HarnessModelCharacteristics(
                    capabilities=frozenset({ModelCapability.IMAGE_UNDERSTANDING})
                )
            }
        )
        changed = source.model_copy(update={"models": {**source.models, model.id: model}})
        with pytest.raises(StoreIntegrityError) as collision:
            await service.accept(changed, expected_current_digest=source.source_digest)
        assert collision.value.code == "configuration_digest_collision"
        assert await store.configurations.reference(source.source_digest) == envelope.ref


async def test_reacceptance_preserves_digest_collisions_and_compare_and_select(tmp_path: Path) -> None:
    from a13n_harness_ui.errors import StoreConflictError, StoreIntegrityError

    root = _write_source(tmp_path)
    source = await load_harness_ui_configuration(root)
    async with open_local_store(StorageSettings(data_root=tmp_path / "state")) as store:
        service = CompositionAcceptanceService(store, AgentCompositionResolver(_catalog()))
        first = await service.accept(source, expected_current_digest=None)
        assert await service.accept(source, expected_current_digest=source.source_digest) == first
        assert len(await store.objects.references()) == 1
        altered = source.model_copy(update={"content_plugin_diagnostics": ("Different content",)})
        with pytest.raises(StoreIntegrityError) as collision:
            await service.accept(altered, expected_current_digest=source.source_digest)
        assert collision.value.code == "configuration_digest_collision"
        assert await service.current() == source
        assert len(await store.objects.references()) == 1
        root.write_text(root.read_text() + "display: {logo: false}\n")
        changed = await load_harness_ui_configuration(root)
        await service.accept(changed, expected_current_digest=source.source_digest)
        with pytest.raises(StoreConflictError) as conflict:
            await service.accept(source, expected_current_digest=source.source_digest)
        assert conflict.value.code == "configuration_selection_conflict"
        assert await service.current() == changed
        assert await store.configurations.reference(source.source_digest) == first.generation


@pytest.mark.parametrize(("retained_value", "candidate_value"), [(False, 0), (1, 1.0)])
async def test_reacceptance_rejects_equal_python_values_with_different_json_types(
    tmp_path: Path, retained_value: bool | int, candidate_value: int | float
) -> None:
    import json

    from a13n_harness_ui.configuration import LoadedHarnessUiConfiguration
    from a13n_harness_ui.errors import StoreIntegrityError

    root = _write_source(tmp_path)
    payload = (await load_harness_ui_configuration(root)).model_dump(mode="json")
    settings = payload["models"]["model-primary"]["settings"]
    settings["provider_option"] = retained_value
    source = LoadedHarnessUiConfiguration.model_validate_json(json.dumps(payload))
    settings["provider_option"] = candidate_value
    candidate = LoadedHarnessUiConfiguration.model_validate_json(json.dumps(payload))
    assert source == candidate  # Python equality alone loses JSON scalar distinctions.
    async with open_local_store(StorageSettings(data_root=tmp_path / "state")) as store:
        service = CompositionAcceptanceService(store, AgentCompositionResolver(_catalog()))
        accepted = await service.accept(source, expected_current_digest=None)
        with pytest.raises(StoreIntegrityError) as collision:
            await service.accept(candidate, expected_current_digest=source.source_digest)
        assert collision.value.code == "configuration_digest_collision"
        retained = await service.current()
        assert retained is not None
        assert type(retained.models["model-primary"].settings["provider_option"]) is type(retained_value)
        assert await store.configurations.reference(source.source_digest) == accepted.generation
        assert len(await store.objects.references()) == 1


def test_plugin_layout_routes_skills_inside_the_full_plugin_mount(tmp_path: Path) -> None:
    plugin = tmp_path / "plugin-reviewer"
    layout = EnvironmentPathLayout.resolve(
        canonical_host_paths=False,
        project_roots=(tmp_path / "project",),
        content_plugins=(
            ("plugin-reviewer", str(plugin), str(plugin / "skills")),
            ("plugin-subagents", str(tmp_path / "plugin-subagents"), None),
        ),
    )
    assert layout.content_plugin_roots == (
        ("plugin-reviewer", "/environment/content-plugin-1"),
        ("plugin-subagents", "/environment/content-plugin-2"),
    )
    assert layout.content_plugin_skills == (("plugin-reviewer", "/environment/content-plugin-1/skills"),)


async def test_missing_markdown_only_blocks_the_agent_that_selects_it(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    source = await load_harness_ui_configuration(path)
    source = source.model_copy(update={"subagents": {}})
    resolver = AgentCompositionResolver(_catalog())
    resolver.validate_generation(source)
    with pytest.raises(CompositionError) as exc:
        resolver.resolve_run(source, _selection())
    assert exc.value.code == "composition_subagent_unavailable"
    # Reviewer has no Markdown dependency; its Run remains resolvable.
    composition = resolver.resolve_run(source, replace(_selection(), agent_source_id="agent-reviewer"))
    assert composition.root.source_id == "agent-reviewer"


@pytest.mark.parametrize("on_error", ["approval_required", "deny", "allow"])
async def test_shell_review_captures_and_registers_its_subscription_model(tmp_path: Path, on_error: str) -> None:
    from a13n_harness.tools import ToolPermissionsCapability
    from a13n_harness_ui.model_runtime import model_recipe_id

    path = _write_source(tmp_path)
    review_model = tmp_path / "models" / "review.yaml"
    review_model.write_text("""schema_version: "1"
kind: model
id: model-review
name: Review
route: openai-codex:gpt-5.6-luna
authentication: {kind: codex_subscription}
settings: {thinking: low, openai_store: false, openai_reasoning_summary: detailed}
""")
    agent = tmp_path / "agents" / "assistant.yaml"
    agent.write_text(
        agent.read_text().replace(
            "harness_plugins: null",
            f"""  - capability: ToolPermissionsCapability
    configuration:
      rules: {{environment.shell_exec: review}}
      review:
        model: model-review
        risk_threshold: high
        on_flagged: approval_required
        on_error: {on_error}
        model_settings: {{openai_reasoning_summary: concise}}
harness_plugins: null""",
        )
    )
    source = await load_harness_ui_configuration(path)
    resolver = AgentCompositionResolver(_catalog())
    resolver.validate_generation(source)
    composition = resolver.resolve_run(source, _selection())
    recipe = next(item for item in composition.root.capabilities if item.capability == "ToolPermissionsCapability")
    assert recipe.model is not None
    assert recipe.model.route == "openai-codex:gpt-5.6-luna"
    assert recipe.model.authentication.kind == "codex_subscription"
    review_model.unlink()
    reconstructed = AgentReconstructor(_catalog()).reconstruct(composition, subagent_operator=_UnusedOperator())
    capability = next(
        item for item in reconstructed.executable.definition.capabilities if isinstance(item, ToolPermissionsCapability)
    )
    assert capability.config.model == model_recipe_id(recipe.model)
    assert capability.config.on_error == on_error
    assert capability.policy.on_flagged == "approval_required"
    assert capability.config.model_settings == {
        "thinking": "low",
        "openai_store": False,
        "openai_reasoning_summary": "concise",
    }
    assert recipe.model.settings == {
        "thinking": "low",
        "openai_store": False,
        "openai_reasoning_summary": "detailed",
    }
    assert recipe.model in reconstructed.model_resolver._recipes.values()


@pytest.mark.parametrize("enabled", [None, False, True])
@pytest.mark.parametrize("mode", ["deny", "ask"])
async def test_invalid_reviewer_never_discards_authored_permissions(
    tmp_path: Path, enabled: bool | None, mode: str
) -> None:
    import yaml

    path = _write_source(tmp_path)
    if enabled is not None:
        root = yaml.safe_load(path.read_text())
        root["security"] = {"shell_review": {"enable": enabled}}
        path.write_text(yaml.safe_dump(root))
    agent_path = tmp_path / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["capabilities"].append(
        {
            "capability": "ToolPermissionsCapability",
            "configuration": {
                "default": mode,
                "rules": {"environment.read": mode},
                "review": {"model": "model-missing"},
            },
        }
    )
    agent_path.write_text(yaml.safe_dump(agent))
    source = await load_harness_ui_configuration(path)
    resolver = AgentCompositionResolver(_catalog())
    warnings: list[str] = []
    with pytest.raises(CompositionError) as validation:
        resolver.validate_generation(source, warnings=warnings)
    assert validation.value.code == "capability_model_missing"
    assert not warnings
    with pytest.raises(CompositionError) as resolution:
        resolver.resolve_run(source, _selection())
    assert resolution.value.code == "capability_model_missing"


async def test_webui_collaboration_is_absent_from_reconstructed_children(tmp_path: Path) -> None:
    from a13n_harness_ui.thread_capability import ThreadCollaborationCapability

    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    controller: Any = object()
    capability = ThreadCollaborationCapability(controller=controller, source_thread_id="thread-1")
    reconstructed = AgentReconstructor(_catalog()).reconstruct(
        composition,
        subagent_operator=_UnusedOperator(),
        root_capabilities=(capability,),
    )
    assert capability in reconstructed.executable.definition.capabilities
    for child in reconstructed.executable.subagents.values():
        assert not any(isinstance(item, ThreadCollaborationCapability) for item in child.definition.capabilities)


@pytest.mark.parametrize("instructions", ["", "   \n", "Reply in Chinese; prioritize short patches."])
async def test_system_prompt_is_always_frozen_separately_from_additions(tmp_path: Path, instructions: str) -> None:
    source = await load_harness_ui_configuration(_write_source(tmp_path))
    root = source.agents["agent-assistant"]
    source = source.model_copy(
        update={"agents": {**source.agents, root.id: root.model_copy(update={"instructions": instructions})}}
    )
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    assert composition.root.system_prompt == (PACKAGE_SYSTEM_PROMPT,)
    assert composition.root.instructions == ((instructions,) if instructions.strip() else ())
    rebuilt = AgentReconstructor(_catalog()).reconstruct(composition, subagent_operator=_UnusedOperator())
    spec = rebuilt.executable.definition.agent
    assert spec.system_prompt == [PACKAGE_SYSTEM_PROMPT]
    assert spec.instructions == ([instructions] if instructions.strip() else [])


async def test_legacy_capture_keeps_original_combined_prompt_without_new_defaults(tmp_path: Path) -> None:
    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    payload = composition.model_dump(mode="json")
    payload["root"].pop("system_prompt")
    payload["root"]["instructions"] = ["Original frozen identity", "Original additions"]
    restored = type(composition).model_validate(payload)
    rebuilt = AgentReconstructor(_catalog()).reconstruct(restored, subagent_operator=_UnusedOperator())
    assert rebuilt.executable.definition.agent.system_prompt == ["Original frozen identity", "Original additions"]
    assert rebuilt.executable.definition.agent.instructions == []


async def test_explicit_notes_opt_out_overrides_the_enabled_default(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    agent_path.write_text(
        agent_path.read_text().replace(
            "capabilities:\n",
            "capabilities:\n  - capability: working_state\n    configuration: {notes_enabled: false}\n",
            1,
        )
    )
    source = await load_harness_ui_configuration(path)
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    working = [item for item in composition.root.capabilities if item.capability == "working_state"]
    assert len(working) == 1 and working[0].configuration["notes_enabled"] is False
    reconstructed = AgentReconstructor(_catalog()).reconstruct(
        composition,
        subagent_operator=_UnusedOperator(),
        subscription_sources={},
    )
    from a13n_harness.capabilities import WorkingStateCapability

    native = [
        item for item in reconstructed.executable.definition.capabilities if isinstance(item, WorkingStateCapability)
    ]
    assert len(native) == 1 and native[0].configuration.notes_enabled is False


@pytest.mark.parametrize("tier", [None, "auto", "default", "flex", "priority"])
@pytest.mark.parametrize(
    "route,tier_key,configured",
    [
        ("openai:gpt-5", "service_tier", "default"),
        ("openai:gpt-5", "openai_service_tier", "priority"),
        ("anthropic:claude-sonnet-4-6", "anthropic_service_tier", "standard_only"),
        ("google:gemini-2.5-pro", "google_cloud_service_tier", "priority_only"),
        ("deepseek:deepseek-chat", "openai_service_tier", "flex"),
    ],
)
async def test_generic_service_tier_override_only_changes_root_and_inherited_children(
    tmp_path: Path, tier, route: str, tier_key: str, configured: str
) -> None:
    from a13n_harness_ui.surfaces import RunModelOverrides

    path = _write_source(tmp_path)
    model = tmp_path / "models/primary.yaml"
    model.write_text(
        model.read_text()
        .replace("route: openai:gpt-5", f"route: {route}")
        .replace("settings: {temperature: 0}", f"settings: {{{tier_key}: {configured}, max_tokens: 32768}}")
    )
    source = await load_harness_ui_configuration(path)
    resolver = AgentCompositionResolver(_catalog())
    original = resolver.resolve_run(source, _selection())
    # Service-tier support does not imply thinking support (deepseek-chat is
    # non-reasoning). Unsupported explicit thinking is tested separately.
    thinking = None if route == "deepseek:deepseek-chat" else "low"
    composition = resolver.resolve_run(
        source, _selection(), model_overrides=RunModelOverrides(service_tier=tier, thinking=thinking)
    )
    assert composition.root.model.route == route
    if tier is None:
        assert composition.root.model.settings[tier_key] == configured
    else:
        assert composition.root.model.settings["service_tier"] == tier
        if tier_key != "service_tier":
            assert tier_key not in composition.root.model.settings
    assert composition.root.model.settings.get("thinking") == thinking
    assert composition.root.model.settings["max_tokens"] == 32768
    assert composition.root.children[0].definition.model == composition.root.model
    assert composition.root.children[1].definition.model == original.root.children[1].definition.model
    assert source.models["model-primary"].settings == {tier_key: configured, "max_tokens": 32768}
    assert resolver.resolve_run(source, _selection()).root == original.root


@pytest.mark.parametrize("mount", ["workspace", "thread-files"])
async def test_full_control_shell_inherits_host_path_and_custom_variables(tmp_path, monkeypatch, mount) -> None:
    import os
    import shlex
    import sys

    from a13n_harness.providers.environment.commands import CommandEnvironment, CommandRequest, ShellCommand
    from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy

    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    selected = EnvironmentSnapshotReconstructor(catalog=_catalog()).reconstruct(composition.environment_profile)
    root = tmp_path / mount
    root.mkdir(exist_ok=True)
    # Resolve Python by basename through an inherited PATH, not an absolute executable.
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("A13N_TEST_TOOL_SETTING", "inherited-test-value")
    (root / "probe.py").write_text(
        "import os\nprint(os.environ.get('A13N_TEST_TOOL_SETTING', 'missing'))\n", encoding="utf-8"
    )
    python = Path(sys.executable).name
    script = f"& '{python}' probe.py" if sys.platform == "win32" else f"{shlex.quote(python)} probe.py"
    environment = await selected.adapter.bind(
        profile=composition.environment_profile,
        root=root,
        state=None,
        provider=selected.provider,
        runtime=None,
    )
    await environment.enter(mount_id=mount)
    try:
        await environment.prepare()
        shell = environment.operations.shell
        assert shell is not None
        request = CommandRequest(
            command=ShellCommand(profile_id="default", script=script),
            output_policy=EnvironmentOutputPolicy(max_inline_bytes=4096, max_output_bytes=65536, overflow="retain"),
        )
        inherited = await shell.exec(request)
        assert inherited.status.exit_code == 0
        assert inherited.output.stdout.inline.strip() == b"inherited-test-value"
        override = await shell.exec(
            request.model_copy(update={"environment": CommandEnvironment(set={"A13N_TEST_TOOL_SETTING": "override"})})
        )
        assert override.output.stdout.inline.strip() == b"override"
        removed = await shell.exec(
            request.model_copy(update={"environment": CommandEnvironment(unset=("A13N_TEST_TOOL_SETTING",))})
        )
        assert removed.output.stdout.inline.strip() == b"missing"
        assert os.environ["A13N_TEST_TOOL_SETTING"] == "inherited-test-value"
        assert environment.dump_state() is None
        assert "inherited-test-value" not in composition.model_dump_json()
        assert "inherited-test-value" not in environment.descriptor.model_dump_json()
    finally:
        await environment.close()


async def test_proxy_groups_capture_enabled_sources_and_markdown_inheritance(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    agent_path = tmp_path / "agents/assistant.yaml"
    agent_path.write_text(
        agent_path.read_text()
        + """
tool_proxy:
  groups:
    knowledge:
      description: Search documents and memory.
      mcp_servers: [mcp-docs]
      harness_plugins: [plugin-memory]
  config: {max_results: 5}
"""
    )
    source = await load_harness_ui_configuration(path)
    resolver = AgentCompositionResolver(_catalog())
    selection = replace(_selection(), mcp_server_ids=())
    composition = resolver.resolve_run(source, selection)
    proxy = composition.root.tool_proxy
    assert proxy is not None
    assert proxy.config.max_results == 5
    assert proxy.groups["knowledge"].mcp_servers == ()
    assert proxy.groups["knowledge"].harness_plugins == ("plugin-memory",)
    assert composition.root.children[0].definition.tool_proxy == proxy
    assert composition.root.children[1].definition.tool_proxy is None
    assert type(composition).model_validate_json(composition.model_dump_json()) == composition
    AgentReconstructor(_catalog()).reconstruct(composition, subagent_operator=_UnusedOperator())
    assert source.agents["agent-assistant"].tool_proxy.groups["knowledge"].mcp_servers == ("mcp-docs",)


@pytest.mark.parametrize("host_mode", ["local", "webui"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("generic_ids", [(), ("mcp-docs",)])
async def test_apps_union_preserves_generic_selection_for_every_child(
    tmp_path: Path, host_mode, enabled: bool, generic_ids: tuple[str, ...]
) -> None:
    import yaml
    from a13n_harness_ui.subagent_operator import _initial_child_configuration
    from a13n_harness_ui.subagent_operator import _selection as child_selection

    path = _write_source(tmp_path)
    document = yaml.safe_load(path.read_text())
    document["webui"] = {"mcp_apps": {"enabled": enabled, "servers": ["mcp-docs", "mcp-app"]}}
    path.write_text(yaml.safe_dump(document))
    (tmp_path / "mcp/app.yaml").write_text(
        "schema_version: '1'\nkind: mcp_server\nid: mcp-app\nname: App\ntransport: {url: 'https://example.test/app'}\n"
    )
    agent_path = tmp_path / "agents/assistant.yaml"
    agent_path.write_text(
        agent_path.read_text()
        + "tool_proxy:\n  groups:\n    apps:\n      description: App tools\n      mcp_servers: [mcp-app]\n"
    )
    # Exercise nested Agent-resource children as well as root Markdown inheritance.
    reviewer_path = tmp_path / "agents/reviewer.yaml"
    reviewer_path.write_text(
        reviewer_path.read_text().replace("subagents: []", "subagents: [{markdown: subagent-explorer}]")
    )
    source = await load_harness_ui_configuration(path)
    resolver = AgentCompositionResolver(_catalog(), host_mode=host_mode)
    composition = resolver.resolve_run(source, replace(_selection(), mcp_server_ids=generic_ids))
    injected = enabled and host_mode == "webui"
    expected = tuple(dict.fromkeys((*generic_ids, *(("mcp-docs", "mcp-app") if injected else ()))))
    root = composition.root
    assert tuple(item.server_id for item in root.mcp_servers) == expected
    assert tuple(item.server_id for item in root.mcp_servers if item.generic_selected) == generic_ids
    assert all(item.apps_enabled == injected for item in root.mcp_servers)
    assert root.tool_proxy is not None
    if injected:
        assert root.tool_proxy.groups["apps"].mcp_servers == ("mcp-app",)
    else:
        assert root.tool_proxy.groups == {}
    assert root.children[0].definition.tool_proxy == root.tool_proxy
    assert root.children[0].definition.tools == ("glob", "grep")
    assert source.agents["agent-assistant"].mcp_servers is None
    assert source.agents["agent-reviewer"].mcp_servers == ()
    assert type(composition).model_validate_json(composition.model_dump_json()) == composition

    for edge in (*root.children, *root.children[1].definition.children):
        inherited = generic_ids if edge is root.children[0] else ()
        node = edge.definition
        assert tuple(item.server_id for item in node.mcp_servers if item.generic_selected) == inherited
        assert tuple(item.server_id for item in node.mcp_servers) == (
            tuple(dict.fromkeys((*inherited, "mcp-docs", "mcp-app"))) if injected else inherited
        )
        configuration = _initial_child_configuration(composition, edge)
        assert configuration.mcp_server_ids == inherited
        # Child admission/resume reapplies the union to its generic sticky selection.
        resumed = resolver.resolve_run(source, child_selection("child", configuration), parent_node=root)
        assert resumed.root.mcp_servers == node.mcp_servers

    # Removing the Apps selection affects future resolution, not captured recipes.
    document["webui"]["mcp_apps"]["servers"] = []
    path.write_text(yaml.safe_dump(document))
    changed = await load_harness_ui_configuration(path)
    for edge in root.children:
        configuration = _initial_child_configuration(composition, edge)
        resumed = resolver.resolve_run(changed, child_selection("child", configuration), parent_node=root)
        assert tuple(item.server_id for item in resumed.root.mcp_servers) == configuration.mcp_server_ids
        assert all(not item.apps_enabled for item in resumed.root.mcp_servers)
    assert tuple(item.server_id for item in root.mcp_servers) == expected


async def test_apps_union_supports_both_full_server_selections(tmp_path: Path) -> None:
    from a13n_harness_ui.configuration.models import McpAppsConfiguration

    source = await load_harness_ui_configuration(_write_source(tmp_path))
    generic_ids = tuple(f"mcp-generic-{index}" for index in range(128))
    app_ids = tuple(f"mcp-app-{index}" for index in range(128))
    template = source.mcp_servers["mcp-docs"]
    source = source.model_copy(
        update={
            "mcp_servers": {
                **source.mcp_servers,
                **{key: template.model_copy(update={"id": key}) for key in (*generic_ids, *app_ids)},
            },
            "document": source.document.model_copy(
                update={
                    "webui": source.document.webui.model_copy(
                        update={
                            "mcp_apps": McpAppsConfiguration(enabled=True, servers=app_ids),
                        }
                    ),
                }
            ),
        }
    )
    composition = AgentCompositionResolver(_catalog(), host_mode="webui").resolve_run(
        source, replace(_selection(), mcp_server_ids=generic_ids)
    )
    assert tuple(item.server_id for item in composition.root.mcp_servers) == (*generic_ids, *app_ids)
    assert type(composition).model_validate_json(composition.model_dump_json()) == composition


async def test_legacy_mcp_recipes_preserve_generic_child_selection(tmp_path: Path) -> None:
    from a13n_harness_ui.subagent_operator import _initial_child_configuration

    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    payload = composition.model_dump_json()
    assert '"generic_selected"' not in payload
    restored = type(composition).model_validate_json(payload)
    assert restored.model_dump_json() == payload
    assert _initial_child_configuration(restored, restored.root.children[0]).mcp_server_ids == ("mcp-docs",)


async def test_legacy_composition_missing_proxy_roundtrips_without_changing_payload(tmp_path: Path) -> None:
    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    payload = composition.model_dump_json()
    assert '"tool_proxy"' not in payload
    restored = type(composition).model_validate_json(payload)
    assert restored.root.tool_proxy is None
    assert restored.model_dump_json() == payload


@pytest.mark.parametrize("agent_mode", ["allow", "deny", "ask", "review"])
async def test_root_shell_review_merges_before_capture_and_preserves_other_rules(
    tmp_path: Path, agent_mode: str
) -> None:
    import yaml
    from a13n_harness.tools import ToolIdentity, ToolPermissionsCapability
    from a13n_harness_ui.composition.models import ResolvedRunComposition

    path = _write_source(tmp_path)
    path.write_text(
        path.read_text()
        + "security:\n  shell_review: {enable: true, model: model-primary, risk_threshold: extra_high}\n"
    )
    agent_path = tmp_path / "agents" / "assistant.yaml"
    authored = yaml.safe_load(agent_path.read_text())
    authored["capabilities"].extend(
        [
            {
                "capability": "ToolPermissionsCapability",
                "configuration": {
                    "default": "allow",
                    "rules": {"environment.shell_exec": agent_mode, "environment.read": "deny", "mcp/docs/*": "ask"},
                    "review": {
                        "model": "model-missing",
                        "risk_threshold": "low",
                        "on_error": "deny",
                        "rules": {
                            "environment.*": {"risk_threshold": "medium", "on_flagged": "deny"},
                            "environment.shell_exec": {"risk_threshold": "high", "on_flagged": "approval_required"},
                            "mcp/docs/*": {"risk_threshold": "medium"},
                        },
                    },
                },
            },
        ]
    )
    agent_path.write_text(yaml.safe_dump(authored))
    source = await load_harness_ui_configuration(path)
    resolver = AgentCompositionResolver(_catalog())
    resolver.validate_generation(source)
    captured = resolver.resolve_run(source, _selection())
    composition = ResolvedRunComposition.model_validate_json(captured.model_dump_json())
    reconstructed = AgentReconstructor(_catalog()).reconstruct(composition, subagent_operator=_UnusedOperator())
    caps = reconstructed.executable.definition.capabilities
    permissions = next(c.permissions for c in caps if isinstance(c, ToolPermissionsCapability))
    review = next(c for c in caps if isinstance(c, ToolPermissionsCapability))
    assert permissions.resolve(ToolIdentity("environment.shell_exec")) == "review"
    assert permissions.resolve(ToolIdentity("environment.read")) == "deny"
    assert permissions.resolve(ToolIdentity("mcp/docs/find")) == "ask"
    assert permissions.resolve(ToolIdentity("web.search")) == "allow"
    assert review.policy.risk_threshold == "low"
    assert review.policy.rules["environment.shell_exec"].risk_threshold == "extra_high"
    assert review.policy.rules["environment.shell_exec"].on_flagged == "approval_required"
    assert review.policy.rules["mcp/docs/*"].risk_threshold == "medium"
    assert review.config.on_error == "deny"
    for child in composition.root.children:
        assert sum(c.capability == "ToolPermissionsCapability" for c in child.definition.capabilities) == 1
    assert source.agents["agent-assistant"].capabilities[-1].configuration["review"]["model"] == "model-missing"


@pytest.mark.parametrize("enabled", [False, True])
async def test_shell_shortcut_omitted_fields_preserve_agent_policy(tmp_path: Path, enabled: bool) -> None:
    import yaml

    path = _write_source(tmp_path)
    path.write_text(path.read_text() + f"security:\n  shell_review: {{enable: {str(enabled).lower()}}}\n")
    agent_path = tmp_path / "agents" / "assistant.yaml"
    authored = yaml.safe_load(agent_path.read_text())
    authored["capabilities"].extend(
        [
            {
                "capability": "ToolPermissionsCapability",
                "configuration": {
                    "rules": {"environment.shell_exec": "review"},
                    "review": {
                        "model": "model-primary",
                        "risk_threshold": "high",
                        "on_error": "deny",
                        "rules": {"environment.shell_exec": {"risk_threshold": "low", "on_flagged": "deny"}},
                    },
                },
            },
        ]
    )
    agent_path.write_text(yaml.safe_dump(authored))
    source = await load_harness_ui_configuration(path)
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    review = next(c for c in composition.root.capabilities if c.capability == "ToolPermissionsCapability")
    assert review.configuration["review"]["model"] == "model-primary"
    assert review.configuration["review"]["risk_threshold"] == "high"
    assert review.configuration["review"]["rules"] == {
        "environment.shell_exec": {"risk_threshold": "low", "on_flagged": "deny"}
    }
    assert review.configuration["review"]["on_error"] == "deny"
    assert any(c.capability == "ToolPermissionsCapability" for c in composition.root.capabilities)


async def test_enabled_shell_shortcut_rejects_missing_model_and_disabled_does_not_inject(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    original = path.read_text()
    path.write_text(original + "security:\n  shell_review: {enable: true, model: model-missing}\n")
    source = await load_harness_ui_configuration(path)
    with pytest.raises(CompositionError, match="available Model"):
        AgentCompositionResolver(_catalog()).validate_generation(source)
    path.write_text(original + "security:\n  shell_review: {enable: false, model: model-missing}\n")
    source = await load_harness_ui_configuration(path)
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    assert not any(c.capability in {"ToolPermissionsCapability"} for c in composition.root.capabilities)


def test_tool_policy_capabilities_are_not_authoring_catalog_choices() -> None:
    catalog = _catalog()
    assert not {"ToolPermissionsCapability"} & {ref.key for ref in catalog.references if ref.kind == "capability"}
    selected = catalog.capabilities((("ToolPermissionsCapability", {"review": {"model": "model-primary"}}),))
    assert len(selected) == 1


async def test_shell_shortcut_fallback_uses_effective_model_and_freezes_it(tmp_path: Path) -> None:
    from a13n_harness_ui.configuration.models import LoadedHarnessUiConfiguration
    from a13n_harness_ui.surfaces import RunModelOverrides

    path = _write_source(tmp_path)
    path.write_text(path.read_text() + "security:\n  shell_review: {enable: true}\n")
    source = await load_harness_ui_configuration(path)
    source = LoadedHarnessUiConfiguration.model_validate_json(source.model_dump_json())
    resolver = AgentCompositionResolver(_catalog())
    resolver.validate_generation(source)
    composition = resolver.resolve_run(source, _selection(), model_overrides=RunModelOverrides(thinking="low"))
    review = next(c for c in composition.root.capabilities if c.capability == "ToolPermissionsCapability")
    assert review.model == composition.root.model
    assert review.model.settings["thinking"] == "low"
    assert review.configuration["review"]["on_flagged"] == "approval_required"
    assert review.configuration["review"]["on_error"] == "allow"
    # Source/Model edits do not affect the captured recipe.
    path.write_text('schema_version: "1"\n')
    assert review.model.settings["thinking"] == "low"


@pytest.mark.parametrize("selector", ["*", "environment.*", "environment.shell_exec"])
async def test_root_shell_threshold_preserves_effective_rule_action(tmp_path: Path, selector: str) -> None:
    import yaml

    path = _write_source(tmp_path)
    path.write_text(path.read_text() + "security:\n  shell_review: {enable: true, risk_threshold: extra_high}\n")
    agent_path = tmp_path / "agents" / "assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["capabilities"].append(
        {
            "capability": "ToolPermissionsCapability",
            "configuration": {
                "review": {
                    "model": "model-primary",
                    "rules": {selector: {"on_flagged": "deny", "risk_threshold": "low"}},
                }
            },
        }
    )
    agent_path.write_text(yaml.safe_dump(agent))
    source = await load_harness_ui_configuration(path)
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    review = next(c for c in composition.root.capabilities if c.capability == "ToolPermissionsCapability")
    assert review.configuration["review"]["rules"]["environment.shell_exec"] == {
        "on_flagged": "deny",
        "risk_threshold": "extra_high",
    }


@pytest.mark.parametrize(
    "shortcut",
    [{"enable": "true"}, {"enable": True, "risk_threshold": "critical"}, {"enable": True, "model": "agent-review"}],
)
def test_shell_shortcut_validates_known_fields(shortcut: dict[str, object]) -> None:
    from a13n_harness_ui.configuration.models import HarnessUiDocument
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        HarnessUiDocument.model_validate({"security": {"shell_review": shortcut}})


@pytest.mark.parametrize("on_error", ["allow", "deny", "approval_required"])
@pytest.mark.parametrize("on_flagged", ["deny", "approval_required"])
async def test_shell_shortcut_actions_override_shell_rule_and_preserve_other_tools(
    tmp_path: Path, on_error: str, on_flagged: str
) -> None:
    import yaml

    path = _write_source(tmp_path)
    root = yaml.safe_load(path.read_text())
    root["security"] = {"shell_review": {"enable": True, "on_error": on_error, "on_flagged": on_flagged}}
    path.write_text(yaml.safe_dump(root))
    agent_path = tmp_path / "agents/assistant.yaml"
    agent = yaml.safe_load(agent_path.read_text())
    agent["capabilities"].append(
        {
            "capability": "ToolPermissionsCapability",
            "configuration": {
                "rules": {"environment.read": "deny"},
                "review": {
                    "model": "model-primary",
                    "on_error": "deny",
                    "on_flagged": "deny",
                    "rules": {
                        "environment.*": {"risk_threshold": "high", "on_flagged": "deny"},
                        "mcp/docs/*": {"risk_threshold": "low", "on_flagged": "approval_required"},
                    },
                },
            },
        }
    )
    agent_path.write_text(yaml.safe_dump(agent))
    source = await load_harness_ui_configuration(path)
    captured = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    policies = [c for c in captured.root.capabilities if c.capability == "ToolPermissionsCapability"]
    assert len(policies) == 1
    config = policies[0].configuration
    assert config["rules"] == {"environment.read": "deny", "environment.shell_exec": "review"}
    review = config["review"]
    assert review["on_error"] == on_error
    assert review["on_flagged"] == "deny"  # The root shortcut changes the shell action, not the global action.
    assert review["rules"]["environment.shell_exec"] == {"risk_threshold": "high", "on_flagged": on_flagged}
    assert review["rules"]["mcp/docs/*"] == {"risk_threshold": "low", "on_flagged": "approval_required"}


@pytest.mark.parametrize("field,value", [("on_flagged", "allow"), ("on_flagged", "skip"), ("on_error", "skip")])
def test_shell_shortcut_rejects_unsupported_actions(field: str, value: str) -> None:
    from a13n_harness_ui.configuration.models import HarnessUiDocument
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        HarnessUiDocument.model_validate({"security": {"shell_review": {"enable": True, field: value}}})


@pytest.mark.parametrize("header", ["x-session-id", "X-SESSION-ID"])
async def test_auxiliary_review_settings_cannot_override_managed_affinity(tmp_path: Path, header: str) -> None:
    from a13n_harness_ui.configuration.models import CapabilitySelection

    source = await load_harness_ui_configuration(_write_source(tmp_path))
    agent = source.agents["agent-assistant"]
    model = source.models["model-primary"]
    source = source.model_copy(
        update={
            "models": {
                **source.models,
                model.id: model.model_copy(
                    update={
                        "model_configuration": {"session_affinity_header": "x-session-id"},
                    }
                ),
            },
            "agents": {
                **source.agents,
                agent.id: agent.model_copy(
                    update={
                        "capabilities": (
                            CapabilitySelection(
                                capability="ToolPermissionsCapability",
                                configuration={
                                    "review": {
                                        "model": model.id,
                                        "model_settings": {"extra_headers": {header: "fixed-review-thread"}},
                                    },
                                },
                            ),
                        ),
                    }
                ),
            },
        }
    )
    resolver = AgentCompositionResolver(_catalog())
    with pytest.raises(CompositionError) as error:
        resolver.resolve_run(source, _selection())
    assert error.value.code == "model_configuration_unsupported"


@pytest.mark.parametrize(
    "route,key,on,off,fast",
    [
        (route, key, on, off, fast)
        for route, key, on, off in [
            ("openai:gpt-5", "service_tier", "priority", "default"),
            ("anthropic:claude-opus-4-8", "anthropic_speed", "fast", "standard"),
            ("openai-codex:gpt-6-astra", "service_tier", "ultrafast", "default"),
        ]
        for fast in (True, False, None)
    ]
    + [("openai-codex:gpt-6-astra", "service_tier", "priority", "default", "ultrafast")],
)
async def test_fast_capture_preserves_resources_and_independent_children(tmp_path, fast, route, key, on, off):
    from a13n_harness_ui.configuration_inspection import captured_configuration
    from a13n_harness_ui.surfaces import RunModelOverrides

    path = _write_source(tmp_path)
    model = tmp_path / "models/primary.yaml"
    model.write_text(
        model.read_text()
        .replace("route: openai:gpt-5", f"route: {route}")
        .replace("settings: {temperature: 0}", f"settings: {{{key}: {on}}}")
        .replace(
            "authentication: {kind: api_key, env: OPENAI_API_KEY}",
            "authentication: {kind: codex_subscription}"
            if route.startswith("openai-codex:")
            else "authentication: {kind: api_key, env: OPENAI_API_KEY}",
        )
    )
    source = await load_harness_ui_configuration(path)
    resolver = AgentCompositionResolver(_catalog())
    original = resolver.resolve_run(source, _selection())
    composition = resolver.resolve_run(source, _selection(), model_overrides=RunModelOverrides(fast=fast))
    expected = (
        "ultrafast"
        if fast == "ultrafast"
        else off
        if fast is False
        else ("priority" if route.startswith("openai-codex:") and fast is True else on)
    )
    assert composition.root.model.settings[key] == expected
    assert composition.root.children[0].definition.model == composition.root.model
    assert composition.root.children[1].definition.model == original.root.children[1].definition.model
    assert source.models["model-primary"].settings[key] == on
    assert resolver.resolve_run(source, _selection()).root == original.root
    view = captured_configuration("capture", composition)
    assert view.agent.fast == ("ultrafast" if expected == "ultrafast" else "off" if fast is False else "on")


@pytest.mark.parametrize("mode", [None, "standard", "pro"])
async def test_reasoning_mode_capture_preserves_model_and_independent_children(tmp_path, mode):
    from a13n_harness_ui.configuration_inspection import captured_configuration
    from a13n_harness_ui.surfaces import RunModelOverrides

    path = _write_source(tmp_path)
    model = tmp_path / "models/primary.yaml"
    model.write_text(
        model.read_text()
        .replace("route: openai:gpt-5", "route: openai:gpt-5.6-sol")
        .replace(
            "settings: {temperature: 0}",
            "settings: {openai_reasoning_mode: pro, openai_reasoning_summary: detailed}",
        )
    )
    original_bytes = model.read_bytes()
    source = await load_harness_ui_configuration(path)
    resolver = AgentCompositionResolver(_catalog())
    original = resolver.resolve_run(source, _selection())
    composition = resolver.resolve_run(
        source, _selection(), model_overrides=RunModelOverrides(reasoning_mode=mode, thinking="low", fast=False)
    )
    settings = composition.root.model.settings
    assert settings["openai_reasoning_mode"] == (mode or "pro")
    assert settings["openai_reasoning_summary"] == "detailed"
    assert settings["service_tier"] == "default"
    assert composition.root.children[0].definition.model == composition.root.model
    assert composition.root.children[1].definition.model == original.root.children[1].definition.model
    assert source.models["model-primary"].settings["openai_reasoning_mode"] == "pro"
    assert model.read_bytes() == original_bytes
    assert captured_configuration("capture", composition).agent.reasoning_mode == (mode or "pro")


async def test_run_configuration_is_captured_for_root_and_children(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    with path.open("a") as stream:
        stream.write(
            "run_configuration:\n  allowed_hosts: [EXAMPLE.com.]\n  extensions:\n    example.filter: {image: keep}\n"
        )
    source = await load_harness_ui_configuration(path)
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    captured = type(composition).model_validate_json(composition.model_dump_json())
    assert captured.run_configuration.allowed_hosts == {"example.com"}
    reconstructor = AgentReconstructor(_catalog())
    root = reconstructor.reconstruct(captured, subagent_operator=_UnusedOperator())
    child_composition = captured.model_copy(update={"root": captured.root.children[0].definition})
    child = reconstructor.reconstruct(child_composition, subagent_operator=_UnusedOperator())
    assert root.run_configuration == captured.run_configuration
    assert child.run_configuration == captured.run_configuration
    assert root.model_resolver.configuration == child.model_resolver.configuration == captured.run_configuration


@pytest.mark.parametrize("policy", [None, {}, {"support_gif": False, "max_images": 3}])
async def test_image_input_policy_is_captured_per_model_and_reconstructed(tmp_path, policy):
    import yaml
    from a13n_harness import ImageInputPolicy
    from a13n_harness_ui.composition.models import ResolvedRunComposition

    root = _write_source(tmp_path)
    path = tmp_path / "models/primary.yaml"
    document = yaml.safe_load(path.read_text())
    document["model_characteristics"] = {"image_input": policy}
    path.write_text(yaml.safe_dump(document))
    document["id"] = "model-child"
    document["model_characteristics"] = {"image_input": {"max_images": 0}}
    (tmp_path / "models/child.yaml").write_text(yaml.safe_dump(document))
    child = tmp_path / "agents/reviewer.yaml"
    child.write_text(child.read_text().replace("model-primary", "model-child"))
    source = await load_harness_ui_configuration(root)
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    encoded = composition.model_dump_json()
    restored = ResolvedRunComposition.model_validate_json(encoded)
    expected = None if policy is None else ImageInputPolicy.model_validate(policy)
    assert restored.root.model.model_characteristics.image_input == expected
    source.models["model-primary"] = source.models["model-child"]
    rebuilt = AgentReconstructor(_catalog(), instrumentation=None).reconstruct(
        restored, subagent_operator=_UnusedOperator()
    )
    assert rebuilt.executable.definition.agent.model_characteristics.image_input == expected
    children = {child.name: child for child in rebuilt.executable.definition.subagents}
    assert children["explorer"].agent.agent.model_characteristics.image_input == expected
    assert children["agent-reviewer"].agent.agent.model_characteristics.image_input == ImageInputPolicy(max_images=0)
    assert restored.model_dump_json() == encoded


def test_legacy_model_recipe_retains_canonical_bytes_and_identity():
    import hashlib
    import json

    from a13n_harness import ImageInputPolicy
    from a13n_harness_ui.composition.models import ResolvedModelRecipe
    from a13n_harness_ui.model_runtime import model_recipe_id

    legacy = {
        "model_id": "model-primary",
        "route": "openai:gpt-5",
        "authentication": {"kind": "api_key", "env": "OPENAI_API_KEY", "credential_ref": None},
        "settings": {},
        "model_configuration": {},
        "model_characteristics": {
            "capabilities": ["image_understanding"],
            "context_window_tokens": 32000,
            "proactive_context_management_threshold": 0.8,
            "compact_threshold": 0.9,
        },
    }
    encoded = json.dumps(legacy, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    recipe = ResolvedModelRecipe.model_validate_json(encoded)
    assert recipe.model_characteristics.image_input == ImageInputPolicy()
    assert recipe.model_dump(mode="json") == legacy
    assert model_recipe_id(recipe) == f"a13n-harness-ui:model-{hashlib.sha256(encoded.encode()).hexdigest()[:24]}"
    explicit = recipe.model_copy(
        update={"model_characteristics": recipe.model_characteristics.model_copy(update={"image_input": None})}
    )
    assert explicit.model_dump(mode="json")["model_characteristics"]["image_input"] is None
    assert model_recipe_id(explicit) != model_recipe_id(recipe)
