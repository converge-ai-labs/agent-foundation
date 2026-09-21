"""Run-local, lazy media inference through accepted Service Models."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from a13n_harness import AgentContext
from a13n_harness.errors import ModelResolutionError
from a13n_harness.toolsets.file_media import (
    AgentMediaUnderstandingProvider,
    MediaUnderstandingError,
    MediaUnderstandingRequest,
    MediaUnderstandingResult,
    NativeInputMediaKind,
)
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.settings import ModelSettings

from a13n_service.agents.domain import EffectiveAgentModel

from .runtime import SnapshotRunModelResolver


class FileMediaUnderstanding:
    """A fresh binding per root or inline child; Harness still owns native dispatch.

    Service Runs select auxiliary Models through configuration only; an unselected kind is
    unavailable rather than falling back to Worker environment settings.
    """

    def __init__(
        self, models: Mapping[NativeInputMediaKind, EffectiveAgentModel], resolver: SnapshotRunModelResolver
    ) -> None:
        self._models = {kind: model.model_copy(deep=True) for kind, model in models.items()}
        self._resolver = resolver
        self._thread_id: str | None = None

    def bind_thread(self, thread_id: str) -> None:
        if self._thread_id is not None and self._thread_id != thread_id:
            raise ValueError("Media binding cannot cross Harness Threads")
        self._thread_id = thread_id

    async def understand(self, request: MediaUnderstandingRequest) -> MediaUnderstandingResult:
        selected = self._models.get(request.kind)
        if selected is None:
            raise MediaUnderstandingError("media_understanding_unavailable")
        if self._thread_id is None:
            raise MediaUnderstandingError("media_understanding_configuration_invalid")
        try:
            model = await self._resolver.resolve(selected.execution.model_id, thread_id=self._thread_id)
            provider = AgentMediaUnderstandingProvider(
                models={request.kind: model},
                model_settings={request.kind: cast(ModelSettings, dict(selected.settings))},
            )
        except (ModelResolutionError, TypeError, ValueError) as error:
            raise MediaUnderstandingError("media_understanding_configuration_invalid") from error
        return await provider.understand(request)


class FileMediaUnderstandingCapability(AbstractCapability[AgentContext]):
    """Bind the fresh media port after Harness assigns the executing Thread identity."""

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        media = ctx.deps.file_media_understanding
        if not isinstance(media, FileMediaUnderstanding):
            raise ValueError("Service media understanding binding is missing")
        media.bind_thread(ctx.deps.thread_id)
        return self
