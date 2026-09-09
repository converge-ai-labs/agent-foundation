"""Local identity's public HTTP contract on the canonical SQLite adapter."""

from dataclasses import replace
from uuid import uuid4

import pytest
from a13n_service.api import install_api_conventions
from a13n_service.database.metadata import service_metadata
from a13n_service.iam.configuration import IdentityConfiguration
from a13n_service.iam.http.auth_router import router as auth_router
from a13n_service.iam.http.image_router import router as image_router
from a13n_service.iam.http.management_router import router
from a13n_service.iam.http.profile_router import router as profile_router
from a13n_service.iam.http.recovery_router import router as recovery_router
from a13n_service.iam.runtime import build_identity_runtime
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from fastapi import FastAPI
from httpx2 import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

PASSWORD = "valid-test-password-123"
ORIGIN = "https://testserver"


@pytest.fixture
def iam_pg_config(pg_url):
    database = f"iam_{uuid4().hex}"
    admin = create_engine(pg_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
    url = make_url(pg_url).set(database=database)
    schema = create_engine(url)
    service_metadata().create_all(schema)
    schema.dispose()
    try:
        yield PostgreSQLConfig(url=url.render_as_string(hide_password=False))
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
        admin.dispose()


@pytest.fixture(params=["sqlite", "postgresql"])
async def identity_runtime(request, service_sqlite_database):
    config = (
        SQLiteConfig(path=service_sqlite_database)
        if request.param == "sqlite"
        else request.getfixturevalue("iam_pg_config")
    )
    engine = create_sql_engine(config)
    runtime = await build_identity_runtime(
        create_session_factory(engine),
        IdentityConfiguration(public_origin=ORIGIN, initial_admin_email="admin@example.com"),
    )
    try:
        yield runtime
    finally:
        await engine.dispose()


@pytest.fixture
async def identity_http(identity_runtime, process_runtime_factory, tmp_path):
    runtime = identity_runtime
    issued = await runtime.invitations.initialize()
    assert issued is not None
    process = process_runtime_factory(agents=object(), request_authenticator=runtime.authenticator)
    app = FastAPI()
    install_api_conventions(app)
    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(profile_router)
    app.include_router(recovery_router)
    app.include_router(image_router)
    from types import SimpleNamespace

    process.shared.storage = SimpleNamespace(
        objects=await LocalObjectStore.create(tmp_path / "profile-objects"), sessions=runtime.membership._sessions
    )
    app.state.runtime = replace(process, control=replace(process.control, identity=runtime))
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        yield client, runtime, issued


async def accept(client, issued):
    result = await client.post(
        f"/api/v1/invitations/{issued.invitation.id}/accept",
        json={"token": issued.token, "password": PASSWORD},
    )
    assert result.status_code == 200, result.text
    client.headers["X-A13N-CSRF-Token"] = result.json()["csrf_token"]
    organizations = (await client.get("/api/v1/organizations")).json()
    organization = organizations["items"][0]["id"]
    workspaces = (await client.get(f"/api/v1/organizations/{organization}/workspaces")).json()
    return organization, workspaces["items"][0]["id"]
