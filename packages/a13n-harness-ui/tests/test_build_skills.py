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
    (repository / "mkdocs.yml").write_text(
        "site_url: https://example.test/docs/\nnav:\n"
        "  - Workbench:\n      - Configure:\n"
        "          - Settings: a13n-harness-ui/settings.md\n"
        "          - Start: a13n-harness-ui/index.md\n",
        encoding="utf-8",
    )
    (docs / "settings.md").write_text(
        "# Settings\n\n## Models\n\n### Credentials\n\nUse references.\n\n"
        "```markdown\n## Not a heading\n```\n\n## Tools\n\n"
        "[Start](index.md) and [Other](../a13n-harness/index.md#agents).\n",
        encoding="utf-8",
    )
    (docs / "index.md").write_text("# Overview\n\n## Setup\n\nStart here.\n", encoding="utf-8")
    return package, repository


def _files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_builds_navigation_from_mkdocs_and_real_headings(tmp_path: Path) -> None:
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
    assert "Models — lines 3-12" in index
    assert "Credentials — lines 5-12" in index
    assert "Tools — lines 13-15" in index
    assert "https://example.test/docs/a13n-harness/#agents" in index
    assert (output / "docs/settings.md").read_bytes() == (repository / "docs/a13n-harness-ui/settings.md").read_bytes()
    assert not (repository / ".agents").exists()


def test_rebuild_is_deterministic_and_tracks_reorganized_docs(tmp_path: Path) -> None:
    package, repository = _source(tmp_path)
    output = build_skills(package, repository)
    original = _files(output)
    assert _files(build_skills(package, repository)) == original
    docs = repository / "docs/a13n-harness-ui"
    (docs / "settings.md").rename(docs / "models.md")
    nav = repository / "mkdocs.yml"
    nav.write_text(nav.read_text().replace("settings.md", "models.md"))
    output = build_skills(package, repository)
    assert not (output / "docs/settings.md").exists()
    assert "[Settings](docs/models.md)" in (output / "SKILL.md").read_text()


def test_includes_unlisted_pages_and_nested_assets(tmp_path: Path) -> None:
    package, repository = _source(tmp_path)
    docs = repository / "docs/a13n-harness-ui"
    (docs / "extra").mkdir()
    (docs / "extra/guide.md").write_text("# Extra\n\n## Details\n\n![Picture](image.png)\n")
    (docs / "extra/image.png").write_bytes(b"test image")
    output = build_skills(package, repository)
    assert "docs/extra/guide.md" in (output / "SKILL.md").read_text()
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
    (repository / "mkdocs.yml").unlink()
    assert prepare_skills(package) == output
    assert _files(output) == original
    (output / "SKILL.md").unlink()
    with pytest.raises(RuntimeError, match="missing"):
        prepare_skills(package)


def test_headings_ignore_code_and_preserve_parent_section_boundaries() -> None:
    content = "# Page\n\nSection\n-------\n\n### Child\n\ntext\n\n# Next page\n"
    headings = BUILDER["_headings"](content)
    assert headings == [(1, "Page", 1, 9), (2, "Section", 3, 9), (3, "Child", 6, 9), (1, "Next page", 10, 10)]
