"""One composed graph and dedicated, bounded migration connection."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from a13n_service.distribution import Distribution
from a13n_service.settings import Database

MIGRATION_LOCK_KEY = 169029633


def configuration(distribution: Distribution) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent))
    config.set_main_option("path_separator", "os")
    config.set_main_option("version_locations", ":".join(str(path) for path in distribution.migrations))
    config.set_main_option("file_template", "%%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s")
    config.attributes["metadata"] = distribution.metadata()
    config.attributes["rules"] = distribution.table_rules()
    return config


def _heads(config: Config) -> tuple[str, ...]:
    result = tuple(ScriptDirectory.from_config(config).get_heads())
    if len(result) > 1:
        raise ValueError("Distribution migration graph must have at most one head")
    return result


def heads(distribution: Distribution) -> tuple[str, ...]:
    return _heads(configuration(distribution))


@contextmanager
def migration_connection(settings: Database, distribution: Distribution) -> Iterator[Config]:
    # Validate and execute the same composition without copying every table twice.
    config = configuration(distribution)
    _heads(config)
    engine = create_engine(
        settings.url.get_secret_value(), poolclass=NullPool, connect_args={"connect_timeout": settings.connect_timeout}
    )
    try:
        with engine.connect() as connection:
            # The session lock and all Alembic work use this same NullPool connection.
            connection.execute(text(f"SET statement_timeout = {settings.migration_advisory_lock_timeout * 1000}"))
            connection.execute(text("SET lock_timeout = 0"))
            connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
            connection.commit()
            connection.execute(text(f"SET lock_timeout = {settings.migration_lock_timeout * 1000}"))
            connection.execute(text(f"SET statement_timeout = {settings.migration_statement_timeout * 1000}"))
            connection.execute(
                text(f"SET idle_in_transaction_session_timeout = {settings.migration_idle_transaction_timeout * 1000}")
            )
            connection.commit()
            config.attributes["connection"] = connection
            yield config
    finally:
        # Closing the dedicated connection releases the lock on success and failure.
        engine.dispose()


def upgrade(settings: Database, distribution: Distribution) -> None:
    with migration_connection(settings, distribution) as config:
        command.upgrade(config, "head")


def generate(settings: Database, distribution: Distribution, message: str) -> None:
    with migration_connection(settings, distribution) as config:
        command.upgrade(config, "head")
        command.revision(config, message=message, autogenerate=True)


def check(settings: Database, distribution: Distribution) -> None:
    with migration_connection(settings, distribution) as config:
        command.check(config)
