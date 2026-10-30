"""Readable recovery files retain semantic text without changing the JSON spill."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness._json import redact_json
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.toolsets.files import FileToolset
from a13n_harness.toolsets.output import (
    DEFAULT_TOOL_OUTPUT_CHARS,
    create_tool_output_disclosure,
    disclose_text_fields,
    disclose_text_paths,
    tool_output_size,
)

from .environment_helpers import DirectLocalFilePolicy

pytestmark = pytest.mark.anyio


class _Context:
    def __init__(self, root: Path, unavailable: frozenset[str] = frozenset()) -> None:
        self.root = root
        self.unavailable = unavailable
        self.files: dict[str, bytes] = {}

    async def _spill_tool_result(self, data: bytes, *, suffix: str) -> str | None:
        if suffix in self.unavailable:
            return None
        name = f"result-{len(self.files)}{suffix}"
        self.files[name] = data
        (self.root / name).write_bytes(data)
        return f"/{name}"


@pytest.mark.parametrize("complete", [True, False])
async def test_nested_text_recovery_can_be_paged_with_view(tmp_path: Path, complete: bool) -> None:
    context = _Context(tmp_path)
    stdout = "".join(f'Section {i}: Unicode 界 and a quoted "value".\n' for i in range(900))
    value = {
        "ok": True,
        "stdout": {"text": stdout, "content_complete": complete},
        "stderr": {"text": "diagnostic\n"},
    }
    result = await disclose_text_paths(
        cast(Any, context),
        cast(Any, value),
        text_paths=(("stdout", "text"), ("stderr", "text")),
        content_complete=complete,
    )
    disclosure = result["disclosure"]
    assert disclosure["content_complete"] is complete
    assert tool_output_size(result) <= DEFAULT_TOOL_OUTPUT_CHARS
    saved = context.files[disclosure["output_file_path"].lstrip("/")]
    assert json.loads(saved) == value
    assert disclosure["output_chars"] == len(saved.decode())
    assert disclosure["output_bytes"] == len(saved)
    assert context.files[disclosure["text_file_paths"]["/stderr/text"].lstrip("/")] == b"diagnostic\n"

    files = LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=256 * 1024),
        execution_id="recovery",
        generation="test",
    )
    tools = FileToolset(files)
    ctx = cast(Any, SimpleNamespace(deps=context, capabilities={}))
    offset = 0
    recovered = ""
    while True:
        page = await tools.view(ctx, disclosure["text_file_paths"]["/stdout/text"], line_offset=offset)
        assert page["ok"] and not page["truncated_lines"]
        recovered += page["content"]
        if not page["has_more"]:
            break
        assert page["next_line_offset"] > offset
        offset = page["next_line_offset"]
    assert recovered == stdout
    assert offset > 0


async def test_text_files_use_redacted_values_and_unambiguous_field_paths(tmp_path: Path) -> None:
    context = _Context(tmp_path)
    value = {"content/~": "Bearer secret-value\n" + "line\n" * 4000, "api_key": "secret-value"}
    result = await disclose_text_fields(
        cast(Any, context), value, text_fields=("content/~", "api_key"), content_complete=True
    )
    disclosure = result["disclosure"]
    safe = redact_json(value)
    assert json.loads(context.files[disclosure["output_file_path"].lstrip("/")]) == safe
    for field, pointer in (("content/~", "/content~1~0"), ("api_key", "/api_key")):
        assert context.files[disclosure["text_file_paths"][pointer].lstrip("/")].decode() == safe[field]
    assert all(b"secret-value" not in data for data in context.files.values())


@pytest.mark.parametrize("unavailable", [frozenset({".txt"}), frozenset({".json", ".txt"})])
async def test_unavailable_text_files_preserve_json_fallback(tmp_path: Path, unavailable: frozenset[str]) -> None:
    context = _Context(tmp_path, unavailable)
    result = await disclose_text_fields(
        cast(Any, context), {"content": "line\n" * 4000}, text_fields=("content",), content_complete=False
    )
    disclosure = result["disclosure"]
    assert "text_file_paths" not in disclosure
    assert disclosure["content_complete"] is False
    assert (disclosure["output_file_path"] is None) == (".json" in unavailable)
    assert "text_file_paths" not in disclosure["hint"]


async def test_spill_ceiling_also_bounds_text_recovery_files(tmp_path: Path) -> None:
    context = _Context(tmp_path)
    disclosure = await create_tool_output_disclosure(
        cast(Any, context),
        {"content": "界" * 100},
        content_complete=True,
        text_paths=(("content",),),
        max_spill_bytes=100,
    )
    assert disclosure["output_file_path"] is None
    assert "text_file_paths" not in disclosure
    assert context.files == {}


async def test_small_text_result_does_not_create_recovery_files(tmp_path: Path) -> None:
    context = _Context(tmp_path)
    value = {"content": "short\n"}
    assert (
        await disclose_text_fields(cast(Any, context), value, text_fields=("content",), content_complete=True) == value
    )
    assert context.files == {}
