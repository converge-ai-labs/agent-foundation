"""Finding submission and review share the same workspace authority for HTTP and Agent tools."""

from collections.abc import Sequence

from a13n_harness.observation import redact_json
from pydantic import JsonValue
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, advisory_lock, assign, short_session, transaction
from a13n_service.infra.errors import conflict, invalid
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.rows import audit_row, find_row, record_update
from a13n_service.runs.findings.schemas import Assessment, Finding, FindingCreate, FindingPage, FindingUpdate, Severity
from a13n_service.runs.findings.tables import AnalysisRow, FindingRow
from a13n_service.runs.schemas import canonical_json
from a13n_service.runs.threads import get_run
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, authorize


async def create_finding(
    storage: Storage, actor: Principal, workspace_id: str, body: FindingCreate, *, source_run_id: str | None = None
) -> Finding:
    # Content has the same credential masking as traces, also for external producers.
    body = FindingCreate.model_validate(redact_json(body.model_dump(mode="json")))
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        authorize(actor, scope, "read")
        await advisory_lock(session, "finding_submission", scope.workspace_id, actor.id, body.source_key)
        existing = await session.scalar(
            select(FindingRow).where(
                FindingRow.workspace_id == scope.workspace_id,
                FindingRow.created_by_id == actor.id,
                FindingRow.source_key == body.source_key,
            )
        )
        if existing is not None:
            previous = FindingCreate.model_validate(existing, from_attributes=True)
            if previous != body or existing.source_run_id != source_run_id:
                raise conflict("finding", existing.id, "idempotency_key_reused")
            return (await _views(session, [existing]))[0]
        analysis = None
        if source_run_id is not None:
            source = await get_run(session, scope.workspace_id, source_run_id)
            analysis = await session.scalar(
                select(AnalysisRow).where(
                    AnalysisRow.workspace_id == scope.workspace_id, AnalysisRow.run_id == source.id
                )
            )
        for evidence in body.evidence:
            run = await get_run(session, scope.workspace_id, evidence.run_id)
            if (run.agent_id, run.agent_revision_id) != (body.agent_id, body.agent_revision_id):
                raise invalid("evidence", "each cited run must use the named Agent revision")
            if analysis is not None and (
                (analysis.agent_id is not None and analysis.agent_id != body.agent_id)
                or evidence.trace_id not in analysis.read_trace_ids
                or not any(
                    item["trace_id"] == evidence.trace_id and item["run_id"] == evidence.run_id
                    for item in analysis.selected_traces
                )
            ):
                raise invalid("evidence", "the analysis must read a selected trace before citing it")
        row = FindingRow(
            id=new_object_id("fnd"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            **body.model_dump(mode="json"),
            source_run_id=source_run_id,
            created_by_id=actor.id,
            updated_by_id=actor.id,
            assessment="unreviewed",
            assessment_note="",
            closed=False,
        )
        session.add(row)
        audit_row(session, actor, row, "create")
        await session.flush()
        return (await _views(session, [row]))[0]


async def list_findings(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    *,
    agent_id: str | None = None,
    severity: Severity | None = None,
    assessment: Assessment | None = None,
    closed: bool | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> FindingPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = select(FindingRow).where(FindingRow.workspace_id == scope.workspace_id)
        for name, value in (
            ("agent_id", agent_id),
            ("severity", severity),
            ("assessment", assessment),
            ("closed", closed),
        ):
            if value is not None:
                query = query.where(getattr(FindingRow, name) == value)
        rows, next_cursor = await cursors.keyset_page(
            session,
            query,
            (FindingRow.created_at, FindingRow.id),
            kind="findings",
            owner=cursors.query_owner(scope.workspace_id, agent_id, severity, assessment, closed),
            cursor=cursor,
            limit=limit,
            newest_first=True,
        )
        return FindingPage(items=await _views(session, rows), next_cursor=next_cursor)


async def get_finding(storage: Storage, actor: Principal, workspace_id: str, finding_id: str) -> Finding:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return (await _views(session, [await find_row(session, actor, FindingRow, scope, finding_id, "read")]))[0]


async def update_finding(
    storage: Storage, actor: Principal, workspace_id: str, finding_id: str, body: FindingUpdate, *, if_match: str | None
) -> Finding:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await find_row(session, actor, FindingRow, scope, finding_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        changes = body.model_dump(exclude_unset=True)
        if "assessment_note" in changes:
            note = str(redact_json(body.assessment_note or ""))
            if len(note) > 2048:
                raise invalid("assessment_note", "redacted note exceeds 2048 characters")
            changes["assessment_note"] = note
        elif body.assessment is not None and body.assessment != row.assessment:
            changes["assessment_note"] = ""
        changed = assign(row, changes)
        if record_update(session, actor, row, changed):
            await session.flush()
        return (await _views(session, [row]))[0]


async def _views(session: AsyncSession, rows: Sequence[FindingRow]) -> list[Finding]:
    source_ids = {row.source_run_id for row in rows if row.source_run_id is not None}
    analyses = (
        {
            run_id: analysis_id
            for run_id, analysis_id in await session.execute(
                select(AnalysisRow.run_id, AnalysisRow.id).where(
                    AnalysisRow.workspace_id == rows[0].workspace_id, AnalysisRow.run_id.in_(source_ids)
                )
            )
        }
        if source_ids
        else {}
    )
    return [
        Finding.model_validate(
            {
                **{name: getattr(row, name) for name in Finding.model_fields if name != "analysis_id"},
                "analysis_id": analyses.get(row.source_run_id),
            }
        )
        for row in rows
    ]


CONTEXT_ITEMS = 20
CONTEXT_BYTES = 12000


async def analysis_context(
    storage: Storage, actor: Principal, workspace_id: str, revisions: set[tuple[str, str]]
) -> dict[str, JsonValue]:
    """Capture bounded exact-revision diagnoses and review values for the ordinary analysis input."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows = (
            await session.scalars(
                select(FindingRow)
                .where(
                    FindingRow.workspace_id == scope.workspace_id,
                    tuple_(FindingRow.agent_id, FindingRow.agent_revision_id).in_(sorted(revisions)),
                )
                .order_by(FindingRow.updated_at.desc(), FindingRow.id.desc())
                .limit(CONTEXT_ITEMS + 1)
            )
        ).all()
        items: list[JsonValue] = []
        truncated = len(rows) > CONTEXT_ITEMS
        for row in rows[:CONTEXT_ITEMS]:
            limits = {"title": 256, "explanation": 768, "suggestion": 512, "limitations": 256, "assessment_note": 1024}
            trimmed: list[JsonValue] = [name for name, limit in limits.items() if len(getattr(row, name)) > limit]
            evidence: list[JsonValue] = [
                {**entry, "span_ids": entry.get("span_ids", [])[:3]} for entry in row.evidence[:3]
            ]
            if len(row.evidence) > 3 or any(len(entry.get("span_ids", [])) > 3 for entry in row.evidence[:3]):
                trimmed.append("evidence")
            diagnosis: dict[str, JsonValue] = {
                "category": row.category,
                "severity": row.severity,
                **{name: getattr(row, name)[:limit] for name, limit in limits.items() if name != "assessment_note"},
            }
            item = redact_json(
                {
                    "id": row.id,
                    "version": row.version,
                    "agent_id": row.agent_id,
                    "agent_revision_id": row.agent_revision_id,
                    "diagnosis": diagnosis,
                    "evidence": evidence,
                    "closed": row.closed,
                    "assessment": row.assessment,
                    "assessment_note": row.assessment_note[: limits["assessment_note"]],
                    "truncated_fields": trimmed,
                }
            )
            if len(canonical_json({"items": [*items, item], "truncated": True})) > CONTEXT_BYTES:
                truncated = True
                break
            items.append(item)
            truncated = truncated or bool(trimmed)
        return {"items": items, "truncated": truncated}
