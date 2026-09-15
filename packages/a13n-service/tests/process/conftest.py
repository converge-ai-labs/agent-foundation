"""Independent process databases cloned from the fully migrated template."""

from collections.abc import Callable, Iterator
from uuid import uuid4

import pytest
from a13n_service.settings import Settings
from sqlalchemy.engine import make_url

from tests.conftest import create_postgres_database, drop_postgres_database

from .support import local_settings as build_local_settings


@pytest.fixture
def local_settings(
    pg_url: str, postgres_admin_url: str, service_postgres_template: str
) -> Iterator[Callable[..., Settings]]:
    created: list[str] = []

    def create_database_url() -> str:
        name = f"a13n_process_{uuid4().hex}"
        create_postgres_database(postgres_admin_url, name, template=service_postgres_template)
        created.append(name)
        return make_url(pg_url).set(database=name).render_as_string(hide_password=False)

    def build_settings(tmp_path, **updates):
        return build_local_settings(tmp_path, database_url=create_database_url(), **updates)

    try:
        yield build_settings
    finally:
        for name in created:
            drop_postgres_database(postgres_admin_url, name)
