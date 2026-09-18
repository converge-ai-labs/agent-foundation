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
    WebFetchConfiguration,
    WebRequest,
    WebResponse,
    WebScrapeBackendBinding,
    WebScrapeConfiguration,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchConfiguration,
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
        self.toolset_instructions = True

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


class _WebProvider:
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
            content="m" * 80_000,
            source_url="https://example.com/final",
            canonical_url="https://example.com/final",
        )


class _CanonicalEscapeProvider:
    async def scrape(self, request: WebScrapeRequest, *, policy: _Policy) -> WebScrapeResult:
        del request, policy
        return WebScrapeResult(
            content="must not disclose",
            source_url="https://example.com/article",
            canonical_url="https://denied.test/article",
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


async def test_web_instructions_follow_provider_activation() -> None:
    ctx, _ = _run_context()

    base_parts = (
        await _toolset(configuration=WebConfiguration(search=WebSearchConfiguration(mode="off")))
        .get_toolset()
        .get_instructions(ctx)
    )
    assert base_parts is not None
    base = "\n".join(part.content for part in base_parts)
    assert '<tool-instruction name="fetch">' in base
    assert '<tool-instruction name="download">' in base
    assert '<tool-instruction name="search">' not in base
    assert '<tool-instruction name="scrape">' not in base

    provider_parts = (
        await _toolset(
            search_provider=_WebProvider(),
            scrape_provider=_ScrapeProvider(),
        )
        .get_toolset()
        .get_instructions(ctx)
    )
    assert provider_parts is not None
    provider = "\n".join(part.content for part in provider_parts)
    assert '<tool-instruction name="search">' in provider
    assert '<tool-instruction name="scrape">' in provider


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
        search_provider=_WebProvider(),
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


async def test_web_scrape_spills_full_content_before_semantic_truncation() -> None:
    ctx, context = _run_context()
    policy = _Policy()
    toolset = _toolset(
        policy=policy,
        scrape_provider=_ScrapeProvider(),
    )

    result = await toolset.scrape(ctx, "https://example.com/start")

    assert result["ok"] is True
    assert len(result["content"]) < 80_000
    assert result["disclosure"]["content_complete"] is True
    assert tool_output_size(cast(dict[str, JsonValue], result)) <= DEFAULT_TOOL_OUTPUT_CHARS
    assert json.loads(context.spills[0])["content"] == "m" * 80_000
    assert policy.authorized == [
        ("https://example.com/start", "scrape"),
        ("https://example.com/final", "scrape"),
    ]


async def test_web_scrape_rejects_denied_canonical_url_before_disclosure() -> None:
    ctx, context = _run_context()
    policy = _Policy()
    toolset = _toolset(
        policy=policy,
        scrape_backends=(
            WebScrapeBackendBinding(
                "domain-capable",
                _CanonicalEscapeProvider(),
                supports_domain_restrictions=True,
            ),
        ),
        configuration=WebConfiguration(
            scrape=WebScrapeConfiguration(allow_domains=("example.com",)),
        ),
    )

    result = await toolset.scrape(ctx, "https://example.com/start")

    assert result["ok"] is False
    assert result["error"]["code"] == "web_domain_denied"
    assert "must not disclose" not in repr(result)
    assert context.spills == []
    assert policy.authorized == [
        ("https://example.com/start", "scrape"),
        ("https://example.com/article", "scrape"),
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


@pytest.mark.parametrize("cancel_caller", [False, True])
@pytest.mark.parametrize("fail_close", [False, True])
async def test_response_cleanup_retains_late_tasks_until_completion(cancel_caller: bool, fail_close: bool) -> None:
    import asyncio
    import gc
    import weakref

    from a13n_harness.toolsets import web

    baseline = set(web._RESPONSE_CLOSE_TASKS)
    started = asyncio.Event()
    resisting = asyncio.Event()
    finished = asyncio.Event()
    pending_ref = None
    failures: list[str] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: failures.append(context["message"]))

    async def close():
        nonlocal pending_ref
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pending = asyncio.Future()
            pending_ref = weakref.ref(pending)
            resisting.set()
            await pending
        finished.set()
        if fail_close:
            raise RuntimeError("late close failure")

    async def body():
        yield b""

    response = WebResponse(
        status_code=200,
        final_url="https://example.com/",
        canonical_url="https://example.com/",
        headers={},
        body=body(),
        _close=close,
    )
    caller = asyncio.create_task(web._close_response(response))
    try:
        async with asyncio.timeout(2):
            await started.wait()
            if cancel_caller:
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
            else:
                await caller
            await resisting.wait()
            while any(task.get_name() == "web-response-close-drain" for task in web._RESPONSE_CLOSE_TASKS - baseline):
                await asyncio.sleep(0.01)
            gc.collect()
            assert failures == []
            assert pending_ref is not None and pending_ref() is not None
            assert len(web._RESPONSE_CLOSE_TASKS - baseline) == 1
            pending_ref().set_result(None)
            await finished.wait()
            while web._RESPONSE_CLOSE_TASKS != baseline:
                await asyncio.sleep(0.01)
            gc.collect()
            assert failures == []
    finally:
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        remaining = web._RESPONSE_CLOSE_TASKS - baseline
        for task in remaining:
            task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
        loop.set_exception_handler(previous_handler)


@pytest.mark.parametrize("behavior", ["success", "error", "cancel"])
async def test_response_cleanup_releases_completed_tasks(behavior: str) -> None:
    import asyncio

    from a13n_harness.toolsets import web

    baseline = set(web._RESPONSE_CLOSE_TASKS)
    calls = 0

    async def close():
        nonlocal calls
        calls += 1
        if behavior == "error":
            raise RuntimeError("close failure")
        if behavior == "cancel":
            await asyncio.Future()

    async def body():
        yield b""

    response = WebResponse(
        status_code=200,
        final_url="https://example.com/",
        canonical_url="https://example.com/",
        headers={},
        body=body(),
        _close=close,
    )
    async with asyncio.timeout(2):
        await web._close_response(response)
        await web._close_response(response)
        await asyncio.sleep(0)
    assert calls == 1
    assert web._RESPONSE_CLOSE_TASKS == baseline


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://example.com/a", True),
        ("https://docs.example.com/a", True),
        ("https://private.example.com", False),
        ("https://notexample.com", False),
        ("https://example.com.evil.test", False),
    ],
)
def test_domain_restrictions_cover_apex_and_subdomains_and_deny_wins(url: str, allowed: bool) -> None:
    from a13n_harness.providers.web.domains import DomainRestrictions

    restrictions = DomainRestrictions(allow_domains=("EXAMPLE.COM.",), deny_domains=("private.example.com",))
    assert restrictions.allows(url) is allowed
    assert DomainRestrictions(allow_domains=("example.com",)).allows("https://docs.example.com")


@pytest.mark.parametrize(
    "domain", ["https://example.com", "example.com:443", "user@example.com", "foo.*.example.com", "bad domain"]
)
def test_invalid_domain_configuration_is_rejected(domain: str) -> None:
    from a13n_harness.providers.web.domains import DomainRestrictions

    with pytest.raises(ValueError):
        DomainRestrictions(allow_domains=(domain,))


async def test_search_domain_restrictions_filter_provider_results() -> None:
    ctx, _ = _run_context()
    toolset = _toolset(
        search_provider=_WebProvider(),
        configuration=WebConfiguration(
            search=WebSearchConfiguration(mode="host", deny_domains=("example.com",)),
        ),
    )
    result = await toolset.search(ctx, "query", num=2)
    assert result["ok"] is True and result["results"] == []


async def test_domain_denial_precedes_web_transport() -> None:
    ctx, _ = _run_context()
    policy = _Policy()
    toolset = _toolset(
        policy=policy,
        configuration=WebConfiguration(fetch=WebFetchConfiguration(deny_domains=("example.com",))),
    )
    result = await toolset.fetch(ctx, "https://example.com/page")
    assert result["ok"] is False and result["error"]["code"] == "web_domain_denied"
    assert policy.authorized == []


def test_restricted_native_search_is_rejected() -> None:
    with pytest.raises(ValueError, match="Host execution"):
        WebSearchConfiguration(mode="native", allow_domains=("example.com",))
