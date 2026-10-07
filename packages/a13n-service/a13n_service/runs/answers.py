"""Durable single-answer collection. Only the complete batch can start a successor."""

import hashlib
from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import transaction, violated_constraint
from a13n_service.infra.errors import IDEMPOTENCY_KEY_REUSED, ServiceError, conflict
from a13n_service.runs import resume
from a13n_service.runs.runs import run_view
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Pending, PendingAnswer, PendingAnswers, Resume, SavedAnswer, canonical_json
from a13n_service.runs.tables import PendingAnswerRow, RunRow, ThreadRow
from a13n_service.runs.threads import get_run, get_thread, require_open
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


async def _rows(session: AsyncSession, run_id: str) -> Sequence[PendingAnswerRow]:
    return (
        await session.scalars(
            select(PendingAnswerRow)
            .where(PendingAnswerRow.run_id == run_id)
            .order_by(PendingAnswerRow.created_at, PendingAnswerRow.tool_call_id)
        )
    ).all()


async def _view(session: AsyncSession, waiting: RunRow, thread: ThreadRow) -> PendingAnswers:
    successor = await session.scalar(
        select(RunRow).where(
            RunRow.parent_run_id == waiting.id, RunRow.thread_id == thread.id, RunRow.trigger == "resume"
        )
    )
    status = (
        "resumed"
        if successor
        else "waiting"
        if (
            waiting.status == "waiting"
            and thread.archived_at is None
            and thread.last_run_id == waiting.id
            and thread.current_run_id is None
        )
        else "closed"
    )
    return PendingAnswers(
        run_id=waiting.id,
        status=status,
        answers=tuple(
            SavedAnswer.model_validate(row, from_attributes=True) for row in await _rows(session, waiting.id)
        ),
        successor=await run_view(session, successor) if successor else None,
    )


async def get(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str) -> PendingAnswers:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        waiting = await get_run(session, scope.workspace_id, run_id)
        thread = await get_thread(session, scope.workspace_id, waiting.thread_id, lock=True)
        return await _view(session, waiting, thread)


def _normalize(waiting: RunRow, request: PendingAnswer) -> PendingAnswer:
    pending = Pending.model_validate(waiting.pending)
    selected = Pending.model_construct(
        approvals=tuple(item for item in pending.approvals if item.tool_call_id == request.tool_call_id),
        calls=tuple(item for item in pending.calls if item.tool_call_id == request.tool_call_id),
    )
    normalized = resume.normalize(selected, Resume(approvals=request.approvals, calls=request.calls))
    return PendingAnswer(approvals=normalized.approvals, calls=normalized.calls)


async def _replay(
    session: AsyncSession, workspace_id: str, actor: Principal, key: str, digest: str
) -> PendingAnswerRow | None:
    found = await session.scalar(
        select(PendingAnswerRow).where(
            PendingAnswerRow.workspace_id == workspace_id,
            PendingAnswerRow.answered_by_id == actor.id,
            PendingAnswerRow.request_key == key,
        )
    )
    if found is not None and found.request_digest != digest:
        raise conflict("request", key, IDEMPOTENCY_KEY_REUSED)
    return found


async def submit(
    runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, request: PendingAnswer, *, request_key: str
) -> tuple[PendingAnswers, bool]:
    digest = hashlib.sha256(canonical_json([run_id, request.model_dump(mode="json")])).hexdigest()
    try:
        async with transaction(runtime.storage) as session:
            scope = await workspace_scope(session, actor, workspace_id, "run")
            waiting = await get_run(session, scope.workspace_id, run_id)
            thread = await get_thread(session, scope.workspace_id, waiting.thread_id, lock=True)
            if await _replay(session, scope.workspace_id, actor, request_key, digest):
                return await _view(session, waiting, thread), False
            # Previously saved answers remain readable and replayable after closure, but never execute again.
            if waiting.pending is not None:
                answer = _normalize(waiting, request)
                existing = await session.get(PendingAnswerRow, (waiting.id, request.tool_call_id))
                if existing:
                    if canonical_json(existing.answer) != canonical_json(answer.model_dump(mode="json")):
                        raise conflict("run", waiting.id, "answer_already_saved")
                    return await _view(session, waiting, thread), False
            require_open(thread)
            if not (waiting.status == "waiting" and thread.last_run_id == waiting.id and thread.current_run_id is None):
                raise conflict("run", waiting.id, "not_idle_waiting_head")
            answer = _normalize(waiting, request)
            collected = [PendingAnswer.model_validate(row.answer) for row in await _rows(session, waiting.id)] + [
                answer
            ]
            try:
                batch = Resume(
                    approvals={key: value for item in collected for key, value in item.approvals.items()},
                    calls={key: value for item in collected for key, value in item.calls.items()},
                )
            except ValidationError as error:
                raise ServiceError(
                    "invalid_argument", "Collected answers exceed the resume byte limit", {"reason": "resume_too_large"}
                ) from error
            session.add(
                PendingAnswerRow(
                    run_id=waiting.id,
                    workspace_id=scope.workspace_id,
                    tool_call_id=answer.tool_call_id,
                    answer=answer.model_dump(mode="json"),
                    answered_by_id=actor.id,
                    request_key=request_key,
                    request_digest=digest,
                )
            )
            await session.flush()
            pending = Pending.model_validate(waiting.pending)
            if len(collected) == len(pending.approvals) + len(pending.calls):
                # The last answer and the successor commit together under the same thread lock.
                await resume.complete(session, runtime, actor, waiting, thread, resume.normalize(pending, batch))
            return await _view(session, waiting, thread), True
    except IntegrityError as error:
        if violated_constraint(error) != "uq_pending_answers_request":
            raise
    # A same-key request on another thread can win while this transaction waits; never retain tentative writes.
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        found = await _replay(session, scope.workspace_id, actor, request_key, digest)
        assert found is not None
        waiting = await get_run(session, scope.workspace_id, run_id)
        thread = await get_thread(session, scope.workspace_id, waiting.thread_id, lock=True)
        return await _view(session, waiting, thread), False
