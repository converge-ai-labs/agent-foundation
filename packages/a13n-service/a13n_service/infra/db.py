"""One engine/session owner, short SQL transaction scopes and the database rules tables declare."""

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from typing import Any, ClassVar

import anyio
from a13n_logging import exception_details, get_logger
from sqlalchemy import BigInteger, DateTime, FetchedValue, MetaData, Table, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from a13n_service.infra.errors import ServiceError

logger = get_logger(__name__)

_AFTER_COMMIT = "a13n.after_commit"


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class Stamped:
    """Mutable rows; the `stamp_resource` trigger advances `version` and `updated_at` on every update, except
    one confined to columns the table declares `unversioned`.

    ORM flushes read both back, so a row's ETag is current after its own update. A trigger that touches the
    row from elsewhere (an inbox change bumping its thread) leaves loaded copies stale until refreshed.
    """

    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}

    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"), server_onupdate=FetchedValue())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), server_onupdate=FetchedValue()
    )


# Rules are the SQL that declarative constraints cannot express: transition guards, immutability and
# cross-row checks. A table declares its own with `rules(...)` next to its constraints; tests apply them after
# `create_all`, and migration generation renders the same statements into the revision that creates the table.

_STAMP_RESOURCE = """
CREATE FUNCTION stamp_resource() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.version := OLD.version + 1;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $$
"""

_REFUSE_MUTATION = """
CREATE FUNCTION refuse_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME;
END $$
"""

_GUARD_IDENTITY = """
CREATE FUNCTION guard_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.id, NEW.organization_id, NEW.workspace_id, NEW.created_by_id, NEW.created_at)
        IS DISTINCT FROM ROW(OLD.id, OLD.organization_id, OLD.workspace_id, OLD.created_by_id, OLD.created_at)
    THEN RAISE EXCEPTION '% identity and scope are immutable', TG_TABLE_NAME; END IF;
    RETURN NEW;
END $$
"""

FUNCTIONS = (_STAMP_RESOURCE, _REFUSE_MUTATION, _GUARD_IDENTITY)


def trigger(table: str, function: str, *, on: str = "BEFORE UPDATE", when: str | None = None) -> str:
    condition = f" WHEN ({when})" if when else ""
    return f"CREATE TRIGGER {function} {on} ON {table} FOR EACH ROW{condition} EXECUTE FUNCTION {function}()"


def immutable(table: str) -> str:
    return trigger(table, "refuse_mutation", on="BEFORE UPDATE OR DELETE")


def identity_guarded(table: str) -> str:
    """Tenant resources never change identity, scope or authorship once created."""
    return trigger(table, "guard_identity")


def rules(*statements: str, unversioned: tuple[str, ...] = ()) -> dict[str, Any]:
    """The `__table_args__` entry carrying a table's rules, in execution order (functions before triggers).

    Changes confined to `unversioned` columns of a `Stamped` table keep its version, and so its ETag.
    """
    return {"info": {"rules": statements, "unversioned": unversioned}}


def table_rules(row: type[Base]) -> list[str]:
    """One table's rules: the version stamp of a `Stamped` row, then what the table declares."""
    table = row.__table__
    assert isinstance(table, Table)
    stamp: list[str] = []
    if issubclass(row, Stamped):
        unversioned = table.info.get("unversioned", ())
        ignored = "'{" + ",".join(unversioned) + "}'::text[]"
        when = f"(to_jsonb(OLD) - {ignored}) IS DISTINCT FROM (to_jsonb(NEW) - {ignored})" if unversioned else None
        stamp = [trigger(table.name, "stamp_resource", when=when)]
    return [*stamp, *table.info.get("rules", ())]


def schema_rules(rows: Iterable[type[Base]]) -> list[str]:
    return [*FUNCTIONS, *(statement for row in rows for statement in table_rules(row))]


class Storage:
    def __init__(self, url: str, *, pool_size: int = 5, connect_timeout: int = 5, statement_timeout: int = 10):
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=0,
            pool_timeout=connect_timeout,
            connect_args={
                "connect_timeout": connect_timeout,
                "options": f"-c statement_timeout={statement_timeout * 1000}",
            },
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self) -> None:
        with anyio.move_on_after(5, shield=True):
            await self.engine.dispose()


@asynccontextmanager
async def short_session(storage: Storage) -> AsyncIterator[AsyncSession]:
    session = storage.sessions()
    try:
        yield session
    finally:
        with anyio.move_on_after(5, shield=True):
            await session.close()


@asynccontextmanager
async def transaction(storage: Storage) -> AsyncIterator[AsyncSession]:
    """Commit on normal exit; callbacks registered with `after_commit` run only after that commit succeeded."""
    async with short_session(storage) as session:
        async with session.begin():
            yield session
        callbacks = session.info.pop(_AFTER_COMMIT, ())
    for callback in callbacks:
        try:
            await callback()
        except Exception as error:
            # Callbacks are hints such as wakeups; the committed state is already durable and sweeps recover.
            logger.warning(
                "After-commit callback failed",
                extra={"error_type": type(error).__name__, "exception_details": exception_details(error)},
            )


def after_commit(session: AsyncSession, callback: Callable[[], Awaitable[object]]) -> None:
    session.info.setdefault(_AFTER_COMMIT, []).append(callback)


async def lock[R: Base](session: AsyncSession, row_type: type[R], row_id: str) -> R | None:
    """Row lock that also refreshes any stale copy already loaded in this session."""
    return await session.get(row_type, row_id, with_for_update=True, populate_existing=True)


def assign(row: object, changes: Mapping[str, object]) -> list[str]:
    """Set each field of `changes` on `row`; the names of those whose value differed, in order."""
    changed = [name for name, value in changes.items() if getattr(row, name) != value]
    for name in changed:
        setattr(row, name, changes[name])
    return changed


async def now(session: AsyncSession) -> datetime:
    """Fresh database time; `now()` is frozen at transaction start and is wrong after waiting for locks."""
    return (await session.execute(select(func.clock_timestamp()))).scalar_one()


async def advisory_lock(session: AsyncSession, *key: str) -> None:
    """Transaction-only lock on a namespaced key, such as ("environments", workspace_id), for bounded SQL work,
    never external I/O."""
    digest = hashlib.sha256("\0".join(key).encode()).digest()
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest[:8], signed=True)})


def violated_constraint(error: IntegrityError) -> str | None:
    """The constraint a unique/check/foreign-key violation names, for callers that arbitrate by index."""
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


@contextmanager
def unique_key(kind: str, constraint: str, key: str) -> Iterator[None]:
    """A write the database refuses under the unique `constraint` is `already_exists`: another `kind` holds
    `key`."""
    try:
        yield
    except IntegrityError as error:
        if violated_constraint(error) != constraint:
            raise
        raise ServiceError("already_exists", f"{kind} key {key} already exists", {"kind": kind, "key": key}) from None
