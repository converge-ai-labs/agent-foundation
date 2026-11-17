"""Disposable PostgreSQL databases for App revision generation, parity checks and package tests."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from a13n_service.settings import Database


@contextmanager
def scratch_database(settings: Database) -> Iterator[Database]:
    """Connect only to the server's maintenance database; never mutate the configured business database.

    The caller's role needs CREATE DATABASE. Point settings at a disposable/local server for development
    or CI. The returned settings can also be used by an App's ordinary Storage-based integration tests.
    """
    url = make_url(settings.url.get_secret_value())
    name = f"a13n_app_test_{uuid4().hex}"
    admin = create_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
        connect_args={"connect_timeout": settings.connect_timeout},
    )
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            yield settings.model_copy(
                update={
                    "url": SecretStr(url.set(database=name).render_as_string(hide_password=False)),
                    "auto_migrate": False,
                }
            )
        finally:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()
