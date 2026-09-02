from __future__ import annotations

import json
from pathlib import Path

import a13n_ui.configuration.loader as configuration_loader
import pytest
from a13n_ui.configuration import load_agent_ui_configuration
from a13n_ui.errors import ConfigurationError

pytestmark = pytest.mark.anyio


async def test_loads_one_document_and_canonical_markdown_set(tmp_path: Path) -> None:
    config = tmp_path / "a13n-ui.yaml"
    config.write_text(
        """
schema_version: "1"
process:
  storage:
    data_root: ./state
  log_level: debug
defaults:
  agent: assistant
  environment: native
models:
  primary:
    model: openai:gpt-5
    api_key:
      env: OPENAI_API_KEY
    settings:
      temperature: 0
plugins:
  memory:
    plugin: vendor.memory
    enabled: true
mcp_servers:
  github:
    transport:
      command: npx
      arguments: [-y, server-github]
agents:
  assistant:
    model: primary
    plugins: null
    mcp_servers: []
    subagents:
      - markdown: explorer
      - agent: reviewer
  reviewer:
    model: primary
    plugins: []
environments:
  native:
    kind: native
environment_providers: {}
""".lstrip()
    )
    subagents = tmp_path / "subagents"
    subagents.mkdir()
    (subagents / "explorer.md").write_text(
        """---
name: explorer
description: Inspect an unfamiliar codebase.
instruction: Use for focused repository exploration.
model: inherit
model_settings: null
model_cfg: {}
tools: search, files
optional_tools: [shell]
---

Inspect the relevant code and report evidence.
"""
    )
    (subagents / "README.md").write_text("ignored\n")
    nested = subagents / "nested"
    nested.mkdir()
    (nested / "ignored.md").write_text("not discovered\n")

    loaded = await load_agent_ui_configuration(config)

    assert loaded.document.process.storage.data_root == (tmp_path / "state").resolve()
    assert loaded.document.process.log_level == "DEBUG"
    assistant = loaded.document.agents["assistant"]
    assert loaded.document.selected_plugins(assistant) == ("memory",)
    assert loaded.document.selected_mcp_servers(assistant) == ()
    assert len(loaded.subagents) == 1
    explorer = loaded.markdown("explorer")
    assert explorer.model is None
    assert explorer.model_settings is None
    assert explorer.model_cfg == {}
    assert explorer.tools == ("search", "files")
    assert explorer.optional_tools == ("shell",)
    assert explorer.body == "Inspect the relevant code and report evidence."
    assert loaded.source_digest != loaded.yaml_digest


@pytest.mark.parametrize(
    "body",
    [
        """
schema_version: "1"
unknown: true
""",
        """
schema_version: "1"
models:
  primary:
    model: openai:gpt-5
    settings:
      api_key: literal-secret
""",
        """
schema_version: "1"
models:
  primary:
    model: openai:gpt-5
plugins:
  memory:
    plugin: vendor.memory
    enabled: false
agents:
  assistant:
    model: primary
    plugins: [memory]
""",
        """
schema_version: "1"
models:
  primary:
    model: openai:gpt-5
agents:
  first:
    model: primary
    subagents: [{agent: second}]
  second:
    model: primary
    subagents: [{agent: first}]
""",
    ],
)
async def test_rejects_invalid_document_behavior(tmp_path: Path, body: str) -> None:
    config = tmp_path / "a13n-ui.yaml"
    config.write_text(body.lstrip())

    with pytest.raises(ConfigurationError) as invalid:
        await load_agent_ui_configuration(config)

    assert invalid.value.code == "settings_invalid"
    assert invalid.value.details["validation_error_count"] >= 1


async def test_rejects_missing_markdown_reference(tmp_path: Path) -> None:
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
    subagents: [{markdown: missing}]
""".lstrip()
    )

    with pytest.raises(ConfigurationError) as invalid:
        await load_agent_ui_configuration(config)

    assert invalid.value.code == "configuration_invalid"


async def test_rejects_symlinked_canonical_directory(tmp_path: Path) -> None:
    config = tmp_path / "a13n-ui.yaml"
    config.write_text('schema_version: "1"\n')
    actual = tmp_path / "actual"
    actual.mkdir()
    (tmp_path / "subagents").symlink_to(actual, target_is_directory=True)

    with pytest.raises(ConfigurationError) as invalid:
        await load_agent_ui_configuration(config)

    assert invalid.value.code == "configuration_source_invalid"


async def test_retries_when_final_yaml_fingerprint_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "a13n-ui.yaml"
    config.write_text('schema_version: "1"\n')
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

    loaded = await load_agent_ui_configuration(config)

    assert loaded.document.schema_version == "1"
    assert calls == 2


async def test_validates_deep_agent_reference_graph_without_python_recursion(tmp_path: Path) -> None:
    config = tmp_path / "a13n-ui.yaml"
    count = 600
    agents: dict[str, object] = {}
    for index in range(count):
        item: dict[str, object] = {"model": "primary"}
        if index + 1 < count:
            item["subagents"] = [{"agent": f"agent-{index + 1}"}]
        agents[f"agent-{index}"] = item
    config.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "models": {"primary": {"model": "openai:gpt-5"}},
                "agents": agents,
            }
        )
    )

    loaded = await load_agent_ui_configuration(config)

    assert len(loaded.document.agents) == count


async def test_long_markdown_filename_has_bounded_file_diagnostic(tmp_path: Path) -> None:
    config = tmp_path / "a13n-ui.yaml"
    config.write_text('schema_version: "1"\n')
    subagents = tmp_path / "subagents"
    subagents.mkdir()
    filename = f"{'x' * 220}.md"
    (subagents / filename).write_text("missing frontmatter\n")

    with pytest.raises(ConfigurationError) as invalid:
        await load_agent_ui_configuration(config)

    assert invalid.value.code == "configuration_markdown_invalid"
    assert invalid.value.details["path"].endswith(filename)
    assert len(invalid.value.details["path"]) <= 4096
