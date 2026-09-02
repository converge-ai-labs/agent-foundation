"""Provider-facing compatibility for tool-based structured output."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, cast

from pydantic_ai import ModelMessage, ModelResponse, ModelSettings, RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.usage import RequestUsage

from a13n_harness.context import AgentContext

STRUCTURED_OUTPUT_AUTO_TOOL_CHOICE_CAPABILITY_ID = "a13n.model.structured-output-auto-tool-choice"


class StructuredOutputAutoToolChoiceModel(WrapperModel):
    """Make output tools optional to providers while preserving local validation."""

    @staticmethod
    def _provider_settings(model_settings: ModelSettings | None) -> ModelSettings:
        return cast(ModelSettings, {**(model_settings or {}), "tool_choice": "auto"})

    @staticmethod
    def _provider_parameters(parameters: ModelRequestParameters) -> ModelRequestParameters:
        return replace(parameters, allow_text_output=True)

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        return await self.wrapped.request(
            messages,
            self._provider_settings(model_settings),
            self._provider_parameters(model_request_parameters),
        )

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        async with self.wrapped.request_stream(
            messages,
            self._provider_settings(model_settings),
            self._provider_parameters(model_request_parameters),
            run_context,
        ) as response:
            yield response

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        return await self.wrapped.count_tokens(
            messages,
            self._provider_settings(model_settings),
            self._provider_parameters(model_request_parameters),
        )


@dataclass(init=False)
class StructuredOutputAutoToolChoiceCapability(AbstractCapability[AgentContext]):
    """Apply provider-only auto tool choice to tool-only output requests."""

    id = STRUCTURED_OUTPUT_AUTO_TOOL_CHOICE_CAPABILITY_ID

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost")

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        del ctx
        parameters = request_context.model_request_parameters
        if parameters.output_tools:
            request_context.model = StructuredOutputAutoToolChoiceModel(request_context.model)
        return request_context


__all__ = [
    "STRUCTURED_OUTPUT_AUTO_TOOL_CHOICE_CAPABILITY_ID",
    "StructuredOutputAutoToolChoiceCapability",
    "StructuredOutputAutoToolChoiceModel",
]
