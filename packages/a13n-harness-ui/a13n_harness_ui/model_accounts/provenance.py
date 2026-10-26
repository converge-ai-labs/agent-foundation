"""MAIN-only attribution scoped to an actual model request, never tool inference."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import Any

from a13n_harness.context import AgentContext
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestContext, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from .models import MainAccountSelection

# A request-local wrapper establishes this only while MAIN provider I/O is active.
# Auxiliary calls to the same Model outside that wrapper do not inherit attribution.
type SelectionSink = Callable[[MainAccountSelection], Awaitable[None]]
main_request: ContextVar[tuple[str, SelectionSink] | None] = ContextVar("subscription_main_request", default=None)


class MainAccountCapability(AbstractCapability[AgentContext]):
    def __init__(self, model_id: str, selected: SelectionSink | None) -> None:
        self.model_id = model_id
        self.selected = selected

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if self.selected is None:
            return request_context
        return replace(
            request_context,
            model=_MainAccountModel(request_context.model, self.model_id, self.selected),
        )


class _MainAccountModel(WrapperModel):
    def __init__(self, model: Model, model_id: str, selected: SelectionSink) -> None:
        super().__init__(model)
        self.binding = (model_id, selected)

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        token = main_request.set(self.binding)
        try:
            return await self.wrapped.request(messages, model_settings, model_request_parameters)
        finally:
            main_request.reset(token)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        async with AsyncExitStack() as scope:
            token = main_request.set(self.binding)
            try:
                response = await scope.enter_async_context(
                    self.wrapped.request_stream(messages, model_settings, model_request_parameters, run_context)
                )
            finally:
                main_request.reset(token)
            # The subscription router reports at stream admission. Do not leak
            # MAIN attribution into the consumer or any subsequent tool work.
            yield response
