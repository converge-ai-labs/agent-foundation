"""The small ChatGPT plan-usage dialect above native OpenAI Responses rendering.

Pydantic AI owns history, tool schemas, reasoning, usage, and the ordinary-request
stream collector. The provider profile enforces streaming and no storage; this
adapter owns only the remaining documented HTTP restrictions and terminal check.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from openai import AsyncStream, Omit
from openai.types import responses
from pydantic_ai.exceptions import ModelAPIError, UserError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import (
    OpenAIModelName,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
    OpenAIResponsesStreamedResponse,
)
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.settings import ModelSettings

if TYPE_CHECKING:
    from pydantic_ai.models.openai import _ResponsesRequestParams

_UNSUPPORTED_SETTINGS = frozenset(
    {
        "openai_background",
        "openai_conversation_id",
        "openai_previous_response_id",
        "openai_moderation",
        "openai_prompt_cache_retention",
        "openai_top_logprobs",
        "openai_logprobs",
        "openai_truncation",
        "openai_user",
    }
)
_UNSUPPORTED_FIELDS = frozenset(
    {
        "background",
        "conversation",
        "max_output_tokens",
        "max_tool_calls",
        "metadata",
        "moderation",
        "multi_agent",
        "prompt",
        "prompt_cache_retention",
        "safety_identifier",
        "temperature",
        "top_logprobs",
        "top_p",
        "truncation",
        "user",
        "previous_response_id",
        "store",
        "stream",
        "input",
        "tools",
    }
)


class _CompletedStream:
    """Validate terminal transport events without replacing native event rendering."""

    def __init__(self, source: AsyncStream[responses.ResponseStreamEvent], model_name: str):
        self.source = source
        self.model_name = model_name

    async def __aiter__(self) -> AsyncIterator[responses.ResponseStreamEvent]:
        completed = False
        async for event in self.source:
            if event.type in ("response.failed", "response.incomplete", "error"):
                raise ModelAPIError(model_name=self.model_name, message=f"ChatGPT inference ended with {event.type}.")
            if event.type == "response.completed":
                completed = True
            yield event
        if not completed:
            raise ModelAPIError(model_name=self.model_name, message="ChatGPT stream ended without response.completed.")

    async def close(self) -> None:
        await self.source.close()


class OpenAIChatGPTResponsesModel(OpenAIResponsesModel):
    """Responses Model using the public endpoint with ChatGPT plan authorization."""

    def prepare_request(
        self,
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> tuple[ModelSettings, ModelRequestParameters]:
        settings, parameters = super().prepare_request(model_settings, model_request_parameters)
        settings = dict(settings or {})
        for name in _UNSUPPORTED_SETTINGS:
            if settings.get(name) is not None:
                raise UserError(f"{name} is not supported by ChatGPT plan usage.")
        extra = settings.get("extra_body")
        if isinstance(extra, dict) and (unsupported := extra.keys() & _UNSUPPORTED_FIELDS):
            raise UserError(f"extra_body cannot supply ChatGPT request field: {sorted(unsupported)[0]}.")
        settings["openai_store"] = False
        return cast(ModelSettings, settings), parameters

    async def _build_responses_request_params(
        self,
        messages: list[ModelMessage],
        model_settings: OpenAIResponsesModelSettings,
        model_request_parameters: ModelRequestParameters,
        profile: OpenAIModelProfile,
    ) -> _ResponsesRequestParams:
        params = await super()._build_responses_request_params(
            messages, model_settings, model_request_parameters, profile
        )
        if not isinstance(params.tools, Omit):
            functions = []
            native = []
            for tool in params.tools:
                if tool["type"] in ("function", "custom"):
                    functions.append(tool)
                elif tool["type"] in ("web_search", "web_search_preview"):
                    native.append(tool)
                else:
                    raise UserError(f"Responses tool {tool['type']} is not supported by ChatGPT plan usage.")
            if functions:
                # The public route accepts function/custom definitions in additional_tools
                # input items, not as bare top-level function tools. Preserve native names
                # and replay semantics without inventing a namespace for every function.
                params.input.insert(0, cast(Any, {"type": "additional_tools", "role": "developer", "tools": functions}))
            params = replace(params, tools=native or cast(Any, Omit()))
        return params

    async def _process_streamed_response(
        self,
        response: AsyncStream[responses.ResponseStreamEvent],
        model_settings: OpenAIResponsesModelSettings,
        model_request_parameters: ModelRequestParameters,
        *,
        expected_model_name: OpenAIModelName | None = None,
        expected_response_id: str | None = None,
    ) -> OpenAIResponsesStreamedResponse:
        checked = cast(AsyncStream[responses.ResponseStreamEvent], _CompletedStream(response, self.model_name))
        return await super()._process_streamed_response(
            checked,
            model_settings,
            model_request_parameters,
            expected_model_name=expected_model_name,
            expected_response_id=expected_response_id,
        )
