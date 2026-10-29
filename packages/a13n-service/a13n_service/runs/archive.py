"""Archiving a thread: it stops for good and keeps every fact."""

from sqlalchemy import delete, update

from a13n_service.infra.db import lock, now, transaction
from a13n_service.infra.http import require_match
from a13n_service.runs import compactions, inbox
from a13n_service.runs.environments.tables import ThreadEnvironmentRow
from a13n_service.runs.memories.tables import ThreadMemoryRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import ThreadView
from a13n_service.runs.seal import stop
from a13n_service.runs.tables import RunRow, ToolExecutionRow
from a13n_service.runs.threads import get_thread, refresh_version
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


async def archive(
    runtime: Runtime, actor: Principal, workspace_id: str, thread_id: str, *, if_match: str | None
) -> ThreadView:
    """Withdraw input and desired mounts, stop the current Run, and cancel every retained tool.

    Active users keep frozen mounts until cleanup finishes. Archive is final; repeating it returns the thread.
    """
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        thread = await get_thread(session, scope.workspace_id, thread_id, lock=True)
        require_match(if_match, thread.id, thread.version)
        if thread.archived_at is not None:
            return ThreadView.model_validate(thread)
        current = await now(session)
        thread.archived_at = current
        await inbox.withdraw_pending(session, thread, at=current)
        from a13n_service.runs.browsers.delivery import suppress
        from a13n_service.runs.browsers.tables import ThreadBrowserRow

        await suppress(session, thread.id)
        await session.execute(delete(ThreadBrowserRow).where(ThreadBrowserRow.thread_id == thread.id))
        await session.execute(delete(ThreadEnvironmentRow).where(ThreadEnvironmentRow.thread_id == thread.id))
        await session.execute(delete(ThreadMemoryRow).where(ThreadMemoryRow.thread_id == thread.id))
        if thread.current_run_id is not None:
            run = await lock(session, RunRow, thread.current_run_id)
            assert run is not None
            await stop(session, runtime, thread, run)
        # Admission takes the same Thread lock, so it either precedes this update
        # or observes archived_at and is rejected before effects.
        await session.execute(
            update(ToolExecutionRow)
            .where(
                ToolExecutionRow.thread_id == thread.id,
                ToolExecutionRow.completed_at.is_(None),
                ToolExecutionRow.cancel_requested_at.is_(None),
            )
            .values(cancel_requested_at=current)
        )
        await compactions.archive(session, runtime, thread.id, at=current)
        return ThreadView.model_validate(await refresh_version(session, thread))
