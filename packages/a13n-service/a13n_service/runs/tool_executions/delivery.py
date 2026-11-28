"""Enqueue tool completion hints without acknowledging Agent consumption."""

from pydantic import JsonValue

from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.outbox import Claim, Handler, settle
from a13n_service.runs import inbox
from a13n_service.runs.accept import advance
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow, ThreadRow, ToolExecutionRow
from a13n_service.runs.tool_executions.store import managed_inline, observation


def tool_results(runtime: Runtime) -> Handler:
    async def deliver(claimed: Claim) -> None:
        async with transaction(runtime.storage) as session:
            thread = await lock(session, ThreadRow, claimed.target["thread_id"])
            row = await lock(session, ToolExecutionRow, claimed.payload["execution_id"])
            current = await now(session)
            if row is None or row.expires_at is None or row.expires_at <= current:
                await settle(session, claimed, "delivered", error="result_expired")
                return
            origin = await session.get(RunRow, row.run_id)
            if thread is None or origin is None or row.thread_id != thread.id:
                await settle(session, claimed, "dead", error="invalid_target")
                return
            if await managed_inline(session, origin):
                # Even an already-claimed old notification cannot fork a managed inline Analysis.
                await settle(session, claimed, "delivered", error="inline_result")
                return
            if row.first_delivered_at is not None:
                await settle(session, claimed, "delivered")
                return
            if thread.archived_at is not None or origin.cancel_requested_at is not None:
                # A stopped origin never wakes the conversation. Its result stays explicitly readable.
                await settle(session, claimed, "delivered", error="continuation_suppressed")
                return
            record = observation(row)
            payload: dict[str, JsonValue] = {
                "execution_id": row.id,
                "tool_name": record.tool_name,
                "status": row.status,
            }
            size = inbox.payload_size(payload)
            if not await inbox.has_room(session, thread.id, runtime.settings.control, adding=1, growth=size):
                await settle(session, claimed, "deferred", error="inbox_full", retry_after=30)
                return
            if not await settle(session, claimed, "delivered"):
                return
            entry = await inbox.append_tool_result(session, thread, origin, payload)
            runtime.mailbox.notify(session, entry)
            thread_id = thread.id
        await advance(runtime, thread_id)

    return deliver
