"""Mandatory Thread affinity defaults for upstream model requests."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from copy import copy
from dataclasses import dataclass
from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, WrapModelRequestHandler
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.settings import ModelSettings

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.models.inference import _merge_headers

MODEL_REQUEST_HEADERS_CAPABILITY_ID = "a13n.model.request-headers"
MODEL_REQUEST_X_SESSION_ID_ENABLED_ENV = "A13N_HARNESS_MODEL_REQUEST_X_SESSION_ID_ENABLED"
MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV = "A13N_HARNESS_MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class ModelRequestPatchConfiguration:
    """Build-scoped snapshot of independently controlled request defaults."""

    x_session_id_enabled: bool = True
    openai_prompt_cache_key_enabled: bool = True

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        x_session_id_enabled: bool | None = None,
        openai_prompt_cache_key_enabled: bool | None = None,
    ) -> ModelRequestPatchConfiguration:
        """Snapshot Host overrides, falling back independently to environment and then True."""

        source = os.environ if environ is None else environ
        return cls(
            x_session_id_enabled=_resolve_enabled(
                x_session_id_enabled,
                source=source,
                parameter="x_session_id_enabled",
                environment_name=MODEL_REQUEST_X_SESSION_ID_ENABLED_ENV,
            ),
            openai_prompt_cache_key_enabled=_resolve_enabled(
                openai_prompt_cache_key_enabled,
                source=source,
                parameter="openai_prompt_cache_key_enabled",
                environment_name=MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV,
            ),
        )


def _resolve_enabled(
    override: bool | None,
    *,
    source: Mapping[str, str],
    parameter: str,
    environment_name: str,
) -> bool:
    if override is None:
        return _parse_enabled(source.get(environment_name), name=environment_name)
    if not isinstance(override, bool):
        raise DefinitionError(
            f"{parameter} must be a boolean or None.",
            code="model_request_patch_configuration_invalid",
            details={"name": parameter},
        )
    return override


def _parse_enabled(value: object, *, name: str) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or value != value.strip():
        raise DefinitionError(
            "The model-request patch environment value is invalid.",
            code="model_request_patch_environment_invalid",
            details={"name": name},
        )
    normalized = value.lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise DefinitionError(
        "The model-request patch environment value is invalid.",
        code="model_request_patch_environment_invalid",
        details={"name": name},
    )


@dataclass(init=False)
class ModelRequestHeadersCapability(AbstractCapability[AgentContext]):
    """Apply stable Thread affinity defaults without overriding explicit settings."""

    id = MODEL_REQUEST_HEADERS_CAPABILITY_ID

    def __init__(self, configuration: ModelRequestPatchConfiguration) -> None:
        if not isinstance(configuration, ModelRequestPatchConfiguration):
            raise TypeError("configuration must be a ModelRequestPatchConfiguration")
        self._configuration = configuration

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost", wrapped_by=(AbstractCapability,))

    async def wrap_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        configuration = self._configuration
        if not configuration.x_session_id_enabled and not configuration.openai_prompt_cache_key_enabled:
            return await handler(request_context)

        settings: dict[str, Any] = dict(request_context.model_settings or {})
        if configuration.x_session_id_enabled:
            settings["extra_headers"] = _merge_headers(
                {"x-session-id": ctx.deps.thread_id},
                cast(Mapping[str, str] | None, settings.get("extra_headers")),
            )
        # This is a conservative naming policy, not endpoint capability detection.
        if (
            configuration.openai_prompt_cache_key_enabled
            and "openai_prompt_cache_key" not in settings
            and re.match(r"(?:openai/)?gpt-[0-9]", request_context.model.model_name) is not None
        ):
            settings["openai_prompt_cache_key"] = ctx.deps.thread_id

        updated = copy(request_context)
        updated.model_settings = cast(ModelSettings, settings)
        return await handler(updated)


__all__ = [
    "MODEL_REQUEST_HEADERS_CAPABILITY_ID",
    "MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV",
    "MODEL_REQUEST_X_SESSION_ID_ENABLED_ENV",
    "ModelRequestHeadersCapability",
    "ModelRequestPatchConfiguration",
]
