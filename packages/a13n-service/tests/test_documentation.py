"""Canonical Markdown becomes a deterministic, offline, source-attributed search bundle."""

import json
import runpy
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
