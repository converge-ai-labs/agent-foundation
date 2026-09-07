from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from a13n_environment import LocalEnvdEnvironmentProvider
from a13n_harness.capabilities import SubagentOperator
from a13n_harness.capabilities.skills import SkillsCapability
from a13n_harness.plugin_factories import HarnessPluginFactory, HarnessPluginFactoryContext
from a13n_harness.plugins import AbstractHarnessPlugin
from a13n_harness_ui.composition import (
    IMPLICIT_NATIVE_PROFILE,
    PACKAGE_SYSTEM_PROMPT,
    AgentCompositionResolver,
    AgentReconstructor,
    CompositionAcceptanceService,
    ThreadCompositionSelection,
)
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.environment_paths import EnvironmentPathLayout
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
    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())

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


async def test_resolves_release_owned_sandbox_profile_without_configuration_resource(tmp_path: Path) -> None:
    source = await load_harness_ui_configuration(_write_source(tmp_path))
    selection = replace(_selection(), environment_profile_id=SANDBOX_PROFILE_ID)

    composition = AgentCompositionResolver(_catalog()).resolve_run(source, selection)
    reconstructed = EnvironmentSnapshotReconstructor(catalog=_catalog()).reconstruct(composition.environment_profile)

    assert composition.environment_profile.profile_id == SANDBOX_PROFILE_ID
    assert composition.environment_profile.provider_key == LOCAL_ENVD_PROVIDER_KEY
    assert composition.environment_profile.adapter_key == LOCAL_ENVD_ADAPTER_KEY
    assert isinstance(reconstructed.provider, LocalEnvdEnvironmentProvider)
    assert reconstructed.adapter.preserves_host_paths


async def test_generation_validation_rejects_selected_unknown_capability(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text().replace("dynamic_environment", "vendor.missing"))
    source = await load_harness_ui_configuration(path)

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


async def test_global_guidance_and_default_file_context_are_captured_for_root_and_children(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    (tmp_path / "AGENTS.md").write_text("Global instruction revision one")
    source = await load_harness_ui_configuration(path)
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    nodes = [composition.root, *(child.definition for child in composition.root.children)]
    for node in nodes:
        assert "Global instruction revision one" in node.global_guidance[0]
        assert sum(item.capability == "file_context" for item in node.capabilities) == 1
    (tmp_path / "AGENTS.md").write_text("Global instruction revision two")
    refreshed = AgentCompositionResolver(_catalog()).resolve_run(
        await load_harness_ui_configuration(path), _selection()
    )
    assert "revision two" in refreshed.root.global_guidance[0]
    assert "revision one" in composition.root.global_guidance[0]
    assert refreshed.generation_digest != composition.generation_digest


async def test_reconstruction_builds_fresh_graph_and_keeps_root_capability_root_only(tmp_path: Path) -> None:
    source = await load_harness_ui_configuration(_write_source(tmp_path))
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())
    reconstructed = AgentReconstructor(_catalog()).reconstruct(
        composition,
        subagent_operator=_UnusedOperator(),
    )

    assert reconstructed.executable.definition.definition_id == "a13n-harness-ui:agent:agent-assistant"
    assert tuple(reconstructed.executable.subagents) == ("explorer", "agent-reviewer")
    assert reconstructed.executable.definition.agent.model.startswith("a13n-harness-ui:model-")
    assert "a13n.dynamic-environment" in reconstructed.definition_capability_ids
    for child in reconstructed.executable.subagents.values():
        assert child.definition.definition_id != reconstructed.executable.definition.definition_id


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
    composition = AgentCompositionResolver(_catalog()).resolve_run(source, _selection())

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
        assert {(item.resource_kind, item.resource_id) for item in resources} >= {
            ("agent", "agent-assistant"),
            ("model", "model-primary"),
            ("project", "project-main"),
            ("subagent", "subagent-explorer"),
        }


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


async def test_shell_review_captures_and_registers_its_subscription_model(tmp_path: Path) -> None:
    from a13n_harness.capabilities.shell_review import ShellReviewAction, ShellReviewCapability
    from a13n_harness_ui.model_runtime import model_recipe_id

    path = _write_source(tmp_path)
    review_model = tmp_path / "models" / "review.yaml"
    review_model.write_text("""schema_version: "1"
kind: model
id: model-review
name: Review
route: openai-codex:gpt-5.6-luna
authentication: {kind: codex_subscription}
settings: {thinking: low}
""")
    agent = tmp_path / "agents" / "assistant.yaml"
    agent.write_text(
        agent.read_text().replace(
            "harness_plugins: null",
            """  - capability: ShellReviewCapability
    configuration:
      model: model-review
      risk_threshold: high
      on_flagged: approval_required
      on_error: approval_required
harness_plugins: null""",
        )
    )
    source = await load_harness_ui_configuration(path)
    resolver = AgentCompositionResolver(_catalog())
    resolver.validate_generation(source)
    composition = resolver.resolve_run(source, _selection())
    recipe = next(item for item in composition.root.capabilities if item.capability == "ShellReviewCapability")
    assert recipe.model is not None
    assert recipe.model.route == "openai-codex:gpt-5.6-luna"
    assert recipe.model.authentication.kind == "codex_subscription"
    review_model.unlink()
    reconstructed = AgentReconstructor(_catalog()).reconstruct(composition, subagent_operator=_UnusedOperator())
    capability = next(
        item for item in reconstructed.executable.definition.capabilities if isinstance(item, ShellReviewCapability)
    )
    assert capability.model == model_recipe_id(recipe.model)
    assert capability.on_error is ShellReviewAction.APPROVAL_REQUIRED
    assert capability.model_settings["thinking"] == "low"
    assert recipe.model in reconstructed.model_resolver._recipes.values()


async def test_shell_review_rejects_missing_model_resource(tmp_path: Path) -> None:
    path = _write_source(tmp_path)
    agent = tmp_path / "agents" / "assistant.yaml"
    agent.write_text(
        agent.read_text().replace(
            "harness_plugins: null",
            """  - capability: ShellReviewCapability
    configuration: {model: model-missing}
harness_plugins: null""",
        )
    )
    source = await load_harness_ui_configuration(path)
    with pytest.raises(CompositionError) as error:
        AgentCompositionResolver(_catalog()).validate_generation(source)
    assert error.value.code == "capability_model_missing"


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
    legacy_root = composition.root.model_copy(
        update={"system_prompt": None, "instructions": ("Original frozen identity", "Original additions")}
    )
    rebuilt = AgentReconstructor(_catalog()).reconstruct(
        composition.model_copy(update={"root": legacy_root}), subagent_operator=_UnusedOperator()
    )
    assert rebuilt.executable.definition.agent.system_prompt == ["Original frozen identity", "Original additions"]
    assert rebuilt.executable.definition.agent.instructions is None
