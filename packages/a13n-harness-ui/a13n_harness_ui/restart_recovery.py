"""Consume a saved task forest before opening the App to new work."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a13n_logging import get_logger
from anyio import CancelScope, move_on_after

from a13n_harness_ui.restart import GracefulRestart
from a13n_harness_ui.restart_models import RestartItem, RestartResult

if TYPE_CHECKING:
    from a13n_harness_ui.root_run import RootRunCoordinator
    from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator


async def recover_restart(
    restart_coordinator: GracefulRestart,
    roots: RootRunCoordinator,
    children: HarnessUiSubagentOperator,
) -> None:
    if not restart_coordinator.enabled:
        return
    batch = await restart_coordinator.repository.claim()
    if batch is None:
        return
    restart_coordinator.restoring = True
    restart_coordinator.restoration_pending = {item.thread_id for item in batch.items}
    restart_coordinator.continued_threads.update(restart_coordinator.restoration_pending)
    restored: dict[str, RestartResult] = {}
    receipts: dict[str, str] = {}
    by_thread = {item.thread_id: item for item in batch.items}

    def depth(item: RestartItem) -> int:
        result = 0
        seen = {item.thread_id}
        while item.parent_thread_id in by_thread:
            item = by_thread[item.parent_thread_id]
            if item.thread_id in seen:
                raise ValueError("Cyclic restart lineage")
            seen.add(item.thread_id)
            result += 1
        return result

    try:
        # Bound reconstruction as well as the wait for initial model boundaries.
        with move_on_after(60):
            # Children can reconstruct their captured Host scope without executing a
            # completed parent. Parents receive the complete successor mapping.
            for item in sorted(batch.items, key=depth, reverse=True):
                try:
                    if item.execution_id is None:
                        receipt = await roots.resume_restart(item)
                        receipts[item.thread_id] = receipt.receipt_id
                        task = RestartResult(thread_id=item.thread_id, state="restoring")
                    else:
                        execution_id, run_id = await children.resume_restart(item, batch.batch_id)
                        if item.parent_thread_id is not None:
                            restart_coordinator.successors.setdefault(item.parent_thread_id, {})[item.execution_id] = (
                                execution_id
                            )
                        task = RestartResult(
                            thread_id=item.thread_id,
                            execution_id=item.execution_id,
                            state="restoring",
                            resumed_execution_id=execution_id,
                            resumed_run_id=run_id,
                        )
                    restored[item.thread_id] = task
                except Exception as exc:
                    restart_coordinator.errors[item.thread_id] = (
                        f"Restart reconstruction failed ({type(exc).__name__})."
                    )
            while True:
                changed = restart_coordinator.changed
                unsettled = [
                    item.thread_id
                    for item in batch.items
                    if item.thread_id not in restart_coordinator.errors
                    and (
                        item.thread_id in restart_coordinator.active
                        and restart_coordinator.active[item.thread_id].state is None
                    )
                ]
                if not unsettled:
                    break
                await changed.wait()
        for item in batch.items:
            entry = restart_coordinator.active.get(item.thread_id)
            if item.thread_id not in restart_coordinator.errors and (entry is None or entry.state is None):
                restart_coordinator.errors[item.thread_id] = "The resumed task could not reach its startup boundary."
        blocked_roots = {item.root_thread_id for item in batch.items if item.thread_id in restart_coordinator.errors}
        for item in batch.items:
            previous = restored.get(item.thread_id, RestartResult(thread_id=item.thread_id, state="blocked"))
            if item.root_thread_id in blocked_roots:
                message = restart_coordinator.errors.get(
                    item.thread_id, "Another task in this family could not be restored."
                )
                restored[item.thread_id] = previous.model_copy(update={"state": "blocked", "message": message})
                if item.thread_id in receipts:
                    await roots.cancel(receipts[item.thread_id])
                else:
                    await children.cancel_restart(item.thread_id)
            else:
                run_id = previous.resumed_run_id
                if item.thread_id in receipts:
                    run_id = (await roots.get(receipts[item.thread_id])).run_id
                restored[item.thread_id] = previous.model_copy(update={"state": "restored", "resumed_run_id": run_id})
        # Durable consumption precedes model execution. A crash here never
        # reclaims the old handoff automatically, even if no request was sent.
        settled = batch.model_copy(
            update={
                "state": "consumed",
                "results": tuple(restored.values()),
                "error": "; ".join(restart_coordinator.errors.values()) or None,
            }
        )
        await restart_coordinator.repository.replace(batch, settled)
        restart_coordinator.restoration_pending.clear()
        restart_coordinator.restoring = False
        for item in batch.items:
            entry = restart_coordinator.active.get(item.thread_id)
            if entry is not None and item.root_thread_id not in blocked_roots:
                entry.state = None
                entry.release.set()
        restart_coordinator.signal()
    except BaseException as exc:
        # No release on partial staging. Consumption is permanent, so an
        # interrupted attempt is not replayed and never locks normal admission.
        with CancelScope(shield=True):
            for receipt_id in receipts.values():
                await roots.cancel(receipt_id)
            for item in batch.items:
                if item.execution_id is not None:
                    await children.cancel_restart(item.thread_id)
        restart_coordinator.errors["app"] = "Startup recovery did not finish; automatic retry is skipped."
        restart_coordinator.restoring = False
        restart_coordinator.restoration_pending.clear()
        restart_coordinator.signal()
        if not isinstance(exc, Exception):
            raise
    finally:
        if restart_coordinator.errors:
            get_logger(__name__).warning(
                "Startup recovery skipped work", extra={"errors": tuple(restart_coordinator.errors.values())}
            )
        # Only the newly staged capabilities retain this continuation context;
        # later ordinary Runs on the same Threads must not inherit it.
        restart_coordinator.continued_threads.clear()
        restart_coordinator.successors.clear()
