"""Async relational storage construction and short session scopes."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol, cast

from anyio import CancelScope, current_effective_deadline, current_time, fail_after, move_on_after
from anyio.lowlevel import checkpoint
from sqlalchemy import URL, event, text
from sqlalchemy.engine import make_url
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
async def _session_scope(
    factory: async_sessionmaker[AsyncSession],
    *,
    cleanup_timeout_seconds: float,
    operation_timeout_seconds: float,
) -> AsyncGenerator[tuple[AsyncSession, CancelScope]]:
    # Repeated level cancellation can interrupt the driver's own invalidation
    # and strand a connection before session.close() can recover it.
    await checkpoint()
    timeout = min(operation_timeout_seconds, max(0, current_effective_deadline() - current_time()))
    session = factory()
    primary_error: BaseException | None = None
    try:
        with CancelScope(shield=True) as scope:
            # SQLAlchemy owns asyncio drivers. Deliver a deadline cancellation
            # once so their invalidation awaits can finish without another cancel.
            async with asyncio.timeout(timeout):
                yield session, scope
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            with move_on_after(cleanup_timeout_seconds, shield=True):
                await session.close()
        except Exception as cleanup_error:
            if primary_error is None:
                raise
            primary_error.add_note(f"session cleanup failed with {type(cleanup_error).__name__}")
    await checkpoint()


@asynccontextmanager
async def short_session(
    factory: async_sessionmaker[AsyncSession],
    *,
    cleanup_timeout_seconds: float = 5,
    operation_timeout_seconds: float = 30,
) -> AsyncGenerator[AsyncSession]:
    async with _session_scope(
        factory, cleanup_timeout_seconds=cleanup_timeout_seconds, operation_timeout_seconds=operation_timeout_seconds
    ) as (session, _scope):
        yield session


@asynccontextmanager
async def transaction(
    factory: async_sessionmaker[AsyncSession],
    *,
    cleanup_timeout_seconds: float = 5,
    operation_timeout_seconds: float = 30,
) -> AsyncGenerator[AsyncSession]:
    async with _session_scope(
        factory, cleanup_timeout_seconds=cleanup_timeout_seconds, operation_timeout_seconds=operation_timeout_seconds
    ) as (session, scope):
        database_transaction = await session.begin()
        try:
            yield session
            # Observe drain before starting commit. Once commit starts, its
            # bounded outcome must settle without repeated cancellation.
            scope.shield = False
            await checkpoint()
            scope.shield = True
            await database_transaction.commit()
        except BaseException as error:
            scope.shield = True
            try:
                with move_on_after(cleanup_timeout_seconds, shield=True):
                    await database_transaction.rollback()
            except Exception as rollback_error:
                error.add_note(f"rollback cleanup failed with {type(rollback_error).__name__}")
            raise


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
