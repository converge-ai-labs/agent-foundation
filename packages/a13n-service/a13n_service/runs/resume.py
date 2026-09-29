"""Resume: answering the exact wait of a thread's idle waiting head with a successor run.

The waiting run, not a tool call ID, identifies the suspension: an answer for a superseded wait conflicts
instead of landing on a newer one. Answers are normalized against the complete sealed pending set, so the
successor always carries one decision per pending call and no partial progress is ever stored.
"""

import hashlib

from a13n_harness.toolsets.interaction import validate_user_question_result
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import transaction, violated_constraint
from a13n_service.infra.errors import IDEMPOTENCY_KEY_REUSED, ServiceError, conflict
from a13n_service.resources.assets.service import require_usable
from a13n_service.runs.accept import Source, start_run
from a13n_service.runs.attachments import asset_fields
from a13n_service.runs.runs import run_view
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Pending, Resume, Returned, RunView, canonical_json
from a13n_service.runs.tables import RunRow
from a13n_service.runs.threads import get_run, get_thread, require_open
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


def normalize(pending: Pending, request: Resume) -> Resume:
    """Validate exact category coverage and normalize built-in question values; never infer decisions."""
    for category, items, results in (
        ("approvals", pending.approvals, request.approvals),
        ("calls", pending.calls, request.calls),
    ):
        expected: set[str] = {item.tool_call_id for item in items}
        actual: set[str] = set(results)
        if actual != expected:
            missing: list[JsonValue] = list(sorted(expected - actual))
            unexpected: list[JsonValue] = list(sorted(actual - expected))
            raise ServiceError(
                "invalid_argument",
                "Results must exactly cover the pending category",
                {
                    "field": category,
                    "reason": "pending_coverage_mismatch",
                    "missing": missing,
                    "unexpected": unexpected,
                },
            )
    calls = dict(request.calls)
    for item in pending.calls:
        result = calls[item.tool_call_id]
        if item.tool_name == "ask_user_question" and isinstance(result, Returned):
            try:
                value = validate_user_question_result(item.arguments, result.value)
            except ValueError as error:
                raise ServiceError(
                    "invalid_argument",
                    "Question response does not match the pending question",
                    {"field": "calls", "reason": "invalid_question_response", "id": item.tool_call_id},
                ) from error
            calls[item.tool_call_id] = Returned.model_validate({"status": "returned", "value": value})
    return Resume(approvals=request.approvals, calls=calls, input=request.input)


def request_digest(run_id: str, request: Resume) -> str:
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
    runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, request: Resume, *, request_key: str
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
                # A concurrent request may have committed this same intent while we waited for the thread.
                if found := await _replay(session, scope.workspace_id, actor, request_key, digest):
                    return await run_view(session, found), False
                raise conflict("run", waiting.id, "not_idle_waiting_head")
            answers = normalize(Pending.model_validate(waiting.pending), request)
            if answers.input is not None:
                await require_usable(session, scope.workspace_id, asset_fields(answers.input))
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
