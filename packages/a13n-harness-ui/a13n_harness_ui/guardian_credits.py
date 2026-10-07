"""Opt-in Guardian credit request linking; the provider still owns eligibility and billing."""

from dataclasses import replace
from typing import Any, cast

from a13n_harness import AgentContext
from a13n_harness.capabilities.tool_review import ToolReviewRequest
from a13n_logging import get_logger
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

_LOGGER = get_logger(__name__)
_METADATA_KEYS = frozenset({"guardian_credits_requested", "x-openai-subagent", "parent_response_id"})
_HEADERS = frozenset({"x-openai-subagent", "x-codex-guardian"})


def supports_guardian_credits(route: str) -> bool:
    return route.partition(":")[0] in {"openai-codex", "openai-responses"}


def _settings(request: ModelRequestContext) -> dict[str, Any] | None:
    from pydantic_ai.models.openai import OpenAIResponsesModel

    model = request.model
    while isinstance(model, WrapperModel):
        model = model.wrapped
    if not isinstance(model, OpenAIResponsesModel):
        _LOGGER.warning("guardian_credits_unavailable", extra={"reason": "responses_model_required"})
        return None
    # Copy every modified mapping. Shared Model settings and composition recipes stay immutable.
    settings: dict[str, Any] = {**(model.settings or {}), **(request.model_settings or {})}
    body = dict(settings.get("extra_body") or {})
    metadata = dict(body.get("client_metadata") or {})
    body["client_metadata"] = {key: value for key, value in metadata.items() if key not in _METADATA_KEYS}
    settings["extra_body"] = body
    settings["extra_headers"] = {
        key: value for key, value in (settings.get("extra_headers") or {}).items() if key.lower() not in _HEADERS
    }
    return settings


class GuardianParentCapability(AbstractCapability[AgentContext]):
    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        settings = _settings(request_context)
        if settings is None:
            return request_context
        settings["extra_body"]["client_metadata"]["guardian_credits_requested"] = "true"
        return replace(request_context, model_settings=cast(ModelSettings, settings))


class GuardianReviewCapability(AbstractCapability[ToolReviewRequest]):
    async def before_model_request(
        self, ctx: RunContext[ToolReviewRequest], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        settings = _settings(request_context)
        if settings is None:
            return request_context
        source = ctx.deps.source
        if ctx.deps.profile == "shell":
            if source is not None and source.provider_response_id:
                settings["extra_headers"].update({"x-openai-subagent": "guardian", "x-codex-guardian": "reviewer"})
                settings["extra_body"]["client_metadata"].update(
                    {"x-openai-subagent": "guardian", "parent_response_id": source.provider_response_id}
                )
            else:
                _LOGGER.warning("guardian_credits_unavailable", extra={"reason": "source_response_id_missing"})
        return replace(request_context, model_settings=cast(ModelSettings, settings))
