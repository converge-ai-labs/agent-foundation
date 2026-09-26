"""Child runs: delivering each sealed child run's result to the thread that spawned it.

A child's seal enqueues one `child_result` outbox row keyed by the sealed run. Delivery locks only the parent
thread, settles the row and inserts the unique result entry in one commit; acceptance of a successor is a
separate transaction after it. A result that does not fit the parent's inbox stays in its outbox row: it is
retried however long that takes, because a child's result is never dropped.
"""

from a13n_logging import get_logger

from a13n_service.infra.db import lock, transaction
from a13n_service.infra.outbox import Claim, Handler, settle
from a13n_service.runs import inbox
from a13n_service.runs.accept import advance
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow, ThreadRow

logger = get_logger(__name__)
# A full inbox drains only as the parent's runs take its entries, so a deferred result looks again this much later.
FULL_INBOX_SECONDS = 30


def child_results(runtime: Runtime) -> Handler:
    async def deliver(claimed: Claim) -> None:
        async with transaction(runtime.storage) as session:
            parent = await lock(session, ThreadRow, claimed.target["thread_id"])
            child = await session.get(RunRow, claimed.payload["child_run_id"])
            origin = await session.get(RunRow, claimed.target["origin_run_id"])
            if parent is None or child is None or origin is None or origin.thread_id != parent.id:
                logger.error("Child result names no valid parent", extra={"outbox_id": claimed.id})
                await settle(session, claimed, "dead", error="invalid_target")
                return
            if parent.archived_at is not None:
                # Delivered and ignored: an archived thread takes no more input.
                await settle(session, claimed, "delivered", error="thread_archived")
                return
            result = inbox.child_result(await session.get_one(ThreadRow, child.thread_id), child)
            control, size = runtime.settings.control, inbox.payload_size(result)
            if not await inbox.has_room(session, parent.id, control, adding=1, growth=size):
                await settle(session, claimed, "deferred", error="inbox_full", retry_after=FULL_INBOX_SECONDS)
                return
            # Settled first: a claim that lost its lease must not insert the entry its successor delivers.
            if not await settle(session, claimed, "delivered"):
                return
            await inbox.append_child_result(session, parent, child, origin, result)
            parent_id = parent.id
        await advance(runtime, parent_id)

    return deliver
