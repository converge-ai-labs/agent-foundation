"""Restore admitted calls as receipts before Harness considers retrying interrupted tools."""

from dataclasses import replace

from a13n_harness import HarnessState
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from sqlalchemy import select

from a13n_service.infra.db import now, transaction
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import ToolExecutionRow
from a13n_service.runs.tool_executions.store import notify, observation


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
    returns: list[ToolReturnPart] = []
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
