"""Async relational storage construction and short session scopes."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from anyio import CancelScope, fail_after
from anyio.lowlevel import checkpoint_if_cancelled
from psycopg import OperationalError as PsycopgOperationalError
from sqlalchemy import URL, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import PostgreSQLConfig


def database_url(config: PostgreSQLConfig) -> URL:
    """Return the validated SQLAlchemy URL for application and migration I/O."""
    return make_url(config.url.get_secret_value())


def create_sql_engine(config: PostgreSQLConfig) -> AsyncEngine:
    return create_async_engine(
        database_url(config),
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


def is_unique_conflict(error: IntegrityError, *, constraint: str) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    return diagnostic is not None and diagnostic.sqlstate == "23505" and diagnostic.constraint_name == constraint


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


def is_database_contention(error: BaseException) -> bool:
    """Recognize transactions aborted by PostgreSQL contention or query deadlines.

    Only a fresh transactional admission/sweep may retry these failures after its
    transaction context rolls back. This never authorizes external-effect replay.
    """
    if isinstance(error, BaseExceptionGroup):
        return all(is_database_contention(item) for item in error.exceptions)
    return isinstance(error, DBAPIError) and getattr(error.orig, "sqlstate", None) in {
        "40001",  # serialization_failure
        "40P01",  # deadlock_detected
        "55P03",  # lock_not_available
        "57014",  # query_canceled (including statement_timeout)
    }
