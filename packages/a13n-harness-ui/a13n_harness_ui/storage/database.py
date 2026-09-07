"""Harness UI-owned SQLite construction and short transaction scopes."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from alembic.util.exc import CommandError
from anyio import CancelScope, fail_after, move_on_after, to_thread
from anyio.lowlevel import checkpoint_if_cancelled
from sqlalchemy import URL, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from a13n_harness_ui.errors import StoreIntegrityError

from .migration import DatabaseMigrator, DatabaseSchemaError, MigrationGraphError

if TYPE_CHECKING:
    from a13n_harness_ui.settings import StorageSettings


class _Cursor(Protocol):
    def execute(self, statement: str) -> object: ...

    def close(self) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...


@dataclass(frozen=True, slots=True)
class Database:
    """Process-local async access to one migrated metadata database."""

    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


@asynccontextmanager
async def open_database(path: Path, settings: StorageSettings) -> AsyncGenerator[Database]:
    """Migrate, open, verify, and close one file-backed SQLite database."""

    migrator = DatabaseMigrator(path, busy_timeout_seconds=settings.busy_timeout_seconds)
    try:
        await to_thread.run_sync(migrator.upgrade)
    except (CommandError, MigrationGraphError, OSError, SQLAlchemyError) as exc:
        raise StoreIntegrityError(
            "Harness UI metadata migration failed.",
            code="database_migration_failed",
        ) from exc
    engine = _create_engine(path, settings)
    try:
        database = Database(
            engine=engine,
            sessions=async_sessionmaker(engine, expire_on_commit=False, autoflush=False),
        )
        try:
            await check_database(database)
        except StoreIntegrityError:
            raise
        except (DatabaseSchemaError, OSError, SQLAlchemyError, TimeoutError) as exc:
            raise StoreIntegrityError(
                "Harness UI metadata database is unavailable or incompatible.",
                code="database_incompatible",
            ) from exc
        yield database
    finally:
        with move_on_after(settings.cleanup_timeout_seconds, shield=True):
            await engine.dispose()


@asynccontextmanager
async def short_session(factory: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession]:
    """Own one short session and return its connection before cancellation propagates."""

    await checkpoint_if_cancelled()
    with CancelScope(shield=True):
        session = factory()
        try:
            yield session
        finally:
            with CancelScope(shield=True):
                await session.close()


@asynccontextmanager
async def transaction(factory: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession]:
    """Own one short transaction and its session through commit or rollback."""

    await checkpoint_if_cancelled()
    with CancelScope(shield=True):
        session = factory()
        try:
            await session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
            except BaseException:
                with CancelScope(shield=True):
                    await session.rollback()
                raise
            else:
                with CancelScope(shield=True):
                    await session.commit()
        finally:
            with CancelScope(shield=True):
                await session.close()


async def check_database(database: Database, *, timeout_seconds: float = 3.0) -> None:
    """Verify basic access, schema compatibility, and SQLite integrity."""

    with fail_after(timeout_seconds):
        async with database.engine.connect() as connection:
            result = await connection.execute(text("PRAGMA quick_check"))
            if result.scalar_one() != "ok":
                raise StoreIntegrityError(
                    "Harness UI metadata database failed its integrity check.",
                    code="database_integrity_failed",
                )
    await to_thread.run_sync(DatabaseMigrator(database.engine.url.database or "").verify_current)


def _create_engine(path: Path, settings: StorageSettings) -> AsyncEngine:
    url = URL.create("sqlite+aiosqlite", database=str(path.absolute()))
    engine = create_async_engine(url)
    busy_timeout_ms = int(settings.busy_timeout_seconds * 1000)

    @event.listens_for(engine.sync_engine, "connect")
    def configure_connection(dbapi_connection: object, _connection_record: object) -> None:
        cursor = cast(_Connection, dbapi_connection).cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    return engine


__all__ = ["Database", "check_database", "open_database", "short_session", "transaction"]
