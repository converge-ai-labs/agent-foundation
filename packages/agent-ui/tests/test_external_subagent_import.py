from __future__ import annotations

from pathlib import Path

import pytest
from a13n_ui.configuration import (
    apply_external_subagent_import,
    preview_external_subagent_import,
)
from a13n_ui.errors import ConfigurationError

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("product", "directory"),
    [("claude-code", ".claude"), ("cursor", ".cursor")],
)
async def test_markdown_import_has_explicit_deterministic_preview_and_no_sync(
    tmp_path: Path,
    product: str,
    directory: str,
) -> None:
    configuration = tmp_path / "config/a13n-ui.yaml"
    configuration.parent.mkdir()
    configuration.write_text('schema_version: "2"\n')
    project = tmp_path / "project"
    source_directory = project / directory / "agents"
    source_directory.mkdir(parents=True)
    source = source_directory / "reviewer.md"
    source_content = """---
name: reviewer
description: Review code carefully.
model: inherit
tools: glob, grep, glob
permissionMode: plan
hooks: {}
---
Review the change and cite exact paths.
"""
    source.write_text(source_content)

    first = await preview_external_subagent_import(
        configuration,
        product=product,
        scope="project",
        project_root=project,
    )
    second = await preview_external_subagent_import(
        configuration,
        product=product,
        scope="project",
        project_root=project,
    )

    assert first.candidates == second.candidates
    candidate = first.candidates[0]
    assert candidate.status == "ready"
    assert candidate.target_relative_path == "subagents/reviewer.md"
    assert (
        candidate.canonical_content
        == """---
name: "reviewer"
description: "Review code carefully."
tools:
  - "glob"
  - "grep"
---

Review the change and cite exact paths.
"""
    )
    assert [(item.code, item.field) for item in candidate.diagnostics] == [
        ("unsupported_field", "hooks"),
        ("unsupported_field", "permissionMode"),
    ]

    result = await apply_external_subagent_import(configuration, candidate)

    assert result.action == "created"
    assert source.read_text() == source_content
    target = configuration.parent / "subagents/reviewer.md"
    assert target.read_text() == candidate.canonical_content

    unchanged = await preview_external_subagent_import(
        configuration,
        product=product,
        scope="project",
        project_root=project,
    )
    assert unchanged.candidates[0].status == "unchanged"
    unchanged_result = await apply_external_subagent_import(configuration, unchanged.candidates[0])
    assert unchanged_result.action == "unchanged"
    assert target.read_text() == candidate.canonical_content


async def test_import_rejects_changed_source_and_racing_target_without_clobber(tmp_path: Path) -> None:
    configuration = tmp_path / "config/a13n-ui.yaml"
    configuration.parent.mkdir()
    configuration.write_text('schema_version: "2"\n')
    project = tmp_path / "project"
    source = project / ".claude/agents/explorer.md"
    source.parent.mkdir(parents=True)
    source.write_text("---\nname: explorer\ndescription: Explore.\n---\nInspect.\n")

    preview = await preview_external_subagent_import(
        configuration,
        product="claude-code",
        scope="project",
        project_root=project,
    )
    candidate = preview.candidates[0]
    source.write_text("---\nname: explorer\ndescription: Changed.\n---\nInspect.\n")

    with pytest.raises(ConfigurationError) as stale:
        await apply_external_subagent_import(configuration, candidate)

    assert stale.value.code == "external_subagent_source_conflict"
    assert not (configuration.parent / "subagents/explorer.md").exists()

    fresh = await preview_external_subagent_import(
        configuration,
        product="claude-code",
        scope="project",
        project_root=project,
    )
    target = configuration.parent / "subagents/explorer.md"
    target.parent.mkdir()
    target_content = '---\nname: "explorer"\ndescription: "Other."\n---\n'
    target.write_text(target_content)

    with pytest.raises(ConfigurationError) as conflict:
        await apply_external_subagent_import(configuration, fresh.candidates[0])

    assert conflict.value.code == "external_subagent_target_conflict"
    assert target.read_text() == target_content


async def test_codex_registry_preview_reports_unrepresented_behavior(tmp_path: Path) -> None:
    configuration = tmp_path / "config/a13n-ui.yaml"
    configuration.parent.mkdir()
    configuration.write_text('schema_version: "2"\n')
    project = tmp_path / "project"
    codex = project / ".codex"
    (codex / "agents").mkdir(parents=True)
    (codex / "config.toml").write_text(
        """[agents.reviewer]
description = "Review a patch."
config_file = "agents/reviewer.toml"
"""
    )
    (codex / "agents/reviewer.toml").write_text(
        """model = "gpt-5-codex"
model_reasoning_effort = "high"
sandbox_mode = "read-only"
developer_instructions = "Review carefully."
"""
    )

    preview = await preview_external_subagent_import(
        configuration,
        product="codex",
        scope="project",
        project_root=project,
    )

    candidate = preview.candidates[0]
    assert candidate.status == "ready"
    assert len(candidate.source_facts) == 2
    assert (
        candidate.canonical_content
        == """---
name: "reviewer"
description: "Review a patch."
---

Review carefully.
"""
    )
    assert {(item.code, item.field) for item in candidate.diagnostics} == {
        ("unsupported_model", "model"),
        ("unsupported_field", "model_reasoning_effort"),
        ("unsupported_field", "sandbox_mode"),
    }
