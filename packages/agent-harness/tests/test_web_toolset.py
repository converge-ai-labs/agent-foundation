from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness.context import AgentContext
from a13n_harness.toolsets.output import (
    DEFAULT_TOOL_OUTPUT_CHARS,
    disclose_text_fields,
    fit_text_fields_to_limit,
    tool_output_bytes,
    tool_output_size,
)
from a13n_harness.toolsets.web import (
    WebConfiguration,
    WebRequest,
    WebResponse,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchRequest,
    WebSearchResult,
    WebToolset,
)
from pydantic import JsonValue
from pydantic_ai import RunContext

pytestmark = pytest.mark.anyio


class _Context:
    def __init__(self) -> None:
        self.spills: list[bytes] = []

    async def _spill_tool_result(self, data: bytes, *, suffix: str) -> str:
        assert suffix == ".json"
        self.spills.append(data)
        return f"/workspace/tool-result-{len(self.spills)}.json"

    async def record_provider_usage(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs


class _Policy:
    def __init__(self) -> None:
        self.authorized: list[tuple[str, str]] = []

    async def authorize(self, url: str, *, purpose: str) -> None:
        self.authorized.append((url, purpose))


class _SearchProvider:
    async def search(self, request: WebSearchRequest) -> tuple[WebSearchResult, ...]:
        return tuple(
            WebSearchResult(
                title=f"Result {index}",
                url=f"https://example.com/{index}",
                snippet=f"{index}:" + "x" * 4_000,
            )
            for index in range(request.limit)
        )


class _ScrapeProvider:
    async def scrape(self, request: WebScrapeRequest, *, policy: _Policy) -> WebScrapeResult:
        del request, policy
        return WebScrapeResult(
            markdown="m" * 80_000,
            final_url="https://example.com/final",
            canonical_url="https://example.com/final",
        )


class _TextClient:
    async def request(self, request: WebRequest, *, policy: _Policy) -> WebResponse:
        del request, policy

        async def body():
            yield b"a" * 40_000
            yield b"b" * 40_000

        return WebResponse(
            status_code=200,
            final_url="https://example.com/final",
            canonical_url="https://example.com/final",
            headers={"content-type": "text/plain; charset=utf-8"},
            body=body(),
        )


def _run_context() -> tuple[RunContext[AgentContext], _Context]:
    context = _Context()
    run_context = cast(
        RunContext[AgentContext],
        SimpleNamespace(deps=cast(AgentContext, context), tool_call_id="call-1"),
    )
    return run_context, context


def _toolset(**kwargs: Any) -> WebToolset:
    return WebToolset(
        client=kwargs.pop("client", cast(Any, SimpleNamespace())),
        policy=kwargs.pop("policy", _Policy()),
        files=cast(Any, SimpleNamespace()),
        **kwargs,
    )


def test_tool_output_size_uses_serialized_characters_not_utf8_bytes() -> None:
    value = {"content": "界" * 10_000}

    assert tool_output_size(value) < DEFAULT_TOOL_OUTPUT_CHARS
    assert len(tool_output_bytes(value)) > DEFAULT_TOOL_OUTPUT_CHARS
    assert (
        fit_text_fields_to_limit(
            value,
            text_fields=("content",),
            limit=DEFAULT_TOOL_OUTPUT_CHARS,
            suffix="[truncated]",
        )
        == value
    )


async def test_semantic_disclosure_measures_the_redacted_representation() -> None:
    context = _Context()
    value = {"content": "Bearer x\n" * 1_200}

    result = await disclose_text_fields(
        cast(AgentContext, context),
        value,
        text_fields=("content",),
        content_complete=True,
    )

    assert result["content"] != value["content"]
    assert "Bearer x" not in result["content"]
    assert result["disclosure"]["output_chars"] > DEFAULT_TOOL_OUTPUT_CHARS
    assert b"Bearer x" not in context.spills[0]


async def test_web_search_spills_full_results_and_returns_complete_items() -> None:
    ctx, context = _run_context()
    toolset = _toolset(
        search_provider=_SearchProvider(),
        configuration=WebConfiguration(max_search_results=10),
    )

    result = await toolset.search(ctx, "query", num=10)

    assert result["ok"] is True
    assert result["showing"] == len(result["results"])
    assert 0 < result["showing"] < 10
    assert result["disclosure"]["truncated"] is True
    assert result["disclosure"]["output_file_path"] == "/workspace/tool-result-1.json"
    assert tool_output_size(cast(dict[str, JsonValue], result)) <= DEFAULT_TOOL_OUTPUT_CHARS
    spilled = json.loads(context.spills[0])
    assert len(spilled["results"]) == 10
    assert spilled["showing"] == 10


async def test_web_scrape_spills_full_markdown_before_semantic_truncation() -> None:
    ctx, context = _run_context()
    policy = _Policy()
    toolset = _toolset(
        policy=policy,
        scrape_provider=_ScrapeProvider(),
    )

    result = await toolset.scrape(ctx, "https://example.com/start")

    assert result["ok"] is True
    assert len(result["markdown"]) < 80_000
    assert result["disclosure"]["content_complete"] is True
    assert tool_output_size(cast(dict[str, JsonValue], result)) <= DEFAULT_TOOL_OUTPUT_CHARS
    assert json.loads(context.spills[0])["markdown"] == "m" * 80_000
    assert policy.authorized == [
        ("https://example.com/start", "scrape"),
        ("https://example.com/final", "scrape"),
    ]


async def test_web_text_fetch_distinguishes_transport_and_disclosure_truncation() -> None:
    ctx, context = _run_context()
    toolset = _toolset(client=_TextClient())

    result = await toolset.fetch(ctx, "https://example.com/start")

    assert isinstance(result, dict)
    assert result["ok"] is True
    assert result["truncated"] is False
    assert result["disclosure"]["truncated"] is True
    assert result["disclosure"]["content_complete"] is True
    assert tool_output_size(cast(dict[str, JsonValue], result)) <= DEFAULT_TOOL_OUTPUT_CHARS
    spilled = json.loads(context.spills[0])
    assert spilled["content"] == "a" * 40_000 + "b" * 40_000
    assert spilled["truncated"] is False
