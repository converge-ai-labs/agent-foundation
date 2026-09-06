"""Async relational storage construction and short session scopes."""

from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Protocol, cast

from anyio import fail_after, get_cancelled_exc_class, move_on_after
from anyio.lowlevel import checkpoint_if_cancelled
from sqlalchemy import URL, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .config import PostgreSQLConfig, SQLiteConfig

_SESSION_CLEANUP_LIMIT: ContextVar[float] = ContextVar("session_cleanup_limit", default=float("inf"))


@contextmanager
def limit_session_cleanup(timeout_seconds: float) -> Iterator[None]:
    """Cap shielded database cleanup in this owner and its child tasks."""

    if timeout_seconds <= 0:
        raise ValueError("Session cleanup limit must be positive")
    token = _SESSION_CLEANUP_LIMIT.set(min(timeout_seconds, _SESSION_CLEANUP_LIMIT.get()))
    try:
        yield
    finally:
        _SESSION_CLEANUP_LIMIT.reset(token)


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
    cleanup_timeout_seconds = min(cleanup_timeout_seconds, _SESSION_CLEANUP_LIMIT.get())
    session = factory()
    original_error: BaseException | None = None
    try:
        yield session
    except BaseException as error:
        original_error = error
        if isinstance(error, DBAPIError):
            # A cancelled pool ping can fail while restoring driver state and
            # replace cancellation with a DBAPI error across the greenlet bridge.
            try:
                await checkpoint_if_cancelled()
            except get_cancelled_exc_class() as cancellation:
                original_error = cancellation
                cancellation.add_note(f"database operation failed during cancellation with {type(error).__name__}")
                raise cancellation from None
        raise
    finally:
        with move_on_after(cleanup_timeout_seconds, shield=True):
            try:
                await session.close()
            except Exception as cleanup_error:
                # A cancelled DB driver operation can leave rollback unusable. Retire
                # that connection without replacing the cancellation with a close error.
                try:
                    await session.invalidate()
                except Exception as invalidate_error:
                    cleanup_error.add_note(f"invalidation failed with {type(invalidate_error).__name__}")
                if original_error is None:
                    raise
                original_error.add_note(f"session cleanup failed with {type(cleanup_error).__name__}")


@asynccontextmanager
async def transaction(
    factory: async_sessionmaker[AsyncSession], *, cleanup_timeout_seconds: float = 5
) -> AsyncGenerator[AsyncSession]:
    cleanup_timeout_seconds = min(cleanup_timeout_seconds, _SESSION_CLEANUP_LIMIT.get())
    async with short_session(factory, cleanup_timeout_seconds=cleanup_timeout_seconds) as session:
        database_transaction = await session.begin()
        try:
            yield session
        except BaseException as error:
            try:
                with move_on_after(cleanup_timeout_seconds, shield=True):
                    await database_transaction.rollback()
            except Exception as rollback_error:
                error.add_note(f"rollback cleanup failed with {type(rollback_error).__name__}")
            raise
        else:
            await database_transaction.commit()


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
