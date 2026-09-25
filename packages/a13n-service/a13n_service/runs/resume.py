"""Resume: answering the exact wait of a thread's idle waiting head with a successor run.

The waiting run, not a tool call ID, identifies the suspension: an answer for a superseded wait conflicts
instead of landing on a newer one. Answers are normalized against the complete sealed pending set, so the
successor always carries one decision per pending call and no partial progress is ever stored.
"""

import hashlib

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import transaction, violated_constraint
from a13n_service.infra.errors import IDEMPOTENCY_KEY_REUSED, ServiceError, conflict
from a13n_service.runs.accept import Source, start_run
from a13n_service.runs.runs import run_view
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import (
    Approve,
    Complete,
    NoResponse,
    NormalizedAnswer,
    Pending,
    PendingItem,
    Reject,
    Resume,
    ResumeRequest,
    RunView,
    canonical_json,
)
from a13n_service.runs.tables import RunRow
from a13n_service.runs.threads import get_run, get_thread, require_open
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal

_ACCEPTS: dict[str, tuple[type, ...]] = {
    "approval": (Approve, Reject),
    "client_tool": (Complete,),
    "user_input": (),
}


def _default(item: PendingItem) -> NormalizedAnswer:
    """An omitted approval is denied; an omitted client result or question gets no response."""
    if item.kind == "approval":
        return Reject(tool_call_id=item.tool_call_id, action="reject", reason="No decision was given")
    return NoResponse(tool_call_id=item.tool_call_id)


def normalize(pending: Pending, request: ResumeRequest) -> Resume:
    answers = {answer.tool_call_id: answer for answer in request.answers}
    items = {item.tool_call_id: item for item in pending.items}
    for tool_call_id, answer in answers.items():
        item = items.get(tool_call_id)
        if item is None:
            raise ServiceError(
                "invalid_argument",
                "Answer names no pending call",
                {"field": "tool_call_id", "reason": "no_pending_call", "id": tool_call_id},
            )
        if not isinstance(answer, _ACCEPTS[item.kind]):
            raise ServiceError(
                "invalid_argument",
                f"A {item.kind} call cannot be answered with {answer.action}",
                {"field": "action", "reason": "action_not_accepted", "id": tool_call_id},
            )
    return Resume(answers=tuple(answers.get(item.tool_call_id) or _default(item) for item in pending.items))


def request_digest(run_id: str, request: ResumeRequest) -> str:
    return hashlib.sha256(canonical_json([run_id, request.model_dump(mode="json")])).hexdigest()


async def _replay(session: AsyncSession, workspace_id: str, actor: Principal, key: str, digest: str) -> RunRow | None:
    found = await session.scalar(
        select(RunRow).where(
            RunRow.workspace_id == workspace_id, RunRow.resumed_by_id == actor.id, RunRow.request_key == key
        )
    )
    if found is not None and found.request_digest != digest:
        raise conflict("request", key, IDEMPOTENCY_KEY_REUSED)
    return found


async def resume(
    runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, request: ResumeRequest, *, request_key: str
) -> tuple[RunView, bool]:
    """Returns the successor and whether this call created it (201) rather than replayed it (200)."""
    digest = request_digest(run_id, request)
    try:
        async with transaction(runtime.storage) as session:
            scope = await workspace_scope(session, actor, workspace_id, "run")
            if found := await _replay(session, scope.workspace_id, actor, request_key, digest):
                return await run_view(session, found), False
            waiting = await get_run(session, scope.workspace_id, run_id)
            thread = await get_thread(session, scope.workspace_id, waiting.thread_id, lock=True)
            require_open(thread)
            if not (waiting.status == "waiting" and thread.head_run_id == waiting.id and thread.current_run_id is None):
                raise conflict("run", waiting.id, "not_idle_waiting_head")
            answers = normalize(Pending.model_validate(waiting.pending), request)
            source = await Source.inherited(
                session,
                runtime,
                waiting,
                "resume",
                resume=answers,
                resumed_by_id=actor.id,
                request_key=request_key,
                request_digest=digest,
            )
            successor = await start_run(session, runtime, thread, source)
            return await run_view(session, successor), True
    except IntegrityError as error:
        if violated_constraint(error) != "uq_runs_resume_request":
            raise
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        found = await _replay(session, scope.workspace_id, actor, request_key, digest)
        assert found is not None
        return await run_view(session, found), False
