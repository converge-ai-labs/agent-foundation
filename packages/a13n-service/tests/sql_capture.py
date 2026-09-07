"""Measure executed SQL without changing the session lifecycle under test."""

from contextlib import contextmanager

from sqlalchemy import event


@contextmanager
def capture_sql(sessions):
    engine = sessions.kw["bind"].sync_engine
    statements: list[str] = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)
