from __future__ import annotations

import runpy
from pathlib import Path

import pytest
import yaml

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
BUILDER = runpy.run_path(str(PACKAGE_ROOT / "build_skills.py"))
build_skills = BUILDER["build_skills"]
prepare_skills = BUILDER["prepare_skills"]


def _source(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    package = repository / "packages/a13n-harness-ui"
    assets = package / "a13n_harness_ui/assets"
    assets.mkdir(parents=True)
    (assets / "configuration_skill.md").write_bytes(
        (PACKAGE_ROOT / "a13n_harness_ui/assets/configuration_skill.md").read_bytes()
    )
    docs = repository / "docs/a13n-harness-ui"
    docs.mkdir(parents=True)
    (docs / "meta.json").write_text(
        '{"title": "Workbench", "pages": ["---Configure---", "settings", "index"]}', encoding="utf-8"
    )
    (docs / "settings.md").write_text(
        "---\ntitle: Settings reference\nsidebarTitle: Settings\n---\n\n## Models\n\n### Credentials\n\n"
        "Use references.\n\n```markdown\n## Not a heading\n```\n\n## Tools\n\n"
        "[Start](index.md) and [Other](../a13n-harness/index.md#agents).\n",
        encoding="utf-8",
    )
    (docs / "index.md").write_text("---\ntitle: Start\n---\n\n## Setup\n\nStart here.\n", encoding="utf-8")
    return package, repository


def _files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_builds_navigation_from_site_metadata_and_real_headings(tmp_path: Path) -> None:
    package, repository = _source(tmp_path)
    output = build_skills(package, repository)
    skill = (output / "SKILL.md").read_text()
    metadata = yaml.safe_load(skill.split("---", 2)[1])
    assert metadata["name"] == "harness-ui-configuration"
    assert metadata["description"]
    assert "### Workbench / Configure" in skill
    assert skill.index("[Settings](docs/settings.md)") < skill.index("[Start](docs/index.md)")
    assert "Topics: Models; Tools." in skill
    assert "Not a heading" not in skill
    assert "Credentials" not in skill
    index = (output / "references/navigation.md").read_text()
    assert "Models — lines 6-15" in index
    assert "Credentials — lines 8-15" in index
    assert "Tools — lines 16-18" in index
    assert "https://a13n-docs.converge.ai/a13n-harness/#agents" in index
    assert (output / "docs/settings.md").read_bytes() == (repository / "docs/a13n-harness-ui/settings.md").read_bytes()
    assert not (repository / ".agents").exists()


def test_rebuild_is_deterministic_and_tracks_reorganized_docs(tmp_path: Path) -> None:
    package, repository = _source(tmp_path)
    output = build_skills(package, repository)
    original = _files(output)
    assert _files(build_skills(package, repository)) == original
    docs = repository / "docs/a13n-harness-ui"
    (docs / "settings.md").rename(docs / "models.md")
    meta = docs / "meta.json"
    meta.write_text(meta.read_text().replace('"settings"', '"models"'))
    output = build_skills(package, repository)
    assert not (output / "docs/settings.md").exists()
    assert "[Settings](docs/models.md)" in (output / "SKILL.md").read_text()


def test_includes_unlisted_pages_and_nested_assets(tmp_path: Path) -> None:
    package, repository = _source(tmp_path)
    docs = repository / "docs/a13n-harness-ui"
    (docs / "extra").mkdir()
    (docs / "extra/guide.md").write_text("---\ntitle: Extra\n---\n\n## Details\n\n![Picture](image.png)\n")
    (docs / "extra/image.png").write_bytes(b"test image")
    output = build_skills(package, repository)
    assert "[Extra](docs/extra/guide.md)" in (output / "SKILL.md").read_text()
    assert not (output / "docs/meta.json").exists()
    assert (output / "docs/extra/image.png").read_bytes() == b"test image"


@pytest.mark.parametrize("missing", ["page", "reference"])
def test_missing_local_documents_fail_before_replacing_bundle(tmp_path: Path, missing: str) -> None:
    package, repository = _source(tmp_path)
    output = build_skills(package, repository)
    original = _files(output)
    if missing == "page":
        (repository / "docs/a13n-harness-ui/settings.md").unlink()
    else:
        page = repository / "docs/a13n-harness-ui/settings.md"
        page.write_text(page.read_text() + "\n[Missing](missing.md)\n")
    with pytest.raises((FileNotFoundError, ValueError)):
        build_skills(package, repository)
    assert _files(output) == original


def test_sdist_preparation_uses_bundled_files_without_repository(tmp_path: Path) -> None:
    package, repository = _source(tmp_path)
    output = prepare_skills(package)
    original = _files(output)
    (repository / "docs/a13n-harness-ui/meta.json").unlink()
    assert prepare_skills(package) == output
    assert _files(output) == original
    (output / "SKILL.md").unlink()
    with pytest.raises(RuntimeError, match="missing"):
        prepare_skills(package)


def test_headings_ignore_code_and_preserve_parent_section_boundaries() -> None:
    content = "# Page\n\nSection\n-------\n\n### Child\n\ntext\n\n# Next page\n"
    headings = BUILDER["_headings"](content)
    assert headings == [(1, "Page", 1, 9), (2, "Section", 3, 9), (3, "Child", 6, 9), (1, "Next page", 10, 10)]


def test_headings_skip_front_matter_without_shifting_lines() -> None:
    content = "---\ntitle: Page\ndescription: Not a heading\n---\n\n## Section\n\ntext\n"
    assert BUILDER["_headings"](content) == [(2, "Section", 6, 8)]


def test_localized_pages_are_not_bundled(tmp_path: Path) -> None:
    package_root, repository = _source(tmp_path)
    source = repository / "docs/a13n-harness-ui"
    english = _files(build_skills(package_root, repository))
    (source / "settings.zh-CN.md").write_text(
        "---\ntitle: 设置\ndescription: 中文文档\n---\n\n## 配置\n", encoding="utf-8"
    )
    (source / "meta.zh-CN.json").write_text('{"title": "Harness UI", "pages": ["settings"]}', encoding="utf-8")
    output = build_skills(package_root, repository)
    assert _files(output) == english
    assert not list(output.rglob("*.zh-CN.*"))
    assert "设置" not in (output / "SKILL.md").read_text(encoding="utf-8")
    assert (output / "docs/settings.md").read_bytes() == (source / "settings.md").read_bytes()
