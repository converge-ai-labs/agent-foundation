"""Independent process databases copied from a fully migrated, closed template."""

from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest
from a13n_service.settings import Settings

from .support import local_settings as build_local_settings


@pytest.fixture(scope="session")
def migrated_process_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    settings = build_local_settings(tmp_path_factory.mktemp("process-database-template"))
    return settings.database_sqlite_path


@pytest.fixture
def local_settings(migrated_process_database: Path) -> Callable[..., Settings]:
    return partial(build_local_settings, database_template=migrated_process_database)
