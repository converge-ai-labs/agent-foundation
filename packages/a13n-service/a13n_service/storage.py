"""Database configuration and schema readiness shared by application and operator commands."""

from sqlalchemy import text

from a13n_service.infra.db import Storage, short_session
from a13n_service.settings import Database


def open_storage(config: Database) -> Storage:
    """The process's database pool; the caller closes it."""
    return Storage(
        config.url.get_secret_value(),
        pool_size=config.pool_size,
        connect_timeout=config.connect_timeout,
        statement_timeout=config.statement_timeout,
    )


async def check_schema(storage: Storage, expected: tuple[str, ...]) -> None:
    async with short_session(storage) as session:
        actual = tuple(
            (await session.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num"))).scalars()
        )
    if actual != tuple(sorted(expected)) or not expected:
        raise RuntimeError("Database schema is incompatible; run a13n-service migrate")
