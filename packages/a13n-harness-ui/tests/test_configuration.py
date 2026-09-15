from __future__ import annotations

import json
from pathlib import Path

import a13n_harness_ui.configuration.loader as configuration_loader
import pytest
from a13n_harness_ui.configuration import load_harness_ui_configuration
from a13n_harness_ui.errors import ConfigurationError

pytestmark = pytest.mark.anyio


def _write_source_tree(
    tmp_path: Path,
    *,
    root: str = 'schema_version: "1"\n',
    resources: dict[str, str] | None = None,
) -> Path:
    config = tmp_path / "a13n-harness-ui.yaml"
    config.write_text(root.lstrip())
    for relative_path, content in (resources or {}).items():
        target = tmp_path / relative_path
        target.parent.mkdir(exist_ok=True)
        target.write_text(content.lstrip())
    return config


async def test_native_configuration_payloads_preserve_keys_values_and_whitespace(tmp_path: Path) -> None:
    payload = {
        "future_option": {"items": [1, True, None, "  keep  "]},
        "schema": {"properties": {"password": {"type": "string"}, "api_key": {"type": "string"}}},
        "stop_sequences": ["\n", "  END  "],
    }
    resources = {
        "models/native.yaml": {
            "kind": "model",
            "id": "model-native",
            "name": "Native",
            "route": "openai:gpt-5",
            "authentication": {"kind": "api_key", "env": "TEST_KEY"},
            "settings": payload,
        },
        "agents/native.yaml": {
            "kind": "agent",
            "id": "agent-native",
            "name": "Native",
            "model": "model-native",
            "instructions": "  exact instruction\n",
            "capabilities": [{"capability": "Example", "configuration": payload}],
        },
        "extensions/plugin.yaml": {
            "kind": "harness_plugin",
            "id": "plugin-native",
            "name": "Native",
            "plugin_key": "example.plugin",
            "configuration": payload,
        },
        "extensions/environment.yaml": {
            "kind": "environment_profile",
            "id": "environment-custom",
            "name": "Native",
            "provider_key": "example.provider",
            "provider_schema_version": "1",
            "provider_configuration": payload,
            "adapter_key": "example.adapter",
            "adapter_configuration": payload,
        },
        "extensions/run.yaml": {
            "kind": "environment_run_extension",
            "id": "extension-native",
            "name": "Native",
            "extension_key": "example.extension",
            "configuration": payload,
        },
        "mcp/native.yaml": {
            "kind": "mcp_server",
            "id": "mcp-native",
            "name": "Native",
            "transport": {"command": "example", "arguments": ["  exact argument  "]},
        },
    }
    path = _write_source_tree(
        tmp_path, resources={key: json.dumps({"schema_version": "1", **value}) for key, value in resources.items()}
    )
    loaded = await load_harness_ui_configuration(path)
    assert loaded.models["model-native"].settings == payload
    assert loaded.agents["agent-native"].capabilities[0].configuration == payload
    assert loaded.agents["agent-native"].instructions == "  exact instruction\n"
    assert loaded.harness_plugins["plugin-native"].configuration == payload
    assert loaded.environment_profiles["environment-custom"].provider_configuration == payload
    assert loaded.environment_profiles["environment-custom"].adapter_configuration == payload
    assert loaded.environment_run_extensions["extension-native"].configuration == payload
    assert loaded.mcp_servers["mcp-native"].transport.arguments == ("  exact argument  ",)
    # Saved generations must retain the same payload, not just the first parse.
    restored = type(loaded).model_validate_json(loaded.model_dump_json())
    assert restored == loaded


async def test_root_defaults_enable_codeact_and_match_empty_onboarding(tmp_path: Path) -> None:
    loaded = await load_harness_ui_configuration(_write_source_tree(tmp_path))
    empty = configuration_loader.empty_harness_ui_configuration()
    assert loaded.document == empty.document
    assert empty.document.schema_version == "1"
    assert empty.document.tools.model_dump() == {
        "enable_ask_user_question": True,
        "interaction_timeout_seconds": 120,
        "enable_codeact": True,
    }
    assert empty.sources[0].content == 'schema_version: "1"\n'


@pytest.mark.parametrize("setting", ["enable_user_input: false", "user_input_timeout_seconds: 30"])
async def test_rejects_obsolete_question_tool_settings(tmp_path: Path, setting: str) -> None:
    config = _write_source_tree(tmp_path, root=f'schema_version: "1"\ntools:\n  {setting}\n')
    with pytest.raises(ConfigurationError) as invalid:
        await load_harness_ui_configuration(config)
    assert invalid.value.code == "settings_invalid"


@pytest.mark.parametrize("version", ["2", "3"])
async def test_rejects_unsupported_root_schema_version(tmp_path: Path, version: str) -> None:
    config = _write_source_tree(tmp_path, root=f'schema_version: "{version}"\n')
    with pytest.raises(ConfigurationError) as invalid:
        await load_harness_ui_configuration(config)
    assert invalid.value.code == "settings_invalid"


async def test_global_agents_guidance_is_generation_owned_and_only_loads_agents_md(tmp_path: Path) -> None:
    config = _write_source_tree(tmp_path)
    (tmp_path / "RULES.md").write_text("Ignored legacy filename")
    (tmp_path / "AGENTS.override.md").write_text("Ignored override filename")
    empty = await load_harness_ui_configuration(config)
    assert empty.global_guidance == ()
    (tmp_path / "AGENTS.md").write_text("Global defaults")
    baseline = await load_harness_ui_configuration(config)
    assert "Global defaults" in baseline.global_guidance[0]
    assert baseline.source_digest != empty.source_digest
    assert baseline.source("AGENTS.md").resource_kind == "instructions"
    before = await configuration_loader.configuration_tree_fingerprint(config)
    (tmp_path / "AGENTS.md").write_text("Updated global guidance")
    assert await configuration_loader.configuration_tree_fingerprint(config) != before
    updated = await load_harness_ui_configuration(config)
    assert "Updated global guidance" in updated.global_guidance[0]
    assert "Global defaults" in baseline.global_guidance[0]
    assert updated.source_digest != baseline.source_digest
    (tmp_path / "AGENTS.md").unlink()
    removed = await load_harness_ui_configuration(config)
    assert removed.global_guidance == ()
    assert removed.source_digest == empty.source_digest


async def test_loads_multi_file_resources_and_canonical_markdown_set(tmp_path: Path) -> None:
    config = _write_source_tree(
        tmp_path,
        root="""
schema_version: "1"
process:
  log_level: debug
defaults:
  agent: agent-assistant
  harness_plugins: [plugin-memory]
  mcp_servers: [mcp-github]
""",
        resources={
            "models/primary.yaml": """
schema_version: "1"
kind: model
id: model-primary
name: Primary
route: openai:gpt-5
authentication:
  kind: api_key
  env: OPENAI_API_KEY
settings:
  temperature: 0
model_configuration: {}
""",
            "extensions/memory.yaml": """
schema_version: "1"
kind: harness_plugin
id: plugin-memory
name: Memory
plugin_key: vendor.memory
configuration: {}
""",
            "mcp/github.yaml": """
schema_version: "1"
kind: mcp_server
id: mcp-github
name: GitHub
transport:
  command: npx
  arguments: [-y, server-github]
""",
            "agents/assistant.yaml": """
schema_version: "1"
kind: agent
id: agent-assistant
name: Assistant
model: model-primary
harness_plugins: null
mcp_servers: []
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
harness_plugins: []
""",
            "subagents/explorer.md": """---
name: explorer
description: Inspect an unfamiliar codebase.
instruction: Use for focused repository exploration.
tools: search, files
---

Inspect the relevant code and report evidence.
""",
        },
    )
    (tmp_path / "subagents/README.md").write_text("ignored\n")
    nested = tmp_path / "subagents/nested"
    nested.mkdir()
    (nested / "ignored.md").write_text("not discovered\n")

    loaded = await load_harness_ui_configuration(config)

    assert loaded.document.schema_version == "1"
    assert loaded.document.process.log_level == "DEBUG"
    assert set(loaded.models) == {"model-primary"}
    assert set(loaded.harness_plugins) == {"plugin-memory"}
    assert set(loaded.mcp_servers) == {"mcp-github"}
    assert set(loaded.agents) == {"agent-assistant", "agent-reviewer"}
    assistant = loaded.agents["agent-assistant"]
    assert loaded.selected_plugins(assistant) == ("plugin-memory",)
    assert loaded.selected_mcp_servers(assistant) == ()
    assert len(loaded.subagents) == 4
    explorer = loaded.markdown("subagent-explorer")
    assert explorer.tools == ("search", "files")
    assert explorer.body == "Inspect the relevant code and report evidence."
    assert {source.relative_path for source in loaded.sources} == {
        "a13n-harness-ui.yaml",
        "agents/assistant.yaml",
        "agents/reviewer.yaml",
        "extensions/memory.yaml",
        "mcp/github.yaml",
        "models/primary.yaml",
        "subagents/explorer.md",
        "built-in-subagents/code-reviewer.md",
        "built-in-subagents/executor.md",
        "built-in-subagents/explorer.md",
    }
    assert loaded.source_digest != loaded.root_digest


@pytest.mark.parametrize(
    ("root", "resources", "error_code"),
    [
        (
            'schema_version: "1"\nprocess: {log_level: true}\n',
            {},
            "settings_invalid",
        ),
        (
            'schema_version: "1"\n',
            {
                "models/primary.yaml": """
schema_version: "1"
kind: model
id: model-primary
name: Primary
route: openai:gpt-5
authentication: {kind: api_key, api_key: literal-secret}
settings: {}
"""
            },
            "configuration_resource_invalid",
        ),
        (
            'schema_version: "1"\n',
            {
                "models/primary.yaml": """
schema_version: "1"
kind: model
id: model-primary
name: Primary
route: openai:gpt-5
authentication: {kind: api_key, env: OPENAI_API_KEY}
""",
                "agents/first.yaml": """
schema_version: "1"
kind: agent
id: agent-first
name: First
model: model-primary
subagents: [{agent: agent-second}]
""",
                "agents/second.yaml": """
schema_version: "1"
kind: agent
id: agent-second
name: Second
model: model-primary
subagents: [{agent: agent-first}]
""",
            },
            "configuration_invalid",
        ),
    ],
)
async def test_rejects_invalid_configuration_tree(
    tmp_path: Path,
    root: str,
    resources: dict[str, str],
    error_code: str,
) -> None:
    config = _write_source_tree(tmp_path, root=root, resources=resources)

    with pytest.raises(ConfigurationError) as invalid:
        await load_harness_ui_configuration(config)

    assert invalid.value.code == error_code
    assert invalid.value.details["validation_error_count"] >= 1


async def test_missing_markdown_reference_is_deferred_to_run_resolution(tmp_path: Path) -> None:
    config = _write_source_tree(
        tmp_path,
        resources={
            "models/primary.yaml": """
schema_version: "1"
kind: model
id: model-primary
name: Primary
route: openai:gpt-5
authentication: {kind: api_key, env: OPENAI_API_KEY}
""",
            "agents/assistant.yaml": """
schema_version: "1"
kind: agent
id: agent-assistant
name: Assistant
model: model-primary
subagents: [{markdown: subagent-missing}]
""",
        },
    )

    loaded = await load_harness_ui_configuration(config)
    assert loaded.agents["agent-assistant"].subagents
    assert "subagent-missing" not in loaded.subagents


async def test_accepts_release_owned_sandbox_as_global_default(tmp_path: Path) -> None:
    config = _write_source_tree(
        tmp_path,
        root='schema_version: "1"\ndefaults:\n  environment_profile: environment-sandbox\n',
    )

    loaded = await load_harness_ui_configuration(config)

    assert loaded.document.defaults.environment_profile == "environment-sandbox"
    assert loaded.environment_profiles == {}


async def test_rejects_configured_profile_that_shadows_a_release_owned_mode(tmp_path: Path) -> None:
    config = _write_source_tree(
        tmp_path,
        resources={
            "extensions/sandbox.yaml": """
schema_version: "1"
kind: environment_profile
id: environment-sandbox
name: Shadow Sandbox
provider_key: a13n.local-envd
provider_schema_version: "1"
provider_configuration: {}
adapter_key: a13n.local-envd-project-root
adapter_configuration: {}
"""
        },
    )

    with pytest.raises(ConfigurationError) as invalid:
        await load_harness_ui_configuration(config)

    assert invalid.value.code == "configuration_resource_invalid"


async def test_rejects_symlinked_canonical_directory(tmp_path: Path) -> None:
    config = _write_source_tree(tmp_path)
    actual = tmp_path / "actual"
    actual.mkdir()
    (tmp_path / "subagents").symlink_to(actual, target_is_directory=True)

    with pytest.raises(ConfigurationError) as invalid:
        await load_harness_ui_configuration(config)

    assert invalid.value.code == "configuration_source_invalid"


async def test_retries_when_final_yaml_fingerprint_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _write_source_tree(tmp_path)
    original = configuration_loader._regular_file_fingerprint
    calls = 0

    def one_mismatch(path: Path) -> tuple[int, int, int, int]:
        nonlocal calls
        calls += 1
        fingerprint = original(path)
        if calls == 1:
            return (*fingerprint[:3], fingerprint[3] + 1)
        return fingerprint

    monkeypatch.setattr(configuration_loader, "_regular_file_fingerprint", one_mismatch)

    loaded = await load_harness_ui_configuration(config)

    assert loaded.document.schema_version == "1"
    assert calls == 3


async def test_validates_deep_agent_reference_graph_without_python_recursion(tmp_path: Path) -> None:
    count = 600
    resources = {
        "models/primary.yaml": json.dumps(
            {
                "schema_version": "1",
                "kind": "model",
                "id": "model-primary",
                "name": "Primary",
                "route": "openai:gpt-5",
                "authentication": {"kind": "api_key", "env": "OPENAI_API_KEY"},
            }
        )
    }
    for index in range(count):
        agent: dict[str, object] = {
            "schema_version": "1",
            "kind": "agent",
            "id": f"agent-{index}",
            "name": f"Agent {index}",
            "model": "model-primary",
        }
        if index + 1 < count:
            agent["subagents"] = [{"agent": f"agent-{index + 1}"}]
        resources[f"agents/agent-{index}.yaml"] = json.dumps(agent)
    config = _write_source_tree(tmp_path, resources=resources)

    loaded = await load_harness_ui_configuration(config)

    assert len(loaded.agents) == count


async def test_long_markdown_filename_has_bounded_file_diagnostic(tmp_path: Path) -> None:
    config = _write_source_tree(tmp_path)
    subagents = tmp_path / "subagents"
    subagents.mkdir()
    filename = f"{'x' * 220}.md"
    (subagents / filename).write_text("missing frontmatter\n")

    with pytest.raises(ConfigurationError) as invalid:
        await load_harness_ui_configuration(config)

    assert invalid.value.code == "configuration_markdown_invalid"
    assert invalid.value.details["path"].endswith(filename)
    assert len(invalid.value.details["path"]) <= 4096


@pytest.mark.parametrize("reference", ["mcp-missing", "plugin-memory"])
async def test_proxy_group_references_require_the_configured_resource_kind(tmp_path: Path, reference: str) -> None:
    path = _write_source_tree(
        tmp_path,
        resources={
            "extensions/memory.yaml": """schema_version: "1"
kind: harness_plugin
id: plugin-memory
name: Memory
plugin_key: vendor.memory
""",
            "agents/main.yaml": f"""schema_version: "1"
kind: agent
id: agent-main
name: Main
tool_proxy:
  groups:
    knowledge:
      description: Knowledge tools
      mcp_servers: [{reference}]
""",
        },
    )
    with pytest.raises(ConfigurationError, match=r"tool_proxy\.groups\.knowledge\.mcp_servers"):
        await load_harness_ui_configuration(path)


@pytest.mark.parametrize("config", [{"max_results": True}, {"search_name": "invalid name"}, {"unexpected": 1}])
def test_proxy_config_uses_native_validation(config: dict) -> None:
    from a13n_harness_ui.configuration.models import AgentToolProxy
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AgentToolProxy.model_validate({"config": config})


def test_proxy_rejects_duplicate_membership_and_copies_lists() -> None:
    from a13n_harness_ui.configuration.models import AgentToolProxy
    from pydantic import ValidationError

    group = {"description": "Knowledge tools", "mcp_servers": ["mcp-docs"]}
    with pytest.raises(ValidationError, match="selected more than once"):
        AgentToolProxy.model_validate({"groups": {"first": group, "second": group}})
    proxy = AgentToolProxy.model_validate({"groups": {"knowledge": group}})
    group["mcp_servers"].clear()
    assert proxy.groups["knowledge"].mcp_servers == ("mcp-docs",)
