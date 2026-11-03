"""Thread affinity and per-run routing state layered on the official Codex provider."""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cached_property
from types import TracebackType
from typing import Any, Self, cast

import httpx2
from a13n_logging import get_logger
from anyio import Lock
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelHTTPError, UserError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models._openai_responses_websocket import ResponsesWebSocketStream
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai_codex import OpenAICodexCredentialSource, OpenAICodexProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage

from a13n_harness.http import outbound_tls_verify
from a13n_harness.model_affinity import derive_model_affinity_id
from a13n_harness.models.responses import ResponsesModel

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


class _CodexResponsesModel(ResponsesModel):
    """Bind the upstream credential engine to its native WebSocket reservation seam."""

    def __init__(self, model_name: str, *, provider: OpenAICodexProvider, owner: CodexRequestModel) -> None:
        self._codex_provider = provider
        self._owner = owner
        super().__init__(
            model_name,
            provider=provider,
            settings=OpenAIResponsesModelSettings(openai_transport="websocket"),
            response_headers=owner._capture_handshake,
        )

    @cached_property
    def profile(self) -> OpenAIModelProfile:
        return {
            **super().profile,
            "openai_responses_supports_websocket": True,
            "openai_responses_supports_websocket_stream_id": False,
        }

    def _websocket_unsupported_reason(self) -> str | None:
        # This adapter prepares the known Codex OAuth credentials before every reservation.
        # Keep the HTTP auth flow intact for refresh and native HTTP fallback.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return "Responses WebSockets need the asyncio event loop."
        return None

    async def _reserve_websocket_lane(
        self, loop: asyncio.AbstractEventLoop, messages: Sequence[ModelMessage], settings: OpenAIResponsesModelSettings
    ) -> ResponsesWebSocketStream | None:
        endpoint = httpx2.URL(self.client.websocket_base_url or self.client.base_url)
        if (
            endpoint.scheme not in {"https", "wss"}
            or endpoint.host != "chatgpt.com"
            or endpoint.port not in (None, 443)
        ):
            raise UserError("Codex WebSocket credentials require the HTTPS Codex endpoint")
        credentials, replay = await self._codex_provider._prepare_request_credentials()

        async def reserve() -> ResponsesWebSocketStream | None:
            headers = {**settings.get("extra_headers", {}), **self._codex_provider._auth_headers(credentials)}
            return await super(_CodexResponsesModel, self)._reserve_websocket_lane(
                loop, messages, {**settings, "extra_headers": headers}
            )

        try:
            response = await reserve()
        except ModelHTTPError as error:
            if error.status_code != 401:
                raise
            # Only the opening handshake was rejected; no response.create was dispatched.
            credentials = await replay()
            response = await reserve()
        if response is not None:
            socket = response.websocket
            # The handshake hook captured state in the opening request's Run scope.
            # Teach the native header-equality check that this state belongs to this socket.
            current = self._owner._request_headers.current()
            if _CODEX_TURN_STATE_HEADER in current:
                socket.headers = {**socket.headers, _CODEX_TURN_STATE_HEADER: current[_CODEX_TURN_STATE_HEADER]}
        return response


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
        response_headers: Callable[[Mapping[str, str]], Awaitable[None]] | None = None,
    ) -> None:
        self._response_headers = response_headers
        self._thread_id = thread_id
        self._model_name = model_name
        self._credential_source = credential_source
        self._request_headers = _CodexRequestHeaders(model_name)
        self._owns_client = http_client is None
        self._client = http_client or httpx2.AsyncClient(verify=outbound_tls_verify(), follow_redirects=False)
        self._entries = 0
        self._entry_lock = Lock()
        super().__init__(self._create_model())

    def _create_model(self) -> OpenAIResponsesModel:
        self._client.follow_redirects = False
        provider = OpenAICodexProvider(credential_source=self._credential_source, http_client=self._client)
        self._client.event_hooks["request"].append(self._prepare_request)
        self._client.event_hooks["response"].append(self._capture_response)
        return _CodexResponsesModel(self._model_name, provider=provider, owner=self)

    async def _prepare_request(self, request: httpx2.Request) -> None:
        for name in _CODEX_DYNAMIC_HEADERS:
            request.headers.pop(name, None)
        if request.url.scheme == "https" and request.url.host == "chatgpt.com" and request.url.port in (None, 443):
            request.headers.update(self._request_headers.current())
        else:
            for name in ("authorization", "chatgpt-account-id", "originator"):
                request.headers.pop(name, None)

    async def _capture_handshake(self, headers: Mapping[str, str]) -> None:
        self._request_headers.capture(headers)
        await self._observe_headers(headers)

    async def _capture_response(self, response: httpx2.Response) -> None:
        if response.url.scheme == "https" and response.url.host == "chatgpt.com" and response.url.port in (None, 443):
            if response.is_success:
                self._request_headers.capture(response.headers)
            await self._observe_headers(response.headers)

    async def _observe_headers(self, headers: Mapping[str, str]) -> None:
        if self._response_headers is not None:
            try:
                await self._response_headers(headers)
            except Exception:
                # Observational failures must never turn a delivered response into a retry.
                get_logger("a13n_harness.models.codex").warning("Codex response-header observer failed")

    async def __aenter__(self) -> Self:
        async with self._entry_lock:
            if self._owns_client and self._client.is_closed:
                self._client = httpx2.AsyncClient(verify=outbound_tls_verify(), follow_redirects=False)
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
            affinity_id = derive_model_affinity_id(thread_id)
            for name in ("session-id", "thread-id", "x-client-request-id"):
                if name not in lower:
                    headers[name] = affinity_id
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
