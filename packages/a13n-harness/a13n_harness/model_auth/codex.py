"""Thread affinity and per-run routing state layered on the official Codex provider."""

from __future__ import annotations

import weakref
from collections.abc import AsyncGenerator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self, cast

import httpx2
from anyio import Lock
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openai_codex import OpenAICodexCredentialSource, OpenAICodexProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage

_CODEX_ROUTING_HINT_HEADER = "x-codex-routing-hint"
_CODEX_TURN_STATE_HEADER = "x-codex-turn-state"
_CODEX_DYNAMIC_HEADERS = frozenset({_CODEX_ROUTING_HINT_HEADER, _CODEX_TURN_STATE_HEADER})


@dataclass(slots=True)
class _CodexTurnState:
    value: str | None = None

    def capture(self, headers: Mapping[str, str]) -> None:
        if self.value is not None:
            return
        for name in headers:
            if name.lower() != _CODEX_TURN_STATE_HEADER:
                continue
            try:
                value = headers[name]
            except LookupError:
                return
            if normalized := value.strip():
                self.value = normalized
            return


@dataclass(slots=True)
class _CodexRequestScope:
    turn_state: _CodexTurnState | None
    routing_hint: str | None = None


class _CodexRequestHeaders:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._turn_states: dict[str, tuple[weakref.ReferenceType[RunUsage], _CodexTurnState]] = {}
        self._active_request: ContextVar[_CodexRequestScope | None] = ContextVar(
            f"a13n_harness.codex_request_headers.{id(self)}",
            default=None,
        )

    @contextmanager
    def scope(self, run_context: RunContext[Any] | None) -> Iterator[None]:
        token = self._active_request.set(_CodexRequestScope(turn_state=self._state_for_run(run_context)))
        try:
            yield
        finally:
            self._active_request.reset(token)

    def apply(self, model_settings: ModelSettings) -> ModelSettings:
        settings: dict[str, Any] = dict(model_settings)
        raw_headers = cast(Mapping[str, str] | None, settings.get("extra_headers"))
        headers = {
            name: value for name, value in (raw_headers or {}).items() if name.lower() not in _CODEX_DYNAMIC_HEADERS
        }
        service_tier = settings.get("openai_service_tier") or settings.get("service_tier")
        routing_hint = f"model={self._model_name}"
        if isinstance(service_tier, str) and service_tier:
            routing_hint = f"{routing_hint};tier={service_tier}"
        active_request = self._active_request.get()
        if active_request is None:
            raise RuntimeError("Codex request headers must be prepared inside a request scope")
        active_request.routing_hint = routing_hint
        headers.update(self.current())
        settings["extra_headers"] = headers
        return cast(ModelSettings, settings)

    def current(self) -> Mapping[str, str]:
        active_request = self._active_request.get()
        if active_request is None or active_request.routing_hint is None:
            return {}
        headers = {_CODEX_ROUTING_HINT_HEADER: active_request.routing_hint}
        if active_request.turn_state is not None and active_request.turn_state.value is not None:
            headers[_CODEX_TURN_STATE_HEADER] = active_request.turn_state.value
        return headers

    def capture(self, headers: Mapping[str, str]) -> None:
        active_request = self._active_request.get()
        if active_request is not None and active_request.turn_state is not None:
            active_request.turn_state.capture(headers)

    def _state_for_run(self, run_context: RunContext[Any] | None) -> _CodexTurnState | None:
        if not isinstance(run_context, RunContext) or not run_context.run_id:
            return None
        run_id = run_context.run_id
        existing = self._turn_states.get(run_id)
        if existing is not None and existing[0]() is run_context.usage:
            return existing[1]

        state = _CodexTurnState()
        owner_ref = weakref.ref(self)

        def remove_state(usage_ref: weakref.ReferenceType[RunUsage]) -> None:
            owner = owner_ref()
            if owner is None:
                return
            current = owner._turn_states.get(run_id)
            if current is not None and current[0] is usage_ref:
                owner._turn_states.pop(run_id, None)

        usage_ref = weakref.ref(run_context.usage, remove_state)
        self._turn_states[run_id] = (usage_ref, state)
        return state


class CodexRequestModel(WrapperModel):
    """Add Harness request affinity without reimplementing Codex authentication or dialect.

    Pydantic AI owns credentials, refresh, retries, and Responses rendering. This
    adapter owns only its response-header hook and, unless supplied, its HTTP client.
    """

    def __init__(
        self,
        model_name: str,
        *,
        credential_source: OpenAICodexCredentialSource,
        http_client: httpx2.AsyncClient | None = None,
        thread_id: str | None = None,
    ) -> None:
        self._thread_id = thread_id
        self._model_name = model_name
        self._credential_source = credential_source
        self._request_headers = _CodexRequestHeaders(model_name)
        self._owns_client = http_client is None
        self._client = http_client or httpx2.AsyncClient(follow_redirects=False)
        self._entries = 0
        self._entry_lock = Lock()
        super().__init__(self._create_model())

    def _create_model(self) -> OpenAIResponsesModel:
        self._client.follow_redirects = False
        provider = OpenAICodexProvider(credential_source=self._credential_source, http_client=self._client)
        self._client.event_hooks["request"].append(self._prepare_request)
        self._client.event_hooks["response"].append(self._capture_response)
        return OpenAIResponsesModel(self._model_name, provider=provider)

    async def _prepare_request(self, request: httpx2.Request) -> None:
        for name in _CODEX_DYNAMIC_HEADERS:
            request.headers.pop(name, None)
        if request.url.scheme == "https" and request.url.host == "chatgpt.com" and request.url.port in (None, 443):
            request.headers.update(self._request_headers.current())
        else:
            for name in ("authorization", "chatgpt-account-id", "originator"):
                request.headers.pop(name, None)

    async def _capture_response(self, response: httpx2.Response) -> None:
        if response.url.scheme == "https" and response.url.host == "chatgpt.com" and response.is_success:
            self._request_headers.capture(response.headers)

    async def __aenter__(self) -> Self:
        async with self._entry_lock:
            if self._owns_client and self._client.is_closed:
                self._client = httpx2.AsyncClient(follow_redirects=False)
                self.wrapped = self._create_model()
            await self.wrapped.__aenter__()
            self._entries += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        async with self._entry_lock:
            try:
                return await self.wrapped.__aexit__(exc_type, exc_val, exc_tb)
            finally:
                self._entries -= 1
                if self._owns_client and self._entries == 0:
                    await self._client.aclose()

    def _affinity_settings(self, model_settings: ModelSettings | None) -> ModelSettings:
        settings: dict[str, Any] = dict(model_settings or {})
        raw_headers = cast(Mapping[str, str] | None, settings.get("extra_headers"))
        headers = dict(raw_headers or {})
        lower = {name.lower() for name in headers}
        thread_id = self._thread_id
        if thread_id is not None:
            for name in ("session-id", "thread-id", "x-client-request-id"):
                if name not in lower:
                    headers[name] = thread_id
        settings["extra_headers"] = headers
        return cast(ModelSettings, settings)

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        with self._request_headers.scope(None):
            settings = self._request_headers.apply(self._affinity_settings(model_settings))
            return await self.wrapped.request(messages, settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        with self._request_headers.scope(run_context):
            settings = self._request_headers.apply(self._affinity_settings(model_settings))
            async with self.wrapped.request_stream(
                messages, settings, model_request_parameters, run_context
            ) as response:
                yield response


__all__ = ["CodexRequestModel"]
