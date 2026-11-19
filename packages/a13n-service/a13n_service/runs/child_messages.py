"""Durable, replay-safe explicit messages from an async child Run to its immediate parent."""

from datetime import datetime, timedelta
from hashlib import sha256

from a13n_harness.context import AgentContext
from a13n_harness.tools.metadata import HarnessTool, HarnessToolMetadata, ToolOutputPolicy
from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy import func, or_, select

from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import conflict, invalid
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbox import Claim, Handler, OutboxRow, enqueue_once, settle
from a13n_service.runs import inbox
from a13n_service.runs.accept import advance
from a13n_service.runs.attempts import Lease, LeaseLost
from a13n_service.runs.children import FULL_INBOX_SECONDS
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import ChildMessageRow, InboxEntryRow, RunRow, ThreadRow
from a13n_service.runs.threads import lock_thread
from a13n_service.runs.tool_executions.authority import lock_owner
from a13n_service.runs.tools import tool_failures

MAX_BYTES = 16 * 1024
MAX_PER_RUN = 8
LIFETIME = timedelta(hours=24)


def _payload(sender: RunRow, child: ThreadRow, body: str | None, deadline: datetime) -> dict[str, JsonValue]:
    return {
        "child_run_id": sender.id,
        "child_thread_id": child.id,
        "subagent": child.subagent,
        "message": body,
        "deadline": deadline.isoformat(),
    }


async def send(runtime: Runtime, lease: Lease, tool_call_id: str | None, message: str) -> dict[str, JsonValue]:
    if not runtime.settings.worker.child_messages_enabled:
        raise invalid("message", "Child messages are not enabled")
    if not tool_call_id:
        raise invalid("tool_call_id", "A stable tool-call ID is required")
    body = message.strip()
    try:
        encoded = body.encode()
    except UnicodeEncodeError:
        raise invalid("message", "Message must be valid UTF-8 text") from None
    if not body or len(encoded) > MAX_BYTES or "\x00" in body:
        raise invalid("message", "Message must be non-blank UTF-8 text of at most 16 KiB without NUL characters")
    digest = sha256(encoded).hexdigest()
    async with transaction(runtime.storage) as session:
        child = await lock_thread(session, lease.thread_id)
        if child is None or child.archived_at is not None:
            raise LeaseLost()
        run, _ = await lock_owner(session, runtime, lease)
        if child.origin != "child" or child.origin_thread_id is None or run.thread_id != child.id:
            raise invalid("message", "Only an asynchronous child can message its parent")
        prior = await session.scalar(
            select(ChildMessageRow).where(
                ChildMessageRow.child_run_id == run.id, ChildMessageRow.tool_call_id == tool_call_id
            )
        )
        if prior is not None:
            if prior.digest != digest:
                raise conflict("child_message", prior.id, "tool_call_message_changed")
            message_id = prior.id
        else:
            count = (
                await session.scalar(
                    select(func.count()).select_from(ChildMessageRow).where(ChildMessageRow.child_run_id == run.id)
                )
                or 0
            )
            if count >= MAX_PER_RUN:
                raise invalid("message", "This child Run has used its parent message allowance")
            deadline = await now(session) + LIFETIME
            if inbox.payload_size(_payload(run, child, body, deadline)) > runtime.settings.control.inbox_bytes:
                raise invalid("message", "Message exceeds the parent inbox byte limit")
            message_id = new_object_id("inb")
            session.add(
                ChildMessageRow(
                    id=message_id,
                    child_run_id=run.id,
                    tool_call_id=tool_call_id,
                    ordinal=count + 1,
                    parent_thread_id=child.origin_thread_id,
                    digest=digest,
                    body=body,
                    deadline=deadline,
                )
            )
            await enqueue_once(
                session,
                organization_id=run.organization_id,
                workspace_id=run.workspace_id,
                kind="child_message",
                dedupe_key=message_id,
                target={"thread_id": child.origin_thread_id},
                payload={"message_id": message_id},
            )
        return {
            "message_id": message_id,
            "child_thread_id": child.id,
            "child_run_id": run.id,
            "parent_thread_id": child.origin_thread_id,
            "admission": "persisted",
        }


class ParentMessageCapability(AbstractCapability[AgentContext]):
    id = "a13n.service.child-messages"

    def __init__(self, runtime: Runtime, lease: Lease):
        self.runtime, self.lease = runtime, lease

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(
            tools=[
                HarnessTool(
                    self.send_parent_message,
                    description="Send a short question or update to your immediate parent agent. Returns after the message is saved, without waiting for a reply.",
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


def child_messages(runtime: Runtime) -> Handler:
    async def deliver(claimed: Claim) -> None:
        async with transaction(runtime.storage) as session:
            parent = await lock_thread(session, claimed.target["thread_id"])
            intent = await session.get(ChildMessageRow, claimed.payload["message_id"], with_for_update=True)
            if intent is None:
                await settle(session, claimed, "dead", error="missing_message")
                return
            if intent.disposition is not None:
                await settle(
                    session,
                    claimed,
                    "delivered",
                    error=None if intent.disposition == "appended" else intent.disposition,
                )
                return
            sender = await session.get(RunRow, intent.child_run_id)
            child = await session.get(ThreadRow, sender.thread_id) if sender is not None else None
            origin = (
                await session.get(RunRow, child.origin_run_id) if child is not None and child.origin_run_id else None
            )
            if (
                parent is None
                or sender is None
                or child is None
                or origin is None
                or intent.parent_thread_id != parent.id
                or child.origin != "child"
                or child.origin_thread_id != parent.id
                or origin.thread_id != parent.id
                or parent.workspace_id != child.workspace_id
                or parent.workspace_id != claimed.workspace_id
                or parent.organization_id != claimed.organization_id
                or child.session_id != parent.session_id
            ):
                if await settle(session, claimed, "dead", error="invalid_target"):
                    intent.disposition, intent.body = "suppressed", None
                return
            if parent.archived_at is not None or intent.deadline <= await now(session):
                reason = "suppressed" if parent.archived_at is not None else "expired"
                if await settle(session, claimed, "delivered", error=reason):
                    intent.disposition, intent.body = reason, None
                return
            earlier = await session.scalar(
                select(ChildMessageRow.id)
                .join(OutboxRow, (OutboxRow.kind == "child_message") & (OutboxRow.dedupe_key == ChildMessageRow.id))
                .where(
                    OutboxRow.status == "pending",
                    ChildMessageRow.child_run_id == intent.child_run_id,
                    ChildMessageRow.ordinal < intent.ordinal,
                    ChildMessageRow.disposition.is_(None),
                )
                .limit(1)
            )
            if earlier is not None:
                await settle(session, claimed, "deferred", error="earlier_message_pending", retry_after=2)
                return
            payload = _payload(sender, child, intent.body, intent.deadline)
            size = inbox.payload_size(payload)
            if size > runtime.settings.control.inbox_bytes:
                if await settle(session, claimed, "dead", error="message_exceeds_inbox_limit"):
                    intent.disposition, intent.body = "suppressed", None
                return
            if not await inbox.has_room(session, parent.id, runtime.settings.control, adding=1, growth=size):
                await settle(session, claimed, "deferred", error="inbox_full", retry_after=FULL_INBOX_SECONDS)
                return
            if not await settle(session, claimed, "delivered"):
                return
            entry = await inbox.append_child_message(session, parent, origin, intent.id, payload)
            intent.disposition, intent.body = "appended", None
            runtime.mailbox.notify(session, entry)
            parent_id = parent.id
        await advance(runtime, parent_id)

    return deliver


async def expire_child_messages(runtime: Runtime, *, limit: int) -> None:
    async with short_session(runtime.storage) as session:
        due = (
            await session.execute(
                select(ChildMessageRow.id, ChildMessageRow.parent_thread_id)
                .outerjoin(InboxEntryRow, InboxEntryRow.id == ChildMessageRow.id)
                .where(
                    ChildMessageRow.deadline <= func.now(),
                    or_(
                        ChildMessageRow.disposition.is_(None),
                        (ChildMessageRow.disposition == "appended") & (InboxEntryRow.status == "pending"),
                    ),
                )
                .order_by(ChildMessageRow.deadline, ChildMessageRow.id)
                .limit(limit)
            )
        ).all()
    for message_id, parent_id in due:
        async with transaction(runtime.storage) as session:
            if await lock_thread(session, parent_id, skip_locked=True) is None:
                continue
            intent = await session.get(ChildMessageRow, message_id, with_for_update=True)
            if intent is None:
                continue
            if intent.disposition is None:
                intent.disposition, intent.body = "expired", None
            elif intent.disposition == "appended":
                entry = await session.get(InboxEntryRow, message_id)
                if entry is not None and inbox.expire_child_message(entry, await now(session)):
                    intent.disposition = "expired"
