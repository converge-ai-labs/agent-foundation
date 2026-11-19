"""Send explicit input from an asynchronous child to its immediate parent's inbox."""

from a13n_harness.context import AgentContext
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

from a13n_service.infra.db import transaction
from a13n_service.infra.errors import conflict, invalid
from a13n_service.runs import inbox
from a13n_service.runs.accept import advance
from a13n_service.runs.attempts import Lease, LeaseLost
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.runs.threads import lock_thread
from a13n_service.runs.tool_executions.authority import lock_owner
from a13n_service.runs.tools import tool_failures

MAX_BYTES = 16 * 1024


async def send(runtime: Runtime, lease: Lease, tool_call_id: str | None, message: str) -> dict[str, JsonValue]:
    if not tool_call_id:
        raise invalid("tool_call_id", "A stable tool-call ID is required")
    body = message.strip()
    try:
        size = len(body.encode())
    except UnicodeEncodeError:
        raise invalid("message", "Message must be valid UTF-8 text") from None
    if not body or size > MAX_BYTES or "\x00" in body:
        raise invalid("message", "Message must be non-blank UTF-8 text of at most 16 KiB without NUL characters")
    async with transaction(runtime.storage) as session:
        # Delegation provenance is immutable. Resolve the destination before taking any child-side lock.
        child = await session.get(ThreadRow, lease.thread_id)
        if child is None or child.origin != "child" or child.origin_thread_id is None or child.origin_run_id is None:
            raise invalid("message", "Only an asynchronous child can message its parent")
        parent = await lock_thread(session, child.origin_thread_id)
        if parent is None:
            raise conflict("thread", child.origin_thread_id, "parent_missing")
        # Parent-to-child controls lock the parent Run before the child Thread. Take the origin FK's
        # key-share lock here, not implicitly during INSERT after we hold the child locks.
        origin = await session.get(RunRow, child.origin_run_id, with_for_update={"read": True, "key_share": True})
        assert origin is not None and origin.thread_id == parent.id
        child = await lock_thread(session, child.id)
        if child is None or child.archived_at is not None:
            raise LeaseLost()
        run, _ = await lock_owner(session, runtime, lease)
        payload: dict[str, JsonValue] = {
            "child_run_id": run.id,
            "child_thread_id": child.id,
            "subagent": child.subagent,
            "message": body,
        }
        request = inbox.Request.for_child_message(run.id, tool_call_id, parent.id, payload)
        entry = await inbox.find_request(session, parent.workspace_id, origin.principal_id, request.key)
        appended = entry is None
        if entry is None:
            if parent.archived_at is not None:
                raise conflict("thread", parent.id, "thread_archived")
            if inbox.payload_size(payload) > runtime.settings.worker.delivery_bytes:
                raise invalid("message", "Message exceeds the worker steering byte limit")
            entry = await inbox.append_child_message(
                session, parent, origin, payload, request, runtime.settings.control
            )
            runtime.mailbox.notify(session, entry)
        else:
            inbox.check_replay(entry, request)
        receipt: dict[str, JsonValue] = {
            "message_id": entry.id,
            "child_thread_id": child.id,
            "child_run_id": run.id,
            "parent_thread_id": parent.id,
            "admission": "persisted",
        }
        parent_id = parent.id
    if appended:
        await advance(runtime, parent_id, skip_locked=True)
    return receipt


class ParentMessageCapability(AbstractCapability[AgentContext]):
    id = "a13n.service.child-messages"

    def __init__(self, runtime: Runtime, lease: Lease):
        self.runtime, self.lease = runtime, lease

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=[
                HarnessTool(
                    self.send_parent_message,
                    description="Send a short question or update to your immediate parent agent. Returns after the message is saved in the parent inbox, without waiting for a reply.",
                    harness_metadata=HarnessToolMetadata(
                        tool_id="subagents.send_parent_message",
                        effects=frozenset({"write"}),
                        credential_audiences=(),
                        idempotency="provider_key",
                        output_policy=ToolOutputPolicy(
                            max_inline_bytes=4096, max_output_bytes=4096, overflow="truncate"
                        ),
                    ),
                )
            ],
            id=self.id,
        )

    async def send_parent_message(self, ctx: RunContext[AgentContext], message: str) -> JsonValue:
        with tool_failures():
            return await send(self.runtime, self.lease, ctx.tool_call_id, message)
