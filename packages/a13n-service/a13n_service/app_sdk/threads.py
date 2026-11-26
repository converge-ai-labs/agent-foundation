"""Native Thread admission, activity and child discovery."""

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from a13n_service.infra import cursors
from a13n_service.infra.errors import invalid, not_found
from a13n_service.resources.assets.tables import AssetRow
from a13n_service.runs import submit
from a13n_service.runs.accept import eligible, paused
from a13n_service.runs.environments.schemas import SessionConfiguration as SessionConfiguration
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Failure, RunStatus, WaitReason
from a13n_service.runs.schemas import Message as Message
from a13n_service.runs.schemas import NewThread as NewThread
from a13n_service.runs.schemas import Submitted as Submitted
from a13n_service.runs.schemas import ThreadView as ThreadView
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow

from ._access import app_workspace, run_actor
from ._thread_resources import UpdatedThreadResources as UpdatedThreadResources
from ._thread_resources import update_app_egress_secrets as update_app_egress_secrets
from ._thread_resources import update_thread_resources as update_thread_resources


@dataclass(frozen=True, slots=True)
class Admission:
    """Current App and Service Account authority, usable only in the issuing transaction."""

    _native: submit.AppSubmission


async def prepare_admission(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    principal_id: str,
    *,
    app_id: str,
) -> Admission:
    """Call before locking App state or Threads in the outer Service transaction."""
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    actor = await run_actor(session, runtime, scope, principal_id)
    native = submit.AppSubmission(session, runtime, workspace_id, actor)
    session.info["a13n.app_submission"] = native
    return Admission(native)


async def create_thread_in_transaction(
    session: AsyncSession,
    runtime: Runtime,
    admission: Admission,
    workspace_id: str,
    body: NewThread,
    *,
    request_key: str,
) -> tuple[ThreadView, Submitted]:
    """Create, bind resources and accept the first Run atomically; reuse the key on retry."""
    submitted = await submit.create_thread_in_transaction(
        session,
        runtime,
        admission._native,
        workspace_id,
        body,
        request_key=request_key,
    )
    return submitted.thread, submitted


async def submit_message_in_transaction(
    session: AsyncSession,
    runtime: Runtime,
    admission: Admission,
    workspace_id: str,
    thread_id: str,
    message: Message,
    *,
    request_key: str,
) -> tuple[Submitted, bool]:
    """Submit to the native Inbox; the flag distinguishes creation from replay."""
    return await submit.submit_message_in_transaction(
        session,
        runtime,
        admission._native,
        workspace_id,
        thread_id,
        message,
        request_key=request_key,
    )


@dataclass(frozen=True, slots=True)
class ThreadActivity:
    current_run_id: str | None
    last_run_id: str | None
    last_status: RunStatus | None
    last_failure: Failure | None
    has_pending_native_entries: bool
    paused: bool
    eligible: bool


async def get_thread_activity(session: AsyncSession, workspace_id: str, thread_id: str) -> ThreadActivity:
    """Read one snapshot of native Inbox activity for an App-authorized Thread."""
    pending = (
        select(InboxEntryRow.id)
        .where(
            InboxEntryRow.thread_id == ThreadRow.id,
            InboxEntryRow.status == "pending",
        )
        .exists()
    )
    found = (
        (
            await session.execute(
                select(ThreadRow, RunRow, pending)
                .options(defer(ThreadRow.message_history))
                .outerjoin(
                    RunRow,
                    RunRow.id == ThreadRow.last_run_id,
                )
                .where(ThreadRow.workspace_id == workspace_id, ThreadRow.id == thread_id)
            )
        )
        .tuples()
        .one_or_none()
    )
    if found is None:
        raise not_found("thread", thread_id)
    thread, last, has_pending = found
    return ThreadActivity(
        thread.current_run_id,
        thread.last_run_id,
        last.status if last else None,
        Failure.model_validate(last.failure) if last and last.failure is not None else None,
        has_pending,
        paused(last),
        eligible(thread, last) and not paused(last) and thread.purpose == "conversation",
    )


@dataclass(frozen=True, slots=True)
class ChildThread:
    id: str
    origin_thread_id: str | None
    subagent: str | None
    current_run_id: str | None
    last_run_id: str | None
    last_status: RunStatus | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ChildThreadPage:
    items: tuple[ChildThread, ...]
    next_cursor: str | None


async def list_child_threads(
    session: AsyncSession,
    workspace_id: str,
    *,
    session_id: str,
    origin_thread_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> ChildThreadPage:
    """List Service child Threads in a Session the App has already authorized."""
    if not 1 <= limit <= 200:
        raise invalid("limit", "must_be_between_1_and_200")
    if (
        await session.scalar(
            select(SessionRow.id).where(
                SessionRow.workspace_id == workspace_id,
                SessionRow.id == session_id,
            )
        )
        is None
    ):
        raise not_found("session", session_id)
    query = (
        select(ThreadRow)
        .options(defer(ThreadRow.message_history))
        .where(
            ThreadRow.workspace_id == workspace_id,
            ThreadRow.session_id == session_id,
            ThreadRow.origin == "child",
        )
    )
    if origin_thread_id is not None:
        query = query.where(ThreadRow.origin_thread_id == origin_thread_id)
    rows, next_cursor = await cursors.keyset_page(
        session,
        query,
        (ThreadRow.created_at, ThreadRow.id),
        kind="app_child_threads",
        owner=cursors.query_owner(workspace_id, session_id, origin_thread_id),
        cursor=cursor,
        limit=limit,
        newest_first=False,
    )
    statuses = dict(
        (
            await session.execute(
                select(RunRow.id, RunRow.status).where(
                    RunRow.id.in_({row.last_run_id for row in rows if row.last_run_id is not None}),
                )
            )
        )
        .tuples()
        .all()
    )
    return ChildThreadPage(
        tuple(
            ChildThread(
                row.id,
                row.origin_thread_id,
                row.subagent,
                row.current_run_id,
                row.last_run_id,
                statuses.get(row.last_run_id) if row.last_run_id is not None else None,
                row.updated_at,
            )
            for row in rows
        ),
        next_cursor,
    )


@dataclass(frozen=True, slots=True)
class RunAsset:
    """An immutable workspace asset published by a Run."""

    id: str
    name: str
    content_type: str
    size: int
    digest: str
    retired_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RunOutput:
    """Read-only projection of a Run's sealed output, failure and published assets."""

    run_id: str
    session_id: str
    thread_id: str
    status: RunStatus
    sealed_at: datetime | None
    output: JsonValue | None
    output_text: str | None
    failure: Failure | None
    wait_reason: WaitReason | None
    assets: tuple[RunAsset, ...]

    @property
    def sealed(self) -> bool:
        return self.sealed_at is not None


async def get_run_output(session: AsyncSession, workspace_id: str, run_id: str) -> RunOutput:
    """Read a Run's status, output, failure and published assets within an App-authorized workspace.

    Non-sealed Runs return their current status with `sealed_at=None` and empty output/assets.
    """
    row = (
        await session.execute(
            select(
                RunRow.id,
                RunRow.session_id,
                RunRow.thread_id,
                RunRow.status,
                RunRow.sealed_at,
                RunRow.output,
                RunRow.failure,
                RunRow.wait_reason,
            ).where(RunRow.workspace_id == workspace_id, RunRow.id == run_id)
        )
    ).one_or_none()
    if row is None:
        raise not_found("run", run_id)
    found_id, session_id, thread_id, status, sealed_at, output, failure_raw, wait_reason = row
    if sealed_at is None:
        return RunOutput(
            run_id=found_id,
            session_id=session_id,
            thread_id=thread_id,
            status=status,
            sealed_at=None,
            output=None,
            output_text=None,
            failure=None,
            wait_reason=None,
            assets=(),
        )
    asset_rows = (
        await session.execute(
            select(
                AssetRow.id,
                AssetRow.name,
                AssetRow.content_type,
                AssetRow.size,
                AssetRow.digest,
                AssetRow.retired_at,
                AssetRow.created_at,
            )
            .where(
                AssetRow.workspace_id == workspace_id,
                AssetRow.source.is_not(None),
                AssetRow.source["run_id"].astext == found_id,
            )
            .order_by(AssetRow.created_at, AssetRow.id)
        )
    ).all()
    return RunOutput(
        run_id=found_id,
        session_id=session_id,
        thread_id=thread_id,
        status=status,
        sealed_at=sealed_at,
        output=output,
        output_text=output if isinstance(output, str) else None,
        failure=Failure.model_validate(failure_raw) if failure_raw is not None else None,
        wait_reason=cast(WaitReason | None, wait_reason),
        assets=tuple(
            RunAsset(
                id=item.id,
                name=item.name,
                content_type=item.content_type,
                size=item.size,
                digest=item.digest,
                retired_at=item.retired_at,
                created_at=item.created_at,
            )
            for item in asset_rows
        ),
    )
