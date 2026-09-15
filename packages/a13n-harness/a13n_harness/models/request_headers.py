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
from a13n_harness.model_affinity import derive_model_affinity_id, validate_session_affinity_header
from a13n_harness.models.inference import _merge_headers

MODEL_REQUEST_HEADERS_CAPABILITY_ID = "a13n.model.request-headers"
MODEL_REQUEST_X_SESSION_ID_ENABLED_ENV = "A13N_HARNESS_MODEL_REQUEST_X_SESSION_ID_ENABLED"
MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED_ENV = "A13N_HARNESS_MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class ModelRequestPatchConfiguration:
    """Build-scoped snapshot of independently controlled request defaults."""

    session_affinity_header: str | None = None
    openai_prompt_cache_key_enabled: bool = True

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        session_affinity_header: str | None = None,
        x_session_id_enabled: bool | None = None,
        openai_prompt_cache_key_enabled: bool | None = None,
    ) -> ModelRequestPatchConfiguration:
        """Snapshot explicit affinity and independent cache policy.

        The legacy boolean/environment switch remains an explicit opt-in alias for
        x-session-id. A custom name replaces it; omission no longer enables it.
        """

        source = os.environ if environ is None else environ
        legacy_enabled = _resolve_enabled(
            x_session_id_enabled,
            source=source if session_affinity_header is None else {},
            parameter="x_session_id_enabled",
            environment_name=MODEL_REQUEST_X_SESSION_ID_ENABLED_ENV,
            default=False,
        )
        try:
            header = (
                validate_session_affinity_header(session_affinity_header)
                if session_affinity_header is not None
                else "x-session-id"
                if legacy_enabled
                else None
            )
        except ValueError as exc:
            raise DefinitionError(str(exc), code="model_request_patch_configuration_invalid") from exc
        return cls(
            session_affinity_header=header,
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
    default: bool = True,
) -> bool:
    if override is None:
        value = source.get(environment_name)
        return default if value is None else _parse_enabled(value, name=environment_name)
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
        if configuration.session_affinity_header is None and not configuration.openai_prompt_cache_key_enabled:
            return await handler(request_context)

        affinity_id = derive_model_affinity_id(ctx.deps.thread_id)
        settings: dict[str, Any] = dict(request_context.model_settings or {})
        if configuration.session_affinity_header is not None:
            settings["extra_headers"] = _merge_headers(
                {configuration.session_affinity_header: affinity_id},
                cast(Mapping[str, str] | None, settings.get("extra_headers")),
            )
        # This is a conservative naming policy, not endpoint capability detection.
        if (
            configuration.openai_prompt_cache_key_enabled
            and "openai_prompt_cache_key" not in settings
            and re.match(r"(?:openai/)?gpt-[0-9]", request_context.model.model_name) is not None
        ):
            settings["openai_prompt_cache_key"] = affinity_id

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
