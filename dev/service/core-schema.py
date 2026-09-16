"""Generate the Bot-free verification graph using the canonical migration runner."""

import os
import sys
from pathlib import Path

from a13n_service.database.metadata import core_metadata
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig

root = Path(__file__).resolve().parents[2]
migrator = DatabaseMigrator(
    PostgreSQLConfig(url=os.environ["A13N_SERVICE_DATABASE_URL"]),
    script_location=root / "packages/a13n-service/tests/database/core_migrations",
    metadata=core_metadata,
)
if sys.argv[1] == "upgrade":
    migrator.upgrade()
else:
    migrator.revision(sys.argv[2])
