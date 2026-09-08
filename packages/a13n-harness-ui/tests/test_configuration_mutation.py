from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from a13n_harness_ui.configuration import (
    ResourceMutationRequest,
    delete_configuration_source,
    load_harness_ui_configuration,
    mutate_configuration_source,
    mutation,
)
from a13n_harness_ui.errors import ConfigurationError

pytestmark = pytest.mark.anyio


def _subagent(name: str, description: str = "Inspect code.") -> str:
    return f'---\nname: "{name}"\ndescription: "{description}"\n---\n\nReport evidence.\n'


async def test_create_and_update_without_source_preconditions(tmp_path: Path) -> None:
    configuration = tmp_path / "a13n-harness-ui.yaml"
    configuration.write_text('schema_version: "1"\n')
    target = tmp_path / "subagents/explorer.md"
    for description, action in (("First", "created"), ("Second", "updated")):
        content = _subagent("explorer", description)
        result = await mutate_configuration_source(
            configuration, "subagents/explorer.md", ResourceMutationRequest(content=content)
        )
        assert result.action == action
        assert result.source_digest == hashlib.sha256(content.encode()).hexdigest()
        assert target.read_text() == content
        assert result.configuration.subagents["subagent-explorer"].description == description
        assert not tuple(target.parent.glob(".explorer.md.*.tmp"))


async def test_update_overwrites_external_edit_during_validation(tmp_path: Path) -> None:
    configuration = tmp_path / "a13n-harness-ui.yaml"
    configuration.write_text('schema_version: "1"\n')
    target = tmp_path / "subagents/explorer.md"
    target.parent.mkdir()
    target.write_text(_subagent("explorer", "First"))
    requested = _subagent("explorer", "Requested")

    def external_edit(candidate):
        assert candidate.subagents["subagent-explorer"].description == "Requested"
        target.write_text(_subagent("explorer", "External"))
        configuration.write_text('schema_version: "1"\nprocess: {log_level: DEBUG}\n')

    result = await mutate_configuration_source(
        configuration,
        "subagents/explorer.md",
        ResourceMutationRequest(content=requested),
        validate_candidate=external_edit,
    )
    assert result.action == "updated"
    assert target.read_text() == requested
    assert "DEBUG" in configuration.read_text()


async def test_later_external_write_is_loaded_without_post_write_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration = tmp_path / "a13n-harness-ui.yaml"
    configuration.write_text('schema_version: "1"\n')
    target = tmp_path / "subagents/explorer.md"
    publish = mutation._publish_content
    external = _subagent("explorer", "External last writer")

    def publish_then_edit(path: Path, content: bytes) -> None:
        publish(path, content)
        path.write_text(external)

    monkeypatch.setattr(mutation, "_publish_content", publish_then_edit)
    result = await mutate_configuration_source(
        configuration, "subagents/explorer.md", ResourceMutationRequest(content=_subagent("explorer"))
    )
    assert target.read_text() == external
    assert result.configuration.subagents["subagent-explorer"].description == "External last writer"


async def test_update_uses_atomic_replace_and_invalid_candidate_is_not_published(tmp_path: Path) -> None:
    configuration = tmp_path / "a13n-harness-ui.yaml"
    configuration.write_text('schema_version: "1"\n')
    target = tmp_path / "subagents/explorer.md"
    target.parent.mkdir()
    original = _subagent("explorer", "First")
    target.write_text(original)
    original_inode = target.stat().st_ino

    with pytest.raises(ConfigurationError) as invalid:
        await mutate_configuration_source(
            configuration, "subagents/explorer.md", ResourceMutationRequest(content="not frontmatter\n")
        )
    assert invalid.value.code == "configuration_markdown_invalid"
    assert target.read_text() == original

    replacement = _subagent("explorer", "Second")
    result = await mutate_configuration_source(
        configuration, "subagents/explorer.md", ResourceMutationRequest(content=replacement)
    )
    assert result.action == "updated"
    assert target.read_text() == replacement
    assert target.stat().st_ino != original_inode


async def test_delete_validates_then_removes_current_source_and_is_idempotent(tmp_path: Path) -> None:
    configuration = tmp_path / "a13n-harness-ui.yaml"
    configuration.write_text('schema_version: "1"\n')
    target = tmp_path / "subagents/explorer.md"
    target.parent.mkdir()
    target.write_text(_subagent("explorer"))

    def external_edit(candidate):
        assert "subagent-explorer" not in candidate.subagents
        target.write_text(_subagent("explorer", "External"))

    result = await delete_configuration_source(configuration, "subagents/explorer.md", validate_candidate=external_edit)
    assert result.action == "deleted"
    assert result.source_digest is None
    assert not target.exists()
    assert "subagent-explorer" not in result.configuration.subagents
    assert (await delete_configuration_source(configuration, "subagents/explorer.md")).action == "deleted"


async def test_mutation_preserves_global_guidance(tmp_path: Path) -> None:
    configuration = tmp_path / "a13n-harness-ui.yaml"
    configuration.write_text('schema_version: "1"\n')
    guidance = tmp_path / "AGENTS.md"
    content = "# Guidance\r\nPreserve authored instructions — 研究.\r\n".encode()
    guidance.write_bytes(content)
    result = await mutate_configuration_source(
        configuration, "subagents/explorer.md", ResourceMutationRequest(content=_subagent("explorer"))
    )
    assert guidance.read_bytes() == content
    assert result.configuration.source_digest == (await load_harness_ui_configuration(configuration)).source_digest
