"""Workspace-scoped retained execution inspection and best-effort cancellation."""

import json
from datetime import datetime
from typing import Literal

from a13n_harness.tool_execution import ToolExecutionRecord
from a13n_harness.toolsets.shell_observation import ShellStream
from a13n_harness.toolsets.shell_results import ProcessStatusProjection, ShellExecSuccess
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors, reads
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import not_found
from a13n_service.infra.objects.interface import ObjectRef, read
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import ToolExecutionRow
from a13n_service.runs.threads import get_thread
from a13n_service.runs.tool_executions.payload import Payload
from a13n_service.runs.tool_executions.store import available, observation
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


class ExecutionView(ToolExecutionRecord):
    """Run identity is the Service Run; attempt and worker identify the retained owner."""

    attempt_id: str
    worker_id: str
    cancel_requested_at: datetime | None
    first_delivered_at: datetime | None
    expires_at: datetime | None


class ExecutionPage(BaseModel):
    items: list[ExecutionView]
    next_cursor: str | None


def view(row: ToolExecutionRow) -> ExecutionView:
    return ExecutionView.model_validate(
        {
            **observation(row).model_dump(),
            "run_id": row.run_id,
            "attempt_id": row.attempt_id,
            "worker_id": row.worker_id,
            "cancel_requested_at": row.cancel_requested_at,
            "first_delivered_at": row.first_delivered_at,
            "expires_at": row.expires_at,
        }
    )


async def _get(session: AsyncSession, thread_id: str, execution_id: str, *, lock: bool = False) -> ToolExecutionRow:
    query = select(ToolExecutionRow).where(
        ToolExecutionRow.thread_id == thread_id, ToolExecutionRow.id == execution_id, available()
    )
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise not_found("tool_execution", execution_id)
    return row


async def list_executions(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    thread_id: str,
    *,
    run_id: str | None,
    active: bool | None,
    limit: int,
    cursor: str | None,
) -> ExecutionPage:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
        await get_thread(session, workspace_id, thread_id)
        query = select(ToolExecutionRow).where(ToolExecutionRow.thread_id == thread_id, available())
        if run_id is not None:
            query = query.where(ToolExecutionRow.run_id == run_id)
        if active is not None:
            query = query.where(
                ToolExecutionRow.completed_at.is_(None) if active else ToolExecutionRow.completed_at.is_not(None)
            )
        rows, next_cursor = await cursors.keyset_page(
            session,
            query,
            (ToolExecutionRow.created_at, ToolExecutionRow.id),
            kind="tool_executions",
            owner=cursors.query_owner(workspace_id, thread_id, run_id, active),
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        return ExecutionPage(items=[view(row) for row in rows], next_cursor=next_cursor)


async def get_execution(
    runtime: Runtime, actor: Principal, workspace_id: str, thread_id: str, execution_id: str
) -> ExecutionView:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
        await get_thread(session, workspace_id, thread_id)
        return view(await _get(session, thread_id, execution_id))


async def result(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    thread_id: str,
    execution_id: str,
    *,
    offset: int,
    limit: int,
    version: str | None,
) -> reads.TextRead:
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
        await get_thread(session, workspace_id, thread_id)
        row = await _get(session, thread_id, execution_id)
        ref = ObjectRef(**row.result_ref) if row.result_ref is not None else None
    # Reuse the stored native result codec; spill artifacts are retrieved by Harness, not this inspector.
    payload = json.loads(await read(runtime.objects, ref)) if ref is not None else None
    value = None if payload is None else {"result": payload["result"], "retry_prompt": payload["retry_prompt"]}
    result = reads.read_value(
        value, source=f"tool-executions/{execution_id}/result", offset=offset, limit=limit, version=version
    )
    return result


class OutputPreview(BaseModel):
    text: str
    truncated: bool
    produced_bytes: int | None
    observation_closed: bool


class ShellPreview(BaseModel):
    status: ProcessStatusProjection
    stdout: OutputPreview
    stderr: OutputPreview


class ExecutionObservation(BaseModel):
    execution: ExecutionView
    source: Literal["live", "retained", "unavailable"]
    shell: ShellPreview | None = None


_SHELL_RESULT = TypeAdapter(ShellExecSuccess)
_PREVIEW_CHARS = 8000


def _preview(value: object) -> ShellPreview | None:
    try:
        shell = _SHELL_RESULT.validate_python(value)
    except ValidationError:
        return None
    streams = {}
    for name in ("stdout", "stderr"):
        stream = shell[name]
        streams[name] = OutputPreview(
            text=stream["text"][-_PREVIEW_CHARS:],
            truncated=(
                len(stream["text"]) > _PREVIEW_CHARS
                or not stream["content_complete"]
                or stream["omitted_before_bytes"] > 0
            ),
            produced_bytes=stream["produced_bytes"],
            observation_closed=stream["observation_closed"],
        )
    return ShellPreview(status=shell["status"], stdout=streams["stdout"], stderr=streams["stderr"])


def _stream_preview(stream: ShellStream) -> OutputPreview:
    text = stream.data.decode("utf-8", errors="replace")
    return OutputPreview(
        text=text[-_PREVIEW_CHARS:],
        truncated=(
            len(text) > _PREVIEW_CHARS
            or stream.start_offset > 0
            or stream.capture.coverage != "complete"
            or stream.start_offset + len(stream.data) < stream.capture.available_end
        ),
        produced_bytes=stream.capture.produced_bytes,
        observation_closed=stream.capture.observation_closed,
    )


async def observe(
    runtime: Runtime, actor: Principal, workspace_id: str, thread_id: str, execution_id: str
) -> ExecutionObservation:
    # Human observation uses workspace read authority, never a fabricated Run caller.
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
        await get_thread(session, workspace_id, thread_id)
        row = await _get(session, thread_id, execution_id)
        worker_id = row.worker_id
        terminal = row.completed_at is not None
    cached = None if terminal else await runtime.executions.output.get(thread_id, execution_id, worker_id)
    # Recheck after cache I/O: durable completion, expiry and owner loss win.
    async with short_session(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "read")
        await get_thread(session, workspace_id, thread_id)
        row = await _get(session, thread_id, execution_id)
        execution = view(row)
        ref = ObjectRef(**row.result_ref) if row.result_ref is not None else None
        live = row.completed_at is None and row.worker_id == worker_id and row.lease_expires_at > await now(session)
    if execution.completed_at is not None:
        payload = Payload.model_validate_json(await read(runtime.objects, ref)) if ref is not None else None
        shell = (
            _preview(payload.result.return_value)
            if payload is not None and execution.tool_name == "shell_exec"
            else None
        )
        return ExecutionObservation(execution=execution, source="retained", shell=shell)
    if live and cached is not None:
        return ExecutionObservation(
            execution=execution,
            source="live",
            shell=ShellPreview(
                status=ProcessStatusProjection(**cached.status.model_dump()),
                stdout=_stream_preview(cached.stdout),
                stderr=_stream_preview(cached.stderr),
            ),
        )
    return ExecutionObservation(execution=execution, source="unavailable")


async def cancel(
    runtime: Runtime, actor: Principal, workspace_id: str, thread_id: str, execution_id: str
) -> ExecutionView:
    async with transaction(runtime.storage) as session:
        await workspace_scope(session, actor, workspace_id, "run")
        await get_thread(session, workspace_id, thread_id, lock=True)
        row = await _get(session, thread_id, execution_id, lock=True)
        if row.completed_at is None:
            row.cancel_requested_at = row.cancel_requested_at or await now(session)
        return view(row)
