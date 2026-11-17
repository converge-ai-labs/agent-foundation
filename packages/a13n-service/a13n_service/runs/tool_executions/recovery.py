"""Recover admitted calls without repeating effects; managed inline analyses retain native results."""

from dataclasses import replace
from datetime import datetime

from a13n_harness import HarnessState
from pydantic_ai._tool_execution import build_tool_return_part
from pydantic_ai.messages import (
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow, ToolExecutionRow
from a13n_service.runs.tool_executions.store import Store, delivered, managed_inline, notify, observation


async def acknowledge(session: AsyncSession, run: RunRow, state: HarnessState, *, at: datetime, seconds: int) -> None:
    """A restored result is consumed only when its answering history is durably checkpointed."""
    if not await managed_inline(session, run):
        return
    answered = {
        part.tool_call_id
        for message in state.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart | RetryPromptPart)
    }
    if not answered:
        return
    rows = await session.scalars(
        select(ToolExecutionRow)
        .where(ToolExecutionRow.run_id == run.id, ToolExecutionRow.record["tool_call_id"].astext.in_(answered))
        .with_for_update()
    )
    for row in rows:
        delivered(row, at, seconds)


async def _inline(
    runtime: Runtime, pending: dict[str, ToolCallPart], *, thread_id: str, run_id: str
) -> list[ModelRequestPart]:
    async with short_session(runtime.storage) as session:
        rows = list(
            await session.scalars(
                select(ToolExecutionRow)
                .where(
                    ToolExecutionRow.thread_id == thread_id,
                    ToolExecutionRow.run_id == run_id,
                    ToolExecutionRow.record["tool_call_id"].astext.in_(pending),
                )
                .order_by(ToolExecutionRow.created_at)
            )
        )
    parts: list[ModelRequestPart] = []
    for row in rows:
        record = observation(row)
        call = pending[record.tool_call_id]
        if call.tool_name != record.tool_name:
            raise ServiceError(
                "conflict",
                "An admitted tool's identity no longer matches its checkpoint.",
                {"reason": "tool_result_unavailable"},
            )
        try:
            execution = await Store(runtime).get(thread_id, row.id)
        except Exception as error:
            raise ServiceError(
                "conflict",
                "An admitted tool's retained result could not be restored; its effects will not be repeated.",
                {"reason": "tool_result_unavailable"},
            ) from error
        if execution is not None and not execution.record.terminal:
            # Existing bounded attempt release/backoff observes the same admission on the next claim.
            raise ServiceError("unavailable", "The original inline tool execution is still running.")
        if execution is None or not (execution.record.result_available or execution.retry_prompt is not None):
            raise ServiceError(
                "conflict",
                "An admitted tool's result is unavailable; its effects will not be repeated.",
                {"reason": "tool_result_unavailable"},
            )
        if execution.retry_prompt is not None:
            parts.append(ToolReturnPart(call.tool_name, execution.retry_prompt, call.tool_call_id, outcome="retried"))
        else:
            returned, content, _ = build_tool_return_part(execution.result, call=call, tool_kind="function")
            parts.append(returned)
            if content is not None:
                parts.append(UserPromptPart(content))
    return parts


async def restore(runtime: Runtime, state: HarnessState, *, thread_id: str, origin_run_id: str) -> HarnessState:
    history = list(state.message_history)
    index = next((i for i in range(len(history) - 1, -1, -1) if isinstance(history[i], ModelResponse)), None)
    if index is None:
        return state
    answered = {
        part.tool_call_id
        for message in history[index + 1 :]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart | RetryPromptPart)
    }
    pending = {
        part.tool_call_id: part
        for part in history[index].parts
        if isinstance(part, ToolCallPart) and part.tool_call_id not in answered
    }
    if not pending:
        return state
    async with short_session(runtime.storage) as session:
        run = await session.get(RunRow, origin_run_id)
        inline = run is not None and await managed_inline(session, run)
    if inline:
        returns = await _inline(runtime, pending, thread_id=thread_id, run_id=origin_run_id)
    else:
        returns = []
        async with transaction(runtime.storage) as session:
            rows = await session.scalars(
                select(ToolExecutionRow)
                .where(
                    ToolExecutionRow.thread_id == thread_id,
                    ToolExecutionRow.run_id == origin_run_id,
                    ToolExecutionRow.record["tool_call_id"].astext.in_(pending),
                )
                .order_by(ToolExecutionRow.created_at)
                .with_for_update()
            )
            current = await now(session)
            for row in rows:
                record = observation(row)
                call = pending.get(record.tool_call_id)
                if call is None or call.tool_name != record.tool_name:
                    continue
                record = record.model_copy(update={"foreground_released": True})
                row.record = record.model_dump(mode="json")
                value = record.receipt()
                if row.expires_at is not None and row.expires_at <= current:
                    value["message"] = (
                        "The original invocation was admitted. Its retained result has expired; do not reissue it."
                    )
                elif record.terminal and row.first_delivered_at is None:
                    await notify(session, row)
                returns.append(ToolReturnPart(call.tool_name, value, call.tool_call_id))
                pending.pop(call.tool_call_id)
    if not returns:
        return state
    if isinstance(history[-1], ModelRequest):
        history[-1] = replace(history[-1], parts=[*history[-1].parts, *returns])
    else:
        history.append(ModelRequest(parts=returns))
    return HarnessState.model_validate({**state.model_dump(), "message_history": history})
