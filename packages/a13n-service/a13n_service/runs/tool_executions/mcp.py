"""Control-plane polling of durable MCP handles on the retained tool execution lease."""

import asyncio
import secrets
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx2
from a13n_harness import RunConfiguration
from a13n_harness.mcp_task_state import McpTaskExecutionState
from a13n_harness.mcp_tasks import cancel_mcp_task, get_mcp_task, has_tasks, task_server_id, task_value
from a13n_harness.tool_execution import StoredToolExecution
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError
from pydantic_ai.exceptions import ToolFailed
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.tools import ToolDispatch
from a13n_service.providers.tools.mcp import mcp_client, task_dispatch
from a13n_service.resources.agents import stored
from a13n_service.resources.connections.access import McpConnection
from a13n_service.resources.connections.oauth import open_mcp_client
from a13n_service.resources.connections.runtime import resolve_connections
from a13n_service.runs import definitions
from a13n_service.runs.accept import delegation
from a13n_service.runs.admission import CallContext
from a13n_service.runs.attempts import AuthorityRevoked, Lease, LeaseLost, authorize_execution
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import RunOptions
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow, ToolExecutionRow
from a13n_service.runs.tool_executions.authority import lock_execution
from a13n_service.runs.tool_executions.claims import relinquish_shell
from a13n_service.runs.tool_executions.store import ORIGIN, Store, observation
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope


async def _claim(runtime: Runtime) -> tuple[Lease, StoredToolExecution] | None:
    async with transaction(runtime.storage) as session:
        current = await now(session)
        row = await session.scalar(
            select(ToolExecutionRow)
            .where(
                ToolExecutionRow.completed_at.is_(None),
                ToolExecutionRow.mcp_poll_at.is_not(None),
                ToolExecutionRow.lease_expires_at <= current,
                or_(ToolExecutionRow.mcp_poll_at <= current, ToolExecutionRow.cancel_requested_at.is_not(None)),
            )
            .order_by(ToolExecutionRow.mcp_poll_at, ToolExecutionRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        attempt = await session.get(AttemptRow, row.attempt_id)
        assert attempt is not None
        token = secrets.token_urlsafe(32)
        row.worker_id = "control:mcp-poll"
        row.lease_token_hash = secret_hash(token)
        # One MCP operation gets at most 20 seconds, leaving time for publication.
        row.lease_expires_at = current + timedelta(seconds=60)
        record = observation(row).model_copy(update={"foreground_released": True})
        row.record = record.model_dump(mode="json")
        return (
            Lease(
                row.run_id,
                row.attempt_id,
                row.thread_id,
                row.organization_id,
                row.workspace_id,
                attempt.number,
                row.worker_id,
                token,
            ),
            StoredToolExecution(record, mcp_task=McpTaskExecutionState.model_validate(row.mcp_task)),
        )


async def _binding(
    session: AsyncSession, runtime: Runtime, run: RunRow, state: McpTaskExecutionState
) -> tuple[McpConnection, Mapping[str, str]]:
    principal = await authorize_execution(session, runtime.access, run)
    scope = WorkspaceScope(run.organization_id, run.workspace_id)
    thread = await session.get(ThreadRow, run.thread_id)
    if thread is None:
        raise LeaseLost()
    options = RunOptions.model_validate(run.options)
    revision = await definitions.resolve(
        session,
        thread,
        run.agent_id,
        run.agent_revision_id,
        options.overrides,
        accepting=False,
    )
    config = stored.require_effective(revision.config, options.overrides, revision_id=revision.definition_id)
    selection = next(
        (source for source in config.connection_tools if source.connection_id == state.connection_id), None
    )
    if selection is None:
        raise ToolFailed("The originating MCP task binding is no longer selected.")
    (selected,) = await resolve_connections(
        session,
        principal,
        scope,
        (selection,),
        run.mcp_headers,
        authority=ExecutionAuthority.model_validate(run.authority),
    )
    connection = selected.connection
    if (
        not isinstance(connection, McpConnection)
        or not connection.config.durable_tasks
        or (task_server_id(connection.config.url) != state.server_id)
        or (selected.tools is not None and state.tool_name not in selected.tools)
    ):
        raise ToolFailed("The originating MCP task binding is no longer available.")
    return connection, selected.caller_headers


async def _poll(runtime: Runtime, lease: Lease, execution: StoredToolExecution) -> StoredToolExecution:
    record, original = execution.record, execution.mcp_task
    assert original is not None
    state = original
    status: Literal["succeeded", "failed", "cancelled"] = "failed"
    error: str | None = None
    result = None
    cancelled = record.cancel_requested
    try:
        if datetime.now(UTC) >= state.expires_at:
            raise ToolFailed("MCP task expired.")
        async with transaction(runtime.storage) as session:
            run, row, _ = await lock_execution(session, lease, record.execution_id, cleanup=True)
            cancelled = row.cancel_requested_at is not None or run.cancel_requested_at is not None
            connection, headers = await _binding(session, runtime, run, state)
            options = RunOptions.model_validate(run.options)

        async def check(dispatch: ToolDispatch) -> None:
            async with transaction(runtime.storage) as session:
                run, _, _ = await lock_execution(session, lease, record.execution_id, cleanup=cancelled)
                current, _ = await _binding(session, runtime, run, original)
                if current.config != connection.config or current.auth != connection.auth:
                    raise ToolFailed("The originating MCP task binding changed during polling.")
                if runtime.admission is not None:
                    thread = await session.get(ThreadRow, lease.thread_id)
                    if thread is None:
                        raise LeaseLost()
                    origin = await delegation(session, thread, run.id)
                    await runtime.admission.proceed(
                        session,
                        CallContext(
                            organization_id=lease.organization_id,
                            workspace_id=lease.workspace_id,
                            session_id=run.session_id,
                            thread_id=lease.thread_id,
                            run_id=run.id,
                            run_attempt_id=lease.attempt_id,
                            root_run_id=origin.root_run_id,
                            call_id=dispatch.tool_call_id,
                            source="connection",
                            provider_id=None,
                            connection_id=dispatch.connection_id,
                            tool_name=dispatch.tool_name,
                        ),
                    )

        settings = runtime.settings.providers
        policy = runtime.endpoint_policy.for_run(options.configuration or RunConfiguration())
        dispatch = ToolDispatch(connection.id, None, state.tool_name, record.tool_call_id)
        async with (
            asyncio.timeout(20),
            open_mcp_client(
                connection,
                storage=runtime.storage,
                keys=runtime.keys,
                policy=policy,
                settings=settings,
            ) as http,
        ):
            if options.context and settings.forward_mcp_context_headers and connection.config.forward_context_headers:
                http.headers.update(
                    {
                        name: value
                        for name, value in options.context.headers.items()
                        if name not in connection.config.headers
                    }
                )
            http.headers.update(headers)
            async with mcp_client(connection.config.url, connection.id, http, timeout=10, durable_tasks=True) as client:
                if not has_tasks(client.server_capabilities):
                    raise ToolFailed("The originating MCP server no longer supports Tasks.")
                with task_dispatch(dispatch, check):
                    if cancelled:
                        await cancel_mcp_task(client.session, state)
                    else:
                        snapshot = await get_mcp_task(client.session, state)
                        state = snapshot.checkpoint(state)
                        if state.status in {"working", "input_required"}:
                            return replace(execution, mcp_task=state)
                        if state.status != "cancelled":
                            result = task_value(snapshot.outcome())
                            status = "succeeded"
    except (
        AuthorityRevoked,
        ServiceError,
        ToolFailed,
        MCPError,
        ValidationError,
        ConnectionError,
        TimeoutError,
        httpx2.TransportError,
    ) as exc:
        if not cancelled and (
            isinstance(exc, (ConnectionError, TimeoutError, httpx2.TransportError))
            or (isinstance(exc, ServiceError) and exc.code == "unavailable")
        ):
            state = state.model_copy(update={"next_poll_at": datetime.now(UTC) + timedelta(seconds=5)})
            return replace(execution, mcp_task=state)
        error = str(exc) if isinstance(exc, ToolFailed) else "The MCP task could not be polled."
    if cancelled or state.status == "cancelled":
        status, error = (
            "cancelled",
            "MCP task cancellation requested." if cancelled else "MCP task cancelled by the server.",
        )
    terminal = record.model_copy(
        update={
            "status": status,
            "completed_at": datetime.now(UTC),
            "result_available": status == "succeeded",
            "error": error,
        }
    )
    return StoredToolExecution(terminal, result=result, mcp_task=state)


async def poll_mcp_tasks(runtime: Runtime, *, limit: int) -> int:
    count = 0
    for _ in range(limit):
        claim = await _claim(runtime)
        if claim is None:
            break
        lease, execution = claim
        token = ORIGIN.set(lease)
        try:
            updated = await _poll(runtime, lease, execution)
            await Store(runtime).save(updated, poll_cancelled=updated.record.status == "cancelled")
        except LeaseLost:
            # A newer owner or cancellation fence won; no stale result is published.
            pass
        finally:
            ORIGIN.reset(token)
            await relinquish_shell(runtime, lease, execution.record.execution_id)
        count += 1
    return count
