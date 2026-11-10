"""Isolated file-memory output and atomic, explicitly reviewed publication."""

import difflib
from io import StringIO
from itertools import islice

from a13n_harness.providers.memory import Origin, validate_path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import now, transaction
from a13n_service.infra.errors import conflict, not_found
from a13n_service.resources.memories import store
from a13n_service.resources.memories.files import refusals
from a13n_service.resources.memories.guidance import FileMemoryGuidance
from a13n_service.resources.memories.schemas import MemoryCreate
from a13n_service.resources.memories.service import insert_memory
from a13n_service.resources.memories.tables import MemoryFileRow, MemoryFileStoreRow, MemoryRow, MemorySpaceRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.runs.memories.organization_schemas import (
    CandidateApplication,
    CandidateApply,
    CandidateChange,
    CandidateDiff,
    CandidateFile,
    CandidateReplacement,
    OrganizationCandidate,
)
from a13n_service.runs.memories.organization_tables import OrganizationRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, Verb, WorkspaceScope


async def contents(session: AsyncSession, memory_id: str) -> dict[str, str]:
    rows = await session.execute(
        select(MemoryFileRow.path, MemoryFileRow.content).where(MemoryFileRow.memory_id == memory_id)
    )
    return {path: content for path, content in rows}


async def prepare(
    session: AsyncSession,
    runtime: Runtime,
    actor: Principal,
    scope: WorkspaceScope,
    target: MemoryRow,
) -> tuple[str, dict]:
    """Copy a consistent bounded tree; the caller owns admission and rollback."""
    source = await store.store_row(session, target.id, for_update=True)
    files = await contents(session, target.id)
    candidate = await insert_memory(
        session,
        actor,
        scope,
        MemoryCreate(
            name=f"Organization candidate: {target.name}"[:256],
            guidance=FileMemoryGuidance.model_validate(target.guidance),
            always_load=tuple(target.always_load),
        ),
        settings=runtime.settings.memory,
    )
    await store.replace_files(
        session, runtime.settings.memory, candidate.id, files, expected_seq=0, origin=Origin(principal_id=actor.id)
    )
    return candidate.id, {"seq": source.seq, "files": files}


async def _load(
    session: AsyncSession, actor: Principal, scope: WorkspaceScope, identifier: str, verb: Verb
) -> OrganizationRow:
    row = await find_row(session, actor, OrganizationRow, scope, identifier, verb, lock=True)
    if row.candidate_memory_id is None or row.baseline is None:
        raise conflict(OrganizationRow.KIND, identifier, "candidate_required")
    return row


async def _stores(session: AsyncSession, row: OrganizationRow) -> dict[str, MemoryFileStoreRow]:
    # The same ordering covers requests that organize another request's candidate memory.
    return {
        item.memory_id: item
        for item in await session.scalars(
            select(MemoryFileStoreRow)
            .where(MemoryFileStoreRow.memory_id.in_([row.memory_id, row.candidate_memory_id]))
            .order_by(MemoryFileStoreRow.memory_id)
            .with_for_update()
        )
    }


async def _view(
    session: AsyncSession, row: OrganizationRow, stores: dict[str, MemoryFileStoreRow]
) -> OrganizationCandidate:
    assert row.candidate_memory_id is not None and row.baseline is not None
    run = await session.get_one(RunRow, row.run_id)
    target, candidate = stores.get(row.memory_id), stores.get(row.candidate_memory_id)
    status = "generating" if run.sealed_at is None else "ready" if run.status == "completed" else "failed"
    if target is None or candidate is None:
        status = "unavailable"
    if row.discarded_at is not None:
        status = "discarded"
    if row.application is not None:
        status = "applied"
    files = await contents(session, row.candidate_memory_id) if candidate is not None else {}
    baseline = row.baseline["files"]
    changes = [
        CandidateChange(
            path=path, operation="create" if path not in baseline else "delete" if path not in files else "update"
        )
        for path in sorted(baseline.keys() | files.keys())
        if candidate is not None and baseline.get(path) != files.get(path)
    ]
    return OrganizationCandidate(
        organization_id=row.id,
        memory_id=row.candidate_memory_id,
        target_memory_id=row.memory_id,
        status=status,
        baseline_seq=row.baseline["seq"],
        candidate_seq=None if candidate is None else candidate.seq,
        target_seq=None if target is None else target.seq,
        changes=changes,
        files=sorted(baseline.keys() | files.keys()) if candidate is not None else [],
        application=None if row.application is None else CandidateApplication.model_validate(row.application),
    )


async def get(runtime: Runtime, actor: Principal, workspace_id: str, identifier: str) -> OrganizationCandidate:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await _load(session, actor, scope, identifier, "read")
        return await _view(session, row, await _stores(session, row))


async def diff(runtime: Runtime, actor: Principal, workspace_id: str, identifier: str, path: str) -> CandidateDiff:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await _load(session, actor, scope, identifier, "read")
        assert row.baseline is not None and row.candidate_memory_id is not None
        with refusals(row.memory_id, runtime.settings.memory):
            path = validate_path(path, store.file_format(runtime.settings.memory))
        stores = await _stores(session, row)
        if row.candidate_memory_id not in stores:
            raise conflict(OrganizationRow.KIND, row.id, "candidate_unavailable")
        files = await contents(session, row.candidate_memory_id)
        before, after = row.baseline["files"].get(path, ""), files.get(path, "")
        lines = difflib.unified_diff(
            StringIO(before).readlines(),
            StringIO(after).readlines(),
            fromfile=f"baseline/{path}",
            tofile=f"candidate/{path}",
        )
        # Separate unterminated content records and make final-newline changes visible.
        display_lines = (
            record
            for line in lines
            for record in ([line] if line.endswith("\n") else [f"{line}\n", "\\ No newline at end of file\n"])
        )
        # Bound normalized display output, including newline markers.
        preview = list(islice(display_lines, 2001))
        text = "".join(preview[:2000])
        return CandidateDiff(path=path, diff=text[:65536], truncated=len(text) > 65536 or len(preview) > 2000)


async def file(
    runtime: Runtime, actor: Principal, workspace_id: str, identifier: str, path: str, candidate_seq: int
) -> CandidateFile:
    """Read both full sides from the reviewed tree, never from the moving target."""
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await _load(session, actor, scope, identifier, "read")
        assert row.baseline is not None and row.candidate_memory_id is not None
        with refusals(row.memory_id, runtime.settings.memory):
            path = validate_path(path, store.file_format(runtime.settings.memory))
        stores = await _stores(session, row)
        current = stores.get(row.candidate_memory_id)
        if current is None:
            raise conflict(OrganizationRow.KIND, row.id, "candidate_unavailable")
        if current.seq != candidate_seq:
            raise conflict(OrganizationRow.KIND, row.id, "review_changed")
        entry = await session.scalar(
            select(MemoryFileRow).where(MemoryFileRow.memory_id == row.candidate_memory_id, MemoryFileRow.path == path)
        )
        baseline = row.baseline["files"].get(path)
        if baseline is None and entry is None:
            raise not_found("memory_file", path)
        return CandidateFile(
            path=path, candidate_seq=current.seq, baseline=baseline, candidate=None if entry is None else entry.content
        )


async def replacement(
    runtime: Runtime, actor: Principal, workspace_id: str, identifier: str, candidate_seq: int, target_seq: int
) -> CandidateReplacement:
    """Name destructive effects against exactly the pair being confirmed."""
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await _load(session, actor, scope, identifier, "read")
        assert row.baseline is not None and row.candidate_memory_id is not None
        stores = await _stores(session, row)
        target, current = stores.get(row.memory_id), stores.get(row.candidate_memory_id)
        if target is None or current is None:
            raise conflict(OrganizationRow.KIND, row.id, "candidate_unavailable")
        if current.seq != candidate_seq or target.seq != target_seq:
            raise conflict(OrganizationRow.KIND, row.id, "review_changed")
        proposed = await contents(session, row.candidate_memory_id)
        saved = await contents(session, row.memory_id)
        baseline = row.baseline["files"]
        return CandidateReplacement(
            candidate_seq=current.seq,
            target_seq=target.seq,
            deletions=sorted(saved.keys() - proposed.keys()),
            restorations=sorted((baseline.keys() & proposed.keys()) - saved.keys()),
            overwrites=sorted(
                path
                for path in saved.keys() & proposed.keys()
                if saved[path] != proposed[path] and saved[path] != baseline.get(path)
            ),
        )


async def apply(
    runtime: Runtime, actor: Principal, workspace_id: str, identifier: str, body: CandidateApply
) -> OrganizationCandidate:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await _load(session, actor, scope, identifier, "write")
        assert row.baseline is not None and row.candidate_memory_id is not None
        if row.application is not None:
            if row.application["request"] != body.model_dump(mode="json"):
                raise conflict(OrganizationRow.KIND, row.id, "already_applied")
            return await _view(session, row, await _stores(session, row))
        if row.discarded_at is not None:
            raise conflict(OrganizationRow.KIND, row.id, "candidate_discarded")
        run = await session.get_one(RunRow, row.run_id)
        if run.sealed_at is None or run.status != "completed":
            raise conflict(OrganizationRow.KIND, row.id, "candidate_not_ready")
        if row.space_id is not None:
            space = await find_row(session, actor, MemorySpaceRow, scope, row.space_id, "write", lock=True)
            if not space.enabled or space.memory_id != row.memory_id:
                raise conflict(OrganizationRow.KIND, row.id, "target_unavailable")
        for memory_id in sorted([row.memory_id, row.candidate_memory_id]):
            await find_row(session, actor, MemoryRow, scope, memory_id, "write", lock=True)
        stores = await _stores(session, row)
        target, candidate = stores[row.memory_id], stores[row.candidate_memory_id]
        if candidate.seq != body.candidate_seq or target.seq != body.target_seq:
            raise conflict(OrganizationRow.KIND, row.id, "review_changed")
        if body.mode == "checked" and target.seq != row.baseline["seq"]:
            raise conflict(OrganizationRow.KIND, row.id, "baseline_changed")
        files = await contents(session, row.candidate_memory_id)
        if row.space_id is not None and any(not path.startswith(("global/", "sessions/")) for path in files):
            raise conflict(OrganizationRow.KIND, row.id, "outside_space_roots")
        with refusals(row.memory_id, runtime.settings.memory):
            result = await store.replace_files(
                session,
                runtime.settings.memory,
                row.memory_id,
                files,
                expected_seq=body.target_seq,
                origin=Origin(principal_id=actor.id, run_id=row.run_id),
            )
        row.application = CandidateApplication(
            request=body, result_seq=result, applied_by_id=actor.id, applied_at=await now(session)
        ).model_dump(mode="json")
        audit_row(session, actor, row, "apply", details={"mode": body.mode, "result_seq": result})
        return await _view(session, row, stores)


async def discard(runtime: Runtime, actor: Principal, workspace_id: str, identifier: str) -> OrganizationCandidate:
    async with transaction(runtime.storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        row = await _load(session, actor, scope, identifier, "write")
        if row.application is not None:
            raise conflict(OrganizationRow.KIND, row.id, "already_applied")
        run = await session.get_one(RunRow, row.run_id)
        if run.sealed_at is None:
            raise conflict(OrganizationRow.KIND, row.id, "candidate_not_settled")
        if row.discarded_at is None:
            row.discarded_at = await now(session)
            audit_row(session, actor, row, "discard")
        return await _view(session, row, await _stores(session, row))
