"""Bounded native media projection through one Host-selected reader."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from a13n_harness._urls import (
    require_audience_safe_url as _require_audience_safe_url,
)
from a13n_harness._urls import (
    require_http_url as _require_http_url,
)
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.toolsets.media import MediaToolset
from a13n_harness.usage import ProviderUsage

MEDIA_CAPABILITY_ID = "a13n.media"
_MEDIA_TOOLSET_ID = "a13n-media-tools"

type MediaKind = Literal["image", "video", "audio"]


class MediaReadRequest(BaseModel):
    """Finite request passed to a trusted media reader."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=16 * 1024)
    instructions: str | None = Field(default=None, max_length=64 * 1024)
    max_image_bytes: int = Field(gt=0)
    max_video_bytes: int = Field(gt=0)
    max_audio_bytes: int = Field(gt=0)
    allow_direct_video_url: bool

    def limit_for(self, kind: MediaKind) -> int:
        if kind == "image":
            return self.max_image_bytes
        if kind == "video":
            return self.max_video_bytes
        return self.max_audio_bytes

    @model_validator(mode="after")
    def _validate_url(self) -> MediaReadRequest:
        _require_http_url(self.url)
        if "\x00" in (self.instructions or ""):
            raise ValueError("media instructions must not contain NUL")
        return self


class MediaResource(BaseModel):
    """One reader-produced native media value."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        revalidate_instances="always",
    )

    kind: MediaKind
    source_url: str = Field(min_length=1, max_length=16 * 1024)
    media_type: str = Field(min_length=1, max_length=256)
    data: bytes | None = None
    direct_url: str | None = Field(default=None, min_length=1, max_length=16 * 1024)
    usage: tuple[ProviderUsage, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def _validate_representation(self) -> MediaResource:
        if (self.data is None) == (self.direct_url is None):
            raise ValueError("media resource must contain exactly one representation")
        normalized = _main_media_type(self.media_type)
        if not normalized.startswith(f"{self.kind}/"):
            raise ValueError("media type does not match media kind")
        object.__setattr__(self, "media_type", normalized)
        _require_audience_safe_url(self.source_url)
        if self.direct_url is not None:
            if self.kind != "video":
                raise ValueError("only video may use a direct URL")
            _require_audience_safe_url(self.direct_url)
        return self


class MediaReadError(Exception):
    """Stable provider failure safe to project to the model."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or not code.strip() or len(code) > 128:
            raise ValueError("media error code must be a short non-blank string")
        self.code = code
        super().__init__(code)


@runtime_checkable
class MediaReader(Protocol):
    """Host-selected reader responsible for URL policy, redirects, and transport."""

    async def read(self, request: MediaReadRequest) -> MediaResource: ...


class MediaConfiguration(BaseModel):
    """Definition-owned finite inline budgets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_image_bytes: int = Field(default=20 * 1024 * 1024, gt=0, le=256 * 1024 * 1024)
    max_video_bytes: int = Field(default=64 * 1024 * 1024, gt=0, le=512 * 1024 * 1024)
    max_audio_bytes: int = Field(default=64 * 1024 * 1024, gt=0, le=512 * 1024 * 1024)
    allow_direct_video_urls: bool = True
    deadline_seconds: float = Field(default=120.0, gt=0, le=600, allow_inf_nan=False)

    def limit_for(self, kind: MediaKind) -> int:
        if kind == "image":
            return self.max_image_bytes
        if kind == "video":
            return self.max_video_bytes
        return self.max_audio_bytes


@dataclass(init=False)
class MediaCapability(AbstractCapability[AgentContext]):
    """Expose read_media while keeping transport and credentials run-scoped."""

    id = MEDIA_CAPABILITY_ID

    def __init__(self, configuration: MediaConfiguration | None = None) -> None:
        self.configuration = (configuration or MediaConfiguration()).model_copy(deep=True)

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(MEDIA_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, _MediaActiveCapability):
                raise DefinitionError("Media has an incompatible run replacement.", code="capability_type_mismatch")
            return existing
        if MEDIA_CAPABILITY_ID not in ctx.deps._capability_provenance.definition_ids:
            raise DefinitionError(
                "MediaCapability must originate from the Agent definition.", code="capability_scope_invalid"
            )
        replacement = _MediaActiveCapability(self.configuration, context=ctx.deps)
        ctx.deps._record_run_capability(MEDIA_CAPABILITY_ID, replacement)
        return replacement


@dataclass(init=False)
class _MediaActiveCapability(MediaCapability):
    def __init__(self, configuration: MediaConfiguration, *, context: AgentContext) -> None:
        super().__init__(configuration)
        self._context = context

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps is not self._context:
            raise DefinitionError("Media run replacement cannot cross logical runs.", code="capability_scope_invalid")
        return self

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return DynamicToolset(self._toolset_for_run, per_run_step=False, id=_MEDIA_TOOLSET_ID)

    async def _toolset_for_run(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
        return MediaToolset(self._bind(ctx), self.configuration).get_toolset()

    def _bind(self, ctx: RunContext[AgentContext]) -> MediaReader:
        if ctx.deps is not self._context:
            raise DefinitionError("Media run replacement cannot cross logical runs.", code="capability_scope_invalid")
        owner = ctx.capabilities.get(MEDIA_CAPABILITY_ID)
        if type(owner) is not _MediaActiveCapability or owner is not self:
            raise DefinitionError(
                "The finalized Media owner has an incompatible identity.", code="capability_scope_invalid"
            )
        provider = ctx.deps.media_reader
        if provider is None:
            raise DefinitionError("MediaCapability requires RunBindings.media_reader.", code="media_binding_missing")
        return provider


def _main_media_type(value: str) -> str:
    return value.split(";", maxsplit=1)[0].strip().lower()


__all__ = [
    "MediaCapability",
    "MediaConfiguration",
    "MediaKind",
    "MediaReadError",
    "MediaReadRequest",
    "MediaReader",
    "MediaResource",
]
