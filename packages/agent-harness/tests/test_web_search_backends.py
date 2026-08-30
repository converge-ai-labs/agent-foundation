from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness import (
    AgentSpec,
    DefinitionError,
    HarnessBuilder,
    RunBindings,
    RunError,
)
from a13n_harness.capabilities import (
    WEB_SCRAPE_BACKEND_PRIORITY_ENV,
    WEB_SCRAPE_MODE_ENV,
    WEB_SEARCH_BACKEND_ENV,
    WEB_SEARCH_BACKEND_PRIORITY_ENV,
    WEB_SEARCH_CONTEXT_SIZE_ENV,
    WEB_SEARCH_MODE_ENV,
    WebCapability,
    WebConfiguration,
    WebProviderError,
    WebRequest,
    WebResponse,
    WebRunCapability,
    WebScrapeBackendBinding,
    WebScrapeConfiguration,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchBackendBinding,
    WebSearchConfiguration,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from a13n_harness.context import AgentContext
from a13n_harness.toolsets import WebToolset
from a13n_harness.toolsets.web import _select_scrape_backend_bindings, _select_search_backend_bindings
from pydantic import ValidationError
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.native_tools import WebSearchTool

pytestmark = pytest.mark.anyio


class _WebPolicy:
    async def authorize(self, url: str, *, purpose: Any) -> None:
        del url, purpose


class _WebClient:
    async def request(self, request: WebRequest, *, policy: Any) -> WebResponse:
        del policy
        raise AssertionError(f"unexpected Web request: {request.url}")


class _WebSearchProvider:
    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        raise AssertionError(f"unexpected Web search: {request.query}")


class _WebScrapeProvider:
    async def scrape(self, request: WebScrapeRequest, *, policy: Any) -> WebScrapeResult:
        del policy
        raise AssertionError(f"unexpected Web scrape: {request.url}")


class _RecordingSearchProvider:
    def __init__(self, backend_id: str, calls: list[str], outcome: object) -> None:
        self.backend_id = backend_id
        self.calls = calls
        self.outcome = outcome

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        del request
        self.calls.append(self.backend_id)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return cast(WebSearchResponse, self.outcome)


class _RecordingScrapeProvider:
    def __init__(self, backend_id: str, calls: list[str], outcome: object) -> None:
        self.backend_id = backend_id
        self.calls = calls
        self.outcome = outcome

    async def scrape(self, request: WebScrapeRequest, *, policy: Any) -> WebScrapeResult:
        del request, policy
        self.calls.append(self.backend_id)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return cast(WebScrapeResult, self.outcome)


class _ToolContext:
    toolset_instructions = True

    async def record_provider_usage(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def _spill_tool_result(self, data: bytes, *, suffix: str) -> str:
        del data, suffix
        raise AssertionError("unexpected tool-result spill")


def _run_context() -> RunContext[AgentContext]:
    return cast(
        RunContext[AgentContext],
        SimpleNamespace(deps=cast(AgentContext, _ToolContext()), tool_call_id="call-1"),
    )


def _configuration(backend: str, *, search_context_size: str = "medium") -> WebConfiguration:
    return WebConfiguration.model_validate(
        {
            "search": {
                "mode": backend,
                "search_context_size": search_context_size,
            }
        }
    )


def _bindings(*, host_search: bool) -> RunBindings:
    return RunBindings.embedded(
        capabilities=(
            WebRunCapability(
                client=_WebClient(),
                policy=_WebPolicy(),
                search_provider=_WebSearchProvider() if host_search else None,
                scrape_provider=_WebScrapeProvider(),
            ),
        )
    )


def _model(
    *,
    supports_native: bool,
    observed: list[AgentInfo],
    dispatched: list[bool] | None = None,
) -> FunctionModel:
    async def stream(
        messages: Sequence[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str]:
        del messages
        if dispatched is not None:
            dispatched.append(True)
        observed.append(info)
        yield "done"

    profile = {"supported_native_tools": frozenset({WebSearchTool}) if supports_native else frozenset()}
    return FunctionModel(stream_function=stream, profile=profile)


def test_web_search_configuration_defaults_to_auto_and_is_strict() -> None:
    configuration = WebConfiguration()

    assert configuration.search == WebSearchConfiguration(mode="auto", search_context_size="medium")
    with pytest.raises(ValidationError):
        WebSearchConfiguration.model_validate({"mode": "provider"})
    with pytest.raises(ValidationError):
        WebSearchConfiguration.model_validate({"backend": "auto", "domains": ["example.com"]})
    with pytest.raises(ValidationError, match="mutually exclusive"):
        WebSearchConfiguration(backend="brave", backend_priority=("tavily",))
    with pytest.raises(ValidationError, match="unique"):
        WebScrapeConfiguration(backend_priority=("local", "local"))


def test_web_configuration_loads_selection_defaults_from_environment() -> None:
    configuration = WebConfiguration.from_environment(
        {
            WEB_SEARCH_MODE_ENV: " host ",
            WEB_SEARCH_BACKEND_ENV: " brave ",
            WEB_SEARCH_BACKEND_PRIORITY_ENV: ",ignored,priority,",
            WEB_SEARCH_CONTEXT_SIZE_ENV: " high ",
            WEB_SCRAPE_MODE_ENV: "host",
            WEB_SCRAPE_BACKEND_PRIORITY_ENV: " firecrawl, local ",
        }
    )

    assert configuration.search == WebSearchConfiguration(
        mode="host",
        backend="brave",
        search_context_size="high",
    )
    assert configuration.scrape == WebScrapeConfiguration(
        mode="host",
        backend_priority=("firecrawl", "local"),
    )


def test_web_capability_uses_environment_only_when_configuration_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WEB_SEARCH_MODE_ENV, "off")
    monkeypatch.setenv(WEB_SCRAPE_MODE_ENV, "off")

    assert WebCapability().configuration.search.mode == "off"
    assert WebCapability().configuration.scrape.mode == "off"

    explicit = WebConfiguration(
        search=WebSearchConfiguration(mode="host"),
        scrape=WebScrapeConfiguration(mode="host"),
    )
    capability = WebCapability(explicit)
    assert capability.configuration == explicit
    assert capability.configuration is not explicit


def test_backend_selection_uses_binding_order_priority_and_exact_selection() -> None:
    search_calls: list[str] = []
    scrape_calls: list[str] = []
    search_backends = tuple(
        WebSearchBackendBinding(
            backend_id,
            _RecordingSearchProvider(backend_id, search_calls, WebSearchResponse(results=())),
        )
        for backend_id in ("google", "brave", "tavily")
    )
    scrape_backends = tuple(
        WebScrapeBackendBinding(
            backend_id,
            _RecordingScrapeProvider(
                backend_id,
                scrape_calls,
                WebScrapeResult(
                    markdown="ok",
                    final_url="https://example.com",
                    canonical_url="https://example.com",
                ),
            ),
        )
        for backend_id in ("local", "firecrawl", "reader")
    )

    assert [item.backend_id for item in _select_search_backend_bindings(search_backends, WebSearchConfiguration())] == [
        "google",
        "brave",
        "tavily",
    ]
    assert [
        item.backend_id
        for item in _select_search_backend_bindings(
            search_backends,
            WebSearchConfiguration(backend_priority=("missing", "tavily", "brave")),
        )
    ] == ["tavily", "brave", "google"]
    assert [
        item.backend_id
        for item in _select_scrape_backend_bindings(
            scrape_backends,
            WebScrapeConfiguration(backend="firecrawl"),
        )
    ] == ["firecrawl"]

    with pytest.raises(DefinitionError) as exc_info:
        _select_search_backend_bindings(search_backends, WebSearchConfiguration(backend="missing"))
    assert exc_info.value.code == "web_search_backend_missing"


async def test_public_web_toolset_applies_exact_and_priority_selection() -> None:
    search_calls: list[str] = []
    scrape_calls: list[str] = []
    search_response = WebSearchResponse(results=())
    scrape_response = WebScrapeResult(
        markdown="selected",
        final_url="https://example.com/selected",
        canonical_url="https://example.com/selected",
    )
    toolset = WebToolset(
        client=_WebClient(),
        policy=_WebPolicy(),
        files=cast(Any, SimpleNamespace()),
        configuration=WebConfiguration(
            search=WebSearchConfiguration(mode="host", backend="second"),
            scrape=WebScrapeConfiguration(backend_priority=("second",)),
        ),
        search_backends=(
            WebSearchBackendBinding(
                "first",
                _RecordingSearchProvider("first", search_calls, search_response),
            ),
            WebSearchBackendBinding(
                "second",
                _RecordingSearchProvider("second", search_calls, search_response),
            ),
        ),
        scrape_backends=(
            WebScrapeBackendBinding(
                "first",
                _RecordingScrapeProvider("first", scrape_calls, scrape_response),
            ),
            WebScrapeBackendBinding(
                "second",
                _RecordingScrapeProvider("second", scrape_calls, scrape_response),
            ),
        ),
    )

    assert (await toolset.search(_run_context(), "query"))["ok"] is True
    assert (await toolset.scrape(_run_context(), "https://example.com/start"))["ok"] is True
    assert search_calls == ["second"]
    assert scrape_calls == ["second"]

    with pytest.raises(DefinitionError) as exc_info:
        WebToolset(
            client=_WebClient(),
            policy=_WebPolicy(),
            files=cast(Any, SimpleNamespace()),
            configuration=WebConfiguration(
                search=WebSearchConfiguration(mode="host", backend="missing"),
            ),
            search_backends=(
                WebSearchBackendBinding(
                    "available",
                    _RecordingSearchProvider("available", search_calls, search_response),
                ),
            ),
        )
    assert exc_info.value.code == "web_search_backend_missing"


def test_run_binding_accepts_many_backends_without_an_arbitrary_count_limit() -> None:
    calls: list[str] = []
    backends = tuple(
        WebSearchBackendBinding(
            f"search-{index}",
            _RecordingSearchProvider(f"search-{index}", calls, WebSearchResponse(results=())),
        )
        for index in range(40)
    )

    capability = WebRunCapability(
        client=_WebClient(),
        policy=_WebPolicy(),
        search_backends=backends,
    )

    assert capability.search_backends == backends


def test_web_capability_contributes_native_search_from_copied_configuration() -> None:
    configuration = _configuration("native", search_context_size="high")
    capability = WebCapability(configuration)
    copied = capability.configuration

    assert copied is not configuration
    assert copied.search is not configuration.search
    assert capability.get_native_tools() == [WebSearchTool(search_context_size="high")]
    assert WebCapability(_configuration("host")).get_native_tools() == []
    assert WebCapability(_configuration("off")).get_native_tools() == []


@pytest.mark.parametrize(
    ("backend", "supports_native", "host_search", "expected_host", "expected_native"),
    [
        ("off", True, True, False, False),
        ("host", True, True, True, False),
        ("native", True, True, False, True),
        ("auto", True, True, False, True),
        ("auto", False, True, True, False),
    ],
)
async def test_web_search_backend_selects_one_effective_surface(
    backend: str,
    supports_native: bool,
    host_search: bool,
    expected_host: bool,
    expected_native: bool,
) -> None:
    observed: list[AgentInfo] = []
    executable = HarnessBuilder().build(
        AgentSpec(name=f"web-search-{backend}"),
        output_type=str,
        model=_model(supports_native=supports_native, observed=observed),
        capabilities=(WebCapability(_configuration(backend)),),
    )

    result = await executable.run("Search when useful", bindings=_bindings(host_search=host_search))

    assert result.output_or_raise() == "done"
    assert len(observed) == 1
    function_names = {tool.name for tool in observed[0].function_tools}
    native_kinds = {tool.kind for tool in observed[0].model_request_parameters.native_tools}
    assert ("search" in function_names) is expected_host
    assert ("web_search" in native_kinds) is expected_native
    assert {"fetch", "scrape", "download"} <= function_names


@pytest.mark.parametrize(
    ("backend", "host_search"),
    [
        ("native", True),
        ("auto", False),
    ],
)
async def test_unsupported_required_native_search_fails_before_model_dispatch(
    backend: str,
    host_search: bool,
) -> None:
    observed: list[AgentInfo] = []
    dispatched: list[bool] = []
    executable = HarnessBuilder().build(
        AgentSpec(name=f"web-search-unsupported-{backend}"),
        output_type=str,
        model=_model(supports_native=False, observed=observed, dispatched=dispatched),
        capabilities=(WebCapability(_configuration(backend)),),
    )

    result = await executable.run("Search", bindings=_bindings(host_search=host_search))

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.code == "agent_run_failed"
    assert dispatched == []
    assert observed == []


async def test_missing_exact_backend_fails_before_model_dispatch() -> None:
    observed: list[AgentInfo] = []
    dispatched: list[bool] = []
    configuration = WebConfiguration(
        search=WebSearchConfiguration(mode="host", backend="missing"),
        scrape=WebScrapeConfiguration(mode="off"),
    )
    executable = HarnessBuilder().build(
        AgentSpec(name="web-search-missing-backend"),
        output_type=str,
        model=_model(supports_native=False, observed=observed, dispatched=dispatched),
        capabilities=(WebCapability(configuration),),
    )

    with pytest.raises(DefinitionError) as exc_info:
        await executable.run("Search", bindings=_bindings(host_search=True))

    assert exc_info.value.code == "web_search_backend_missing"
    assert dispatched == []
    assert observed == []


async def test_search_and_scrape_fall_back_in_selected_backend_order() -> None:
    search_calls: list[str] = []
    scrape_calls: list[str] = []
    search_result = WebSearchResponse(
        results=(
            WebSearchResult(
                title="Result",
                url="https://example.com/result",
                snippet="Found it",
            ),
        )
    )
    scrape_result = WebScrapeResult(
        markdown="# Result",
        final_url="https://example.com/final",
        canonical_url="https://example.com/final",
    )
    policy = _WebPolicy()
    toolset = WebToolset(
        client=_WebClient(),
        policy=policy,
        files=cast(Any, SimpleNamespace()),
        configuration=WebConfiguration(search=WebSearchConfiguration(mode="host")),
        search_backends=(
            WebSearchBackendBinding(
                "first",
                _RecordingSearchProvider("first", search_calls, WebProviderError("first_failed")),
            ),
            WebSearchBackendBinding(
                "second",
                _RecordingSearchProvider("second", search_calls, search_result),
            ),
        ),
        scrape_backends=(
            WebScrapeBackendBinding(
                "first",
                _RecordingScrapeProvider("first", scrape_calls, ValueError("invalid response")),
            ),
            WebScrapeBackendBinding(
                "second",
                _RecordingScrapeProvider("second", scrape_calls, scrape_result),
            ),
        ),
    )

    search = await toolset.search(_run_context(), "query")
    scrape = await toolset.scrape(_run_context(), "https://example.com/start")

    assert search["ok"] is True
    assert search_calls == ["first", "second"]
    assert scrape["ok"] is True
    assert scrape_calls == ["first", "second"]


async def test_oversized_scrape_response_falls_back() -> None:
    calls: list[str] = []
    oversized = WebScrapeResult(
        markdown="too large",
        final_url="https://example.com/oversized",
        canonical_url="https://example.com/oversized",
    )
    valid = WebScrapeResult(
        markdown="ok",
        final_url="https://example.com/valid",
        canonical_url="https://example.com/valid",
    )
    toolset = WebToolset(
        client=_WebClient(),
        policy=_WebPolicy(),
        files=cast(Any, SimpleNamespace()),
        configuration=WebConfiguration(max_scrape_bytes=2),
        scrape_backends=(
            WebScrapeBackendBinding(
                "oversized",
                _RecordingScrapeProvider("oversized", calls, oversized),
            ),
            WebScrapeBackendBinding(
                "valid",
                _RecordingScrapeProvider("valid", calls, valid),
            ),
        ),
    )

    result = await toolset.scrape(_run_context(), "https://example.com/start")

    assert result["ok"] is True
    assert result["markdown"] == "ok"
    assert calls == ["oversized", "valid"]


async def test_valid_empty_search_result_does_not_fall_back() -> None:
    calls: list[str] = []
    toolset = WebToolset(
        client=_WebClient(),
        policy=_WebPolicy(),
        files=cast(Any, SimpleNamespace()),
        configuration=WebConfiguration(search=WebSearchConfiguration(mode="host")),
        search_backends=(
            WebSearchBackendBinding(
                "empty",
                _RecordingSearchProvider("empty", calls, WebSearchResponse(results=())),
            ),
            WebSearchBackendBinding(
                "unused",
                _RecordingSearchProvider("unused", calls, WebProviderError("unexpected")),
            ),
        ),
    )

    result = await toolset.search(_run_context(), "query")

    assert result["ok"] is True
    assert result["results"] == []
    assert calls == ["empty"]


async def test_search_timeout_does_not_fall_back() -> None:
    calls: list[str] = []
    toolset = WebToolset(
        client=_WebClient(),
        policy=_WebPolicy(),
        files=cast(Any, SimpleNamespace()),
        configuration=WebConfiguration(search=WebSearchConfiguration(mode="host")),
        search_backends=(
            WebSearchBackendBinding(
                "timeout",
                _RecordingSearchProvider("timeout", calls, TimeoutError()),
            ),
            WebSearchBackendBinding(
                "unused",
                _RecordingSearchProvider("unused", calls, WebSearchResponse(results=())),
            ),
        ),
    )

    result = await toolset.search(_run_context(), "query")

    assert result == {
        "ok": False,
        "error": {"code": "web_timeout", "retry_hint": "retry"},
    }
    assert calls == ["timeout"]


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (WebProviderError("web_policy_denied"), "web_policy_denied"),
        (TimeoutError(), "web_timeout"),
        (RuntimeError("policy failed"), "web_scrape_failed"),
        (RunError("policy failed", code="web_policy_run_error"), None),
    ],
)
async def test_scrape_policy_failure_does_not_fall_back(
    failure: Exception,
    expected_code: str | None,
) -> None:
    calls: list[str] = []

    class _RedirectDenyingPolicy:
        async def authorize(self, url: str, *, purpose: Any) -> None:
            del purpose
            if url == "https://denied.example.com":
                raise failure

    class _PolicyCheckingProvider:
        async def scrape(self, request: WebScrapeRequest, *, policy: Any) -> WebScrapeResult:
            del request
            calls.append("first")
            await policy.authorize("https://denied.example.com", purpose="scrape")
            raise AssertionError("policy failure should have stopped the provider")

    fallback = WebScrapeResult(
        markdown="unexpected",
        final_url="https://example.com/fallback",
        canonical_url="https://example.com/fallback",
    )
    toolset = WebToolset(
        client=_WebClient(),
        policy=_RedirectDenyingPolicy(),
        files=cast(Any, SimpleNamespace()),
        scrape_backends=(
            WebScrapeBackendBinding("first", _PolicyCheckingProvider()),
            WebScrapeBackendBinding(
                "second",
                _RecordingScrapeProvider("second", calls, fallback),
            ),
        ),
    )

    if expected_code is None:
        with pytest.raises(RunError) as exc_info:
            await toolset.scrape(_run_context(), "https://example.com/start")
        assert exc_info.value.code == "web_policy_run_error"
    else:
        result = await toolset.scrape(_run_context(), "https://example.com/start")
        assert result["ok"] is False
        assert result["error"]["code"] == expected_code
    assert calls == ["first"]


async def test_scrape_off_removes_only_the_scrape_surface() -> None:
    observed: list[AgentInfo] = []
    configuration = WebConfiguration(
        search=WebSearchConfiguration(mode="off"),
        scrape=WebScrapeConfiguration(mode="off"),
    )
    executable = HarnessBuilder().build(
        AgentSpec(name="web-scrape-off"),
        output_type=str,
        model=_model(supports_native=False, observed=observed),
        capabilities=(WebCapability(configuration),),
    )

    result = await executable.run("Fetch when useful", bindings=_bindings(host_search=True))

    assert result.output_or_raise() == "done"
    assert {tool.name for tool in observed[0].function_tools} == {"fetch", "download"}
