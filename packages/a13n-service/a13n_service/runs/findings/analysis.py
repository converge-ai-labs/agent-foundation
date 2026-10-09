"""On-demand analysis starts an ordinary run atomically with its bounded provenance."""

import hashlib
from datetime import UTC, datetime, timedelta

from a13n_harness.observation import redact_json
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import advisory_lock, short_session, transaction, violated_constraint
from a13n_service.infra.errors import conflict, invalid, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.agents import service as agents
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.resources.rows import audit_row
from a13n_service.runs import traces
from a13n_service.runs.accept import Source, accept
from a13n_service.runs.findings.preset import RULES
from a13n_service.runs.findings.schemas import Analysis, AnalysisCreate, AnalysisPage, AnalysisReport
from a13n_service.runs.findings.tables import AnalysisRow
from a13n_service.runs.inbox import Request, append_message, find_request
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Message, MessagePayload, RunOptions, TextPart, UsageLimit, canonical_json
from a13n_service.runs.sessions import new_session
from a13n_service.runs.submit import validate_message
from a13n_service.runs.tables import RunRow
from a13n_service.runs.threads import new_thread
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, execution_authority


async def _view(session: AsyncSession, row: AnalysisRow) -> Analysis:
    run = await session.get_one(RunRow, row.run_id)
    return Analysis.model_validate(
        {
            **{name: getattr(row, name) for name in Analysis.model_fields if name != "run_status"},
            "run_status": run.status,
        }
    )


async def start(
    runtime: Runtime, actor: Principal, workspace_id: str, body: AnalysisCreate, *, request_key: str
) -> tuple[Analysis, bool]:
    digest = hashlib.sha256(canonical_json(body.model_dump(mode="json"))).hexdigest()
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "run")
        await workspace_scope(session, actor, workspace_id, "write")
        existing = await session.scalar(
            select(AnalysisRow).where(
                AnalysisRow.workspace_id == scope.workspace_id,
                AnalysisRow.created_by_id == actor.id,
                AnalysisRow.request_key == request_key,
            )
        )
        if existing is not None:
            if existing.request_digest != digest:
                raise conflict("request", request_key, "idempotency_key_reused")
            return await _view(session, existing), False
        if body.agent_id is not None:
            target = await agents.resolve_agent(session, scope.workspace_id, body.agent_id)
            if target.preset_kind == "finding":
                raise invalid("agent_id", "Finding Agent cannot analyze itself")
        finder = await session.scalar(
            select(AgentRow).where(AgentRow.workspace_id == scope.workspace_id, AgentRow.preset_kind == "finding")
        )
        if finder is None or finder.default_revision_id is None:
            raise conflict("workspace", scope.workspace_id, "finding_agent_required")
        finder_id, revision_id = finder.id, finder.default_revision_id
    before = body.started_before or datetime.now(UTC)
    after = body.started_after or before - timedelta(days=1)
    selected: list[str] = []
    trace_runs: dict[str, str] = {}
    targets: dict[str, dict[str, str]] = {}
    target_id = body.agent_id
    cursor = None
    truncated = False
    for _ in range(5):
        if body.trace_id:
            roots = [await traces.get_trace(runtime.storage, runtime.traces, actor, workspace_id, body.trace_id)]
        else:
            page = await traces.list_traces(
                runtime.storage,
                runtime.traces,
                actor,
                workspace_id,
                session_id=None,
                thread_id=None,
                run_id=None,
                attributes=(),
                started_after=after,
                started_before=before,
                limit=20,
                cursor=cursor,
            )
            roots, cursor = page.items, page.next_cursor
        run_ids = [root.attributes.get("a13n.observation.metadata.service_run_id") for root in roots]
        async with short_session(runtime.storage) as session:
            await workspace_scope(session, actor, workspace_id, "run")
            query = select(RunRow).where(
                RunRow.workspace_id == scope.workspace_id,
                RunRow.agent_id != finder_id,
                RunRow.sealed_at.is_not(None),
                RunRow.status.in_(("completed", "failed", "cancelled")),
                RunRow.id.in_([value for value in run_ids if isinstance(value, str)]),
            )
            if body.agent_id is not None:
                query = query.where(RunRow.agent_id == body.agent_id)
            allowed = {
                run.id: {"agent_id": run.agent_id, "agent_revision_id": run.agent_revision_id}
                for run in (await session.scalars(query)).all()
            }
        for root, run_id in zip(roots, run_ids, strict=True):
            if (
                isinstance(run_id, str)
                and run_id in allowed
                and root.ended_at is not None
                and root.trace_id not in selected
            ):
                selected.append(root.trace_id)
                trace_runs[root.trace_id] = run_id
                targets[root.trace_id] = allowed[run_id]
                if body.trace_id:
                    target_id = allowed[run_id]["agent_id"]
        if body.trace_id or len(selected) >= body.max_traces or cursor is None:
            break
    truncated = len(selected) > body.max_traces or cursor is not None
    selected = selected[: body.max_traces]
    if not selected:
        raise invalid(
            "trace_id" if body.trace_id else "selection",
            "no completed, queryable traces in the selected scope",
        )
    analysis_id = new_object_id("fan")
    prompt = (
        f"Analysis {analysis_id}. Selected traces: {selected}.\n"
        + "Target Agent revisions by trace: "
        + canonical_json({key: targets[key] for key in selected}).decode()
        + "\n"
        + "\n".join(f"{key}: {RULES[key]}" for key in body.presets)
        + "\nQuery and review only these traces, submit findings, then report_analysis."
    )
    message = Message(
        agent_id=finder_id,
        agent_revision_id=revision_id,
        payload=MessagePayload(content=(TextPart(type="text", text=prompt),)),
        options=RunOptions(max_usage=UsageLimit(requests=40)),
    )
    try:
        async with transaction(runtime.storage) as session:
            scope = await workspace_scope(session, actor, workspace_id, "run")
            await workspace_scope(session, actor, workspace_id, "write")
            await advisory_lock(session, "finding_analysis", scope.workspace_id, actor.id, request_key)
            existing = await session.scalar(
                select(AnalysisRow).where(
                    AnalysisRow.workspace_id == scope.workspace_id,
                    AnalysisRow.created_by_id == actor.id,
                    AnalysisRow.request_key == request_key,
                )
            )
            if existing is not None:
                if existing.request_digest != digest:
                    raise conflict("request", request_key, "idempotency_key_reused")
                return await _view(session, existing), False
            if await find_request(session, scope.workspace_id, actor.id, request_key) is not None:
                raise conflict("request", request_key, "idempotency_key_reused")
            owner = new_session(scope.organization_id, scope.workspace_id, actor.id)
            owner.labels = {"a13n.finding": analysis_id}
            session.add(owner)
            thread = new_thread(owner, mcp_headers={})
            session.add(thread)
            await session.flush()
            authority = execution_authority(actor, scope)
            revision, overrides = await validate_message(
                session, runtime, actor, scope, thread, message, authority=authority
            )
            entry = await append_message(
                session,
                thread,
                message,
                principal_id=actor.id,
                authority=authority,
                request=Request.of(request_key, "thread", scope.workspace_id, body),
                control=runtime.settings.control,
            )
            await accept(session, runtime, thread, explicit=Source.message(entry, "input", actor, revision, overrides))
            if entry.assigned_run_id is None:
                raise conflict("workspace", scope.workspace_id, "analysis_not_accepted")
            row = AnalysisRow(
                id=analysis_id,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                agent_id=target_id,
                session_id=owner.id,
                thread_id=thread.id,
                run_id=entry.assigned_run_id,
                request_key=request_key,
                request_digest=digest,
                selection=body.model_dump(mode="json"),
                trace_ids=selected,
                selection_truncated=truncated,
                trace_runs={key: trace_runs[key] for key in selected},
                read_trace_ids=[],
                reviewed_trace_ids=[],
                reported=False,
                limitations="",
                created_by_id=actor.id,
                updated_by_id=actor.id,
            )
            session.add(row)
            audit_row(session, actor, row, "create")
            await session.flush()
            return await _view(session, row), True
    except IntegrityError as error:
        if violated_constraint(error) != "uq_inbox_entries_request":
            raise
        raise conflict("request", request_key, "idempotency_key_reused") from error


async def list_analyses(
    runtime: Runtime, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> AnalysisPage:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.keyset_page(
            session,
            select(AnalysisRow).where(AnalysisRow.workspace_id == scope.workspace_id),
            (AnalysisRow.created_at, AnalysisRow.id),
            kind="finding_analyses",
            owner=scope.workspace_id,
            limit=limit,
            cursor=cursor,
            newest_first=True,
        )
        # Load statuses in one bounded query rather than a query per analysis.
        runs = {
            run.id: run.status
            for run in (await session.scalars(select(RunRow).where(RunRow.id.in_([row.run_id for row in rows])))).all()
        }
        return AnalysisPage(
            items=[
                Analysis.model_validate(
                    {
                        **{name: getattr(row, name) for name in Analysis.model_fields if name != "run_status"},
                        "run_status": runs[row.run_id],
                    }
                )
                for row in rows
            ],
            next_cursor=next_cursor,
        )


async def read_scope(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, trace_id: str) -> None:
    async with short_session(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await session.scalar(
            select(AnalysisRow).where(AnalysisRow.workspace_id == scope.workspace_id, AnalysisRow.run_id == run_id)
        )
        if row is None:
            return
        if trace_id not in row.trace_ids:
            raise invalid("trace_id", "outside the analysis selection")


async def record_read(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, trace_id: str) -> None:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await session.scalar(
            select(AnalysisRow)
            .where(AnalysisRow.workspace_id == scope.workspace_id, AnalysisRow.run_id == run_id)
            .with_for_update()
        )
        if row is not None and trace_id not in row.trace_ids:
            raise invalid("trace_id", "outside the analysis selection")
        if row is not None and trace_id not in row.read_trace_ids:
            row.read_trace_ids = [*row.read_trace_ids, trace_id]
            row.updated_by_id = actor.id


async def report(runtime: Runtime, actor: Principal, workspace_id: str, run_id: str, body: AnalysisReport) -> Analysis:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await session.scalar(
            select(AnalysisRow)
            .where(AnalysisRow.workspace_id == scope.workspace_id, AnalysisRow.run_id == run_id)
            .with_for_update()
        )
        if row is None:
            raise not_found("finding_analysis", run_id)
        if not set(body.reviewed_trace_ids) <= set(row.read_trace_ids):
            raise invalid("reviewed_trace_ids", "only selected traces read by this analysis may be reported reviewed")
        safe = AnalysisReport.model_validate(redact_json(body.model_dump(mode="json")))
        row.reviewed_trace_ids, row.limitations, row.reported = (
            list(dict.fromkeys(safe.reviewed_trace_ids)),
            safe.limitations,
            True,
        )
        row.updated_by_id = actor.id
        await session.flush()
        return await _view(session, row)
