"""Run policy at the native Model boundary, before SDK-owned media conversion."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic_ai.messages import FileUrl, ModelMessage, ModelRequest, ModelResponse, ToolReturnPart, UserPromptPart
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RequestUsage

from ..configuration import RunConfiguration
from ..errors import RunError


def configured_model(model: Model, configuration: RunConfiguration) -> Model:
    """Keep unrestricted native behavior; reject opaque media URL processing when restricted."""
    if configuration.allowed_hosts is None or isinstance(model, _ConfiguredModel):
        return model
    return _ConfiguredModel(model, configuration)


class _ConfiguredModel(WrapperModel):
    def __init__(self, wrapped: Model, configuration: RunConfiguration) -> None:
        super().__init__(wrapped)
        self._configuration = configuration

    def _validate(self, messages: list[ModelMessage]) -> None:
        for message in messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if isinstance(part, (UserPromptPart, ToolReturnPart)):
                    content = part.content
                    items = content if isinstance(content, (list, tuple)) else (content,)
                    for item in items:
                        if isinstance(item, FileUrl):
                            self._configuration.authorize_url(item.url)
                            # The SDK may download or forward URLs using a different transport,
                            # including redirects. Hosts must materialize them as BinaryContent.
                            raise RunError(
                                "Restricted Runs require materialized media, not native media URLs.",
                                code="model_media_url_unsupported",
                            )

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        self._validate(messages)
        return await super().count_tokens(messages, model_settings, model_request_parameters)

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self._validate(messages)
        return await super().request(messages, model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[object] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        self._validate(messages)
        async with super().request_stream(messages, model_settings, model_request_parameters, run_context) as stream:
            yield stream
