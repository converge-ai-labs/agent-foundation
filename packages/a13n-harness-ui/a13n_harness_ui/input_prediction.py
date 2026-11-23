"""Process-local replay of a settled root model request for composer suggestions."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any
from uuid import uuid4

from a13n_harness.context import AgentContext
from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext
from a13n_harness.metering import MeteredModel, ModelUsageBinding
from a13n_harness.token_pricing import TokenPricingCapability
from a13n_harness.usage import RunUsageLedger, UsageDeltaReporter, UsageReporter
from a13n_logging import get_logger
from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.direct import model_request
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.root_checkpoint import RootCheckpointCapability
from a13n_harness_ui.surfaces import SurfaceModel

PREDICTION_PROMPT = (
    "Predict the single most likely next message the user would type in this chat. Match their language, tone, "
    "and brevity (usually one short sentence or command). Return only the raw user message, without quotes, "
    "Markdown, preamble, or assistant phrasing. Return no text if the conversation is complete or there is no "
    "clear, concrete next step. Never call tools."
)


class InputPredictionRequest(SurfaceModel):
    continuation_id: str = Field(min_length=1, max_length=128)


class InputPredictionResponse(SurfaceModel):
    continuation_id: str
    text: str | None


@dataclass(frozen=True, slots=True)
class PredictionPrefix:
    received_at: float
    model_id: str
    messages: tuple[ModelMessage, ...]
    parameters: ModelRequestParameters
    settings: ModelSettings | None


class PredictionCapture(AbstractCapability[AgentContext]):
    """Observe the prepared request at the response boundary, never the durable history."""

    id = "a13n.harness_ui.input_prediction"

    def __init__(self) -> None:
        self._run_id: str | None = None
        self.prefix: PredictionPrefix | None = None

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost", wrapped_by=(RootCheckpointCapability,))

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Callable[[], Awaitable[Any]]) -> Any:
        if self._run_id is not None:
            return await handler()
        self._run_id = ctx.run_id
        self.prefix = None
        try:
            return await handler()
        finally:
            self._run_id = None

    async def after_model_request(
        self, ctx: RunContext[AgentContext], *, request_context: ModelRequestContext, response: ModelResponse
    ) -> ModelResponse:
        if ctx.run_id == self._run_id:
            params = request_context.model_request_parameters
            model_id = request_context.model_id
            settings = request_context.model_settings or {}
            extra = settings.get("extra_body")
            self.prefix = None
            # Native provider tools can execute before we see the response. Skip
            # those requests rather than removing declarations from the prefix.
            if (
                model_id is not None
                and not params.native_tools
                # A provider conversation appends both prompt and response to shared history.
                and not settings.get("openai_conversation_id")
                and not settings.get("openai_background")
                and not (
                    isinstance(extra, dict) and any(key in extra for key in ("conversation", "tools", "background"))
                )
                and not any(isinstance(part, ToolCallPart) for part in response.parts)
            ):
                self.prefix = PredictionPrefix(
                    received_at=monotonic(),
                    model_id=model_id,
                    messages=(*request_context.messages, response),
                    parameters=replace(
                        params,
                        function_tools=[replace(tool, metadata=None) for tool in params.function_tools],
                        output_mode="text",
                        output_object=None,
                        output_tools=[],
                        prompted_output_template=None,
                        allow_text_output=True,
                        allow_image_output=False,
                    ),
                    settings=request_context.model_settings,
                )
        return response


@dataclass(frozen=True, slots=True)
class PredictionSnapshot:
    prefix: PredictionPrefix
    resolver: HarnessUiModelResolver
    reporter: UsageReporter | UsageDeltaReporter
    pricing: TokenPricingCapability
    account_id: str | None = None

    async def sample(self, thread_id: str, *, expires_at: float) -> str | None:
        prefix = self.prefix
        model = await self.resolver.fresh().resolve(
            prefix.model_id, thread_id=thread_id, prediction=True, prediction_account=self.account_id
        )
        messages = model.prepare_messages(
            [*prefix.messages, ModelRequest(parts=[UserPromptPart(PREDICTION_PROMPT)])], prefix.parameters
        )
        context = ModelRequestContext(
            model=model,
            model_id=prefix.model_id,
            messages=messages,
            model_settings=prefix.settings,
            model_request_parameters=prefix.parameters,
        )
        source = "input_prediction"
        ledger = RunUsageLedger(
            run_id=f"run-{uuid4().hex}",
            thread_id=thread_id,
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="local", subject=source),
                agent_instance_id=f"agent-{uuid4().hex}",
            ),
            reporter=self.reporter,
        )
        async with model:
            if monotonic() >= expires_at:
                return None
            response = await model_request(
                MeteredModel(context, ModelUsageBinding(ledger, self.pricing, source=source), None),
                messages,
                model_settings=prefix.settings,
                model_request_parameters=prefix.parameters,
                instrument=False,
            )
        if any(isinstance(part, ToolCallPart) for part in response.parts):
            return None
        text = " ".join(" ".join(part.content for part in response.parts if isinstance(part, TextPart)).split())
        return text if 0 < len(text) <= 500 and not any(marker in text for marker in ("\x00", "\ufffc")) else None


@dataclass(slots=True)
class _WarmPrediction:
    continuation_id: str
    expires_at: float
    snapshot: PredictionSnapshot
    expiry: asyncio.TimerHandle
    result: str | None = None
    sampled: bool = False
    task: asyncio.Task[str | None] | None = None
    waiters: int = 0


class InputPredictions:
    """One warm continuation per Thread; all clients share its sample on the owner loop."""

    def __init__(self, *, capacity: int = 64, ttl: float = 300, timeout: float = 15) -> None:
        self._entries: OrderedDict[str, _WarmPrediction] = OrderedDict()
        self._capacity = capacity
        self._ttl = ttl
        self._timeout = timeout

    def evict(self, thread_id: str) -> None:
        if (entry := self._entries.pop(thread_id, None)) is not None:
            entry.expiry.cancel()
            if entry.task is not None:
                entry.task.cancel()

    def clear(self) -> None:
        for thread_id in tuple(self._entries):
            self.evict(thread_id)

    def retain(self, thread_id: str, continuation_id: str, snapshot: PredictionSnapshot) -> None:
        now = monotonic()
        self.evict(thread_id)
        expires_at = snapshot.prefix.received_at + self._ttl
        if expires_at <= now:
            return
        expiry = asyncio.get_running_loop().call_later(expires_at - now, self.evict, thread_id)
        self._entries[thread_id] = _WarmPrediction(continuation_id, expires_at, snapshot, expiry)
        while len(self._entries) > self._capacity:
            self.evict(next(iter(self._entries)))

    async def get(self, thread_id: str, continuation_id: str) -> str | None:
        entry = self._entries.get(thread_id)
        if entry is None or entry.continuation_id != continuation_id:
            return None
        if entry.expires_at <= monotonic():
            self.evict(thread_id)
            return None
        self._entries.move_to_end(thread_id)
        if entry.sampled:
            return entry.result
        if entry.task is None:
            entry.task = asyncio.create_task(self._sample(thread_id, entry))
        task = entry.task
        entry.waiters += 1
        try:
            text = await asyncio.shield(task)
            return text if self._entries.get(thread_id) is entry else None
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                raise
            # A newer Run/continuation or eviction invalidated the shared task.
            return None
        finally:
            entry.waiters -= 1
            if entry.waiters == 0:
                entry.task = None
                if not task.done():
                    task.cancel()

    async def _sample(self, thread_id: str, entry: _WarmPrediction) -> str | None:
        try:
            async with asyncio.timeout(self._timeout):
                text = await entry.snapshot.sample(thread_id, expires_at=entry.expires_at)
        except Exception as exc:
            # This is a best-effort helper; do not log model text or prompt data.
            get_logger(__name__).info("Input prediction unavailable", extra={"error_type": type(exc).__name__})
            text = None
        entry.result = text
        entry.sampled = True
        return text
