"""Exercise a historical data conversion without downgrading unrelated newer contracts."""

import importlib
from pathlib import Path

from a13n_service.database import migrations
from a13n_service.storage.relational import database_url
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine


def apply_revision_steps(database, steps):
    engine = create_engine(database_url(database))
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                for revision, direction in steps:
                    path = next((Path(migrations.__file__).parent / "versions").glob(f"*_{revision}_*.py"))
                    module = importlib.import_module(f"a13n_service.database.migrations.versions.{path.stem}")
                    getattr(module, direction)()
    finally:
        engine.dispose()
