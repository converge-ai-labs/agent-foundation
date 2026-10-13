"""Builtin Skill confinement, bounded reads, published references and live schema generation."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from a13n_harness.environment.files import FileQueryRequest, FileTextSearchRequest
from a13n_harness.environment.models import EnvironmentError
from a13n_service.app import _registry
from a13n_service.distribution import OSS
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.runs.builtin_files import ROOT, BuiltinFiles, packaged_text
from a13n_service.runs.builtin_skills import platform_skill
from a13n_service.settings import Settings

pytestmark = pytest.mark.anyio


async def test_local_skill_catalog_schema_and_read_only_tools():
    settings = Settings()
    skill = await platform_skill(SimpleNamespace(settings=settings, registry=_registry(OSS, settings)))
    assert len(skill.items) == 1 and skill.items[0].name == "platform-operations"
    assert "platform_view" in skill.get_instructions()
    schema = await skill.files.read_text(f"{ROOT}/schemas/agent-config.json", line_limit=1000)
    assert schema.lines_read == 1000 and schema.has_more
    assert json.loads(skill.files.files[f"{ROOT}/schemas/agent-config.json"]) == AgentConfig.model_json_schema()
    for text in skill.files.files.values():
        assert "a13n_run_" not in text
    document = await skill.files.read_text(f"{ROOT}/SKILL.md")
    import re

    for relative in re.findall(r"\]\(([^)]+)\)", document.text):
        assert (await skill.files.stat(f"{ROOT}/{relative}")).kind == "file"


async def test_file_scope_denies_traversal_and_media_and_bounds_pages():
    files = BuiltinFiles({f"{ROOT}/guide.md": "first\nsecond\nthird\n", f"{ROOT}/references/.hidden.md": "second"})
    for path in ("/etc/passwd", "../secret", f"{ROOT}/../secret", "/platform-skills/platform-operations-other/file"):
        with pytest.raises(EnvironmentError):
            await files.read_text(path)
        with pytest.raises(EnvironmentError):
            await files.query(FileQueryRequest(root=path, pattern="**/*", max_results=10))
    with pytest.raises(EnvironmentError):
        await files.read_bytes("guide.md")
    with pytest.raises(EnvironmentError):
        await files.write_text("guide.md", "changed", mode="replace")
    page = await files.read_text("guide.md", line_limit=1)
    assert page.text == "first\n" and page.has_more
    assert (await files.read_text("guide.md", line_offset=1, line_limit=1)).text == "second\n"
    result = await files.search_text(FileTextSearchRequest(root=ROOT, pattern="second", max_matches=1))
    assert len(result.matches) == 1 and result.matches[0].path == f"{ROOT}/guide.md"
    hidden = await files.query(FileQueryRequest(root=ROOT, pattern="**/*.md", include_hidden=True, max_results=10))
    assert len(hidden.entries) == 2


def test_packaged_files_reject_symlink_and_special_files(tmp_path: Path):
    root = tmp_path / "docs"
    root.mkdir()
    (tmp_path / "secret").write_text("never reveal")
    (root / "escape.md").symlink_to(tmp_path / "secret")
    with pytest.raises(OSError):
        packaged_text(root)
    (root / "escape.md").unlink()
    (root / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        packaged_text(root)
