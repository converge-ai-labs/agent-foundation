"""The PostgreSQL file store: one memory's files, a revision for each change, and the change feed they form.

Every change locks the memory's `memory_file_stores` row, takes the next change number, writes the file and one
revision holding the content it replaced, prunes history and checks the byte total, in one transaction. The lock
numbers changes without gaps, so a change cursor is a number and the feed lists exactly what changed after it.
A version is a change number as a string: a file's version is the number of its last change.
"""

from collections.abc import Callable
from datetime import datetime

from a13n_harness.providers.memory import (
    Changes,
    FileEntry,
    FileFormat,
    FileText,
    FullResync,
    GrepMatch,
    MemoryStoreError,
    Origin,
    describe,
    validate_directory,
    validate_path,
)
from sqlalchemy import ColumnElement, delete, func, select, true
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, lock, now, short_session, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.memories.tables import MemoryFileRevisionRow, MemoryFileRow, MemoryFileStoreRow
from a13n_service.settings import MemorySettings
from a13n_service.tenancy.authorize import Verb

# PostgreSQL error classes a search pattern can cause: an invalid regular expression, and a match that ran past
# the statement timeout.
_PATTERN_ERRORS = {"2201B": "The pattern is not a valid regular expression.", "57014": "The search took too long."}

# Matching lines, and whether more matched than were returned.
type Matches = tuple[list[GrepMatch], bool]


def file_format(settings: MemorySettings) -> FileFormat:
    return FileFormat(
        max_file_bytes=settings.max_file_bytes,
        description_chars=settings.description_chars,
        frontmatter_bytes=settings.frontmatter_bytes,
        path_bytes=settings.path_bytes,
    )


def memory_deleted() -> MemoryStoreError:
    return MemoryStoreError("memory_deleted", "The memory was deleted.")


def file_text(row: MemoryFileRow) -> FileText:
    return FileText(path=row.path, text=row.content, version=str(row.version))


async def store_row(session: AsyncSession, memory_id: str, *, for_update: bool = False) -> MemoryFileStoreRow:
    """The memory's store row, locked for a change when `for_update`; gone with its memory."""
    if for_update:
        store = await lock(session, MemoryFileStoreRow, memory_id)
    else:
        store = await session.get(MemoryFileStoreRow, memory_id, populate_existing=True)
    if store is None:
        raise memory_deleted()
    return store


async def find_file(session: AsyncSession, memory_id: str, path: str) -> MemoryFileRow | None:
    return await session.scalar(
        select(MemoryFileRow)
        .where(MemoryFileRow.memory_id == memory_id, MemoryFileRow.path == path)
        .execution_options(populate_existing=True)
    )


async def read_file(session: AsyncSession, settings: MemorySettings, memory_id: str, path: str) -> MemoryFileRow:
    path = validate_path(path, file_format(settings))
    await store_row(session, memory_id)
    row = await find_file(session, memory_id, path)
    if row is None:
        raise MemoryStoreError("not_found", f"No file at {path}.")
    return row


def _check(row: MemoryFileRow | None, expected: str | None) -> None:
    """The compare-and-swap: `expected` is the file's current version, or None for a path that must be free."""
    if (None if row is None else str(row.version)) != expected:
        current = None if row is None else file_text(row)
        raise MemoryStoreError("version_mismatch", "The file changed since that version.", current=current)


class _Change:
    """One locked change: it numbers revisions, keeps the counters and settles history before commit."""

    def __init__(self, session: AsyncSession, store: MemoryFileStoreRow, origin: Origin, at: datetime):
        self.session, self.store, self.origin, self.at = session, store, origin, at
        self.paths: list[str] = []

    def revise(self, path: str, op: str, previous: str | None, moved_path: str | None = None) -> int:
        store = self.store
        store.seq += 1
        store.history_bytes += _size(previous)
        self.session.add(
            MemoryFileRevisionRow(
                memory_id=store.memory_id,
                seq=store.seq,
                path=path,
                op=op,
                moved_path=moved_path,
                previous_content=previous,
                run_id=self.origin.run_id,
                tool_call_id=self.origin.tool_call_id,
                principal_id=self.origin.principal_id,
                created_at=self.at,
            )
        )
        self.paths.append(path)
        return store.seq

    def stamp(self, row: MemoryFileRow, version: int) -> None:
        row.version = version
        row.updated_at = self.at
        row.updated_by_run_id = self.origin.run_id
        row.updated_by_principal_id = self.origin.principal_id

    async def settle(self, settings: MemorySettings) -> None:
        """Prune each changed path to its revision count, then the oldest history until the memory fits its
        byte total; only current content over the total refuses the change."""
        store = self.store
        if store.content_bytes > settings.max_total_bytes:
            raise MemoryStoreError(
                "memory_full", f"The memory's files exceed its {settings.max_total_bytes}-byte total."
            )
        await self.session.flush()
        revisions = MemoryFileRevisionRow
        for path in dict.fromkeys(self.paths):
            kept = (
                select(revisions.seq)
                .where(revisions.memory_id == store.memory_id, revisions.path == path)
                .order_by(revisions.seq.desc())
                .offset(settings.revisions_per_file)
                .limit(1)
                .scalar_subquery()
            )
            await prune(self.session, store, (revisions.path == path) & (revisions.seq <= kept))
        excess = store.content_bytes + store.history_bytes - settings.max_total_bytes
        if excess > 0:
            freed = func.sum(_previous_size()).over(order_by=revisions.seq)
            running = select(revisions.seq, freed.label("freed")).where(revisions.memory_id == store.memory_id)
            ordered = running.subquery()
            cutoff = select(func.min(ordered.c.seq)).where(ordered.c.freed >= excess).scalar_subquery()
            await prune(self.session, store, revisions.seq <= cutoff)


def _size(text: str | None) -> int:
    return 0 if text is None else len(text.encode())


def _previous_size() -> ColumnElement[int]:
    return func.coalesce(func.octet_length(MemoryFileRevisionRow.previous_content), 0)


async def prune(session: AsyncSession, store: MemoryFileStoreRow, condition: ColumnElement[bool]) -> int:
    """Delete the locked memory's revisions that match `condition` and account for them; how many went."""
    revisions = MemoryFileRevisionRow
    pruned = (
        await session.execute(
            delete(revisions)
            .where(revisions.memory_id == store.memory_id, condition)
            .returning(revisions.seq, _previous_size())
            .execution_options(synchronize_session=False)
        )
    ).all()
    if pruned:
        store.history_bytes -= sum(size for _, size in pruned)
        store.pruned_through_seq = max(store.pruned_through_seq, *(seq for seq, _ in pruned))
    return len(pruned)


async def _change(session: AsyncSession, memory_id: str, origin: Origin) -> _Change:
    store = await store_row(session, memory_id, for_update=True)
    return _Change(session, store, origin, await now(session))


async def write_file(
    session: AsyncSession,
    settings: MemorySettings,
    memory_id: str,
    path: str,
    text: str,
    *,
    expected: str | None,
    origin: Origin,
) -> MemoryFileRow:
    """Create the file (`expected` None) or replace the version `expected` names; the same text changes nothing."""
    fmt = file_format(settings)
    path = validate_path(path, fmt)
    description = describe(text, fmt)
    change = await _change(session, memory_id, origin)
    row = await find_file(session, memory_id, path)
    _check(row, expected)
    if row is not None and row.content == text:
        return row
    store, size = change.store, _size(text)
    if row is None:
        version = change.revise(path, "create", None)
        row = MemoryFileRow(id=new_object_id("mfile"), memory_id=memory_id, path=path, created_at=change.at)
        session.add(row)
        store.file_count += 1
    else:
        version = change.revise(path, "update", row.content)
        store.content_bytes -= row.size
    store.content_bytes += size
    row.content, row.size, row.description = text, size, description
    change.stamp(row, version)
    await change.settle(settings)
    return row


async def delete_file(
    session: AsyncSession, settings: MemorySettings, memory_id: str, path: str, *, expected: str, origin: Origin
) -> None:
    path = validate_path(path, file_format(settings))
    change = await _change(session, memory_id, origin)
    row = await find_file(session, memory_id, path)
    _check(row, expected)
    assert row is not None
    change.revise(path, "delete", row.content)
    change.store.content_bytes -= row.size
    change.store.file_count -= 1
    await session.delete(row)
    await change.settle(settings)


async def move_file(
    session: AsyncSession,
    settings: MemorySettings,
    memory_id: str,
    source: str,
    destination: str,
    *,
    expected: str,
    origin: Origin,
) -> MemoryFileRow:
    """Move the version `expected` names to a free path in one change; the file keeps its ID."""
    fmt = file_format(settings)
    source, destination = validate_path(source, fmt), validate_path(destination, fmt)
    change = await _change(session, memory_id, origin)
    row = await find_file(session, memory_id, source)
    _check(row, expected)
    assert row is not None
    if await find_file(session, memory_id, destination) is not None:
        raise MemoryStoreError("already_exists", f"A file exists at {destination}.")
    change.revise(source, "move_out", row.content, destination)
    version = change.revise(destination, "move_in", None, source)
    row.path = destination
    change.stamp(row, version)
    await change.settle(settings)
    return row


async def list_entries(session: AsyncSession, memory_id: str) -> list[FileEntry]:
    await store_row(session, memory_id)
    rows = await session.execute(
        select(MemoryFileRow.path, MemoryFileRow.version, MemoryFileRow.size, MemoryFileRow.description)
        .where(MemoryFileRow.memory_id == memory_id)
        .order_by(MemoryFileRow.path)
    )
    return [FileEntry(path, str(version), size, description) for path, version, size, description in rows]


async def changes_since(session: AsyncSession, memory_id: str, since: str | None) -> Changes | FullResync:
    """The paths changed after `since`, as of the store's last change; a full resync when history no longer
    covers `since`."""
    store = await store_row(session, memory_id)
    head = str(store.seq)
    position = int(since) if since is not None and since.isdecimal() else None
    if position is None or position < store.pruned_through_seq or position > store.seq:
        return FullResync(cursor=head)
    paths = await session.scalars(
        select(MemoryFileRevisionRow.path)
        .where(MemoryFileRevisionRow.memory_id == memory_id, MemoryFileRevisionRow.seq > position)
        .distinct()
        .order_by(MemoryFileRevisionRow.path)
    )
    return Changes(cursor=head, paths=tuple(paths))


async def search_lines(
    session: AsyncSession,
    settings: MemorySettings,
    memory_id: str,
    pattern: str,
    *,
    regex: bool,
    case_sensitive: bool,
    path: str,
    limit: int,
) -> Matches:
    """Matching lines under `path` in path and line order, and whether more matched; regular expressions use
    PostgreSQL's syntax."""
    directory = validate_directory(path, file_format(settings))
    await store_row(session, memory_id)
    lines = (
        func.unnest(func.string_to_array(MemoryFileRow.content, "\n"))
        .table_valued("line", with_ordinality="number")
        .render_derived()
        .lateral("lines")
    )
    line, number = lines.c.line, lines.c.number
    if regex:
        matched = line.regexp_match(pattern, flags=None if case_sensitive else "i")
    elif case_sensitive:
        matched = func.strpos(line, pattern) > 0
    else:
        matched = func.strpos(func.lower(line), func.lower(pattern)) > 0
    query = (
        select(MemoryFileRow.path, number, line)
        .select_from(MemoryFileRow)
        .join(lines, true())
        .where(MemoryFileRow.memory_id == memory_id, MemoryFileRow.path.startswith(directory, autoescape=True))
        .where(matched)
        .order_by(MemoryFileRow.path, number)
        .limit(limit + 1)
    )
    try:
        rows = (await session.execute(query)).all()
    except DBAPIError as error:
        message = _PATTERN_ERRORS.get(getattr(error.orig, "sqlstate", None) or "")
        if message is None:
            raise
        raise MemoryStoreError("invalid_pattern", message) from None
    return [GrepMatch(path=path, line=line, text=text) for path, line, text in rows[:limit]], len(rows) > limit


async def purge_files(session: AsyncSession, memory_id: str) -> None:
    """Remove every file and revision; every change cursor then resyncs."""
    store = await store_row(session, memory_id, for_update=True)
    await session.execute(delete(MemoryFileRow).where(MemoryFileRow.memory_id == memory_id))
    await session.execute(delete(MemoryFileRevisionRow).where(MemoryFileRevisionRow.memory_id == memory_id))
    store.pruned_through_seq = store.seq
    store.content_bytes = store.history_bytes = store.file_count = 0


def _open(verb: Verb) -> None:
    """No gate: the caller authorized the operation already."""


class PostgresFileStore:
    """One memory's files as the Harness file memory reads and changes them; each call is one short transaction.

    `gate` checks each call first: `read` for reads, `run` for changes, `write` to purge. It raises
    `MemoryStoreError` to refuse.
    """

    def __init__(
        self,
        storage: Storage,
        memory_id: str,
        settings: MemorySettings,
        *,
        gate: Callable[[Verb], None] = _open,
    ):
        self.storage, self.memory_id, self.settings, self.gate = storage, memory_id, settings, gate

    async def list(self) -> list[FileEntry]:
        self.gate("read")
        async with short_session(self.storage) as session:
            return await list_entries(session, self.memory_id)

    async def read(self, path: str) -> FileText:
        self.gate("read")
        async with short_session(self.storage) as session:
            return file_text(await read_file(session, self.settings, self.memory_id, path))

    async def write(self, path: str, text: str, *, expected: str | None, origin: Origin) -> str:
        self.gate("run")
        async with transaction(self.storage) as session:
            row = await write_file(session, self.settings, self.memory_id, path, text, expected=expected, origin=origin)
            return str(row.version)

    async def move(self, source: str, destination: str, *, expected: str, origin: Origin) -> str:
        self.gate("run")
        async with transaction(self.storage) as session:
            row = await move_file(
                session, self.settings, self.memory_id, source, destination, expected=expected, origin=origin
            )
            return str(row.version)

    async def delete(self, path: str, *, expected: str, origin: Origin) -> None:
        self.gate("run")
        async with transaction(self.storage) as session:
            await delete_file(session, self.settings, self.memory_id, path, expected=expected, origin=origin)

    async def changes(self, since: str | None) -> Changes | FullResync:
        self.gate("read")
        async with short_session(self.storage) as session:
            return await changes_since(session, self.memory_id, since)

    async def search(self, pattern: str, *, regex: bool, case_sensitive: bool, path: str, limit: int) -> Matches:
        self.gate("read")
        async with short_session(self.storage) as session:
            return await search_lines(
                session,
                self.settings,
                self.memory_id,
                pattern,
                regex=regex,
                case_sensitive=case_sensitive,
                path=path,
                limit=limit,
            )

    async def purge(self) -> None:
        self.gate("write")
        async with transaction(self.storage) as session:
            await purge_files(session, self.memory_id)
