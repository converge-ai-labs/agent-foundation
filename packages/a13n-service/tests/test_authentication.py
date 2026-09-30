"""Credentials over real PostgreSQL/Redis: login sessions, CSRF/Origin, key confinement, re-checks, replacement."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from a13n_service.app import build_app
from a13n_service.distribution import OSS, Distribution
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import etag
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.connections.authorization import flow_cookie, return_url_allowed
from a13n_service.settings import Server, Settings
from a13n_service.tenancy.access import Authenticated, principal_for, reauthenticate, unauthenticated
from a13n_service.tenancy.authenticate import HTTP_COOKIE_NAME, authenticate_secret, login
from a13n_service.tenancy.authorize import WorkspaceScope
from a13n_service.tenancy.requests import current_runtime
from a13n_service.tenancy.tables import ApiKeyRow, GrantRow, OrganizationRow, TokenRow, WorkspaceRow
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

pytestmark = pytest.mark.anyio
EMAIL, PASSWORD = "admin@example.com", "test-password-1234"


def new_client(service, **headers: str) -> httpx2.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test", headers=headers
    )


def if_match(item: dict) -> dict[str, str]:
    return {"if-match": etag(item["id"], item["version"])}


async def test_login_session_cookie_csrf_origin_and_logout(service) -> None:  # type: ignore[no-untyped-def]
    async with new_client(service) as client:
        assert (await client.get("/api/v1/users/me")).status_code == 401
        wrong = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "not-the-password"})
        unknown = await client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
        # Addresses compare case-insensitively.
        response = await client.post("/api/v1/auth/login", json={"email": EMAIL.upper(), "password": PASSWORD})
        assert response.status_code == 200, response.text
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        csrf = response.json()["csrf_token"]
        # A reload holds only the HttpOnly cookie; the token is recovered from it.
        restored = await client.get("/api/v1/auth/session")
        assert restored.status_code == 200 and restored.json()["csrf_token"] == csrf
        assert restored.json()["user"]["email"] == EMAIL
        assert restored.headers["cache-control"] == "no-store"
        body = {"workspace_id": service.tenant.workspace_id, "name": "cli"}
        assert (await client.post("/api/v1/users/me/keys", json=body)).status_code == 403
        cross = await client.post(
            "/api/v1/users/me/keys", json=body, headers={"x-csrf-token": csrf, "origin": "https://attacker.test"}
        )
        assert cross.status_code == 403
        created = await client.post("/api/v1/users/me/keys", json=body, headers={"x-csrf-token": csrf})
        assert created.status_code == 201, created.text
        bearer = {"authorization": "Bearer " + created.json()["secret"]}
        logout = await client.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf})
        assert logout.status_code == 204
        # The request's own session renewal comes first, so the logout's expiry of the cookie is what stays.
        sessions = [cookie for cookie in logout.headers.get_list("set-cookie") if cookie.startswith(HTTP_COOKIE_NAME)]
        assert len(sessions) == 2 and "Max-Age=0" in sessions[-1]
        assert (await client.get("/api/v1/users/me")).status_code == 401
        assert (await client.get("/api/v1/auth/session")).status_code == 401
        # Logout ends only the login session, not a separately issued API key.
        assert (await client.get("/api/v1/users/me", headers=bearer)).status_code == 200
    async with short_session(service.runtime.storage) as session:
        events = (await session.scalars(select(AuditEventRow).where(AuditEventRow.organization_id.is_(None)))).all()
        actions = [event.action for event in events if event.target_kind == "login_session"]
        assert actions.count("login_session.create") == 2 and actions.count("login_session.revoke") == 1
        key = await session.scalar(select(ApiKeyRow))
        assert key is not None and key.secret_hash != created.json()["secret"]


async def test_login_is_rate_limited(service) -> None:  # type: ignore[no-untyped-def]
    # The fixture's own login already used one attempt from this client address.
    async with new_client(service) as client:
        responses = [
            await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "wrong-password"})
            for _ in range(10)
        ]
    assert [response.status_code for response in responses] == [401] * 9 + [429]
    wait = responses[-1].json()["error"]["details"]["retry_after_seconds"]
    assert wait >= 1 and responses[-1].headers["retry-after"] == str(wait)


async def test_every_authenticated_answer_is_uncached_and_renews_the_session(service) -> None:  # type: ignore[no-untyped-def]
    """What authentication sets reaches a route's own response and an error too, not only a serialized result."""
    client = service.client
    created = await client.post(f"{service.api}/memories", json={"name": "notes"})
    memory = created.json()
    deleted = await client.delete(f"{service.api}/memories/{memory['id']}", headers=if_match(memory))
    missing = await client.get(f"{service.api}/connections/conn_missing")
    for response, status in ((created, 201), (deleted, 204), (missing, 404)):
        assert response.status_code == status, response.text
        assert response.headers["cache-control"] == "no-store"
        [renewed] = [
            cookie for cookie in response.headers.get_list("set-cookie") if cookie.startswith(HTTP_COOKIE_NAME)
        ]
        assert "Max-Age=" in renewed and "Max-Age=0" not in renewed


async def test_workspace_key_confinement(service) -> None:  # type: ignore[no-untyped-def]
    client, tenant = service.client, service.tenant
    other = await client.post(f"{service.organization}/workspaces", json={"name": "Other"})
    assert other.status_code == 201, other.text
    other_id = other.json()["id"]
    foreign_org, foreign_ws = new_object_id("org"), new_object_id("ws")
    async with transaction(service.runtime.storage) as session:
        session.add(OrganizationRow(id=foreign_org, name="Foreign"))
        await session.flush()
        session.add(WorkspaceRow(id=foreign_ws, organization_id=foreign_org, name="Foreign"))
        await session.flush()
        session.add(
            GrantRow(
                id=new_object_id("rb"),
                organization_id=foreign_org,
                principal_id=tenant.principal_id,
                role="admin",
                created_by_id=tenant.principal_id,
            )
        )
    key_a = await client.post("/api/v1/users/me/keys", json={"workspace_id": tenant.workspace_id, "name": "a"})
    key_b = await client.post("/api/v1/users/me/keys", json={"workspace_id": other_id, "name": "b"})
    assert key_a.status_code == key_b.status_code == 201
    assert (await client.post("/api/v1/users/me/keys", json={"name": "unscoped"})).status_code == 400
    bearer = {"authorization": "Bearer " + key_a.json()["secret"]}
    assert (await client.get(service.workspace, headers=bearer)).status_code == 200
    assert (await client.get(f"/api/v1/workspaces/{other_id}", headers=bearer)).status_code == 403
    assert (await client.get(f"/api/v1/workspaces/{foreign_ws}", headers=bearer)).status_code == 403
    # A key never issues keys, not even for its own workspace: one would outlive its expiry and revocation.
    for target in (tenant.workspace_id, other_id, foreign_ws):
        minted = await client.post("/api/v1/users/me/keys", headers=bearer, json={"workspace_id": target, "name": "x"})
        assert (
            minted.status_code == 403 and minted.json()["error"]["message"] == "This operation requires a login session"
        )
    organizations = (await client.get("/api/v1/organizations", headers=bearer)).json()["items"]
    assert [(item["id"], item["permissions"]) for item in organizations] == [(tenant.organization_id, ["read"])]
    organization = (await client.get(service.organization)).json()
    assert organization["permissions"] == ["admin", "read", "run", "write"]
    renamed = await client.patch(service.organization, headers={**bearer, **if_match(organization)}, json={"name": "x"})
    assert renamed.status_code == 403
    icon = await client.put(
        f"{service.organization}/icon",
        headers={**bearer, **if_match(organization), "content-type": "image/png"},
        content=b"\x89PNG\r\n\x1a\n",
    )
    assert icon.status_code == 403
    assert (await client.get(f"{service.organization}/members", headers=bearer)).status_code == 403
    [grant] = (await client.get(f"{service.organization}/grants")).json()["items"]
    regranted = await client.patch(
        f"{service.organization}/grants/{grant['id']}", headers=bearer, json={"role": "admin"}
    )
    assert regranted.status_code == 403
    assert (
        await client.post(f"{service.organization}/workspaces", headers=bearer, json={"name": "k"})
    ).status_code == 403
    listed = (await client.get("/api/v1/users/me/keys", headers=bearer)).json()["items"]
    assert {item["workspace_id"] for item in listed} == {tenant.workspace_id}
    assert (
        await client.delete(f"/api/v1/users/me/keys/{key_b.json()['key']['id']}", headers=bearer)
    ).status_code == 404
    password = {"current_password": PASSWORD, "password": "another-password-1234"}
    assert (await client.post("/api/v1/users/me/password", headers=bearer, json=password)).status_code == 403
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    expired = await client.post(
        "/api/v1/users/me/keys", json={"workspace_id": tenant.workspace_id, "name": "old", "expires_at": past}
    )
    assert expired.status_code == 400
    # Using the key records `last_used_at` without changing its version, so the ETag from issuance still applies.
    issued = key_a.json()["key"]
    path = f"/api/v1/users/me/keys/{issued['id']}"
    current = next(
        item for item in (await client.get("/api/v1/users/me/keys")).json()["items"] if item["id"] == issued["id"]
    )
    assert current["last_used_at"] is not None and current["version"] == issued["version"]
    revoked = await client.delete(path, headers=if_match(issued))
    assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None
    assert (await client.delete(path, headers=if_match(revoked.json()))).status_code == 409
    assert (await client.get(service.workspace, headers=bearer)).status_code == 401


async def test_business_requests_act_in_the_credential_workspace(service) -> None:  # type: ignore[no-untyped-def]
    """A login session names the workspace of each business request; an API key acts in its own."""
    tenant, agents = service.tenant, f"{service.api}/agents"
    other = await service.client.post(f"{service.organization}/workspaces", json={"name": "Other"})
    assert other.status_code == 201, other.text
    foreign_org, foreign_ws = new_object_id("org"), new_object_id("ws")
    async with transaction(service.runtime.storage) as session:
        session.add(OrganizationRow(id=foreign_org, name="Foreign"))
        await session.flush()
        session.add(WorkspaceRow(id=foreign_ws, organization_id=foreign_org, name="Foreign"))
    key = await service.client.post("/api/v1/users/me/keys", json={"workspace_id": tenant.workspace_id, "name": "k"})
    assert key.status_code == 201, key.text

    async with new_client(service) as client:
        login = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        client.headers["x-csrf-token"] = login.json()["csrf_token"]
        missing = await client.get(agents)
        assert missing.status_code == 400 and missing.json()["error"]["details"]["field"] == "X-Workspace-ID"
        # Management routes name their workspace in the path instead.
        assert (await client.get(service.workspace)).status_code == 200
        assert (await client.get(agents, headers={"x-workspace-id": other.json()["id"]})).status_code == 200
        # The header takes a workspace ID.
        assert (await client.get(agents, headers={"x-workspace-id": "default"})).status_code == 400
        # A workspace outside the caller's organizations is not found, revealing nothing.
        assert (await client.get(agents, headers={"x-workspace-id": foreign_ws})).status_code == 404

    async with new_client(service, authorization="Bearer " + key.json()["secret"]) as client:
        assert (await client.get(agents)).status_code == 200
        assert (await client.get(agents, headers={"x-workspace-id": tenant.workspace_id})).status_code == 200
        crossed = await client.get(agents, headers={"x-workspace-id": other.json()["id"]})
        assert crossed.status_code == 403 and crossed.json()["error"]["code"] == "forbidden"


async def test_reauthenticate_rechecks_without_touching_credentials(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    storage, access = runtime.storage, runtime.access
    issued = await login(storage, access, email=EMAIL, password=PASSWORD, session_seconds=600)
    session_credential = await authenticate_secret(storage, access, issued.secret, "session", session_seconds=600)
    async with short_session(storage) as session:
        before = (await session.get(TokenRow, session_credential.credential_id)).expires_at  # type: ignore[union-attr]
        assert (await reauthenticate(session, access, session_credential)).id == tenant.principal_id
    async with short_session(storage) as session:
        assert (await session.get(TokenRow, session_credential.credential_id)).expires_at == before  # type: ignore[union-attr]
    key_id = new_object_id("key")
    async with transaction(storage) as session:
        session.add(
            ApiKeyRow(
                id=key_id,
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                principal_id=tenant.principal_id,
                name="stream",
                secret_hash="0" * 64,
                created_by_id=tenant.principal_id,
            )
        )
    workspace = WorkspaceScope(tenant.organization_id, tenant.workspace_id)
    key_credential = Authenticated(replace(session_credential.principal, confinement=workspace), key_id, "key")
    async with short_session(storage) as session:
        assert (await reauthenticate(session, access, key_credential)).confinement == workspace
        assert (await session.get(ApiKeyRow, key_id)).last_used_at is None  # type: ignore[union-attr]
    async with transaction(storage) as session:
        await session.execute(update(TokenRow).values(revoked_at=datetime.now(UTC)))
        # Database time, which the check compares with: the database clock may lag this process's.
        await session.execute(update(ApiKeyRow).values(expires_at=func.clock_timestamp()))
    for credential in (session_credential, key_credential):
        async with short_session(storage) as session:
            with pytest.raises(ServiceError, match="Authentication is required"):
                await reauthenticate(session, access, credential)


async def test_session_expiry_rolls_forward(service) -> None:  # type: ignore[no-untyped-def]
    storage = service.runtime.storage
    async with transaction(storage) as session:
        await session.execute(
            update(TokenRow)
            .where(TokenRow.revoked_at.is_(None))
            .values(expires_at=datetime.now(UTC) + timedelta(seconds=30))
        )
    assert (await service.client.get("/api/v1/users/me")).status_code == 200
    async with short_session(storage) as session:
        token = await session.scalar(select(TokenRow).where(TokenRow.revoked_at.is_(None)))
    assert token is not None and token.expires_at > datetime.now(UTC) + timedelta(hours=1)


class HeaderAuthenticator:
    """A stand-in for SSO: trusts a header naming the principal, as a replacement authenticator would its IdP."""

    def __init__(self) -> None:
        self.ended: set[str] = set()

    async def authenticate(self, request: Request, response: Response) -> Authenticated | None:
        principal_id = request.headers.get("x-test-principal")
        if principal_id is None:
            return None
        runtime = await current_runtime(request)
        async with short_session(runtime.storage) as session:
            principal = await principal_for(session, runtime.access, principal_id)
        return Authenticated(principal, f"sso_{principal_id}", "sso")

    async def recheck(self, session: AsyncSession, credential: Authenticated) -> None:
        if credential.credential_id in self.ended:
            raise unauthenticated()

    async def logout(self, request: Request, response: Response, credential: Authenticated) -> None:
        self.ended.add(credential.credential_id)


async def test_replacement_authenticator_keeps_management(serve) -> None:  # type: ignore[no-untyped-def]
    sso = HeaderAuthenticator()
    async with serve(distribution=OSS.extend(Distribution(name="sso", authenticator=sso))) as service:
        principal_id = service.tenant.principal_id
        async with new_client(service, **{"x-test-principal": principal_id}) as client:
            assert (await client.get("/api/v1/users/me")).json()["id"] == principal_id
            created = await client.post(f"{service.organization}/workspaces", json={"name": "SSO"})
            assert created.status_code == 201, created.text
            grants = await client.get(f"/api/v1/workspaces/{created.json()['id']}/grants")
            assert grants.status_code == 200
            assert (await client.get("/api/v1/auth/session")).status_code == 403
            # Long-lived requests such as thread streams re-check through the installed authenticator.
            access = service.runtime.access
            headers = [(b"x-test-principal", principal_id.encode())]
            live = await access.authenticator.authenticate(
                Request({"type": "http", "app": service.app, "headers": headers}), Response()
            )
            assert live is not None
            async with short_session(service.runtime.storage) as session:
                assert (await reauthenticate(session, access, live)).id == principal_id
            assert (await client.post("/api/v1/auth/logout")).status_code == 204
            assert sso.ended == {f"sso_{principal_id}"}
            async with short_session(service.runtime.storage) as session:
                with pytest.raises(ServiceError, match="Authentication is required"):
                    await reauthenticate(session, access, live)
        async with new_client(service) as anonymous:
            assert (await anonymous.get("/api/v1/users/me")).status_code == 401


async def test_origin_is_checked_against_the_public_url(serve, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    server = settings.server.model_copy(update={"public_url": "https://Console.Example.com:443/a13n"})
    assert server.public_origin == "https://console.example.com"
    async with serve(settings=settings.model_copy(update={"server": server})) as service:
        client, body = service.client, {"workspace_id": service.tenant.workspace_id, "name": "cli"}
        public, own_host = {"origin": "https://console.example.com"}, {"origin": "https://service.test"}
        assert (await client.post("/api/v1/users/me/keys", json=body, headers=public)).status_code == 201
        # The request's own Host is not trusted as the public origin.
        assert (await client.post("/api/v1/users/me/keys", json=body, headers=own_host)).status_code == 403
        credentials = {"email": EMAIL, "password": PASSWORD}
        assert (await client.post("/api/v1/auth/login", json=credentials, headers=own_host)).status_code == 403
        assert (await client.post("/api/v1/auth/login", json=credentials, headers=public)).status_code == 200


@pytest.mark.parametrize(
    ("public_url", "session_cookie", "flow"),
    [
        ("https://a13n.example.com", "__Host-a13n_session", "__Secure-a13n_flow_conn_1"),
        ("http://10.0.0.5:8080", "a13n_session", "a13n_flow_conn_1"),
    ],
)
async def test_cookies_follow_the_public_url_scheme(  # type: ignore[no-untyped-def]
    serve, settings: Settings, public_url: str, session_cookie: str, flow: str
) -> None:
    """HTTPS gets host-bound Secure cookies; plain HTTP, over which browsers refuse those, gets plain ones."""
    config = settings.model_copy(update={"server": settings.server.model_copy(update={"public_url": public_url})})
    assert flow_cookie("conn_1", config) == flow
    async with serve(settings=config) as service, new_client(service) as client:
        login = await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        cookie = login.headers["set-cookie"]
        assert cookie.startswith(f"{session_cookie}=") and ("Secure" in cookie) == public_url.startswith("https:")
        assert (await client.get("/api/v1/auth/session")).status_code == 200


async def test_a_loopback_public_url_accepts_either_loopback_name(service, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    assert settings.server.public_origins == {"http://127.0.0.1:8000", "http://localhost:8000"}
    assert Server(public_url="https://a13n.example.com/").public_origins == {"https://a13n.example.com"}
    assert return_url_allowed("http://localhost:8000/connections", settings)
    body = {"workspace_id": service.tenant.workspace_id, "name": "cli"}
    for origin, status in (
        ("http://localhost:8000", 201),
        ("http://127.0.0.1:8000", 201),
        ("http://localhost:8001", 403),
        ("https://localhost:8000", 403),
    ):
        response = await service.client.post("/api/v1/users/me/keys", json=body, headers={"origin": origin})
        assert response.status_code == status, origin


async def test_the_first_visitor_creates_the_administrator_once(settings: Settings) -> None:
    app = build_app(role="control", settings=settings)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="https://service.test") as client,
    ):
        await app.state.runtime.redis.flushdb()
        assert (await client.get("/api/v1/auth/configuration")).json()["initialized"] is False
        credentials = {"email": EMAIL, "password": "eight-88"}
        short = await client.post("/api/v1/auth/bootstrap", json={"email": EMAIL, "password": "short-7"})
        foreign = await client.post("/api/v1/auth/bootstrap", json=credentials, headers={"origin": "https://x.test"})
        assert (short.status_code, foreign.status_code) == (400, 403)
        created = await client.post("/api/v1/auth/bootstrap", json=credentials)
        assert created.status_code == 200, created.text
        assert created.headers["cache-control"] == "no-store"
        restored = await client.get("/api/v1/auth/session")
        assert restored.json()["csrf_token"] == created.json()["csrf_token"]
        [organization] = (await client.get("/api/v1/organizations")).json()["items"]
        assert organization["name"] == "Default organization"
        assert (await client.get("/api/v1/auth/configuration")).json()["initialized"] is True
        again = await client.post("/api/v1/auth/bootstrap", json={"email": "other@example.com", "password": PASSWORD})
        assert again.status_code == 409 and again.json()["error"]["code"] == "already_exists"
