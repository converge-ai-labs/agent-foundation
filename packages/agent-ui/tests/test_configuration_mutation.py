from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from a13n_ui.configuration import (
    ResourceMutationRequest,
    delete_configuration_source,
    load_agent_ui_configuration,
    mutate_configuration_source,
)
from a13n_ui.errors import ConfigurationError

pytestmark = pytest.mark.anyio


def _subagent(name: str, description: str = "Inspect code.") -> str:
    return f'---\nname: "{name}"\ndescription: "{description}"\n---\n\nReport evidence.\n'


async def test_create_is_no_clobber_and_returns_latest_digest(tmp_path: Path) -> None:
    configuration = tmp_path / "a13n-ui.yaml"
    configuration.write_text('schema_version: "2"\n')
    content = _subagent("explorer")

    result = await mutate_configuration_source(
        configuration,
        "subagents/explorer.md",
        ResourceMutationRequest(expected_source_digest=None, content=content),
    )

    target = tmp_path / "subagents/explorer.md"
    assert result.action == "created"
    assert result.source_digest == hashlib.sha256(content.encode()).hexdigest()
    assert target.read_text() == content
    assert result.configuration.subagents["subagent-explorer"].name == "explorer"
    assert not tuple(target.parent.glob(".explorer.md.*.tmp"))

    with pytest.raises(ConfigurationError) as conflict:
        await mutate_configuration_source(
            configuration,
            "subagents/explorer.md",
            ResourceMutationRequest(expected_source_digest=None, content=_subagent("explorer", "Other")),
        )

    assert conflict.value.code == "configuration_source_conflict"
    assert conflict.value.details["latest_source_digest"] == result.source_digest
    assert target.read_text() == content


async def test_stale_update_does_not_clobber_external_edit(tmp_path: Path) -> None:
    configuration = tmp_path / "a13n-ui.yaml"
    configuration.write_text('schema_version: "2"\n')
    target = tmp_path / "subagents/explorer.md"
    target.parent.mkdir()
    target.write_text(_subagent("explorer", "First"))
    loaded = await load_agent_ui_configuration(configuration)
    expected = loaded.source("subagents/explorer.md").source_digest
    external = _subagent("explorer", "External")
    target.write_bytes(external.encode())

    with pytest.raises(ConfigurationError) as conflict:
        await mutate_configuration_source(
            configuration,
            "subagents/explorer.md",
            ResourceMutationRequest(expected_source_digest=expected, content=_subagent("explorer", "Requested")),
        )

    assert conflict.value.code == "configuration_source_conflict"
    assert conflict.value.details["latest_source_digest"] == hashlib.sha256(external.encode()).hexdigest()
    assert target.read_text() == external


async def test_update_uses_atomic_replace_and_invalid_candidate_is_not_published(tmp_path: Path) -> None:
    configuration = tmp_path / "a13n-ui.yaml"
    configuration.write_text('schema_version: "2"\n')
    target = tmp_path / "subagents/explorer.md"
    target.parent.mkdir()
    original = _subagent("explorer", "First")
    target.write_text(original)
    loaded = await load_agent_ui_configuration(configuration)
    expected = loaded.source("subagents/explorer.md").source_digest
    original_inode = target.stat().st_ino

    with pytest.raises(ConfigurationError) as invalid:
        await mutate_configuration_source(
            configuration,
            "subagents/explorer.md",
            ResourceMutationRequest(expected_source_digest=expected, content="not frontmatter\n"),
        )

    assert invalid.value.code == "configuration_markdown_invalid"
    assert target.read_text() == original

    replacement = _subagent("explorer", "Second")
    result = await mutate_configuration_source(
        configuration,
        "subagents/explorer.md",
        ResourceMutationRequest(expected_source_digest=expected, content=replacement),
    )

    assert result.action == "updated"
    assert target.read_text() == replacement
    assert target.stat().st_ino != original_inode


async def test_delete_validates_complete_candidate_before_removing_source(tmp_path: Path) -> None:
    configuration = tmp_path / "a13n-ui.yaml"
    configuration.write_text('schema_version: "2"\n')
    target = tmp_path / "subagents/explorer.md"
    target.parent.mkdir()
    target.write_text(_subagent("explorer"))
    loaded = await load_agent_ui_configuration(configuration)
    expected = loaded.source("subagents/explorer.md").source_digest

    result = await delete_configuration_source(
        configuration,
        "subagents/explorer.md",
        expected_source_digest=expected,
    )

    assert result.action == "deleted"
    assert result.source_digest is None
    assert not target.exists()
    assert not result.configuration.subagents
