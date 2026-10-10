"""Agent-backed media understanding used by Environment file views."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from functools import cache
from importlib.resources import files
from typing import Any, Literal, Protocol, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import Agent, BinaryContent, ModelRetry, UserContent
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage

from a13n_harness.environment.models import EnvironmentPath
from a13n_harness.errors import HarnessError
from a13n_harness.filters.image import ImageFilterCapability
from a13n_harness.metering import ModelUsageBinding, ModelUsageCapability
from a13n_harness.models.binding import selected_model
from a13n_harness.models.inference import infer_model
from a13n_harness.observation import _auxiliary_agent_capabilities
from a13n_harness.spec import ImageInputPolicy
from a13n_harness.usage import ProviderUsage

type NativeInputMediaKind = Literal["image", "video", "audio"]

_DEFAULT_IMAGE_INPUT = ImageInputPolicy()

MAX_MEDIA_UNDERSTANDING_BYTES = 16 * 1024 * 1024
MAX_MEDIA_UNDERSTANDING_TEXT_CHARS = 4 * 1024 * 1024

IMAGE_UNDERSTANDING_MODEL_ENV = "A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL"
VIDEO_UNDERSTANDING_MODEL_ENV = "A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL"
AUDIO_UNDERSTANDING_MODEL_ENV = "A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL"
IMAGE_UNDERSTANDING_MODEL_SETTINGS_ENV = "A13N_HARNESS_IMAGE_UNDERSTANDING_MODEL_SETTINGS"
VIDEO_UNDERSTANDING_MODEL_SETTINGS_ENV = "A13N_HARNESS_VIDEO_UNDERSTANDING_MODEL_SETTINGS"
AUDIO_UNDERSTANDING_MODEL_SETTINGS_ENV = "A13N_HARNESS_AUDIO_UNDERSTANDING_MODEL_SETTINGS"

_MODEL_ENV_BY_KIND: dict[NativeInputMediaKind, str] = {
    "image": IMAGE_UNDERSTANDING_MODEL_ENV,
    "video": VIDEO_UNDERSTANDING_MODEL_ENV,
    "audio": AUDIO_UNDERSTANDING_MODEL_ENV,
}
_MODEL_SETTINGS_ENV_BY_KIND: dict[NativeInputMediaKind, str] = {
    "image": IMAGE_UNDERSTANDING_MODEL_SETTINGS_ENV,
    "video": VIDEO_UNDERSTANDING_MODEL_SETTINGS_ENV,
    "audio": AUDIO_UNDERSTANDING_MODEL_SETTINGS_ENV,
}
_TIMEOUT_SECONDS_BY_KIND: dict[NativeInputMediaKind, float] = {
    "image": 120.0,
    "video": 300.0,
    "audio": 180.0,
}


class MediaUnderstandingRequest(BaseModel):
    """Detached bounded input for a media-understanding model."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    kind: NativeInputMediaKind
    media_type: str = Field(min_length=1, max_length=256)
    source: EnvironmentPath | None = None
    source_name: str = Field(min_length=1, max_length=2_048)
    source_bytes: bytes = Field(max_length=MAX_MEDIA_UNDERSTANDING_BYTES)
    instructions: str | None = Field(default=None, max_length=64 * 1024)

    @model_validator(mode="after")
    def _validate_request(self) -> MediaUnderstandingRequest:
        if not self.media_type.startswith(f"{self.kind}/"):
            raise ValueError("media type does not match media kind")
        if "\x00" in self.source_name or "\x00" in (self.instructions or ""):
            raise ValueError("media understanding text must not contain NUL")
        return self


class MediaUnderstandingResult(BaseModel):
    """Textual provider result returned in place of unsupported native media."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    text: str = Field(min_length=1, max_length=MAX_MEDIA_UNDERSTANDING_TEXT_CHARS)
    usage: tuple[ProviderUsage, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def _validate_text(self) -> MediaUnderstandingResult:
        if "\x00" in self.text:
            raise ValueError("media understanding text must not contain NUL")
        return self


class MediaUnderstandingError(Exception):
    """Stable provider failure with any usage proven before the failure."""

    def __init__(self, code: str, *, usage: Sequence[ProviderUsage] = ()) -> None:
        if not isinstance(code, str) or not code.strip() or len(code) > 128:
            raise ValueError("media understanding error code must be a short non-blank string")
        detached_usage = tuple(
            item.model_copy(deep=True) if isinstance(item, ProviderUsage) else ProviderUsage.model_validate(item)
            for item in usage
        )
        if len(detached_usage) > 64:
            raise ValueError("media understanding errors may carry at most 64 usage receipts")
        self.code = code
        self.usage = detached_usage
        super().__init__(code)


@runtime_checkable
class MediaUnderstandingProvider(Protocol):
    """Run collaborator that analyzes media unsupported by the active model."""

    async def understand(
        self, request: MediaUnderstandingRequest, *, usage: ModelUsageBinding | None = None
    ) -> MediaUnderstandingResult: ...


class AgentMediaUnderstandingProvider:
    """Default image, video, and audio understanding implementation using Pydantic AI Agents.

    `model_ids` names the ID a host selected a kind's model by; that kind's requests carry it as their
    `ModelCall.model_id`.
    """

    def __init__(
        self,
        *,
        models: Mapping[NativeInputMediaKind, str | Model],
        model_settings: Mapping[NativeInputMediaKind, ModelSettings] | None = None,
        model_ids: Mapping[NativeInputMediaKind, str] | None = None,
        image_input: ImageInputPolicy | None = _DEFAULT_IMAGE_INPUT,
    ) -> None:
        model_ids = dict(model_ids or {})
        if any(kind not in models for kind in model_ids):
            raise ValueError("media understanding model IDs name a kind without a model")
        resolved_models: dict[NativeInputMediaKind, Model] = {}
        for kind, model in models.items():
            if kind not in _MODEL_ENV_BY_KIND:
                raise ValueError(f"unsupported media understanding kind: {kind!r}")
            resolved_models[kind] = infer_model(model) if isinstance(model, str) else model
            if not isinstance(resolved_models[kind], Model):
                raise TypeError("media understanding models must be model strings or Pydantic AI Model instances")
        configured_settings = {
            kind: cast(ModelSettings, dict(settings)) for kind, settings in (model_settings or {}).items()
        }
        if any(kind not in _MODEL_ENV_BY_KIND for kind in configured_settings):
            raise ValueError("media understanding settings contain an unsupported kind")
        self._models = resolved_models
        self._agents: dict[NativeInputMediaKind, Agent[object, str]] = {}
        for kind, model in resolved_models.items():
            settings = ModelSettings(temperature=0.1)
            settings.update(configured_settings.get(kind, {}))
            selection, capabilities = selected_model(model, model_ids.get(kind))
            if kind == "image" and image_input is not None:
                capabilities = (*capabilities, ImageFilterCapability(image_input))
            agent = Agent(
                selection,
                capabilities=capabilities,
                output_type=str,
                name=f"{kind}-understanding",
                system_prompt=_system_prompt(kind),
                model_settings=settings,
                retries=2,
            )
            agent.instrument = False
            agent.output_validator(_validate_agent_output)
            self._agents[kind] = agent

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        kind: NativeInputMediaKind | None = None,
    ) -> AgentMediaUnderstandingProvider | None:
        """Build configured defaults without loading dotenv files."""
        source = os.environ if environ is None else environ
        selected_kinds = (kind,) if kind is not None else tuple(_MODEL_ENV_BY_KIND)
        models: dict[NativeInputMediaKind, str] = {}
        settings: dict[NativeInputMediaKind, ModelSettings] = {}
        for selected_kind in selected_kinds:
            value = source.get(_MODEL_ENV_BY_KIND[selected_kind])
            if value is None or not value.strip():
                continue
            models[selected_kind] = value.strip()
            settings_value = source.get(_MODEL_SETTINGS_ENV_BY_KIND[selected_kind])
            if settings_value is not None and settings_value.strip():
                settings[selected_kind] = _parse_model_settings(settings_value)
        if not models:
            return None
        try:
            return cls(models=models, model_settings=settings)
        except MediaUnderstandingError:
            raise
        except Exception as exc:
            raise MediaUnderstandingError("media_understanding_configuration_invalid") from exc

    async def understand(
        self, request: MediaUnderstandingRequest, *, usage: ModelUsageBinding | None = None
    ) -> MediaUnderstandingResult:
        model = self._models.get(request.kind)
        agent = self._agents.get(request.kind)
        if model is None or agent is None:
            raise MediaUnderstandingError("media_understanding_unavailable")
        prompt = request.instructions or _default_instruction(request.kind)
        attached = usage is not None
        binding = usage or ModelUsageBinding.standalone(source="files.media_understanding")
        accounting = ModelUsageCapability(binding)
        native_usage = RunUsage()
        try:
            async with asyncio.timeout(_TIMEOUT_SECONDS_BY_KIND[request.kind]), _enter_agent(agent):
                result = await _run_with_retry(
                    agent,
                    [prompt, BinaryContent(data=request.source_bytes, media_type=request.media_type)],
                    usage=native_usage,
                    accounting=accounting,
                )
        except TimeoutError as exc:
            raise MediaUnderstandingError(
                "media_understanding_timeout",
                usage=() if attached else binding.receipts(),
            ) from exc
        except (HarnessError, UsageLimitExceeded):
            raise
        except UnexpectedModelBehavior as exc:
            raise MediaUnderstandingError(
                "media_understanding_response_invalid",
                usage=() if attached else binding.receipts(),
            ) from exc
        except Exception as exc:
            raise MediaUnderstandingError(
                "media_understanding_failed",
                usage=() if attached else binding.receipts(),
            ) from exc
        return MediaUnderstandingResult(
            text=result.output,
            usage=() if attached else binding.receipts(),
        )


def _validate_agent_output(output: str) -> str:
    text = output.strip()
    if not text or len(text) > MAX_MEDIA_UNDERSTANDING_TEXT_CHARS:
        raise ModelRetry("Return a non-empty plain-text analysis within the output limit.")
    return text


@cache
def _system_prompt(kind: NativeInputMediaKind) -> str:
    return _packaged_prompt(f"{kind}_understanding.md")


@cache
def _default_instruction(kind: NativeInputMediaKind) -> str:
    return _packaged_prompt(f"{kind}_understanding_default.md")


def _packaged_prompt(name: str) -> str:
    return files("a13n_harness.toolsets.prompts").joinpath(name).read_text(encoding="utf-8").strip()


def _parse_model_settings(value: str) -> ModelSettings:
    if len(value) > 64 * 1024 or "\x00" in value:
        raise MediaUnderstandingError("media_understanding_configuration_invalid")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise MediaUnderstandingError("media_understanding_configuration_invalid") from exc
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise MediaUnderstandingError("media_understanding_configuration_invalid")
    return cast(ModelSettings, parsed)


@asynccontextmanager
async def _enter_agent(agent: Agent[object, str]) -> AsyncGenerator[None]:
    """Enter one Agent without allowing cleanup to replace an in-flight failure."""
    task = asyncio.current_task()
    initial_cancelling = task.cancelling() if task is not None else 0
    primary_error: BaseException | None = None
    try:
        async with agent:
            try:
                yield
            except BaseException as exc:
                primary_error = exc
                raise
    except BaseException as exc:
        if primary_error is not None and exc is not primary_error:
            raise primary_error from exc
        if task is not None and task.cancelling() > initial_cancelling and not isinstance(exc, asyncio.CancelledError):
            raise asyncio.CancelledError from exc
        raise
    if primary_error is not None:
        raise primary_error


async def _run_with_retry(
    agent: Agent[object, str],
    prompt: Sequence[UserContent],
    *,
    usage: RunUsage,
    accounting: AbstractCapability[Any],
) -> AgentRunResult[str]:
    for attempt in range(3):
        try:
            capabilities = (*_auxiliary_agent_capabilities(), accounting)
            return await agent.run(prompt, usage=usage, capabilities=capabilities)
        except ModelHTTPError as exc:
            if exc.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            await asyncio.sleep(2**attempt)
    raise AssertionError("unreachable")


__all__ = [
    "AUDIO_UNDERSTANDING_MODEL_ENV",
    "AUDIO_UNDERSTANDING_MODEL_SETTINGS_ENV",
    "IMAGE_UNDERSTANDING_MODEL_ENV",
    "IMAGE_UNDERSTANDING_MODEL_SETTINGS_ENV",
    "VIDEO_UNDERSTANDING_MODEL_ENV",
    "VIDEO_UNDERSTANDING_MODEL_SETTINGS_ENV",
    "AgentMediaUnderstandingProvider",
    "MediaUnderstandingError",
    "MediaUnderstandingProvider",
    "MediaUnderstandingRequest",
    "MediaUnderstandingResult",
    "NativeInputMediaKind",
]
