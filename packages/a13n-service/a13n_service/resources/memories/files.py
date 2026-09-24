"""A memory's files and history through the API: people read, edit, restore and purge what agents wrote.

Edits and restores need `run`, as anyone who may run an agent can make it write through the memory tools; they
change the file `If-Match` names, under the memory's write lock, and are audited by path, never by content.
Purging a file's history destroys evidence, so it needs `write`.
"""

import difflib
from collections.abc import Iterator
from contextlib import contextmanager

from a13n_harness.providers.memory import MemoryStoreError, Origin, validate_directory, validate_path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.infra.http import require_match
from a13n_service.resources.memories import store
from a13n_service.resources.memories.schemas import (
    HistoryPurge,
    MemoryFile,
    MemoryFileCreate,
    MemoryFileEntry,
    MemoryFileMove,
    MemoryFilePage,
    MemoryFileReplace,
    MemoryFileState,
    MemoryRevision,
    MemoryRevisionDetail,
    MemoryRevisionPage,
)
from a13n_service.resources.memories.tables import MemoryFileRevisionRow, MemoryFileRow, MemoryRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.settings import MemorySettings
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, Verb

REVISION_KIND = "memory_revision"


@contextmanager
def refusals(memory_id: str, settings: MemorySettings, *, field: str = "path") -> Iterator[None]:
    """A store refusal as the API reports it; `field` names the request field a path came from."""
    try:
        yield
    except MemoryStoreError as error:
        message = str(error)
        match error.code:
            case "not_found":
                raise ServiceError("not_found", message, {"kind": MemoryFileRow.KIND}) from None
            # Under the write lock a version check fails only where a new file's path is taken.
            case "already_exists" | "version_mismatch":
                raise ServiceError("already_exists", message, {"kind": MemoryFileRow.KIND}) from None
            case "invalid_path" | "invalid_pattern":
                raise invalid(field, message) from None
            case "invalid_file":
                raise invalid("content", message) from None
            case "too_large":
                raise ServiceError("payload_too_large", message, {"limit": settings.max_file_bytes}) from None
            case "memory_full":
                raise conflict(MemoryRow.KIND, memory_id, "memory_full", limit=settings.max_total_bytes) from None
            case _:
                raise not_found(MemoryRow.KIND, memory_id) from None


async def _memory(session: AsyncSession, actor: Principal, workspace_id: str, memory_id: str, verb: Verb) -> MemoryRow:
    scope = await workspace_scope(session, actor, workspace_id, "read")
    return await find_row(session, actor, MemoryRow, scope, memory_id, verb)


def _origin(actor: Principal) -> Origin:
    return Origin(principal_id=actor.id)


async def _current(
    session: AsyncSession, settings: MemorySettings, memory: MemoryRow, path: str, if_match: str | None
) -> MemoryFileRow:
    """The file `If-Match` names, under the memory's write lock."""
    with refusals(memory.id, settings):
        await store.store_row(session, memory.id, for_update=True)
        row = await store.read_file(session, settings, memory.id, path)
    require_match(if_match, row.id, row.version)
    return row


async def list_files(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    memory_id: str,
    *,
    prefix: str,
    limit: int,
    cursor: str | None,
    settings: MemorySettings,
) -> MemoryFilePage:
    """Files in path order, those under the `prefix` directory ("" or a path ending in "/") only."""
    async with short_session(storage) as session:
        memory = await _memory(session, actor, workspace_id, memory_id, "read")
        with refusals(memory.id, settings, field="prefix"):
            directory = validate_directory(prefix, store.file_format(settings))
        rows, next_cursor = await cursors.id_page(
            session,
            select(MemoryFileRow)
            .options(defer(MemoryFileRow.content))
            .where(MemoryFileRow.memory_id == memory.id, MemoryFileRow.path.startswith(directory, autoescape=True)),
            MemoryFileRow.path,
            kind="memory_files",
            owner=cursors.query_owner(memory.id, directory),
            cursor=cursor,
            limit=limit,
            max_length=settings.path_bytes,
        )
    return MemoryFilePage(items=[MemoryFileEntry.model_validate(row) for row in rows], next_cursor=next_cursor)


async def read_file(
    storage: Storage, actor: Principal, workspace_id: str, memory_id: str, path: str, *, settings: MemorySettings
) -> MemoryFile:
    async with short_session(storage) as session:
        memory = await _memory(session, actor, workspace_id, memory_id, "read")
        with refusals(memory.id, settings):
            return MemoryFile.model_validate(await store.read_file(session, settings, memory.id, path))


async def create_file(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    memory_id: str,
    body: MemoryFileCreate,
    *,
    settings: MemorySettings,
) -> MemoryFile:
    async with transaction(storage) as session:
        memory = await _memory(session, actor, workspace_id, memory_id, "run")
        with refusals(memory.id, settings):
            row = await store.write_file(
                session, settings, memory.id, body.path, body.content, expected=None, origin=_origin(actor)
            )
        audit_row(session, actor, memory, "file.create", {"path": row.path})
        return MemoryFile.model_validate(row)


async def replace_file(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    memory_id: str,
    path: str,
    body: MemoryFileReplace,
    *,
    if_match: str | None,
    settings: MemorySettings,
) -> MemoryFile:
    async with transaction(storage) as session:
        memory = await _memory(session, actor, workspace_id, memory_id, "run")
        current = await _current(session, settings, memory, path, if_match)
        version = current.version
        with refusals(memory.id, settings):
            row = await store.write_file(
                session, settings, memory.id, current.path, body.content, expected=str(version), origin=_origin(actor)
            )
        # The same content changes nothing, as with any update.
        if row.version != version:
            audit_row(session, actor, memory, "file.update", {"path": row.path})
        return MemoryFile.model_validate(row)


async def move_file(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    memory_id: str,
    body: MemoryFileMove,
    *,
    if_match: str | None,
    settings: MemorySettings,
) -> MemoryFile:
    async with transaction(storage) as session:
        memory = await _memory(session, actor, workspace_id, memory_id, "run")
        current = await _current(session, settings, memory, body.source, if_match)
        with refusals(memory.id, settings, field="destination"):
            row = await store.move_file(
                session,
                settings,
                memory.id,
                current.path,
                body.destination,
                expected=str(current.version),
                origin=_origin(actor),
            )
        audit_row(session, actor, memory, "file.move", {"path": current.path, "destination": row.path})
        return MemoryFile.model_validate(row)


async def delete_file(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    memory_id: str,
    path: str,
    *,
    if_match: str | None,
    settings: MemorySettings,
) -> None:
    async with transaction(storage) as session:
        memory = await _memory(session, actor, workspace_id, memory_id, "run")
        current = await _current(session, settings, memory, path, if_match)
        with refusals(memory.id, settings):
            await store.delete_file(
                session, settings, memory.id, current.path, expected=str(current.version), origin=_origin(actor)
            )
        audit_row(session, actor, memory, "file.delete", {"path": current.path})


def _revision_position(cursor: str | None, owner: str) -> int | None:
    if cursor is None:
        return None
    position = cursors.decode(cursor, "memory_revisions", owner)
    if len(position) != 1 or not isinstance(position[0], int):
        raise ServiceError("invalid_cursor", "Invalid collection cursor")
    return position[0]


async def list_revisions(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    memory_id: str,
    *,
    path: str | None,
    run_id: str | None,
    limit: int,
    cursor: str | None,
) -> MemoryRevisionPage:
    """Retained revisions, newest first, of one path or one run's changes when asked."""
    async with short_session(storage) as session:
        memory = await _memory(session, actor, workspace_id, memory_id, "read")
        owner = cursors.query_owner(memory.id, path, run_id)
        revisions = MemoryFileRevisionRow
        query = select(revisions).options(defer(revisions.previous_content)).where(revisions.memory_id == memory.id)
        if path is not None:
            query = query.where(revisions.path == path)
        if run_id is not None:
            query = query.where(revisions.run_id == run_id)
        before = _revision_position(cursor, owner)
        if before is not None:
            query = query.where(revisions.seq < before)
        rows = (await session.scalars(query.order_by(revisions.seq.desc()).limit(limit + 1))).all()
    page = [MemoryRevision.model_validate(row) for row in rows[:limit]]
    next_cursor = cursors.encode("memory_revisions", owner, page[-1].seq) if len(rows) > limit else None
    return MemoryRevisionPage(items=page, next_cursor=next_cursor)


async def _revision(session: AsyncSession, memory_id: str, seq: int) -> MemoryFileRevisionRow:
    row = await session.get(MemoryFileRevisionRow, (memory_id, seq))
    if row is None:
        raise not_found(REVISION_KIND, str(seq))
    return row


async def _content_after(session: AsyncSession, revision: MemoryFileRevisionRow) -> str | None:
    """The path's content right after the change: what the path's next change replaced, else what is there now.

    Pruning removes a path's revisions oldest first, so the next one is retained whenever this one is.
    """
    revisions = MemoryFileRevisionRow
    later = await session.execute(
        select(revisions.previous_content)
        .where(revisions.memory_id == revision.memory_id, revisions.path == revision.path, revisions.seq > revision.seq)
        .order_by(revisions.seq)
        .limit(1)
    )
    if (row := later.first()) is not None:
        return row[0]
    current = await store.find_file(session, revision.memory_id, revision.path)
    return None if current is None else current.content


def diff_hunks(before: str | None, after: str | None) -> list[str]:
    """Unified diff hunks with three lines of context, without the file header."""
    hunks: list[list[str]] = []
    for line in difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(), lineterm=""):
        if line.startswith("@@"):
            hunks.append([line])
        elif hunks:
            hunks[-1].append(line)
    return ["\n".join(hunk) for hunk in hunks]


async def get_revision(
    storage: Storage, actor: Principal, workspace_id: str, memory_id: str, seq: int
) -> MemoryRevisionDetail:
    async with short_session(storage) as session:
        memory = await _memory(session, actor, workspace_id, memory_id, "read")
        revision = await _revision(session, memory.id, seq)
        after = await _content_after(session, revision)
    return MemoryRevisionDetail(
        **MemoryRevision.model_validate(revision).model_dump(),
        previous_content=revision.previous_content,
        content=after,
        hunks=diff_hunks(revision.previous_content, after),
    )


async def restore_revision(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    memory_id: str,
    seq: int,
    *,
    if_match: str | None,
    settings: MemorySettings,
) -> MemoryFileState:
    """Set the revision's path back to the content that change replaced, as a new change; restoring a creation
    deletes the file. `If-Match` names the file at the path, and is left out when there is none."""
    async with transaction(storage) as session:
        memory = await _memory(session, actor, workspace_id, memory_id, "run")
        with refusals(memory.id, settings):
            await store.store_row(session, memory.id, for_update=True)
        revision = await _revision(session, memory.id, seq)
        current = await store.find_file(session, memory.id, revision.path)
        if current is not None:
            require_match(if_match, current.id, current.version)
        elif if_match is not None:
            raise ServiceError("precondition_failed", "The file no longer exists", {"current_etag": None})
        expected = None if current is None else str(current.version)
        restored: MemoryFileRow | None = None
        with refusals(memory.id, settings):
            if revision.previous_content is not None:
                restored = await store.write_file(
                    session,
                    settings,
                    memory.id,
                    revision.path,
                    revision.previous_content,
                    expected=expected,
                    origin=_origin(actor),
                )
            elif expected is not None:
                await store.delete_file(
                    session, settings, memory.id, revision.path, expected=expected, origin=_origin(actor)
                )
        audit_row(session, actor, memory, "file.restore", {"path": revision.path, "seq": seq})
        return MemoryFileState(
            path=revision.path, file=None if restored is None else MemoryFile.model_validate(restored)
        )


async def purge_history(
    storage: Storage, actor: Principal, workspace_id: str, memory_id: str, path: str, *, settings: MemorySettings
) -> HistoryPurge:
    """Delete every retained revision of one path; its current file stays. Threads that saw the memory before
    get its full context next time."""
    async with transaction(storage) as session:
        memory = await _memory(session, actor, workspace_id, memory_id, "write")
        with refusals(memory.id, settings):
            path = validate_path(path, store.file_format(settings))
            locked = await store.store_row(session, memory.id, for_update=True)
        purged = await store.prune(session, locked, MemoryFileRevisionRow.path == path)
        audit_row(session, actor, memory, "history.purge", {"path": path, "purged": purged})
        return HistoryPurge(purged=purged)
