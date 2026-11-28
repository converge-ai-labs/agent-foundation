"""Stateless Gemini Interactions transport for Google AI Plan user quotas."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, override

import anyio
import httpx2
from pydantic_ai.exceptions import ModelAPIError, UserError
from pydantic_ai.messages import (
    ERROR_OUTCOMES,
    BinaryContent,
    FinishReason,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponseStreamEvent,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse, check_allow_model_requests
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.providers import Provider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from ..http import ProviderHttpError, bounded_response_body, retry_after_seconds
from ..providers.model.google_ai_plan import BASE_URL, RESERVED_HEADERS
from ..providers.model.oauth.flow import no_auth
from ..providers.model.oauth.google_ai_plan import SCHEME, GoogleAIPlanCredentialSource, user_agent

INTERACTIONS_URL = f"{BASE_URL}/interactions:createPerUserQuota"
_SKIP_SIGNATURE = "skip_thought_signature_validator"
_ERROR_BYTES = 64 * 1024
_EVENT_BYTES = 4 * 1024 * 1024
_THINKING_LEVELS = {
    "gemini-4-hydrogen": ("low", "medium", "high", "xhigh"),
    "gemini-4-neon": ("low", "medium", "high", "xhigh"),
    "gemini-4-carbon": ("low", "medium", "high"),
    "gemini-3.5-flash-lite": ("minimal", "low", "medium", "high"),
}


class GoogleAIPlanProvider(Provider[httpx2.AsyncClient]):
    def __init__(
        self,
        client: httpx2.AsyncClient,
        *,
        source: GoogleAIPlanCredentialSource | None = None,
        headers: dict[str, str] | None = None,
        auth: httpx2.Auth | Callable[[httpx2.Request], httpx2.Request] | None = None,
    ) -> None:
        if (source is None) == (auth is None):
            raise ValueError("Google AI Plan requires exactly one Host credential source or Bearer auth.")
        self._client = client
        self.auth = auth or no_auth
        self.source = source
        self.headers = headers or {}

    @property
    def name(self) -> str:
        return SCHEME

    @property
    def base_url(self) -> str:
        return BASE_URL

    @property
    def client(self) -> httpx2.AsyncClient:
        return self._client

    @staticmethod
    @override
    def model_profile(model_name: str) -> ModelProfile:
        return {"supports_tools": True, "supports_image_input": True, "supports_thinking": True}


class GoogleAIPlanSettings(ModelSettings, total=False):
    google_plan_thinking_level: Literal["minimal", "low", "medium", "high", "xhigh"]


class GoogleAIPlanError(ModelAPIError):
    """Safe plan error; response bodies and headers may contain credentials."""

    def __init__(self, model_name: str, status: int, reason: str, *, retryable: bool = False) -> None:
        hint = {
            "daily_limit": "Daily Google AI Plan quota reached; switch models or wait for quota reset.",
            "model_not_on_plan": "This model is not available on the Google AI Plan.",
            "sign_in": "Google AI Plan authorization is no longer valid. Sign in again.",
            "disabled": "Google AI Plan is unavailable for this application.",
            "ineligible": "This Google account is not eligible for Google AI Plan access.",
            "rate_limit": "Google AI Plan temporarily rate limited this request.",
            "unexpected_response": "Google AI Plan returned an unexpected response.",
        }.get(reason, "Google AI Plan rejected this request.")
        self.status_code = status
        self.reason = reason
        self.retryable = retryable
        super().__init__(model_name=model_name, message=hint)


def _object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _decode_error(body: str) -> dict[str, Any]:
    data = "\n".join(line[5:].lstrip() for line in body.splitlines() if line.startswith("data:")) or body
    try:
        decoded: object = json.loads(data)
    except ValueError:
        return {}
    if isinstance(decoded, list):
        decoded = decoded[0] if decoded else None
    obj = _object(decoded)
    return _object(obj.get("error", obj))


def classify_error(model_name: str, status: int, body: str) -> GoogleAIPlanError:
    error = _decode_error(body)
    text = str(error.get("message", "")).lower()
    details = error.get("details")
    reasons: set[str] = set()
    daily_limits: list[object] = []
    if isinstance(details, list):
        for item in details:
            detail = _object(item)
            reasons.add(str(detail.get("reason", "")))
            if isinstance(violations := detail.get("violations"), list):
                daily_limits.extend(
                    quota.get("quotaValue")
                    for item in violations
                    if "perday" in str((quota := _object(item)).get("quotaId", "")).lower()
                )
    reason, retry = "rejected", False
    if status == 429:
        limit = re.search(r"\(limit: (\d+) (?:tokens|requests) per day\)", text)
        if any(str(value) == "0" for value in daily_limits) or (limit is not None and limit[1] == "0"):
            reason = "model_not_on_plan"
        elif daily_limits or limit or re.search(r"reached today's .*? limit", text):
            reason = "daily_limit"
        else:
            reason, retry = "rate_limit", True
    elif status == 400 and "not supported for per-user quota" in text:
        reason = "model_not_on_plan"
    elif status == 401 or (
        status == 403 and ("ACCESS_TOKEN_SCOPE_INSUFFICIENT" in reasons or "insufficient authentication scopes" in text)
    ):
        reason = "sign_in"
    elif status == 403 and ("SERVICE_DISABLED" in reasons or "oauth client is not authorized" in text):
        reason = "disabled"
    elif status in (402, 403):
        reason = "ineligible"
    elif status in (408, 409, 500, 502, 503, 504):
        retry = True
    return GoogleAIPlanError(model_name, status, reason, retryable=retry)


def _content(items: Sequence[object]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            if item:
                content.append({"type": "text", "text": item})
        elif isinstance(item, TextContent):
            content.append({"type": "text", "text": item.content})
        elif isinstance(item, BinaryContent) and item.is_image:
            content.append(
                {"type": "image", "mime_type": item.media_type, "data": base64.b64encode(item.data).decode()}
            )
        elif isinstance(item, ImageUrl):
            content.append({"type": "image", "mime_type": item.media_type, "uri": item.url})
        else:
            raise UserError("Google AI Plan supports text and image inputs.")
    return content


def _step(type_: str, content: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": type_, "content": content}


def _payload(
    model_name: str,
    messages: list[ModelMessage],
    settings: ModelSettings,
    params: ModelRequestParameters,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    system: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, SystemPromptPart):
                    system.append(part.content)
                elif isinstance(part, UserPromptPart):
                    content = _content([part.content] if isinstance(part.content, str) else part.content)
                    if content:
                        steps.append(_step("user_input", content))
                elif isinstance(part, RetryPromptPart) and not part.tool_name:
                    steps.append(_step("user_input", _content([part.model_response()])))
                elif isinstance(part, (ToolReturnPart, RetryPromptPart)):
                    result = (
                        part.content_items(mode="str", wrap_if_error=False)
                        if isinstance(part, ToolReturnPart)
                        else [part.model_response()]
                    )
                    steps.append(
                        {
                            "type": "function_result",
                            "call_id": part.tool_call_id,
                            "name": part.tool_name,
                            "result": _content(result),
                            "is_error": isinstance(part, RetryPromptPart) or part.outcome in ERROR_OUTCOMES,
                        }
                    )
        else:
            keep_signature = message.provider_name == SCHEME and message.model_name == model_name
            signed = False
            for part in message.parts:
                if isinstance(part, ThinkingPart) and part.signature and keep_signature:
                    thought: dict[str, Any] = {"type": "thought", "signature": part.signature}
                    if part.content:
                        thought["summary"] = _content([part.content])
                    steps.append(thought)
                    signed = True
                elif isinstance(part, TextPart) and part.content:
                    if steps and steps[-1]["type"] == "model_output":
                        steps[-1]["content"].extend(_content([part.content]))
                    else:
                        steps.append(_step("model_output", _content([part.content])))
                elif isinstance(part, ToolCallPart):
                    if not signed:
                        steps.append({"type": "thought", "signature": _SKIP_SIGNATURE})
                        signed = True
                    steps.append(
                        {
                            "type": "function_call",
                            "id": part.tool_call_id,
                            "name": part.tool_name,
                            "arguments": part.args_as_dict(),
                        }
                    )
    body: dict[str, Any] = {"model": model_name, "store": False, "stream": True, "input": steps}
    if params.instruction_parts:
        system.extend(part.content for part in params.instruction_parts)
    if system:
        body["system_instruction"] = "\n\n".join(system)
    tools = [*params.function_tools, *params.output_tools]
    generation: dict[str, object] = {}
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters_json_schema,
            }
            for tool in tools
        ]
        generation["tool_choice"] = "auto" if params.allow_text_output else "any"
    if (max_tokens := settings.get("max_tokens")) is not None:
        generation["max_output_tokens"] = max_tokens
    level: object = settings.get("google_plan_thinking_level")  # pyright: ignore[reportArgumentType]
    if level is None and params.thinking:
        level = "medium" if params.thinking is True else params.thinking
    if level is not None:
        levels = _THINKING_LEVELS.get(model_name)
        if levels is not None and level not in levels:
            level = levels[0] if level == "minimal" else levels[-1]
        generation.update(thinking_level=level, thinking_summaries="auto")
    if generation:
        body["generation_config"] = generation
    return body


async def _sse(response: httpx2.Response) -> AsyncIterator[dict[str, Any]]:
    # Frame bytes before decoding; HTTP chunks can split both UTF-8 and SSE delimiters.
    pending = bytearray()
    data: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        for index, line in enumerate(chunk.split(b"\n")):
            if index:
                value = bytes(pending).removesuffix(b"\r")
                pending.clear()
                if value.startswith(b"data:"):
                    value = value[5:].removeprefix(b" ")
                    size += len(value) + 1
                    data.append(value)
                elif not value and data:
                    payload = b"\n".join(data)
                    size = 0
                    data.clear()
                    if payload != b"[DONE]":
                        decoded: object = json.loads(payload)
                        if isinstance(decoded, dict):
                            yield decoded
            pending.extend(line)
            if size + len(pending) > _EVENT_BYTES:
                raise ValueError("Google AI Plan event exceeds its size limit")


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _token_count(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


@dataclass
class _GoogleStream(StreamedResponse):
    name: str
    response: httpx2.Response
    _timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    _usage_received: bool | None = field(default=False, init=False)
    _steps: dict[int, str] = field(default_factory=dict, init=False)
    _signatures: dict[int, str] = field(default_factory=dict, init=False)

    @property
    def model_name(self) -> str:
        return self.name

    @property
    def provider_name(self) -> str:
        return SCHEME

    @property
    def provider_url(self) -> str:
        return BASE_URL

    @property
    def timestamp(self) -> datetime:
        return self._timestamp

    async def _get_event_iterator(self) -> AsyncIterator[ModelResponseStreamEvent]:
        try:
            async for event in _sse(self.response):
                kind = event.get("event_type")
                if kind == "interaction.completed":
                    self._complete(_object(event.get("interaction")))
                    return
                if kind == "error":
                    error = _object(event.get("error"))
                    code = error.get("code")
                    raise classify_error(self.name, code if isinstance(code, int) else 0, json.dumps(event))
                index = event.get("index")
                if type(index) is not int:
                    continue
                if kind == "step.start":
                    for delta in self._start(index, _object(event.get("step"))):
                        yield delta
                elif kind == "step.delta":
                    for delta in self._delta(index, _object(event.get("delta"))):
                        yield delta
                elif kind == "step.stop":
                    self._steps.pop(index, None)
                    self._signatures.pop(index, None)
        except (ValueError, httpx2.HTTPError):
            raise ModelAPIError(
                self.name, "The Google AI Plan stream could not be decoded or was interrupted."
            ) from None
        raise ModelAPIError(self.name, "The Google AI Plan stream ended before interaction.completed.")

    def _start(self, index: int, step: dict[str, Any]) -> Iterable[ModelResponseStreamEvent]:
        kind = _text(step.get("type"))
        self._steps[index] = kind
        if kind == "model_output":
            for content in step.get("content") or []:
                block = _object(content)
                if block.get("type") == "text":
                    yield from self._parts_manager.handle_text_delta(
                        vendor_part_id=index, content=_text(block.get("text"))
                    )
        elif kind == "thought":
            summary = "".join(_text(_object(value).get("text")) for value in step.get("summary") or [])
            signature = _text(step.get("signature"))
            if signature:
                self._signatures[index] = signature
            if summary or signature:
                yield from self._parts_manager.handle_thinking_delta(
                    vendor_part_id=index, content=summary, signature=signature or None, provider_name=SCHEME
                )
        elif kind == "function_call":
            call_id, tool_name = _text(step.get("id")), _text(step.get("name"))
            if not call_id or not tool_name:
                raise ValueError("A Google AI Plan tool call must have a name and ID")
            args = _object(step.get("arguments"))
            yield self._parts_manager.handle_tool_call_part(
                vendor_part_id=index,
                tool_name=tool_name,
                args=json.dumps(args) if args else "",
                tool_call_id=call_id,
                provider_name=SCHEME,
            )

    def _delta(self, index: int, delta: dict[str, Any]) -> Iterable[ModelResponseStreamEvent]:
        kind, change = self._steps.get(index), delta.get("type")
        if kind == "model_output" and change == "text":
            return self._parts_manager.handle_text_delta(vendor_part_id=index, content=_text(delta.get("text")))
        if kind == "thought" and change == "thought_summary":
            return self._parts_manager.handle_thinking_delta(
                vendor_part_id=index, content=_text(_object(delta.get("content")).get("text")), provider_name=SCHEME
            )
        if kind == "thought" and change == "thought_signature":
            signature = self._signatures.get(index, "") + _text(delta.get("signature"))
            self._signatures[index] = signature
            return self._parts_manager.handle_thinking_delta(
                vendor_part_id=index, signature=signature, provider_name=SCHEME
            )
        if kind == "function_call" and change == "arguments_delta":
            return self._parts_manager.handle_tool_call_delta_events(
                vendor_part_id=index, args=_text(delta.get("arguments"))
            )
        return ()

    def _complete(self, interaction: dict[str, Any]) -> None:
        statuses: dict[str, FinishReason] = {
            "completed": "stop",
            "requires_action": "tool_call",
            "incomplete": "length",
        }
        if (reason := statuses.get(_text(interaction.get("status")))) is None:
            raise ModelAPIError(self.name, "Google AI Plan reported an unsuccessful interaction.")
        self.finish_reason = reason
        self.provider_response_id = _text(interaction.get("id")) or None
        if usage := _object(interaction.get("usage")):
            thought = _token_count(usage.get("total_thought_tokens"))
            self._add_usage(
                RequestUsage(
                    input_tokens=_token_count(usage.get("total_input_tokens")),
                    output_tokens=_token_count(usage.get("total_output_tokens")) + thought,
                    cache_read_tokens=_token_count(usage.get("total_cached_tokens")),
                    details={"reasoning_tokens": thought},
                ),
                reported=True,
            )


class GoogleAIPlanModel(Model[httpx2.AsyncClient]):
    def __init__(self, model_name: str, *, provider: GoogleAIPlanProvider) -> None:
        self._name = model_name
        self._provider = self._plan_provider = provider
        super().__init__(profile=provider.model_profile(model_name))

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def system(self) -> str:
        return SCHEME

    @property
    def base_url(self) -> str:
        return BASE_URL

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        async with self.request_stream(messages, model_settings, model_request_parameters) as response:
            async for _ in response:
                pass
            return response.get()

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncGenerator[StreamedResponse]:
        check_allow_model_requests()
        settings, params = self.prepare_request(model_settings, model_request_parameters)
        body = _payload(self.model_name, messages, settings or {}, params)
        headers = httpx2.Headers(self._plan_provider.headers)
        if settings:
            headers.update(settings.get("extra_headers") or {})
        for name in RESERVED_HEADERS:
            headers.pop(name, None)
        headers.update({"User-Agent": user_agent(), "Accept": "text/event-stream", "Content-Type": "application/json"})
        source = self._plan_provider.source
        current = await source.current() if source is not None else None
        renewed = False
        async with AsyncExitStack() as stack:
            for status_attempt in range(2):
                while True:
                    if current is not None:
                        headers["Authorization"] = f"Bearer {current.access_token}"
                    try:
                        response = await stack.enter_async_context(
                            self._plan_provider.client.stream(
                                "POST",
                                INTERACTIONS_URL,
                                json=body,
                                headers=headers,
                                auth=self._plan_provider.auth,
                                follow_redirects=False,
                            )
                        )
                    except httpx2.HTTPError:
                        raise ModelAPIError(self.model_name, "Google AI Plan could not be reached.") from None
                    if response.status_code == 401 and not renewed and source is not None and current is not None:
                        await stack.aclose()
                        current = await source.refresh(current)
                        renewed = True
                        continue
                    break
                if response.is_success:
                    if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
                        raise GoogleAIPlanError(self.model_name, response.status_code, "unexpected_response")
                    stream = _GoogleStream(params, self.model_name, response)
                    yield stream
                    return
                try:
                    error_body = await bounded_response_body(response, max_bytes=_ERROR_BYTES)
                except (ProviderHttpError, httpx2.HTTPError):
                    raise GoogleAIPlanError(self.model_name, response.status_code, "unexpected_response") from None
                error = classify_error(self.model_name, response.status_code, error_body.decode(errors="replace"))
                delay = retry_after_seconds(response.headers.get("Retry-After", "1"), max_seconds=10)
                await stack.aclose()
                if not error.retryable or status_attempt or delay is None:
                    raise error
                await anyio.sleep(delay)
