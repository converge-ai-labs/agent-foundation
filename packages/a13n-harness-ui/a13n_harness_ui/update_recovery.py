"""Stage a claimed task forest before allowing any restored model request."""

from __future__ import annotations

from typing import TYPE_CHECKING

from anyio import TASK_STATUS_IGNORED, CancelScope, move_on_after
from anyio.abc import TaskStatus

from a13n_harness_ui.maintenance import UpdateMaintenance
from a13n_harness_ui.maintenance_models import MaintenanceTask, RestartItem

if TYPE_CHECKING:
    from a13n_harness_ui.root_run import RootRunCoordinator
    from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator


async def recover_update(
    maintenance: UpdateMaintenance,
    roots: RootRunCoordinator,
    children: HarnessUiSubagentOperator,
    *,
    task_status: TaskStatus[None] = TASK_STATUS_IGNORED,
) -> None:
    if not maintenance.enabled:
        task_status.started()
        return
    batch = await maintenance.repository.claim(maintenance.owner_id)
    if batch is None:
        task_status.started()
        return
    maintenance.batch = batch
    maintenance.restoring = True
    maintenance.restoration_pending = {item.thread_id for item in batch.items}
    maintenance.continued_threads.update(maintenance.restoration_pending)
    task_status.started()
    receipts: dict[str, str] = {}
    by_thread = {item.thread_id: item for item in batch.items}

    def depth(item: RestartItem) -> int:
        result = 0
        seen = {item.thread_id}
        while item.parent_thread_id in by_thread:
            item = by_thread[item.parent_thread_id]
            if item.thread_id in seen:
                raise ValueError("Cyclic update lineage")
            seen.add(item.thread_id)
            result += 1
        return result

    try:
        # Children can reconstruct their captured Host scope without executing a
        # completed parent. Parents receive the complete successor mapping.
        for item in sorted(batch.items, key=depth, reverse=True):
            try:
                if item.execution_id is None:
                    receipt = await roots.resume_update(item, batch.batch_id)
                    receipts[item.thread_id] = receipt.receipt_id
                    task = MaintenanceTask(thread_id=item.thread_id, state="restoring")
                else:
                    execution_id, run_id = await children.resume_update(item, batch.batch_id)
                    if item.parent_thread_id is not None:
                        maintenance.successors.setdefault(item.parent_thread_id, {})[item.execution_id] = execution_id
                    task = MaintenanceTask(
                        thread_id=item.thread_id,
                        execution_id=item.execution_id,
                        state="restoring",
                        resumed_execution_id=execution_id,
                        resumed_run_id=run_id,
                    )
                maintenance.restored[item.thread_id] = task
            except Exception as exc:
                maintenance.errors[item.thread_id] = f"Update reconstruction failed ({type(exc).__name__})."
        with move_on_after(60):
            while True:
                changed = maintenance.changed
                unsettled = [
                    item.thread_id
                    for item in batch.items
                    if item.thread_id not in maintenance.errors
                    and (item.thread_id in maintenance.active and maintenance.active[item.thread_id].state is None)
                ]
                if not unsettled:
                    break
                await changed.wait()
        for item in batch.items:
            entry = maintenance.active.get(item.thread_id)
            if item.thread_id not in maintenance.errors and (entry is None or entry.state is None):
                maintenance.errors[item.thread_id] = "The resumed task could not reach its startup boundary."
        blocked_roots = {item.root_thread_id for item in batch.items if item.thread_id in maintenance.errors}
        for item in batch.items:
            previous = maintenance.restored.get(
                item.thread_id, MaintenanceTask(thread_id=item.thread_id, state="blocked")
            )
            if item.root_thread_id in blocked_roots:
                message = maintenance.errors.get(item.thread_id, "Another task in this family could not be restored.")
                maintenance.restored[item.thread_id] = previous.model_copy(
                    update={"state": "blocked", "message": message}
                )
                if item.thread_id in receipts:
                    await roots.cancel(receipts[item.thread_id])
                else:
                    await children.cancel_update(item.thread_id)
            else:
                run_id = previous.resumed_run_id
                if item.thread_id in receipts:
                    run_id = (await roots.get(receipts[item.thread_id])).run_id
                maintenance.restored[item.thread_id] = previous.model_copy(
                    update={"state": "restored", "resumed_run_id": run_id}
                )
        # Durable consumption precedes model execution. A crash here never
        # reclaims the old handoff automatically, even if no request was sent.
        settled = batch.model_copy(
            update={
                "state": "finished",
                "results": tuple(maintenance.restored.values()),
                "error": "; ".join(maintenance.errors.values()) or None,
            }
        )
        await maintenance.repository.replace(batch, settled)
        maintenance.batch = settled
        maintenance.restoration_pending.clear()
        maintenance.restoring = False
        for item in batch.items:
            entry = maintenance.active.get(item.thread_id)
            if entry is not None and item.root_thread_id not in blocked_roots:
                entry.release.set()
        maintenance.signal()
    except BaseException as exc:
        # No release on partial staging. The claimed marker intentionally
        # survives shutdown; an operator must reconcile it before trying again.
        with CancelScope(shield=True):
            for receipt_id in receipts.values():
                await roots.cancel(receipt_id)
            for item in batch.items:
                if item.execution_id is not None:
                    await children.cancel_update(item.thread_id)
        maintenance.errors["app"] = "Update staging did not finish. Inspect the claimed handoff before dismissing it."
        maintenance.restoring = False
        maintenance.restoration_pending.clear()
        maintenance.signal()
        if not isinstance(exc, Exception):
            raise
    finally:
        # Only the newly staged capabilities retain this continuation context;
        # later ordinary Runs on the same Threads must not inherit it.
        maintenance.continued_threads.clear()
        maintenance.successors.clear()
