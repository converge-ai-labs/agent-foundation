"""Process-local replay of a settled root model request for composer suggestions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, cast
from uuid import uuid4

from a13n_harness.context import AgentContext
from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext
from a13n_harness.metering import MeteredModel, ModelUsageBinding
from a13n_harness.token_pricing import TokenPricingCapability
from a13n_harness.usage import RunUsageLedger, UsageDeltaReporter, UsageReporter
from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.direct import model_request
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.settings import ModelSettings, merge_model_settings

from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.root_checkpoint import RootCheckpointCapability
from a13n_harness_ui.surfaces import SurfaceModel

PREDICTION_PROMPT = (
    "Predict the single most likely next message the user would type in this chat. Match their language, tone, "
    "and level of detail. Return only the raw user message, without surrounding quotes, "
    "Markdown, preamble, or assistant phrasing. Return no text if the conversation is complete or there is no "
    "clear, concrete next step. Never call tools."
)


def _can_sample_settings(settings: ModelSettings | None) -> bool:
    if not settings:
        return True
    # Raw request bodies can reintroduce provider tools or override the input.
    # Provider-managed caches can carry their own tool declarations. A prediction
    # must also stay out of shared conversations and containers.
    return not (
        settings.get("extra_body")
        or settings.get("openai_conversation_id")
        or settings.get("openai_background")
        or settings.get("anthropic_container")
        or settings.get("google_cached_content")
    )


class InputPredictionRequest(SurfaceModel):
    continuation_id: str = Field(min_length=1, max_length=128)


class InputPredictionResponse(SurfaceModel):
    continuation_id: str
    text: str | None


@dataclass(frozen=True, slots=True)
class PredictionPrefix:
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
            self.prefix = None
            if (
                model_id is not None
                and _can_sample_settings(request_context.model_settings)
                and not any(isinstance(part, ToolCallPart) for part in response.parts)
            ):
                self.prefix = PredictionPrefix(
                    model_id=model_id,
                    messages=(*request_context.messages, response),
                    parameters=replace(
                        params,
                        function_tools=[replace(tool, metadata=None) for tool in params.function_tools],
                        # Provider tools can execute before we see the response. Keep
                        # the history, but don't advertise those tools on this request.
                        native_tools=[],
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

    async def sample(self, thread_id: str) -> str | None:
        prefix = self.prefix
        model = await self.resolver.fresh().resolve(
            prefix.model_id, thread_id=thread_id, prediction=True, prediction_account=self.account_id
        )
        settings = prefix.settings
        effective = merge_model_settings(model.settings, settings)
        if not _can_sample_settings(effective):
            return None
        # Responses also accepts raw native tools in settings; override any
        # defaults that the freshly resolved model would otherwise merge back in.
        if "openai_native_tools" in (effective or {}):
            settings = cast(ModelSettings, {**(settings or {}), "openai_native_tools": ()})
        messages = model.prepare_messages(
            [*prefix.messages, ModelRequest(parts=[UserPromptPart(PREDICTION_PROMPT)])], prefix.parameters
        )
        context = ModelRequestContext(
            model=model,
            model_id=prefix.model_id,
            messages=messages,
            model_settings=settings,
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
            response = await model_request(
                MeteredModel(context, ModelUsageBinding(ledger, self.pricing, source=source), None),
                messages,
                model_settings=settings,
                model_request_parameters=prefix.parameters,
                instrument=False,
            )
        if any(isinstance(part, (ToolCallPart, NativeToolCallPart)) for part in response.parts):
            return None
        text = " ".join(" ".join(part.content for part in response.parts if isinstance(part, TextPart)).split())
        return text if text and not any(marker in text for marker in ("\x00", "\ufffc")) else None
