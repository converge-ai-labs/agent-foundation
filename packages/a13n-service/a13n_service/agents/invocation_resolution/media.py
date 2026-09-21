"""One media-understanding resolution per Run acceptance, memoized by Model key."""

from __future__ import annotations

from a13n_harness.toolsets.file_media import NativeInputMediaKind
from a13n_logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.models.domain import MEDIA_KINDS, MediaUnderstandingSelection
from a13n_service.models.media_defaults import read_media_defaults, require_media_capability
from a13n_service.models.runtime import AcceptedModelSelector, PreparedModelExecution
from a13n_service.models.service import ModelError
from a13n_service.storage import short_session

logger = get_logger(__name__)


class MediaUnderstandingResolution:
    """Resolve Run override, then Agent Revision, then Workspace default, per media kind.

    Every graph node shares one instance, so each distinct Model key is read once and every
    node selecting it captures the same execution.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        model_selector: AcceptedModelSelector,
        *,
        organization_id: str,
        workspace_id: str,
    ) -> None:
        self._sessions = sessions
        self._model_selector = model_selector
        self._organization_id = organization_id
        self._workspace_id = workspace_id
        self._defaults: MediaUnderstandingSelection | None = None
        self._models: dict[str, PreparedModelExecution | ModelError] = {}

    async def resolve(
        self, selected: MediaUnderstandingSelection
    ) -> dict[NativeInputMediaKind, PreparedModelExecution]:
        """Reject an unusable explicit selection; skip an unusable Workspace default."""

        defaults = await self._workspace_defaults()
        resolved: dict[NativeInputMediaKind, PreparedModelExecution] = {}
        for kind in MEDIA_KINDS:
            explicit = getattr(selected, kind)
            key = explicit if explicit is not None else getattr(defaults, kind)
            if key is None:
                continue
            prepared = await self._prepared(key)
            if isinstance(prepared, ModelError):
                failure = prepared
            else:
                try:
                    require_media_capability(prepared.resource, kind)
                except ModelError as error:
                    failure = error
                else:
                    resolved[kind] = prepared
                    continue
            if explicit is not None:
                raise failure
            # A Workspace default that became unusable must not block every Run in the
            # Workspace; the view tool reports media_understanding_unavailable instead.
            logger.warning(
                "media_understanding_default_skipped",
                extra={
                    "workspace_id": self._workspace_id,
                    "media_kind": kind,
                    "model_key": key,
                    "reason": failure.code,
                },
            )
        return resolved

    async def _workspace_defaults(self) -> MediaUnderstandingSelection:
        if self._defaults is None:
            async with short_session(self._sessions) as session:
                self._defaults = await read_media_defaults(session, self._workspace_id)
        return self._defaults

    async def _prepared(self, key: str) -> PreparedModelExecution | ModelError:
        if key not in self._models:
            try:
                self._models[key] = await self._model_selector.prepare(
                    organization_id=self._organization_id,
                    workspace_id=self._workspace_id,
                    model_key=key,
                    settings={},
                )
            except ModelError as error:
                self._models[key] = error
        return self._models[key]
