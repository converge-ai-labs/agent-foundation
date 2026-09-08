"""Local identity's public HTTP contract on the canonical SQLite adapter."""

import asyncio
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

    process.shared.storage = SimpleNamespace(objects=await LocalObjectStore.create(tmp_path / "profile-objects"))
    app.state.runtime = replace(process, control=replace(process.control, identity=runtime))
    async with AsyncClient(transport=ASGITransport(app), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        yield client, runtime, issued


@pytest.fixture
async def recovery_delivery(identity_http, monkeypatch):
    _client, runtime, _issued = identity_http
    recovery = runtime.recovery
    configuration = runtime.configuration.model_copy(
        update={"smtp_host": "smtp.example.com", "smtp_sender": "auth@example.com"}
    )
    monkeypatch.setattr(recovery, "_configuration", configuration)
    deliveries = []

    async def send(email, subject, body):
        deliveries.append((email, subject, body))
        return True

    monkeypatch.setattr(recovery._mailer, "send_message", send)
    return deliveries


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


@pytest.mark.anyio
async def test_bootstrap_login_key_and_logout(identity_http):
    client, runtime, issued = identity_http
    page = await client.get(f"/api/v1/invitations/{issued.invitation.id}/accept")
    assert page.status_code == 405
    _organization, workspace = await accept(client, issued)
    assert await runtime.invitations.initialize() is None
    assert (await client.get("/api/v1/users/me")).json()["email_verified_at"] is None
    key = await client.post(f"/api/v1/workspaces/{workspace}/personal-api-keys", json={"name": "automation"})
    assert key.status_code == 201, key.text
    bearer = key.json()["bearer"]
    key_id = key.json()["key"]["id"]
    assert bearer not in (await client.get(f"/api/v1/api-keys/{key_id}")).text
    assert (await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {bearer}"})).status_code == 401
    assert (await client.post("/api/v1/auth/logout")).status_code == 204
    assert (await client.get("/api/v1/users/me")).status_code == 401
    client.headers["Authorization"] = f"Bearer {bearer}"
    assert (await client.get(f"/api/v1/workspaces/{workspace}")).status_code == 200
    assert (await client.get("/api/v1/organizations")).status_code == 404
    assert (await client.post(f"/api/v1/api-keys/{key_id}/revoke")).status_code == 200
    assert (await client.get(f"/api/v1/workspaces/{workspace}")).status_code == 401
    del client.headers["Authorization"]
    login = await client.post("/api/v1/auth/login", json={"email": "ADMIN@example.com", "password": PASSWORD})
    assert login.status_code == 200, login.text
    cookie = login.headers["set-cookie"]
    assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=lax"))


@pytest.mark.anyio
async def test_csrf_validation_and_single_use_invitation(identity_http):
    client, _runtime, issued = identity_http
    organization, workspace = await accept(client, issued)
    body = {"name": "blocked"}
    path = f"/api/v1/workspaces/{workspace}/personal-api-keys"
    assert (await client.post(path, json=body, headers={"Origin": "https://evil.example"})).status_code == 403
    assert (await client.post(path, json=body, headers={"X-A13N-CSRF-Token": "wrong"})).status_code == 403
    invalid = await client.post("/api/v1/auth/login", json={"email": "bad", "password": "secret-invalid"})
    assert invalid.status_code == 400
    assert "secret-invalid" not in invalid.text
    replay = await client.post(
        f"/api/v1/invitations/{issued.invitation.id}/accept", json={"token": issued.token, "password": PASSWORD}
    )
    assert replay.status_code == 401
    invitation = await client.post(
        f"/api/v1/workspaces/{workspace}/invitations", json={"email": "member@example.com", "role": "runner"}
    )
    assert invitation.status_code == 201, invitation.text
    data = invitation.json()
    token = data["invitation_url"].split("#token=")[1]
    invitation_id = data["invitation"]["id"]
    renewed = await client.post(f"/api/v1/invitations/{invitation_id}/resend", json={"expected_version": 1})
    assert renewed.status_code == 200, renewed.text
    assert (
        await client.post(f"/api/v1/invitations/{invitation_id}/revoke", json={"expected_version": 1})
    ).status_code == 409
    old = await client.post(f"/api/v1/invitations/{invitation_id}/accept", json={"token": token, "password": PASSWORD})
    assert old.status_code == 401
    fresh = renewed.json()["invitation_url"].split("#token=")[1]
    result = await client.post(
        f"/api/v1/invitations/{invitation_id}/accept", json={"token": fresh, "password": PASSWORD}
    )
    assert result.status_code == 200, result.text
    client.headers["X-A13N-CSRF-Token"] = result.json()["csrf_token"]
    assert (await client.get(f"/api/v1/workspaces/{workspace}")).status_code == 200
    assert (await client.get(f"/api/v1/organizations/{organization}/users")).status_code == 404
    assert (
        await client.post(f"/api/v1/workspaces/{workspace}/service-accounts", json={"name": "x", "role": "runner"})
    ).status_code == 404


@pytest.mark.anyio
async def test_last_admin_and_workspace_etags(identity_http):
    client, _runtime, issued = identity_http
    organization, _workspace = await accept(client, issued)
    bindings = (await client.get(f"/api/v1/organizations/{organization}/role-bindings")).json()["items"]
    path = f"/api/v1/role-bindings/{bindings[0]['id']}"
    current = await client.get(path)
    assert current.status_code == 200, current.text
    blocked = await client.delete(path, headers={"If-Match": current.headers["etag"]})
    assert blocked.status_code == 409, blocked.text
    created = await client.post(f"/api/v1/organizations/{organization}/workspaces", json={"name": "temporary"})
    assert created.status_code == 201, created.text
    path = f"/api/v1/workspaces/{created.json()['id']}"
    changed = await client.patch(path, json={"name": "renamed"}, headers={"If-Match": created.headers["etag"]})
    assert changed.status_code == 200, changed.text
    assert (await client.delete(path, headers={"If-Match": created.headers["etag"]})).status_code == 412
    assert (await client.delete(path, headers={"If-Match": changed.headers["etag"]})).status_code == 204


@pytest.mark.anyio
async def test_concurrent_bootstrap_and_acceptance(identity_http):
    _client, runtime, issued = identity_http
    assert await asyncio.gather(runtime.invitations.initialize(), runtime.invitations.initialize()) == [None, None]
    from a13n_service.application_errors import ApplicationError
    from a13n_service.iam.schemas import AcceptInvitationRequest

    request = AcceptInvitationRequest(token=issued.token, password=PASSWORD)
    results = await asyncio.gather(
        runtime.invitations.accept(issued.invitation.id, request, request_id=None),
        runtime.invitations.accept(issued.invitation.id, request, request_id=None),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ApplicationError) for result in results) == 1, results
    assert sum(not isinstance(result, Exception) for result in results) == 1, results


@pytest.mark.anyio
async def test_service_account_disable_reenable_and_delete(identity_http):
    client, _runtime, issued = identity_http
    _organization, workspace = await accept(client, issued)
    response = await client.post(
        f"/api/v1/workspaces/{workspace}/service-accounts", json={"name": "ingress", "role": "runner"}
    )
    assert response.status_code == 201, response.text
    account = response.json()
    path = f"/api/v1/service-accounts/{account['id']}"
    key = await client.post(f"{path}/api-keys", json={"name": "worker", "expires_at": None})
    assert key.status_code == 201, key.text
    assert key.json()["key"]["expires_at"] is None
    async with AsyncClient(
        transport=client._transport, base_url=ORIGIN, headers={"Authorization": f"Bearer {key.json()['bearer']}"}
    ) as worker:
        assert (await worker.get(f"/api/v1/workspaces/{workspace}")).status_code == 200
        assert (await worker.get("/api/v1/users/me")).status_code == 403
        body = {"expected_version": 1, "name": "ingress", "role": "runner", "status": "disabled"}
        disabled = await client.patch(path, json=body)
        assert disabled.status_code == 200, disabled.text
        assert (await worker.get(f"/api/v1/workspaces/{workspace}")).status_code == 401
        assert (await client.patch(path, json=body)).status_code == 409
        body.update(expected_version=2, status="active")
        assert (await client.patch(path, json=body)).status_code == 200
        assert (await worker.get(f"/api/v1/workspaces/{workspace}")).status_code == 200
        assert (await client.request("DELETE", path, json={"expected_version": 3})).status_code == 204
        assert (await worker.get(f"/api/v1/workspaces/{workspace}")).status_code == 401
        assert (await client.get(path)).status_code == 404


@pytest.mark.anyio
async def test_password_change_revokes_other_sessions_and_rechecks_stream_actor(identity_http):
    from a13n_service.iam.authorization import AuthorizationError, WorkspaceAction, authorize_workspace
    from a13n_service.storage import short_session
    from starlette.requests import Request

    client, runtime, issued = identity_http
    _organization, workspace = await accept(client, issued)
    cookie = client.cookies.get("a13n_session")
    request = Request({"type": "http", "method": "GET", "headers": [(b"cookie", f"a13n_session={cookie}".encode())]})
    original_actor = await runtime.authenticator(request)
    login = await client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    client.headers["X-A13N-CSRF-Token"] = login.json()["csrf_token"]
    changed = await client.post(
        "/api/v1/users/me/password", json={"current_password": PASSWORD, "password": "replacement-password-456"}
    )
    assert changed.status_code == 204, changed.text
    assert (await client.get("/api/v1/users/me")).status_code == 200
    async with short_session(runtime.authenticator._sessions) as session:
        with pytest.raises(AuthorizationError, match="credential_invalid"):
            await authorize_workspace(
                session, actor=original_actor, workspace_id=workspace, action=WorkspaceAction.agent_read
            )
    assert (
        await client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    ).status_code == 401


@pytest.mark.anyio
async def test_existing_user_invitation_requires_password_and_never_demotes(identity_http):
    client, _runtime, issued = identity_http
    organization, workspace = await accept(client, issued)
    invitation = await client.post(
        f"/api/v1/workspaces/{workspace}/invitations", json={"email": "admin@example.com", "role": "viewer"}
    )
    assert invitation.status_code == 201, invitation.text
    data = invitation.json()
    path = f"/api/v1/invitations/{data['invitation']['id']}/accept"
    token = data["invitation_url"].split("#token=")[1]
    assert (await client.post(path, json={"token": token, "password": "wrong-password-123"})).status_code == 401
    accepted = await client.post(path, json={"token": token, "password": PASSWORD})
    assert accepted.status_code == 200, accepted.text
    client.headers["X-A13N-CSRF-Token"] = accepted.json()["csrf_token"]
    assert (await client.get(f"/api/v1/organizations/{organization}/users")).status_code == 200


@pytest.mark.anyio
async def test_member_removal_revokes_key_and_pending_inviter_authority(identity_http):
    client, _runtime, issued = identity_http
    organization, workspace = await accept(client, issued)
    admin_cookie = client.cookies.get("a13n_session")
    admin_csrf = client.headers["X-A13N-CSRF-Token"]
    invite = await client.post(
        f"/api/v1/workspaces/{workspace}/invitations", json={"email": "manager@example.com", "role": "admin"}
    )
    item = invite.json()
    accepted = await client.post(
        f"/api/v1/invitations/{item['invitation']['id']}/accept",
        json={"token": item["invitation_url"].split("#token=")[1], "password": PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    manager_id = accepted.json()["user"]["id"]
    client.headers["X-A13N-CSRF-Token"] = accepted.json()["csrf_token"]
    key = await client.post(f"/api/v1/workspaces/{workspace}/personal-api-keys", json={"name": "manager"})
    assert key.status_code == 201, key.text
    pending = await client.post(
        f"/api/v1/workspaces/{workspace}/invitations", json={"email": "later@example.com", "role": "runner"}
    )
    assert pending.status_code == 201, pending.text
    client.cookies.clear()
    client.cookies.set("a13n_session", admin_cookie)
    client.headers["X-A13N-CSRF-Token"] = admin_csrf
    bindings = (await client.get(f"/api/v1/organizations/{organization}/role-bindings")).json()["items"]
    binding = next(item for item in bindings if item["principal_id"] == manager_id)
    path = f"/api/v1/role-bindings/{binding['id']}"
    etag = (await client.get(path)).headers["etag"]
    assert (await client.delete(path, headers={"If-Match": etag})).status_code == 204
    metadata = await client.get(f"/api/v1/api-keys/{key.json()['key']['id']}")
    assert metadata.json()["revoked_at"] is not None
    item = pending.json()
    denied = await client.post(
        f"/api/v1/invitations/{item['invitation']['id']}/accept",
        json={"token": item["invitation_url"].split("#token=")[1], "password": PASSWORD},
    )
    assert denied.status_code == 404, denied.text


@pytest.mark.anyio
async def test_expired_invitation_and_revoked_session_fail_closed(identity_http):
    from datetime import timedelta

    from a13n_service.iam.models import InvitationRecord
    from a13n_service.storage import transaction
    from a13n_service.temporal import utc_now

    client, runtime, issued = identity_http
    async with transaction(runtime.authenticator._sessions) as session:
        invitation = await session.get(InvitationRecord, issued.invitation.id)
        invitation.expires_at = utc_now() - timedelta(seconds=1)
    denied = await client.post(
        f"/api/v1/invitations/{issued.invitation.id}/accept", json={"token": issued.token, "password": PASSWORD}
    )
    assert denied.status_code == 401
    renewed = await runtime.invitations.initialize(reissue=True)
    assert renewed.invitation.id == issued.invitation.id
    await accept(client, renewed)
    sessions = (await client.get("/api/v1/users/me/auth-sessions")).json()["items"]
    assert len(sessions) == 1
    assert (await client.delete(f"/api/v1/users/me/auth-sessions/{sessions[0]['id']}")).status_code == 204
    assert (await client.get("/api/v1/users/me")).status_code == 401


@pytest.mark.anyio
async def test_concurrent_empty_database_initializes_once(identity_runtime):
    from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
    from a13n_service.storage import short_session
    from sqlalchemy import func, select

    results = await asyncio.gather(identity_runtime.invitations.initialize(), identity_runtime.invitations.initialize())
    assert sum(result is not None for result in results) == 1
    async with short_session(identity_runtime.authenticator._sessions) as session:
        assert await session.scalar(select(func.count()).select_from(OrganizationRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(WorkspaceRecord)) == 1


@pytest.mark.anyio
async def test_websocket_cookie_authentication_requires_same_origin(identity_http):
    from unittest.mock import AsyncMock

    from a13n_service.application_errors import ApplicationError
    from starlette.websockets import WebSocket

    client, runtime, issued = identity_http
    await accept(client, issued)
    cookie = client.cookies.get("a13n_session")
    scope = {
        "type": "websocket",
        "headers": [(b"cookie", f"a13n_session={cookie}".encode()), (b"origin", ORIGIN.encode())],
    }
    actor = await runtime.authenticator(WebSocket(scope, AsyncMock(), AsyncMock()))
    assert actor.auth_method == "session"
    scope["headers"][-1] = (b"origin", b"https://evil.example")
    with pytest.raises(ApplicationError, match="accepted Origin"):
        await runtime.authenticator(WebSocket(scope, AsyncMock(), AsyncMock()))


@pytest.mark.anyio
async def test_concurrent_admin_demotions_preserve_one_admin(identity_http):
    from a13n_service.application_errors import ApplicationError
    from starlette.requests import Request

    client, runtime, issued = identity_http
    organization, _workspace = await accept(client, issued)

    async def current_actor():
        cookie = client.cookies.get("a13n_session")
        return await runtime.authenticator(
            Request({"type": "http", "method": "GET", "headers": [(b"cookie", f"a13n_session={cookie}".encode())]})
        )

    first = await current_actor()
    invitation = await client.post(
        f"/api/v1/organizations/{organization}/invitations",
        json={
            "email": "second-admin@example.com",
            "grants": [{"resource_type": "organization", "resource_id": organization, "role_key": "admin"}],
        },
    )
    assert invitation.status_code == 201, invitation.text
    item = invitation.json()
    accepted = await client.post(
        f"/api/v1/invitations/{item['invitation']['id']}/accept",
        json={
            "token": item["invitation_url"].split("#token=")[1],
            "password": PASSWORD,
        },
    )
    assert accepted.status_code == 200, accepted.text
    second = await current_actor()
    actors = {actor.principal.principal_id: actor for actor in (first, second)}
    bindings = (await client.get(f"/api/v1/organizations/{organization}/role-bindings")).json()["items"]
    etags = {item["id"]: (await client.get(f"/api/v1/role-bindings/{item['id']}")).headers["etag"] for item in bindings}
    results = await asyncio.gather(
        *(
            runtime.membership.change_binding(
                actors[item["principal_id"]], item["id"], etags[item["id"]], role="member"
            )
            for item in bindings
        ),
        return_exceptions=True,
    )
    failures = [result for result in results if isinstance(result, ApplicationError)]
    assert len(failures) == 1, results
    assert failures[0].code == "last_admin_required"
    assert sum(not isinstance(result, Exception) for result in results) == 1, results


@pytest.mark.anyio
async def test_console_profiles_are_versioned_and_permissions_are_current(identity_http):
    client, _runtime, issued = identity_http
    organization, workspace = await accept(client, issued)
    me = await client.get("/api/v1/users/me")
    assert "image_id" not in me.json()
    assert me.json()["image_url"] is None
    updated = await client.patch("/api/v1/users/me", json={"name": "Admin"}, headers={"If-Match": me.headers["etag"]})
    assert updated.status_code == 200, updated.text
    stale = await client.patch("/api/v1/users/me", json={"name": "Stale"}, headers={"If-Match": me.headers["etag"]})
    assert stale.status_code == 412
    org = await client.get(f"/api/v1/organizations/{organization}")
    renamed = await client.patch(
        f"/api/v1/organizations/{organization}", json={"name": "Example"}, headers={"If-Match": org.headers["etag"]}
    )
    assert renamed.status_code == 200
    permissions = await client.get(f"/api/v1/workspaces/{workspace}/permissions")
    assert permissions.json()["organization_admin"] is True
    assert "agent.create" in permissions.json()["actions"]
    org_permissions = await client.get(f"/api/v1/organizations/{organization}/permissions")
    assert org_permissions.json() == {"organization_admin": True}
    events = await client.get("/api/v1/users/me/security-activity")
    assert "user_profile.update" in {event["action"] for event in events.json()["items"]}
    assert (await client.get(f"/api/v1/workspaces/{workspace}/api-keys")).status_code == 200
    assert (await client.get("/api/v1/auth/configuration")).json() == {"email_delivery": False}
    assert (await client.post("/api/v1/auth/password-reset", json={"email": "admin@example.com"})).status_code == 503


@pytest.mark.anyio
async def test_profile_images_are_normalized_and_old_urls_stop_working(identity_http):
    from io import BytesIO

    from PIL import Image

    client, _runtime, issued = identity_http
    _organization, workspace = await accept(client, issued)
    buffer = BytesIO()
    Image.new("RGB", (800, 400), "red").save(buffer, format="PNG")
    me = await client.get("/api/v1/users/me")
    uploaded = await client.put(
        "/api/v1/users/me/avatar",
        content=buffer.getvalue(),
        headers={"If-Match": me.headers["etag"], "Content-Type": "image/png"},
    )
    assert uploaded.status_code == 200, uploaded.text
    url = uploaded.json()["image_url"]
    assert url.startswith(f"/api/v1/users/{me.json()['id']}/avatar/")
    image = await client.get(url)
    assert image.headers["content-type"] == "image/webp"
    assert Image.open(BytesIO(image.content)).size == (512, 256)
    stale = await client.put(
        "/api/v1/users/me/avatar", content=buffer.getvalue(), headers={"If-Match": me.headers["etag"]}
    )
    assert stale.status_code == 412
    removed = await client.delete("/api/v1/users/me/avatar", headers={"If-Match": uploaded.headers["etag"]})
    assert removed.status_code == 200
    assert removed.json()["image_url"] is None
    assert (await client.get(url)).status_code == 404
    bad = await client.put(
        "/api/v1/users/me/avatar", content=b"not an image", headers={"If-Match": removed.headers["etag"]}
    )
    assert bad.status_code == 400
    ws = await client.get(f"/api/v1/workspaces/{workspace}")
    icon = await client.put(
        f"/api/v1/workspaces/{workspace}/icon", content=buffer.getvalue(), headers={"If-Match": ws.headers["etag"]}
    )
    assert icon.status_code == 200, icon.text
    assert (await client.get(icon.json()["image_url"])).status_code == 200


@pytest.mark.anyio
async def test_reset_links_are_single_use_and_revoke_sessions_not_keys(identity_http, recovery_delivery):
    from urllib.parse import parse_qs, urlsplit

    from a13n_service.iam.models import UserRecord
    from a13n_service.storage import transaction
    from a13n_service.temporal import utc_now

    client, runtime, issued = identity_http
    _organization, workspace = await accept(client, issued)
    deliveries = recovery_delivery
    me = (await client.get("/api/v1/users/me")).json()
    async with transaction(runtime.sessions._sessions) as session:
        user = await session.get(UserRecord, me["id"])
        user.email_verified_at = utc_now()
    key = (await client.post(f"/api/v1/workspaces/{workspace}/personal-api-keys", json={"name": "test"})).json()
    assert (await client.post("/api/v1/auth/password-reset", json={"email": me["email"]})).status_code == 202
    assert (await client.post("/api/v1/auth/password-reset", json={"email": "unknown@example.com"})).status_code == 202
    assert len(deliveries) == 1
    url = deliveries[0][2].splitlines()[2]
    token = parse_qs(urlsplit(url).fragment)["token"][0]
    body = {"token": token, "password": "a-new-valid-password-123"}
    responses = await asyncio.gather(
        *(client.post("/api/v1/auth/password-reset/complete", json=body) for _ in range(2))
    )
    assert sorted(response.status_code for response in responses) == [204, 400]
    assert (await client.get("/api/v1/users/me")).status_code == 401
    client.cookies.clear()
    assert (
        await client.get(f"/api/v1/workspaces/{workspace}", headers={"Authorization": f"Bearer {key['bearer']}"})
    ).status_code == 200
    assert (
        await client.post("/api/v1/auth/login", json={"email": me["email"], "password": body["password"]})
    ).status_code == 200


@pytest.mark.anyio
async def test_workspace_viewer_cannot_change_profiles_or_read_admin_projections(identity_http):
    client, _runtime, issued = identity_http
    organization, workspace = await accept(client, issued)
    invitation = (
        await client.post(
            f"/api/v1/workspaces/{workspace}/invitations", json={"email": "viewer@example.com", "role": "viewer"}
        )
    ).json()
    token = invitation["invitation_url"].split("#token=")[1]
    await client.post("/api/v1/auth/logout")
    accepted = await client.post(
        f"/api/v1/invitations/{invitation['invitation']['id']}/accept", json={"token": token, "password": PASSWORD}
    )
    assert accepted.status_code == 200
    client.headers["X-A13N-CSRF-Token"] = accepted.json()["csrf_token"]
    permissions = (await client.get(f"/api/v1/workspaces/{workspace}/permissions")).json()
    assert permissions["organization_admin"] is False
    assert "agent.read" in permissions["actions"]
    assert "agent.create" not in permissions["actions"]
    org_permissions = await client.get(f"/api/v1/organizations/{organization}/permissions")
    assert org_permissions.json() == {"organization_admin": False}
    for path in [
        f"/api/v1/workspaces/{workspace}/members",
        f"/api/v1/workspaces/{workspace}/api-keys",
        f"/api/v1/workspaces/{workspace}/security-audit-events",
        f"/api/v1/organizations/{organization}/security-audit-events",
    ]:
        assert (await client.get(path)).status_code == 404
    current = await client.get(f"/api/v1/organizations/{organization}")
    assert current.status_code == 200
    assert (
        await client.patch(
            f"/api/v1/organizations/{organization}",
            json={"name": "Denied"},
            headers={"If-Match": current.headers["etag"]},
        )
    ).status_code == 404
    assert (await client.get("/api/v1/users/me/security-activity")).status_code == 200


@pytest.mark.anyio
async def test_email_change_proof_is_single_use_and_expires(identity_http, recovery_delivery):
    from datetime import timedelta
    from urllib.parse import parse_qs, urlsplit

    from a13n_service.iam.models import EmailChangeRecord
    from a13n_service.storage import transaction
    from a13n_service.temporal import utc_now
    from sqlalchemy import select

    client, runtime, issued = identity_http
    await accept(client, issued)
    body = {"email": "new@example.com", "current_password": "wrong-password"}
    assert (await client.post("/api/v1/users/me/email-change", json=body)).status_code == 401
    body["current_password"] = PASSWORD
    assert (await client.post("/api/v1/users/me/email-change", json=body)).status_code == 202
    assert (await client.get("/api/v1/users/me")).json()["email"] == "admin@example.com"
    token = parse_qs(urlsplit(recovery_delivery[-1][2].splitlines()[2]).fragment)["token"][0]
    assert (await client.post("/api/v1/users/me/email-change/complete", json={"token": token})).status_code == 204
    assert (await client.get("/api/v1/users/me")).json()["email"] == "new@example.com"
    assert (await client.post("/api/v1/users/me/email-change/complete", json={"token": token})).status_code == 400
    body["email"] = "expired@example.com"
    assert (await client.post("/api/v1/users/me/email-change", json=body)).status_code == 202
    token = parse_qs(urlsplit(recovery_delivery[-1][2].splitlines()[2]).fragment)["token"][0]
    async with transaction(runtime.sessions._sessions) as session:
        row = await session.scalar(select(EmailChangeRecord).where(EmailChangeRecord.consumed_at.is_(None)))
        row.expires_at = utc_now() - timedelta(seconds=1)
    assert (await client.post("/api/v1/users/me/email-change/complete", json={"token": token})).status_code == 400
    assert (await client.get("/api/v1/users/me")).json()["email"] == "new@example.com"
