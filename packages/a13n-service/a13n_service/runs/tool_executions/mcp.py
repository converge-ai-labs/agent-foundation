"""Control-plane polling of durable MCP handles on the retained tool execution lease."""

import asyncio
import json
import secrets
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal

import httpx2
from a13n_harness import RunConfiguration
from a13n_harness.mcp_inputs import McpInputResponse, McpTaskInputRequest
from a13n_harness.mcp_task_state import McpInputDelivery, McpTaskExecutionState
from a13n_harness.mcp_tasks import cancel_mcp_task, get_mcp_task, has_tasks, task_server_id, task_value, update_mcp_task
from a13n_harness.tool_execution import StoredToolExecution
from a13n_stream_protocol.interactions import McpElicitation
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, INVALID_PARAMS, METHOD_NOT_FOUND, REQUEST_TIMEOUT
from pydantic import ValidationError
from pydantic_ai.exceptions import ToolFailed
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.providers.tools import ToolDispatch
from a13n_service.providers.tools.mcp import mcp_client, task_dispatch
from a13n_service.resources.agents import stored
from a13n_service.resources.connections.access import McpConnection
from a13n_service.resources.connections.oauth import open_mcp_client
from a13n_service.resources.connections.runtime import resolve_connections
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.runs import definitions
from a13n_service.runs.accept import delegation
from a13n_service.runs.admission import CallContext
from a13n_service.runs.attempts import AuthorityRevoked, Lease, LeaseLost, authorize_execution
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import RunOptions
from a13n_service.runs.tables import AttemptRow, RunRow, ThreadRow, ToolExecutionRow
from a13n_service.runs.threads import get_run
from a13n_service.runs.tool_executions.authority import lock_execution
from a13n_service.runs.tool_executions.claims import relinquish_shell
from a13n_service.runs.tool_executions.store import ORIGIN, Store, observation
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, WorkspaceScope
from a13n_service.tenancy.tables import PrincipalRow


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
        return await _take_lease(session, row, current, worker_id="control:mcp-poll")


async def _take_lease(
    session: AsyncSession, row: ToolExecutionRow, current: datetime, *, worker_id: str
) -> tuple[Lease, StoredToolExecution]:
    attempt = await session.get(AttemptRow, row.attempt_id)
    assert attempt is not None
    token = secrets.token_urlsafe(32)
    row.worker_id = worker_id
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
) -> tuple[McpConnection, Mapping[str, str], bool]:
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
    return connection, selected.caller_headers, run.occurrence_trigger_entry_id is not None or principal.kind != "user"


async def _poll(
    runtime: Runtime,
    lease: Lease,
    execution: StoredToolExecution,
    *,
    answer: tuple[str, McpInputResponse] | None = None,
) -> StoredToolExecution:
    record, original = execution.record, execution.mcp_task
    assert original is not None
    state = original
    status: Literal["succeeded", "failed", "cancelled"] = "failed"
    error: str | None = None
    result = None
    cancelled = record.cancel_requested
    input_rejected = False
    try:
        if datetime.now(UTC) >= state.expires_at:
            raise ToolFailed("MCP task expired.")
        async with transaction(runtime.storage) as session:
            run, row, _ = await lock_execution(session, lease, record.execution_id, cleanup=True)
            cancelled = row.cancel_requested_at is not None or run.cancel_requested_at is not None
            connection, headers, unattended = await _binding(session, runtime, run, state)
            options = RunOptions.model_validate(run.options)

        async def check(dispatch: ToolDispatch) -> None:
            async with transaction(runtime.storage) as session:
                run, row, _ = await lock_execution(session, lease, record.execution_id, cleanup=cancelled)
                current, _, unattended_now = await _binding(session, runtime, run, original)
                if current.config != connection.config or current.auth != connection.auth:
                    raise ToolFailed("The originating MCP task binding changed during polling.")
                if answer is not None and unattended_now:
                    raise ToolFailed("This run does not accept human input.")
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

                if answer is not None:
                    # Persist only the key, after dispatch admission and before sending the content.
                    reconciliation = McpTaskExecutionState.model_validate(row.mcp_task)
                    delivery = McpInputDelivery(
                        key=answer[0], reconcile_until=await now(session) + timedelta(seconds=90)
                    )
                    row.mcp_task = reconciliation.model_copy(update={"unconfirmed_input": delivery}).model_dump(
                        mode="json", by_alias=True
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
                        if answer is not None:
                            key, response = answer
                            try:
                                state, snapshot = await update_mcp_task(client.session, state, {key: response})
                            except MCPError as exc:
                                if exc.error.code in {INVALID_PARAMS, METHOD_NOT_FOUND}:
                                    # An explicit refusal permits another decision after a fresh GET.
                                    ready = original.model_copy(update={"next_poll_at": datetime.now(UTC)})
                                    await Store(runtime).save(replace(execution, mcp_task=ready), poll_cancelled=False)
                                    input_rejected = True
                                raise
                        else:
                            # A foreground handoff can already have captured a parked request.
                            snapshot = None
                            if state.status != "input_required" or state.next_poll_at is not None:
                                snapshot = await get_mcp_task(client.session, state)
                                state = snapshot.checkpoint(state)
                                if state.unconfirmed_input is not None:
                                    state = _reconcile_input(state)
                                    if state.unconfirmed_input is not None:
                                        return replace(execution, mcp_task=state)
                            if state.status == "input_required":
                                declines = {
                                    key: McpInputResponse(action="decline")
                                    for key, prompt in (state.input_required or {}).items()
                                    if unattended or not _valid(prompt) or _expired(prompt)
                                }
                                if declines:
                                    state, snapshot = await update_mcp_task(client.session, state, declines)
                        if state.status == "input_required" and any(
                            unattended or not _valid(prompt) or _expired(prompt)
                            for prompt in (state.input_required or {}).values()
                        ):
                            state = state.model_copy(update={"next_poll_at": datetime.now(UTC)})
                        if state.status in {"working", "input_required"}:
                            return replace(execution, mcp_task=state)
                        if state.status != "cancelled":
                            assert snapshot is not None
                            result = task_value(snapshot.outcome())
                            status = "succeeded"
    except (
        AuthorityRevoked,
        ServiceError,
        ToolFailed,
        MCPError,
        ValueError,
        ValidationError,
        ConnectionError,
        TimeoutError,
        httpx2.TransportError,
    ) as exc:
        if answer is not None and state.status in {"working", "input_required"}:
            if input_rejected:
                raise ServiceError("unavailable", "The MCP server rejected the input response.") from exc
            if isinstance(exc, AuthorityRevoked):
                raise ServiceError("forbidden", "The originating MCP execution is no longer authorized.") from exc
            raise ServiceError("unavailable", "The MCP server could not confirm the input response.") from exc
        if not cancelled and (
            isinstance(exc, (ConnectionError, TimeoutError, httpx2.TransportError))
            or (isinstance(exc, ServiceError) and exc.code == "unavailable")
            or (isinstance(exc, MCPError) and exc.error.code in {CONNECTION_CLOSED, REQUEST_TIMEOUT})
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


def _reconcile_input(state: McpTaskExecutionState) -> McpTaskExecutionState:
    delivery = state.unconfirmed_input
    assert delivery is not None
    if delivery.key not in (state.input_required or {}):
        return state.model_copy(
            update={"unconfirmed_input": None, "answered_inputs": state.answered_inputs | {delivery.key}}
        )
    current = datetime.now(UTC)
    if current >= delivery.reconcile_until:
        # Only a fresh GET can reopen the prompt. Never resend the previous content.
        return state.model_copy(update={"unconfirmed_input": None})
    return state.model_copy(
        update={"next_poll_at": min(delivery.reconcile_until, current + timedelta(seconds=state.poll_interval_seconds))}
    )


def _expired(prompt: McpTaskInputRequest) -> bool:
    return prompt.expires_at is not None and datetime.now(UTC) >= prompt.expires_at


def _valid(prompt: McpTaskInputRequest) -> bool:
    try:
        prompt.validate_prompt()
    except ValueError:
        return False
    return True


def _request_id(execution_id: str, key: str, prompt: McpTaskInputRequest) -> str:
    fingerprint = json.dumps([key, prompt.model_dump(mode="json", exclude={"expires_at"})], sort_keys=True)
    return f"{execution_id}.{sha256(fingerprint.encode()).hexdigest()[:24]}"


async def pending(session: AsyncSession, *, thread_ids: list[str], run_id: str | None = None) -> list[McpElicitation]:
    if not thread_ids:
        return []
    query = (
        select(ToolExecutionRow, ConnectionRow.name)
        .join(RunRow, RunRow.id == ToolExecutionRow.run_id)
        .join(PrincipalRow, PrincipalRow.id == RunRow.principal_id)
        .join(
            ConnectionRow,
            (
                (ConnectionRow.id == ToolExecutionRow.mcp_task["connection_id"].as_string())
                & (ConnectionRow.workspace_id == ToolExecutionRow.workspace_id)
            ),
        )
        .where(
            ToolExecutionRow.thread_id.in_(thread_ids),
            ToolExecutionRow.completed_at.is_(None),
            ToolExecutionRow.cancel_requested_at.is_(None),
            ToolExecutionRow.mcp_task["status"].as_string() == "input_required",
            RunRow.cancel_requested_at.is_(None),
            RunRow.occurrence_trigger_entry_id.is_(None),
            PrincipalRow.kind == "user",
        )
        .order_by(ToolExecutionRow.id)
    )
    if run_id is not None:
        query = query.where(ToolExecutionRow.run_id == run_id)
    current = await now(session)
    requests: list[McpElicitation] = []
    for row, name in await session.execute(query):
        state = McpTaskExecutionState.model_validate(row.mcp_task)
        record = observation(row)
        if state.expires_at <= current or state.next_poll_at is not None or not record.foreground_released:
            continue
        for key, prompt in sorted((state.input_required or {}).items()):
            expires_at = min(prompt.expires_at or state.expires_at, state.expires_at)
            if expires_at <= current:
                continue
            try:
                requests.append(
                    McpElicitation.project(
                        prompt,
                        request_id=_request_id(row.id, key, prompt),
                        thread_id=row.thread_id,
                        run_id=row.run_id,
                        tool_call_id=record.tool_call_id,
                        server_id=state.connection_id,
                        server_name=name,
                        expires_at=expires_at,
                    )
                )
            except ValueError:
                continue
    return requests


async def list_inputs(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str) -> list[McpElicitation]:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        run = await get_run(session, scope.workspace_id, run_id)
        return await pending(session, thread_ids=[run.thread_id], run_id=run_id)


async def respond(
    runtime: Runtime,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    request_id: str,
    response: McpInputResponse,
) -> None:
    execution_id, _, _ = request_id.partition(".")
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        run = await get_run(session, scope.workspace_id, run_id)
        row = await session.scalar(
            select(ToolExecutionRow)
            .where(
                ToolExecutionRow.id == execution_id,
                ToolExecutionRow.run_id == run.id,
                ToolExecutionRow.workspace_id == scope.workspace_id,
            )
            .with_for_update()
        )
        if row is None or row.mcp_task is None:
            raise not_found("mcp_input", request_id)
        state = McpTaskExecutionState.model_validate(row.mcp_task)
        current = await now(session)
        if (
            row.completed_at is not None
            or row.cancel_requested_at is not None
            or run.cancel_requested_at is not None
            or state.status != "input_required"
            or state.next_poll_at is not None
            or state.expires_at <= current
            or not observation(row).foreground_released
        ):
            raise conflict("mcp_input", request_id, "not_pending")
        if row.lease_expires_at > current:
            raise conflict("mcp_input", request_id, "execution_busy")
        selected = next(
            (
                (key, prompt)
                for key, prompt in (state.input_required or {}).items()
                if _request_id(row.id, key, prompt) == request_id
            ),
            None,
        )
        if selected is None:
            raise conflict("mcp_input", request_id, "not_pending")
        key, prompt = selected
        if prompt.expires_at is not None and prompt.expires_at <= current:
            raise conflict("mcp_input", request_id, "not_pending")
        try:
            prompt.validate_prompt()
            prompt.validate_response(response)
        except ValueError as exc:
            raise invalid("response", str(exc)) from exc
        if run.occurrence_trigger_entry_id is not None or (
            await session.scalar(select(PrincipalRow.kind).where(PrincipalRow.id == run.principal_id)) != "user"
        ):
            raise conflict("mcp_input", request_id, "unattended_run")
        lease, execution = await _take_lease(session, row, current, worker_id="control:mcp-input")
        # Commit a reconciliation wake-up before the remote effect. A new owner
        # reads tasks/get after an interrupted or ambiguous update, without replaying content.
        row.mcp_task = state.model_copy(update={"next_poll_at": current}).model_dump(mode="json", by_alias=True)
        row.mcp_poll_at = current
    token = ORIGIN.set(lease)
    try:
        updated = await _poll(runtime, lease, execution, answer=(key, response))
        await Store(runtime).save(updated, poll_cancelled=False)
    except LeaseLost as exc:
        raise conflict("mcp_input", request_id, "execution_changed") from exc
    finally:
        ORIGIN.reset(token)
        await relinquish_shell(runtime, lease, execution.record.execution_id)
