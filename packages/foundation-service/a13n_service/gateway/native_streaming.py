"""Authorized Native Run Stream attachment and SSE framing."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from time import monotonic

import anyio
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.lifecycle import LifecycleEntityType
from a13n_service.lifecycle.reconciliation import load_owning_run
from a13n_service.public_errors import PublicError
from a13n_service.run_stream import RedisRunStream, RunReplayStore, RunStreamEntry, RunStreamReplayGap
from a13n_service.storage import ObjectNotFound, short_session


class NativeStreamError(PublicError):
    """Safe Native Run Stream failure."""


class ReplayGapEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1"
    event_type: str = "a13n.foundation.replay_gap"
    run_id: str
    requested_cursor: str | None
    retained_floor: str | None
    high_watermark: str | None


@dataclass(frozen=True, slots=True)
class RunStreamAttachment:
    tenant_id: str
    run_id: str
    actor: AuthenticatedActor
    initial_entries: tuple[RunStreamEntry, ...]
    next_stream_id: str | None
    closed: bool


class NativeRunStreamService:
    """Authorize, replay, and follow one Run presentation stream."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        stream: RedisRunStream,
        replay: RunReplayStore,
        *,
        page_size: int,
        poll_interval_seconds: float,
        heartbeat_interval_seconds: float,
        authorization_interval_seconds: float,
        maximum_lifetime_seconds: float,
    ) -> None:
        self._sessions = sessions
        self._stream = stream
        self._replay = replay
        self._page_size = page_size
        self._poll_interval_seconds = poll_interval_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._authorization_interval_seconds = authorization_interval_seconds
        self._maximum_lifetime_seconds = maximum_lifetime_seconds

    async def attach(
        self,
        *,
        actor: AuthenticatedActor,
        run_id: str,
        after_stream_id: str | None,
    ) -> RunStreamAttachment:
        tenant_id, terminal = await self._authorize(actor=actor, run_id=run_id)
        try:
            page = await self._stream.read(
                tenant_id,
                run_id,
                after_stream_id=after_stream_id,
                limit=self._page_size,
            )
        except (RunStreamReplayGap, ValueError) as error:
            if isinstance(error, ValueError):
                raise NativeStreamError(
                    "invalid_cursor",
                    "The Run Stream cursor is invalid.",
                    status_code=400,
                ) from error
            return await self._attach_replay(
                actor=actor,
                tenant_id=tenant_id,
                run_id=run_id,
                after_stream_id=after_stream_id,
                gap=error,
            )

        if terminal and page.high_watermark is None:
            replay = await self._try_replay(
                actor=actor,
                tenant_id=tenant_id,
                run_id=run_id,
                after_stream_id=after_stream_id,
            )
            if replay is not None:
                return replay
            raise NativeStreamError(
                "run_stream_replay_gap",
                "The retained Run Stream is unavailable.",
                status_code=409,
                details={
                    "run_id": run_id,
                    "requested_cursor": after_stream_id,
                    "retained_floor": None,
                    "high_watermark": None,
                },
            )
        return RunStreamAttachment(
            tenant_id=tenant_id,
            run_id=run_id,
            actor=actor,
            initial_entries=page.items,
            next_stream_id=page.next_stream_id or after_stream_id,
            closed=page.closed and page.next_stream_id == page.high_watermark,
        )

    async def events(self, attachment: RunStreamAttachment) -> AsyncIterator[bytes]:
        cursor = attachment.next_stream_id
        for entry in attachment.initial_entries:
            yield _sse_entry(entry)
        if attachment.closed:
            return

        started = monotonic()
        last_heartbeat = started
        last_authorized = started
        while monotonic() - started < self._maximum_lifetime_seconds:
            now = monotonic()
            if now - last_authorized >= self._authorization_interval_seconds:
                try:
                    await self._authorize(actor=attachment.actor, run_id=attachment.run_id)
                except NativeStreamError:
                    return
                last_authorized = now
            try:
                page = await self._stream.read(
                    attachment.tenant_id,
                    attachment.run_id,
                    after_stream_id=cursor,
                    limit=self._page_size,
                )
            except RunStreamReplayGap as error:
                gap = ReplayGapEvent(
                    run_id=attachment.run_id,
                    requested_cursor=cursor,
                    retained_floor=error.retained_floor,
                    high_watermark=error.high_watermark,
                )
                yield _sse_data(gap.event_type, gap.model_dump_json())
                return
            for entry in page.items:
                yield _sse_entry(entry)
                cursor = entry.stream_id
                last_heartbeat = monotonic()
            if page.closed and (page.next_stream_id == page.high_watermark or not page.items):
                return
            now = monotonic()
            if now - last_heartbeat >= self._heartbeat_interval_seconds:
                yield b": heartbeat\n\n"
                last_heartbeat = now
            await anyio.sleep(self._poll_interval_seconds)

    async def _authorize(self, *, actor: AuthenticatedActor, run_id: str) -> tuple[str, bool]:
        workspace_id = actor.boundary_workspace_id
        async with short_session(self._sessions) as database:
            run = await load_owning_run(
                database,
                workspace_id=workspace_id,
                resource_type=LifecycleEntityType.run,
                resource_id=run_id,
            )
            if run is None:
                raise _resource_not_found()
            try:
                await authorize_agent(
                    database,
                    actor=actor,
                    workspace_id=workspace_id,
                    agent_id=run.agent_id,
                    action=WorkspaceAction.run_read,
                )
            except AuthorizationError as error:
                raise _resource_not_found() from error
            return run.tenant_id, run.status in {"completed", "failed", "cancelled"}

    async def _attach_replay(
        self,
        *,
        actor: AuthenticatedActor,
        tenant_id: str,
        run_id: str,
        after_stream_id: str | None,
        gap: RunStreamReplayGap,
    ) -> RunStreamAttachment:
        replay = await self._try_replay(
            actor=actor,
            tenant_id=tenant_id,
            run_id=run_id,
            after_stream_id=after_stream_id,
        )
        if replay is not None:
            return replay
        raise NativeStreamError(
            "run_stream_replay_gap",
            "The requested Run Stream history is no longer retained.",
            status_code=409,
            details={
                "run_id": run_id,
                "requested_cursor": after_stream_id,
                "retained_floor": gap.retained_floor,
                "high_watermark": gap.high_watermark,
            },
        )

    async def _try_replay(
        self,
        *,
        actor: AuthenticatedActor,
        tenant_id: str,
        run_id: str,
        after_stream_id: str | None,
    ) -> RunStreamAttachment | None:
        try:
            snapshot = await self._replay.read(tenant_id, run_id)
        except ObjectNotFound:
            return None
        events = snapshot.events
        start = 0
        if after_stream_id is not None:
            positions = {entry.stream_id: index for index, entry in enumerate(events)}
            index = positions.get(after_stream_id)
            if index is None:
                return None
            start = index + 1
        entries = tuple(RunStreamEntry(entry.stream_id, entry.event) for entry in events[start:])
        return RunStreamAttachment(
            tenant_id=tenant_id,
            run_id=run_id,
            actor=actor,
            initial_entries=entries,
            next_stream_id=(entries[-1].stream_id if entries else after_stream_id),
            closed=True,
        )


def _sse_entry(entry: RunStreamEntry) -> bytes:
    return (
        f"id: {entry.stream_id}\nevent: {entry.event.event_type}\ndata: {entry.event.model_dump_json()}\n\n"
    ).encode()


def _sse_data(event_type: str, data: str) -> bytes:
    return f"event: {event_type}\ndata: {data}\n\n".encode()


def _resource_not_found() -> NativeStreamError:
    return NativeStreamError(
        "resource_not_found",
        "The requested resource was not found.",
        status_code=404,
    )


__all__ = [
    "NativeRunStreamService",
    "NativeStreamError",
    "ReplayGapEvent",
    "RunStreamAttachment",
]
