"""Async relational storage construction and short session scopes."""

import sqlite3
from asyncio import CancelledError
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol, cast

from anyio import CancelScope, fail_after
from anyio.lowlevel import checkpoint_if_cancelled
from psycopg import OperationalError as PsycopgOperationalError
from sqlalchemy import URL, event, text
from sqlalchemy.engine import ExceptionContext, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .config import PostgreSQLConfig, SQLiteConfig


class _Cursor(Protocol):
    def execute(self, statement: str) -> object: ...

    def close(self) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...


def async_database_url(config: PostgreSQLConfig | SQLiteConfig) -> URL:
    """Return the validated SQLAlchemy URL for asynchronous application I/O."""

    if isinstance(config, PostgreSQLConfig):
        return _postgresql_url(config).set(drivername="postgresql+psycopg")
    return URL.create("sqlite+aiosqlite", database=_sqlite_database(config.path))


def sync_database_url(config: PostgreSQLConfig | SQLiteConfig) -> URL:
    """Return the validated SQLAlchemy URL for synchronous migration I/O."""

    if isinstance(config, PostgreSQLConfig):
        return _postgresql_url(config).set(drivername="postgresql+psycopg")
    return URL.create("sqlite", database=_sqlite_database(config.path))


def create_sql_engine(config: PostgreSQLConfig | SQLiteConfig) -> AsyncEngine:
    if isinstance(config, PostgreSQLConfig):
        return create_async_engine(
            async_database_url(config),
            pool_pre_ping=True,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            pool_timeout=config.pool_timeout_seconds,
            pool_recycle=config.pool_recycle_seconds,
            connect_args={
                "connect_timeout": config.connect_timeout_seconds,
                "options": f"-c statement_timeout={int(config.statement_timeout_seconds * 1000)}",
            },
        )

    if str(config.path) == ":memory:":
        engine = create_async_engine(async_database_url(config), poolclass=StaticPool)
    else:
        engine = create_async_engine(async_database_url(config))
    _configure_sqlite(engine, config.busy_timeout_seconds, config.path)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def short_session(
    factory: async_sessionmaker[AsyncSession], *, cleanup_timeout_seconds: float = 5
) -> AsyncGenerator[AsyncSession]:
    await checkpoint_if_cancelled()
    session = factory()
    error: BaseException | None = None
    try:
        # Checkout must finish assigning the connection to the session before
        # cancellation can unwind it. Backend connect/pool timeouts still apply.
        with CancelScope(shield=True):
            await session.connection()
        await checkpoint_if_cancelled()
        yield session
    except BaseException as caught:
        error = caught
        raise
    finally:
        try:
            with fail_after(cleanup_timeout_seconds, shield=True):
                await session.close()
        except Exception as cleanup_error:
            if error is None:
                raise
            error.add_note(f"session cleanup failed with {type(cleanup_error).__name__}")


@asynccontextmanager
async def transaction(
    factory: async_sessionmaker[AsyncSession], *, cleanup_timeout_seconds: float = 5
) -> AsyncGenerator[AsyncSession]:
    async with short_session(factory, cleanup_timeout_seconds=cleanup_timeout_seconds) as session:
        try:
            yield session
        except BaseException as error:
            try:
                with fail_after(cleanup_timeout_seconds, shield=True):
                    await session.rollback()
            except Exception as rollback_error:
                error.add_note(f"rollback cleanup failed with {type(rollback_error).__name__}")
            raise
        else:
            await checkpoint_if_cancelled()
            # Commit also returns the connection to the pool. Finish that
            # ownership transfer before delivering cancellation to the caller.
            with CancelScope(shield=True):
                await session.commit()
            await checkpoint_if_cancelled()


async def check_database(engine: AsyncEngine, *, timeout_seconds: float = 3) -> None:
    with fail_after(timeout_seconds):
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))


def _postgresql_url(config: PostgreSQLConfig) -> URL:
    url = make_url(config.url.get_secret_value())
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("PostgreSQL backend requires a postgresql or postgresql+psycopg URL")
    return url


def _sqlite_database(path: Path) -> str:
    return ":memory:" if str(path) == ":memory:" else str(path.absolute())


def _configure_sqlite(engine: AsyncEngine, busy_timeout_seconds: float, path: Path) -> None:
    busy_timeout_ms = int(busy_timeout_seconds * 1000)

    @event.listens_for(engine.sync_engine, "handle_error")
    def discard_cancelled_connection(context: ExceptionContext) -> None:
        # Cancellation can leave an unfetched cursor holding a WAL snapshot
        # even after rollback. Drain and close this connection under shielding
        # so neither the cursor nor interrupted termination reaches the pool.
        if isinstance(context.original_exception, CancelledError):
            if context.connection is not None:
                with CancelScope(shield=True):
                    context.connection.invalidate()
            # Other pooled connections are healthy; do not invalidate them.
            context.is_disconnect = False

    @event.listens_for(engine.sync_engine, "connect")
    def configure_connection(dbapi_connection: object, _connection_record: object) -> None:
        cursor = cast(_Connection, dbapi_connection).cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            cursor.execute("PRAGMA synchronous=FULL")
            if str(path) != ":memory:":
                cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()


def is_unique_conflict(error: IntegrityError, *, constraint: str, sqlite_columns: str) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    if diagnostic is not None:
        return diagnostic.sqlstate == "23505" and diagnostic.constraint_name == constraint
    return (
        getattr(error.orig, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE
        and str(error.orig) == f"UNIQUE constraint failed: {sqlite_columns}"
    )


def is_database_unavailable(error: BaseException) -> bool:
    """Recognize connection failures safe to retry at a fresh, fenced sweep boundary.

    This does not authorize replaying a transaction whose commit response was lost.
    Groups qualify only when every leaf is a connection failure; cancellation and
    programming errors must still escape the owning component.
    """
    if isinstance(error, BaseExceptionGroup):
        return all(is_database_unavailable(item) for item in error.exceptions)
    if isinstance(error, PoolTimeoutError):
        return True
    if not isinstance(error, DBAPIError):
        return False
    if error.connection_invalidated:
        return True
    if isinstance(error.orig, PsycopgOperationalError):
        sqlstate = error.orig.sqlstate
        return sqlstate is None or sqlstate.startswith("08") or sqlstate in {"53300", "57P01", "57P02", "57P03"}
    return False
