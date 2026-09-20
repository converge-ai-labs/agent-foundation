"""Shared active/waiting binding policy for asynchronous results."""

from a13n_service.interactions.domain import Run, RunStatus, Thread


def select_result_binding(thread: Thread, current: Run, head: Run | None) -> tuple[str | None, str | None]:
    """Return the active target or selected waiting source; otherwise leave the result unbound."""

    if current.status in {RunStatus.accepted, RunStatus.running}:
        return current.id, None
    if current.status is RunStatus.waiting and thread.head_run_id == current.id:
        return None, current.id
    if current.status in {RunStatus.failed, RunStatus.cancelled} and head is not None:
        if head.status is RunStatus.waiting:
            return None, head.id
    return None, None
