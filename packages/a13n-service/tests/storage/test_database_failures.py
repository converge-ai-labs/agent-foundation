import asyncio

import psycopg
import pytest
from a13n_service.storage import is_database_unavailable
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError


def disconnected():
    return OperationalError(None, None, psycopg.OperationalError("connection refused"))


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (disconnected(), True),
        (OperationalError(None, None, psycopg.errors.ConnectionFailure()), True),
        (OperationalError(None, None, psycopg.errors.AdminShutdown()), True),
        (OperationalError(None, None, psycopg.errors.CannotConnectNow()), True),
        (OperationalError(None, None, psycopg.errors.TooManyConnections()), True),
        (DBAPIError(None, None, Exception(), connection_invalidated=True), True),
        (PoolTimeoutError(), True),
        (OperationalError(None, None, psycopg.errors.InvalidPassword()), False),
        (OperationalError(None, None, psycopg.errors.QueryCanceled()), False),
        (ProgrammingError(None, None, psycopg.errors.UndefinedTable()), False),
        (IntegrityError(None, None, psycopg.errors.UniqueViolation()), False),
        (RuntimeError("bug"), False),
        (asyncio.CancelledError(), False),
        (ExceptionGroup("sweep", [disconnected(), ExceptionGroup("visit", [disconnected()])]), True),
        (ExceptionGroup("sweep", [disconnected(), RuntimeError("bug")]), False),
        (BaseExceptionGroup("sweep", [disconnected(), asyncio.CancelledError()]), False),
    ],
)
def test_only_connection_failures_allow_a_fresh_sweep(error, retryable):
    assert is_database_unavailable(error) is retryable
