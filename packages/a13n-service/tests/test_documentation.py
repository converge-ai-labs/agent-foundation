"""Canonical Markdown becomes a deterministic, offline, source-attributed search bundle."""

import json
import os
import runpy
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
from a13n_service.documentation import MAX_TEXT, Documents
from fastmcp.exceptions import ValidationError
from fastmcp.tools import Tool

BUILD = runpy.run_path(str(Path(__file__).parents[1] / "build_docs.py"))


def test_headings_ignore_fences_and_keep_source_lines() -> None:
    source = "---\ntitle: Guide\n---\n\nIntro\n\n## Upload\nText\n```sh\n# not a heading\n```\n\n### Limits\nSmall.\n"
    sections = BUILD["sections"](source, "docs/a13n-service/example.md")
    assert [section["headings"] for section in sections] == [[], ["Upload"], ["Upload", "Limits"]]
    assert all(section["title"] == "Guide" and section["language"] == "en" for section in sections)
    assert sections[1]["line_start"] == 7 and sections[2]["line_start"] == 13
    for section in sections:
        expected = "\n".join(source.splitlines()[section["line_start"] - 1 : section["line_end"]]).rstrip()
        assert section["text"] == expected


def test_rebuild_without_checkout_and_reject_missing_bundle(tmp_path: Path) -> None:
    repository = tmp_path / "checkout"
    package = repository / "packages/a13n-service"
    (package / "a13n_service").mkdir(parents=True)
    docs = repository / "docs/a13n-service"
    docs.mkdir(parents=True)
    (docs / "meta.json").write_text("{}")
    (docs / "example.md").write_text("---\ntitle: Example\n---\n\n## Upload\nUse multipart.\n")
    output = BUILD["prepare_docs"](package)
    original = output.read_bytes()
    assert BUILD["prepare_docs"](package).read_bytes() == original
    outside = tmp_path / "sdist"
    (outside / "a13n_service").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="missing"):
        BUILD["prepare_docs"](outside)
    (outside / "a13n_service/documentation.json").write_bytes(original)
    assert BUILD["prepare_docs"](outside).read_bytes() == original
    (outside / "a13n_service/documentation.json").write_text(json.dumps({"format": 999, "sections": []}))
    with pytest.raises(ValueError):
        BUILD["prepare_docs"](outside)


def test_cached_editable_install_owns_index_and_refreshes_inputs(tmp_path: Path) -> None:
    source_package = Path(__file__).parents[1]
    checkout = tmp_path / "checkout"
    package = checkout / "packages/a13n-service"
    (package / "a13n_service").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text('[tool.uv.workspace]\nmembers = ["packages/*"]\n')
    for name in ("build_docs.py", "hatch_build.py", "a13n_service/__init__.py", "a13n_service/documentation.py"):
        shutil.copy2(source_package / name, package / name)
    # Exercise the real build configuration with only the search module's runtime
    # dependency, not another installation of the full Service dependency graph.
    manifest = (source_package / "pyproject.toml").read_text()
    (package / "pyproject.toml").write_text(
        '[project]\nname = "a13n-service"\nversion = "0.0.0"\n'
        f'dependencies = ["pydantic=={version("pydantic")}"]\n\n' + manifest[manifest.index("[build-system]") :]
    )
    docs = tmp_path / "checkout/docs/a13n-service"
    (docs / "guides").mkdir(parents=True)
    (docs / "meta.json").write_text("{}")
    markdown = docs / "guides/example.md"
    markdown.write_text("---\ntitle: Guide\n---\n\nOriginal marker\n")
    environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(tmp_path / "installed")}

    def sync() -> None:
        result = subprocess.run(
            ["uv", "sync", "--offline", "--no-dev", "--package", "a13n-service", "--python", sys.executable],
            cwd=checkout,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr

    def search() -> str:
        result = subprocess.run(
            [
                "uv",
                "run",
                "--offline",
                "--no-sync",
                "--project",
                str(package),
                "python",
                "-c",
                "from a13n_service.documentation import Documents; "
                "print(Documents().search('marker')['results'][0]['text'])",
            ],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    sync()
    assert "Original marker" in search()
    bundle = package / "a13n_service/documentation.json"
    built_at = bundle.stat().st_mtime_ns
    sync()
    assert bundle.stat().st_mtime_ns == built_at  # Unchanged inputs must not rebuild perpetually.

    markdown.write_text("---\ntitle: Guide\n---\n\nUpdated marker\n")
    sync()
    assert "Updated marker" in search()
    built_at = bundle.stat().st_mtime_ns
    generator = package / "build_docs.py"
    generator.write_text(generator.read_text() + "\n# Updated generator input.\n")
    sync()
    assert bundle.stat().st_mtime_ns != built_at

    # A fresh environment restores the cached editable wheel, not build-hook
    # side effects in the checkout. Search must still work without that output.
    bundle.unlink()
    environment["UV_PROJECT_ENVIRONMENT"] = str(tmp_path / "restored")
    sync()
    assert not bundle.exists()
    assert "Updated marker" in search()


def test_search_is_bounded_attributed_and_language_specific() -> None:
    documents = Documents()
    results = documents.search("upload", limit=2)
    assert len(results["results"]) == 2 and results["truncated"]
    assert results["version"] and results["language"] == "en"
    assert all(result["source"].startswith("docs/a13n-service/") for result in results["results"])
    assert all(len(result["text"]) <= MAX_TEXT for result in results["results"])
    assert results == documents.search("upload", limit=2)
    assert documents.search("zzzznoresultzzzz")["results"] == []
    chinese = documents.search("上传", language="zh-CN")
    assert chinese["results"] and all(result["language"] == "zh-CN" for result in chinese["results"])
    execution = documents.search("Execute an Agent", limit=10)
    assert any("assigned_run_id" in result["text"] for result in execution["results"])


@pytest.mark.anyio
async def test_tool_enforces_search_limits() -> None:
    tool = Tool.from_function(Documents().search, name="search_documents")
    for arguments in (
        {"query": ""},
        {"query": "a" * 257},
        {"query": "upload", "limit": 11},
        {"query": "x", "language": "xx"},
    ):
        with pytest.raises(ValidationError):
            await tool.run(arguments)
