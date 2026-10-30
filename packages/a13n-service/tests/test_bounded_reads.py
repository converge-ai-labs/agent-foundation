"""Read budgets preserve records and full definitions; continuation never mixes content versions."""

import json
import os
import subprocess
import sys

import pytest
from a13n_service.infra import reads
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.tools import ToolInfo
from a13n_service.resources import catalog_reads
from pydantic import BaseModel


@pytest.mark.parametrize("text", ["汉字🙂" * 4000, "\x00\n\t" * 4000, "", '{"long":' + "x" * 20000])
def test_fragments_reassemble_exact_unicode_and_escaped_text_within_budget(text: str) -> None:
    pieces = []
    page = reads.read_value(text, source="test")
    while True:
        assert reads.size(page) <= reads.BODY_CHARS
        pieces.append(page.text)
        if not page.has_more:
            break
        assert page.next_offset is not None
        page = reads.read_value(text, source="test", offset=page.next_offset, version=page.version)
    assert "".join(pieces) == text


def test_changed_content_and_unversioned_continuations_are_refused() -> None:
    page = reads.read_value("abcdefgh", source="test", limit=2)
    with pytest.raises(ServiceError, match="version"):
        reads.read_value("abcdefgh", source="test", offset=2)
    with pytest.raises(ServiceError, match="changed"):
        reads.read_value("new data", source="test", offset=2, version=page.version)


def test_search_finds_beyond_preview_and_continues_at_next_match() -> None:
    text = "x" * 10000 + "needle" + "y" * 5000 + "needle"
    first = reads.read_value(text, source="test", query="needle", limit=200)
    assert first.match_offset == 10000 and first.has_more and "needle" in first.text
    second = reads.read_value(
        text, source="test", query="needle", offset=first.next_offset or 0, version=first.version, limit=200
    )
    assert second.match_offset == 15006 and not second.has_more


def test_catalog_searches_full_description_and_pages_without_schema_or_record_loss() -> None:
    tools = [
        ToolInfo(name=f"tool{i:03}", description="x" * 500 + "needle", input_schema={"large": "x" * 30000})
        for i in range(30)
    ]
    cursor = None
    names = []
    while True:
        page = catalog_reads.index(tools, scope="test", query="needle", limit=30, cursor=cursor)
        assert reads.size(page) <= reads.BODY_CHARS and "input_schema" not in reads.json_text(page)
        names.extend(item.name for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert names == [tool.name for tool in tools]
    first = catalog_reads.index(tools, scope="test", query=None, limit=1, cursor=None)
    with pytest.raises(ServiceError):
        catalog_reads.index(tools, scope="test", query="changed", limit=1, cursor=first.next_cursor)
    with pytest.raises(ServiceError):
        catalog_reads.index(tools[:-1], scope="test", query=None, limit=1, cursor=first.next_cursor)


def test_definition_reports_missing_and_oversize_without_truncating_schema() -> None:
    tool = ToolInfo(
        name="huge", description="test", input_schema={"properties": {"prompt": {"description": "字" * 30000}}}
    )
    result = reads.definitions({"huge": tool}, ["huge", "missing"], source="test")
    assert isinstance(result, reads.Definitions)
    assert result.missing == ["missing"] and not result.items and result.oversized[0].name == "huge"
    parts = []
    offset, version = 0, result.oversized[0].version
    while True:
        page = reads.definitions({"huge": tool}, ["huge"], source="test", offset=offset, version=version)
        assert isinstance(page, reads.TextRead)
        parts.append(page.text)
        if page.next_offset is None:
            break
        offset = page.next_offset
    assert json.loads("".join(parts)) == tool.model_dump(mode="json")


def test_budget_cursor_stops_at_last_returned_record() -> None:
    class Record(BaseModel):
        id: str
        value: str

    values = [Record(id=str(i), value="x" * 2500) for i in range(5)]
    first, cursor = reads.snapshot_page(
        values, key=lambda item: item.id, scope="records", filters=None, cursor=None, limit=5, revision="v1"
    )
    assert 0 < len(first) < 5 and cursor
    second, following = reads.snapshot_page(
        values, key=lambda item: item.id, scope="records", filters=None, cursor=cursor, limit=5, revision="v1"
    )
    assert first + second == values and following is None


def test_configuration_fragment_versions_are_stable_across_worker_hash_seeds() -> None:
    code = """
from a13n_service.infra.reads import read_value
from a13n_service.resources.agents.schemas import AgentConfig
config = AgentConfig(model="model", toolsets={group: {"enabled": True} for group in ("agents", "models", "execution")})
print(read_value(config, source="config").version)
print(read_value(config.toolsets, source="config/toolsets").version)
"""
    versions = [
        subprocess.check_output([sys.executable, "-c", code], env={**os.environ, "PYTHONHASHSEED": seed}, text=True)
        for seed in ("1", "7")
    ]
    assert versions[0] == versions[1]


def test_individual_summary_shrinks_escaped_previews_without_changing_identity() -> None:
    class Summary(BaseModel):
        id: str
        value: reads.Preview

    result = reads.bounded(Summary(id="stable", value=reads.preview("\x00" * 300)), budget=400)
    assert result.id == "stable" and result.value.truncated and reads.size(result) <= 400


def test_mixed_definition_batch_accounts_for_missing_and_oversized_references() -> None:
    names = ["x" * 1000 + str(index) for index in range(5)]
    values = {name: ToolInfo(name=name, description="x" * 5000, input_schema={}) for name in names[:4]}
    result = reads.definitions(values, names, source="catalog")
    assert isinstance(result, reads.Definitions) and reads.size(result) <= reads.BODY_CHARS
    assert result.missing == names[4:]
    assert {item.name for item in result.items} | {item.name for item in result.oversized} == set(names[:4])
