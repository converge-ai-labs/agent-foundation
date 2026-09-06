"""Authorized lifecycle-event reconciliation reads."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_agent_scoped_collection,
)
from a13n_service.storage import short_session

from .cursors import LifecycleCursorError, decode_lifecycle_cursor, encode_lifecycle_cursor
from .domain import LifecycleEntityType, ResourceLifecycleEventPage, WorkspaceEventPage
from .persistence import LifecycleReplayGap, read_resource_events
from .reconciliation import load_owning_run, read_workspace_events


class LifecycleEventError(ApplicationError):
    """A safe lifecycle reconciliation error exposed through the public API."""


class LifecycleEventService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_workspace_events(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
    ) -> WorkspaceEventPage:
        _validate_page_request(limit=limit)
        scope = _cursor_scope(actor, workspace_id)
        try:
            after_seq = decode_lifecycle_cursor(cursor, scope=scope) if cursor is not None else None
        except LifecycleCursorError as error:
            raise LifecycleEventError(
                "invalid_cursor",
                "The lifecycle cursor is invalid.",
                category=ErrorCategory.invalid_request,
            ) from error
        async with short_session(self._sessions) as database:
            try:
                authorization = await authorize_agent_scoped_collection(
                    database,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.lifecycle_event_read,
                )
            except AuthorizationError as error:
                raise _authorization_error(error) from error
            try:
                page = await read_workspace_events(
                    database,
                    organization_id=authorization.workspace.organization_id,
                    workspace_id=workspace_id,
                    visible_agent_ids=authorization.visible_agent_ids,
                    after_seq=after_seq,
                    limit=limit,
                )
            except LifecycleReplayGap as error:
                raise LifecycleEventError(
                    "lifecycle_replay_gap",
                    "The requested lifecycle history is no longer retained.",
                    category=ErrorCategory.conflict,
                    details={
                        "retained_floor": encode_lifecycle_cursor(
                            sequence=max(error.retained_floor - 1, 0), scope=scope
                        ),
                        "high_watermark": encode_lifecycle_cursor(sequence=error.high_watermark, scope=scope),
                    },
                ) from error
        return WorkspaceEventPage(
            items=page.items,
            next_cursor=(
                None if page.next_seq is None else encode_lifecycle_cursor(sequence=page.next_seq, scope=scope)
            ),
            retained_floor=encode_lifecycle_cursor(sequence=max(page.retained_floor - 1, 0), scope=scope),
            high_watermark=encode_lifecycle_cursor(sequence=page.high_watermark, scope=scope),
        )

    async def list_run_events(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        after_resource_seq: int,
        limit: int,
    ) -> ResourceLifecycleEventPage:
        return await self._list_resource_events(
            actor=actor,
            resource_type=LifecycleEntityType.run,
            resource_id=run_id,
            after_resource_seq=after_resource_seq,
            limit=limit,
        )

    async def list_run_attempt_events(
        self,
        *,
        actor: AuthenticatedActor,
        run_attempt_id: str,
        after_resource_seq: int,
        limit: int,
    ) -> ResourceLifecycleEventPage:
        return await self._list_resource_events(
            actor=actor,
            resource_type=LifecycleEntityType.run_attempt,
            resource_id=run_attempt_id,
            after_resource_seq=after_resource_seq,
            limit=limit,
        )

    async def _list_resource_events(
        self,
        *,
        actor: AuthenticatedActor,
        resource_type: LifecycleEntityType,
        resource_id: str,
        after_resource_seq: int,
        limit: int,
    ) -> ResourceLifecycleEventPage:
        _validate_page_request(limit=limit, after_resource_seq=after_resource_seq)
        workspace_id = actor.workspace_id
        async with short_session(self._sessions) as database:
            run = await load_owning_run(
                database,
                workspace_id=workspace_id,
                resource_type=resource_type,
                resource_id=resource_id,
            )
            if run is None:
                raise _resource_not_found()
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=workspace_id,
                    agent_id=run.agent_id,
                    action=WorkspaceAction.lifecycle_event_read,
                )
            except AuthorizationError as error:
                raise _authorization_error(error) from error
            try:
                page = await read_resource_events(
                    database,
                    organization_id=run.organization_id,
                    entity_type=resource_type,
                    entity_id=resource_id,
                    after_resource_seq=after_resource_seq,
                    limit=limit,
                )
            except LifecycleReplayGap as error:
                resource_path = "runs" if resource_type is LifecycleEntityType.run else "run-attempts"
                raise LifecycleEventError(
                    "lifecycle_resource_replay_gap",
                    "The requested resource lifecycle history is no longer retained.",
                    category=ErrorCategory.conflict,
                    details={
                        "retained_resource_seq_floor": error.retained_floor,
                        "high_watermark_resource_seq": error.high_watermark,
                        "current_resource": f"/api/v1/{resource_path}/{resource_id}",
                    },
                ) from error
        return ResourceLifecycleEventPage(
            resource_type="run" if resource_type is LifecycleEntityType.run else "run_attempt",
            resource_id=resource_id,
            items=page.items,
            next_resource_seq=page.next_resource_seq,
            retained_resource_seq_floor=page.retained_resource_seq_floor,
            high_watermark_resource_seq=page.high_watermark_resource_seq,
        )


def _cursor_scope(actor: AuthenticatedActor, workspace_id: str) -> dict[str, object]:
    return {
        "resource": "workspace_lifecycle_events",
        "workspace_id": workspace_id,
        "principal_type": actor.principal.principal_type.value,
        "principal_id": actor.principal.principal_id,
    }


def _validate_page_request(*, limit: int, after_resource_seq: int | None = None) -> None:
    if limit < 1 or limit > 200:
        raise LifecycleEventError(
            "invalid_request", "limit must be between 1 and 200.", category=ErrorCategory.invalid_request
        )
    if after_resource_seq is not None and after_resource_seq < 0:
        raise LifecycleEventError(
            "invalid_request",
            "after_resource_seq must be non-negative.",
            category=ErrorCategory.invalid_request,
        )


def _authorization_error(error: AuthorizationError) -> LifecycleEventError:
    if error.concealed:
        return _resource_not_found()
    return LifecycleEventError("permission_denied", "Permission denied.", category=ErrorCategory.forbidden)


def _resource_not_found() -> LifecycleEventError:
    return LifecycleEventError(
        "resource_not_found", "The requested resource was not found.", category=ErrorCategory.not_found
    )


__all__ = ["LifecycleEventError", "LifecycleEventService"]
