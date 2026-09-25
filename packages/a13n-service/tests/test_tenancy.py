"""Tenancy management through its HTTP routes over real PostgreSQL/Redis.

Every extra user joins through a real invitation; identity mail is read back from the encrypted outbox.
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace

import anyio
import httpx2
import pytest
from a13n_service.app import build_app
from a13n_service.distribution import OSS, Distribution
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import etag
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbox import Claim, Delivery, Handler, OutboxKind, OutboxRow, settle
from a13n_service.settings import Mail as MailSettings
from a13n_service.settings import Settings
from a13n_service.tenancy import grants as grant_changes
from a13n_service.tenancy.access import OrganizationPath, RoleGrant, lock_organization, principal_for
from a13n_service.tenancy.audit import list_audit_events
from a13n_service.tenancy.authorize import VERBS, Principal, Verb
from a13n_service.tenancy.credentials import issue_link
from a13n_service.tenancy.expiry import expire_credentials
from a13n_service.tenancy.mail import Mail, SmtpMailer, deliver_mail, queue_mail
from a13n_service.tenancy.schemas import WorkspaceCreate
from a13n_service.tenancy.tables import GrantRow, InvitationRow, OrganizationRow, PrincipalRow, TokenRow, WorkspaceRow
from a13n_service.tenancy.users import set_account_status
from a13n_service.tenancy.workspaces import create_workspace
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update

pytestmark = pytest.mark.anyio
ADMIN, PASSWORD = "admin@example.com", "test-password-1234"
MEMBER_PASSWORD = "member-password-1234"


def new_client(service: SimpleNamespace, **headers: str) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test", headers=headers
    )


def if_match(item: dict) -> dict[str, str]:
    return {"if-match": etag(item["id"], item["version"])}


def accept_path(url: str) -> str:
    return "/api/v1/invitations/" + url.split("/invitations/", 1)[1].split("#", 1)[0]


async def accept(client: httpx2.AsyncClient, url: str, password: str, **extra: str) -> httpx2.Response:
    token = url.split("#token=", 1)[1]
    return await client.post(accept_path(url), json={"token": token, "password": password, **extra})


async def join(
    service: SimpleNamespace, stack: AsyncExitStack, email: str, role: str, *, scope: str | None = None
) -> httpx2.AsyncClient:
    """Invite `email` into a scope (the default workspace) and return a client logged in as the new member."""
    receipt = await service.client.post(
        f"{scope or service.workspace}/invitations", json={"email": email, "role": role}
    )
    assert receipt.status_code == 201, receipt.text
    client = await stack.enter_async_context(new_client(service))
    accepted = await accept(client, receipt.json()["invitation_url"], MEMBER_PASSWORD, name=email.split("@")[0])
    assert accepted.status_code == 200, accepted.text
    client.headers["x-csrf-token"] = accepted.json()["csrf_token"]
    return client


async def audit_actions(service: SimpleNamespace, **where: str) -> list[str]:
    async with short_session(service.runtime.storage) as session:
        rows = (
            await session.scalars(select(AuditEventRow).filter_by(**where).order_by(AuditEventRow.occurred_at))
        ).all()
    return [row.action for row in rows]


@pytest.fixture
async def mailing(serve, settings: Settings) -> AsyncIterator[SimpleNamespace]:  # type: ignore[no-untyped-def]
    """The control application with SMTP configured; the port refuses, so identity mail stays in the outbox."""
    mail = MailSettings(smtp_host="127.0.0.1", smtp_port=1, sender="a13n@example.com", timeout=1)
    async with serve(
        settings=settings.model_copy(update={"auth": settings.auth.model_copy(update={"mail": mail})})
    ) as served:
        yield served


async def sent_links(service: SimpleNamespace) -> list[tuple[str, str]]:
    """(recipient, link) of every queued identity mail, decrypted the way the delivery handler does."""
    async with short_session(service.runtime.storage) as session:
        rows = (
            await session.scalars(select(OutboxRow).where(OutboxRow.kind == "email").order_by(OutboxRow.created_at))
        ).all()
    links = []
    for row in rows:
        assert "#token=" not in json.dumps(row.payload) and "#token=" not in json.dumps(row.target)
        location = SecretLocation(row.organization_id, "outbox", "payload", row.id)
        content = json.loads(service.runtime.keys.reveal(Envelope.model_validate(row.payload["content"]), location))
        [url] = re.findall(r"https?://\S+#token=\S+", content["text"])
        links.append((row.target["to"], url))
    return links


async def test_organizations_and_workspaces(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    organizations = (await client.get("/api/v1/organizations")).json()["items"]
    assert [(item["id"], item["permissions"]) for item in organizations] == [
        (service.tenant.organization_id, ["admin", "read", "run", "write"])
    ]
    organization = (await client.get(service.organization)).json()
    assert (await client.patch(service.organization, json={"name": "Acme"})).status_code == 428
    stale = {"if-match": etag(organization["id"], organization["version"] + 1)}
    assert (await client.patch(service.organization, headers=stale, json={"name": "Acme"})).status_code == 412
    renamed = await client.patch(service.organization, headers=if_match(organization), json={"name": "Acme"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Acme"
    assert renamed.json()["version"] == organization["version"] + 1
    created = await client.post(f"{service.organization}/workspaces", json={"key": "research", "name": "Research"})
    assert created.status_code == 201, created.text
    duplicate = await client.post(f"{service.organization}/workspaces", json={"key": "research", "name": "Again"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "already_exists"
    listed = (await client.get(f"{service.organization}/workspaces")).json()["items"]
    assert {item["key"] for item in listed} == {"default", "research"}
    everywhere = (await client.get("/api/v1/workspaces")).json()["items"]
    assert [item["id"] for item in everywhere] == [item["id"] for item in listed]
    by_key = await client.get("/api/v1/workspaces/research")
    assert by_key.status_code == 200 and by_key.json()["permissions"] == ["admin", "read", "run", "write"]
    workspace = by_key.json()
    updated = await client.patch("/api/v1/workspaces/research", headers=if_match(workspace), json={"name": "R&D"})
    assert updated.status_code == 200 and updated.json()["name"] == "R&D"
    archived = await client.post("/api/v1/workspaces/research/archive", headers=if_match(updated.json()))
    assert archived.status_code == 200 and archived.json()["archived_at"] is not None
    assert archived.json()["permissions"] == ["read"]
    refused = await client.patch("/api/v1/workspaces/research", headers=if_match(archived.json()), json={"name": "x"})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "disabled"
    again = await client.post("/api/v1/workspaces/research/archive", headers=if_match(archived.json()))
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "archived"
    events = (await client.get(f"{service.organization}/audit-events")).json()["items"]
    assert {"organization.update", "workspace.create", "workspace.update", "workspace.archive"} <= {
        event["action"] for event in events
    }
    assert all(event["actor"]["email"] == ADMIN for event in events if event["action"] == "workspace.create")


async def test_organization_and_workspace_keys_change(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    organization = (await client.get(service.organization)).json()
    rekeyed = await client.patch(service.organization, headers=if_match(organization), json={"key": "acme"})
    assert rekeyed.status_code == 200 and (rekeyed.json()["key"], rekeyed.json()["name"]) == (
        "acme",
        organization["name"],
    )
    async with transaction(service.runtime.storage) as session:
        session.add(OrganizationRow(id=new_object_id("org"), key="taken", name="Taken"))
    taken = await client.patch(service.organization, headers=if_match(rekeyed.json()), json={"key": "taken"})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "already_exists"
    await client.post(f"{service.organization}/workspaces", json={"key": "lab", "name": "Lab"})
    workspace = (await client.get(service.workspace)).json()
    clash = await client.patch(service.workspace, headers=if_match(workspace), json={"key": "lab"})
    assert clash.status_code == 409 and clash.json()["error"]["details"] == {"kind": "workspace", "key": "lab"}
    invalid = await client.patch(service.workspace, headers=if_match(workspace), json={"key": "Not a key"})
    assert invalid.status_code == 400
    moved = await client.patch(service.workspace, headers=if_match(workspace), json={"key": "main"})
    assert moved.status_code == 200 and (moved.json()["key"], moved.json()["name"]) == ("main", workspace["name"])
    # Paths by key follow the change; the workspace ID, which everything else references, stays.
    assert (await client.get("/api/v1/workspaces/main")).json()["id"] == workspace["id"]
    assert (await client.get("/api/v1/workspaces/default")).status_code == 404
    events = (await client.get(f"{service.organization}/audit-events")).json()["items"]
    changes = [(event["action"], event["details"]) for event in events if event["action"].endswith(".update")]
    assert changes == [("workspace.update", {"fields": ["key"]}), ("organization.update", {"fields": ["key"]})]


async def test_grants_expand_principals_and_keep_an_organization_admin(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    async with AsyncExitStack() as stack:
        viewer = await join(service, stack, "viewer@example.com", "viewer")
        members = (await client.get(f"{service.workspace}/grants")).json()["items"]
        assert [(m["principal"]["email"], m["role"]) for m in members] == [("viewer@example.com", "viewer")]
        viewer_id = members[0]["principal"]["id"]
        assert (await viewer.get(service.workspace)).json()["permissions"] == ["read"]
        await client.post(f"{service.organization}/workspaces", json={"key": "private", "name": "Private"})
        visible = (await viewer.get("/api/v1/workspaces")).json()["items"]
        assert [item["id"] for item in visible] == [service.tenant.workspace_id]
        # A viewer cannot administer: the denial is recorded after the rejected read.
        assert (await viewer.get(f"{service.workspace}/grants")).status_code == 403
        denied = await audit_actions(service, outcome="denied")
        assert denied == ["grant.list"]
        body = {"principal_id": viewer_id, "role": "admin"}
        promoted = await client.post(f"{service.organization}/grants", json=body)
        assert promoted.status_code == 201 and promoted.json()["principal"]["name"] == "viewer"
        assert (await client.post(f"{service.organization}/grants", json=body)).status_code == 409
        assert (await viewer.get(service.workspace)).json()["permissions"] == ["admin", "read", "run", "write"]
        unknown = await client.post(f"{service.workspace}/grants", json={"principal_id": viewer_id, "role": "owner"})
        assert unknown.status_code == 400
        organization_grants = (await client.get(f"{service.organization}/grants")).json()["items"]
        mine = next(g for g in organization_grants if g["principal"]["id"] == service.tenant.principal_id)
        theirs = promoted.json()
        assert (await client.delete(f"{service.organization}/grants/{theirs['id']}")).status_code == 204
        last = await client.delete(f"{service.organization}/grants/{mine['id']}")
        assert last.status_code == 409 and last.json()["error"]["details"]["reason"] == "last_organization_admin"
        # A grant is removed only through the scope it belongs to.
        assert (await client.delete(f"{service.workspace}/grants/{mine['id']}")).status_code == 404


async def test_role_changes_replace_the_grant(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    async with AsyncExitStack() as stack:
        member = await join(service, stack, "member@example.com", "viewer")
        [held] = (await client.get(f"{service.workspace}/grants")).json()["items"]
        path = f"{service.workspace}/grants/{held['id']}"
        assert (await member.patch(path, json={"role": "admin"})).status_code == 403
        assert (await client.patch(path, json={"role": "owner"})).status_code == 400
        changed = await client.patch(path, json={"role": "builder"})
        assert changed.status_code == 200, changed.text
        replacement = changed.json()
        assert replacement["id"] != held["id"]
        assert (replacement["role"], replacement["principal"]) == ("builder", held["principal"])
        assert (await member.get(service.workspace)).json()["permissions"] == ["read", "run", "write"]
        # The replaced grant is gone, so a change based on it is refused rather than applied twice.
        assert (await client.patch(path, json={"role": "admin"})).status_code == 404
        unchanged = await client.patch(f"{service.workspace}/grants/{replacement['id']}", json={"role": "builder"})
        assert unchanged.json()["id"] == replacement["id"]
        # A grant changes only through the scope it belongs to.
        elsewhere = f"{service.organization}/grants/{replacement['id']}"
        assert (await client.patch(elsewhere, json={"role": "viewer"})).status_code == 404
    async with short_session(service.runtime.storage) as session:
        created = await session.scalar(
            select(AuditEventRow).where(
                AuditEventRow.action == "grant.create", AuditEventRow.target_id == replacement["id"]
            )
        )
    assert created is not None and created.details["replaces"] == held["id"]

    # A service account's grant is replaced within one transaction: it is never retired and keeps its keys.
    account = (await client.post(f"{service.workspace}/service-accounts", json={"name": "ci"})).json()
    key = await client.post(f"{service.workspace}/service-accounts/{account['id']}/keys", json={"name": "k"})
    bearer = {"authorization": "Bearer " + key.json()["secret"]}
    [grant] = [
        g
        for g in (await client.get(f"{service.workspace}/grants")).json()["items"]
        if g["principal"]["id"] == account["id"]
    ]
    assert (
        await client.patch(f"{service.workspace}/grants/{grant['id']}", json={"role": "builder"})
    ).status_code == 200
    assert (await client.get(service.workspace, headers=bearer)).json()["permissions"] == ["read", "run", "write"]
    assert (await client.get(f"{service.workspace}/service-accounts/{account['id']}")).json()["role"] == "builder"

    [mine] = (await client.get(f"{service.organization}/grants")).json()["items"]
    demoted = await client.patch(f"{service.organization}/grants/{mine['id']}", json={"role": "viewer"})
    assert demoted.status_code == 409 and demoted.json()["error"]["details"]["reason"] == "last_organization_admin"


async def test_organization_members(service) -> None:  # type: ignore[no-untyped-def]
    client, members = service.client, f"{service.organization}/members"
    async with AsyncExitStack() as stack:
        viewer = await join(service, stack, "viewer@example.com", "viewer")
        deputy = await join(service, stack, "deputy@example.com", "admin")
        account = (await client.post(f"{service.workspace}/service-accounts", json={"name": "ci"})).json()
        listed = (await client.get(members)).json()
        assert {(item["kind"], item["email"]) for item in listed["items"]} == {
            ("user", ADMIN),
            ("user", "viewer@example.com"),
            ("user", "deputy@example.com"),
            ("service_account", None),
        }
        users = (await client.get(members, params={"kind": "user"})).json()["items"]
        assert [item["kind"] for item in users] == ["user"] * 3
        first = (await client.get(members, params={"limit": 2})).json()
        rest = (await client.get(members, params={"limit": 2, "cursor": first["next_cursor"]})).json()
        assert [item["id"] for item in first["items"] + rest["items"]] == [item["id"] for item in listed["items"]]
        # The directory names everyone in the organization, so only its administrators may read it.
        assert (await deputy.get(members)).status_code == 403
        assert (await viewer.get(members)).status_code == 403
        # A user without grants has left; a retired service account stays a member of its home workspace.
        grants = (await client.get(f"{service.workspace}/grants")).json()["items"]
        for grant in grants:
            assert (await client.delete(f"{service.workspace}/grants/{grant['id']}")).status_code == 204
        remaining = (await client.get(members)).json()["items"]
        assert {(item["id"], item["status"]) for item in remaining} == {
            (service.tenant.principal_id, "active"),
            (account["id"], "disabled"),
        }


async def test_custom_roles_and_grant_sources(serve, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="Duplicate role"):
        OSS.extend(Distribution(name="x", roles={"admin": frozenset({"read"})}))
    with pytest.raises(ValueError, match="Invalid role"):
        OSS.extend(Distribution(name="x", roles={"Bad Name": frozenset({"read"})})).access()

    class Directory:
        """A distribution grant source answering from its own cache."""

        workspace: tuple[str, str] | None = None

        async def grants_for(self, principal: Principal) -> Sequence[RoleGrant]:
            if self.workspace is None or principal.email != "sso@x.io":
                return []
            return [RoleGrant(*self.workspace, "operator")]

    directory = Directory()
    roles: dict[str, frozenset[Verb]] = {"operator": frozenset({"read", "run"})}
    extended = OSS.extend(Distribution(name="ext", roles=roles, grant_sources=(directory,)))
    async with serve(distribution=extended) as service, AsyncExitStack() as stack:
        directory.workspace = (service.tenant.organization_id, service.tenant.workspace_id)
        member = await join(service, stack, "member@example.com", "operator")
        assert (await member.get(service.workspace)).json()["permissions"] == ["read", "run"]
        sso = await join(service, stack, "sso@x.io", "viewer")
        assert (await sso.get(service.workspace)).json()["permissions"] == ["read", "run"]
    # The same database served without that role: the stored grant naming it fails closed.
    app = build_app(OSS, role="control", settings=settings)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="https://service.test") as client,
    ):
        credentials = {"email": "member@example.com", "password": MEMBER_PASSWORD}
        refused = await client.post("/api/v1/auth/login", json=credentials)
        assert refused.status_code == 403 and "unknown role" in refused.json()["error"]["message"]


async def test_audit_pages_and_denials(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    for name in ("one", "two"):
        await client.post(f"{service.organization}/workspaces", json={"key": name, "name": name})
    key = await client.post("/api/v1/users/me/keys", json={"workspace_id": service.tenant.workspace_id, "name": "k"})
    bearer = {"authorization": "Bearer " + key.json()["secret"]}
    first = await client.get(f"{service.organization}/audit-events", params={"limit": 1})
    assert first.status_code == 200 and len(first.json()["items"]) == 1
    cursor = first.json()["next_cursor"]
    second = await client.get(f"{service.organization}/audit-events", params={"limit": 1, "cursor": cursor})
    assert second.json()["items"][0]["id"] != first.json()["items"][0]["id"]
    assert (await client.get(f"{service.workspace}/audit-events", params={"cursor": cursor})).status_code == 400
    workspace_events = (await client.get(f"{service.workspace}/audit-events")).json()["items"]
    assert [event["action"] for event in workspace_events] == ["credential.create", "organization.bootstrap"]
    assert {event["workspace_id"] for event in workspace_events} == {service.tenant.workspace_id}
    assert (await client.get(f"{service.workspace}/audit-events", headers=bearer)).status_code == 200
    # A workspace key never administers its organization, even for an organization administrator.
    assert (await client.get(f"{service.organization}/audit-events", headers=bearer)).status_code == 403
    async with short_session(service.runtime.storage) as session:
        denial = await session.scalar(select(AuditEventRow).where(AuditEventRow.outcome == "denied"))
    assert denial is not None and (denial.action, denial.target_kind) == ("audit.read", "organization")
    assert denial.details == {"verb": "admin", "credential_workspace_id": service.tenant.workspace_id}


async def test_account_trail_shows_only_the_callers_events(service) -> None:  # type: ignore[no-untyped-def]
    client, trail = service.client, "/api/v1/users/me/audit-events"
    admin_id = service.tenant.principal_id
    profile = (await client.get("/api/v1/users/me")).json()
    assert (await client.patch("/api/v1/users/me", headers=if_match(profile), json={"name": "Ada"})).status_code == 200
    async with AsyncExitStack() as stack:
        member = await join(service, stack, "member@example.com", "viewer")
        member_id = (await member.get("/api/v1/users/me")).json()["id"]
        # The operator's switch has no actor, yet it belongs to the member's own trail. Disabling ended the
        # member's login session, so they sign in again.
        await set_account_status(service.runtime.storage, "member@example.com", "disabled")
        await set_account_status(service.runtime.storage, "member@example.com", "active")
        relogin = await member.post(
            "/api/v1/auth/login", json={"email": "member@example.com", "password": MEMBER_PASSWORD}
        )
        member.headers["x-csrf-token"] = relogin.json()["csrf_token"]
        theirs = (await member.get(trail)).json()["items"]
        key = await member.post(
            "/api/v1/users/me/keys", json={"workspace_id": service.tenant.workspace_id, "name": "k"}
        )
        # The trail spans every tenant the account acted in, so a workspace key cannot read it.
        assert (await member.get(trail, headers={"authorization": "Bearer " + key.json()["secret"]})).status_code == 403
    assert [(event["action"], event["actor_id"]) for event in theirs[:3]] == [
        ("login_session.create", member_id),
        ("user.enable", None),
        ("user.disable", None),
    ]
    assert {event["organization_id"] for event in theirs[:3]} == {None}
    assert "grant.create" in {event["action"] for event in theirs}
    assert all(event["actor_id"] == member_id for event in theirs[3:])
    mine = (await client.get(trail)).json()["items"]
    assert all(event["actor_id"] == admin_id for event in mine)
    # The caller's own profile change is both theirs and about their account, and appears once.
    assert len({event["id"] for event in mine}) == len(mine)
    assert [event["action"] for event in mine].count("user.update") == 1
    assert {"user.update", "invitation.create", "organization.bootstrap"} <= {event["action"] for event in mine}
    assert "credential.create" not in {event["action"] for event in mine}
    first = (await client.get(trail, params={"limit": 1})).json()
    second = (await client.get(trail, params={"limit": 1, "cursor": first["next_cursor"]})).json()
    assert [event["id"] for event in first["items"] + second["items"]] == [event["id"] for event in mine[:2]]
    assert (
        await client.get(f"{service.workspace}/audit-events", params={"cursor": first["next_cursor"]})
    ).status_code == 400


async def test_invitations_with_manual_links(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    created = await client.post(
        f"{service.workspace}/invitations", json={"email": "New@Example.com", "role": "builder"}
    )
    assert created.status_code == 201, created.text
    receipt = created.json()
    assert receipt["delivery"] == "manual" and receipt["invitation"]["email"] == "new@example.com"
    duplicate = await client.post(
        f"{service.workspace}/invitations", json={"email": "new@example.com", "role": "viewer"}
    )
    assert duplicate.status_code == 409
    listed = (await client.get(f"{service.workspace}/invitations")).json()["items"]
    assert [item["id"] for item in listed] == [receipt["invitation"]["id"]]
    resent = await client.post(
        f"{service.workspace}/invitations/{receipt['invitation']['id']}/resend", headers=if_match(receipt["invitation"])
    )
    assert resent.status_code == 200 and resent.json()["invitation_url"] != receipt["invitation_url"]
    async with new_client(service) as invitee:
        assert (await accept(invitee, receipt["invitation_url"], MEMBER_PASSWORD)).status_code == 404
        assert (await accept(invitee, resent.json()["invitation_url"], "short")).status_code == 400
        accepted = await accept(invitee, resent.json()["invitation_url"], MEMBER_PASSWORD)
        assert accepted.status_code == 200 and "a13n_session=" in accepted.headers["set-cookie"]
        me = (await invitee.get("/api/v1/users/me")).json()
        assert (me["email"], me["name"]) == ("new@example.com", "new")
        assert (await invitee.get(service.workspace)).json()["permissions"] == ["read", "run", "write"]
        replayed = await accept(invitee, resent.json()["invitation_url"], MEMBER_PASSWORD)
        assert replayed.status_code == 409 and replayed.json()["error"]["details"]["reason"] == "accepted"
    invitation = (await client.get(f"{service.workspace}/invitations")).json()["items"][0]
    assert invitation["principal_id"] == me["id"] and invitation["accepted_at"] is not None
    revoked_accepted = await client.post(
        f"{service.workspace}/invitations/{invitation['id']}/revoke", headers=if_match(invitation)
    )
    assert revoked_accepted.status_code == 409

    # An existing user joins another scope by proving their own password; the invited role replaces theirs.
    upgrade = (
        await client.post(f"{service.organization}/invitations", json={"email": "new@example.com", "role": "admin"})
    ).json()
    async with new_client(service) as invitee:
        assert (await accept(invitee, upgrade["invitation_url"], "wrong-password-123")).status_code == 401
        assert (await accept(invitee, upgrade["invitation_url"], MEMBER_PASSWORD)).status_code == 200
        assert (await invitee.get(service.organization)).json()["permissions"] == ["admin", "read", "run", "write"]

    expired = (
        await client.post(f"{service.workspace}/invitations", json={"email": "late@example.com", "role": "viewer"})
    ).json()
    revoked = (
        await client.post(f"{service.workspace}/invitations", json={"email": "gone@example.com", "role": "viewer"})
    ).json()
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(InvitationRow)
            .where(InvitationRow.id == expired["invitation"]["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    withdrawn = await client.post(
        f"{service.workspace}/invitations/{revoked['invitation']['id']}/revoke", headers=if_match(revoked["invitation"])
    )
    assert withdrawn.status_code == 200 and withdrawn.json()["revoked_at"] is not None
    async with new_client(service) as invitee:
        late = await accept(invitee, expired["invitation_url"], MEMBER_PASSWORD)
        gone = await accept(invitee, revoked["invitation_url"], MEMBER_PASSWORD)
    assert late.json()["error"]["details"]["reason"] == "expired"
    assert gone.json()["error"]["details"]["reason"] == "revoked"


async def test_only_a_login_session_sends_invitations(service) -> None:  # type: ignore[no-untyped-def]
    """The login an accepted invitation starts would outlive the API key that sent it, as a key-minted key would;
    every operation reserved for a login session refuses a key the same way."""
    client = service.client
    issued = await client.post("/api/v1/users/me/keys", json={"workspace_id": service.tenant.workspace_id, "name": "k"})
    bearer = {"authorization": "Bearer " + issued.json()["secret"]}
    body = {"email": "key@example.com", "role": "admin"}
    created = await client.post(f"{service.workspace}/invitations", headers=bearer, json=body)
    assert (created.status_code, created.json()["error"]["message"]) == (403, "This operation requires a login session")
    assert (await client.get(f"{service.workspace}/invitations")).json()["items"] == []
    invitation = (await client.post(f"{service.workspace}/invitations", json=body)).json()["invitation"]
    resent = await client.post(
        f"{service.workspace}/invitations/{invitation['id']}/resend", headers={**bearer, **if_match(invitation)}
    )
    assert resent.status_code == 403
    for refused in (
        await client.get("/api/v1/auth/session", headers=bearer),
        await client.post("/api/v1/auth/logout", headers=bearer),
    ):
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden")
    assert (await client.get("/api/v1/auth/session")).status_code == 200


async def test_identity_mail_is_queued_encrypted(mailing) -> None:  # type: ignore[no-untyped-def]
    client = mailing.client
    assert (await client.get("/api/v1/auth/configuration")).json() == {"email_delivery": True, "initialized": True}
    receipt = await client.post(
        f"{mailing.workspace}/invitations", json={"email": "mail@example.com", "role": "runner"}
    )
    assert receipt.json()["delivery"] == "queued" and receipt.json()["invitation_url"] is None
    [(to, url)] = await sent_links(mailing)
    assert to == "mail@example.com"
    async with new_client(mailing) as invitee, new_client(mailing) as anonymous:
        accepted = await accept(invitee, url, MEMBER_PASSWORD)
        assert accepted.status_code == 200
        invitee.headers["x-csrf-token"] = accepted.json()["csrf_token"]
        profile = (await invitee.get("/api/v1/users/me")).json()
        moving = {"email": "moved@example.com", "current_password": MEMBER_PASSWORD}
        assert (await invitee.patch("/api/v1/users/me", headers=if_match(profile), json=moving)).status_code == 200
        unknown = await anonymous.post("/api/v1/auth/password-reset", json={"email": "nobody@example.com"})
        known = await anonymous.post("/api/v1/auth/password-reset", json={"email": "mail@example.com"})
        assert unknown.status_code == known.status_code == 204
        [_, (_, move), (to, reset)] = await sent_links(mailing)
        assert "/confirm-email#token=" in move
        assert to == "mail@example.com" and "/reset-password#token=" in reset
        confirm = {"token": reset.split("#token=", 1)[1], "password": "renewed-password-1234"}
        assert (await anonymous.post("/api/v1/auth/password-reset/confirm", json=confirm)).status_code == 204
        assert (await anonymous.post("/api/v1/auth/password-reset/confirm", json=confirm)).status_code == 400
        # The reset ended every login session and outstanding link of the account, including the session
        # acceptance started and the pending email change.
        assert (await invitee.get("/api/v1/users/me")).status_code == 401
        moved = await anonymous.post("/api/v1/auth/email-change/confirm", json={"token": move.split("#token=", 1)[1]})
        assert moved.status_code == 400
        relogin = await anonymous.post(
            "/api/v1/auth/login", json={"email": "mail@example.com", "password": "renewed-password-1234"}
        )
        assert relogin.status_code == 200

    profile = (await client.get("/api/v1/users/me")).json()
    missing = await client.patch("/api/v1/users/me", headers=if_match(profile), json={"email": "boss@example.com"})
    assert missing.status_code == 400 and missing.json()["error"]["details"]["field"] == "current_password"
    # Whether an address is taken is decided at confirmation, so requesting a change reveals nothing.
    taken = await client.patch(
        "/api/v1/users/me", headers=if_match(profile), json={"email": "mail@example.com", "current_password": PASSWORD}
    )
    assert taken.status_code == 200
    (to, taken_url) = (await sent_links(mailing))[-1]
    assert to == "mail@example.com"
    refused = await client.post("/api/v1/auth/email-change/confirm", json={"token": taken_url.split("#token=", 1)[1]})
    assert refused.status_code == 409
    changed = await client.patch(
        "/api/v1/users/me", headers=if_match(profile), json={"email": "boss@example.com", "current_password": PASSWORD}
    )
    assert changed.status_code == 200 and changed.json()["email"] == ADMIN
    (to, confirm_url) = (await sent_links(mailing))[-1]
    assert to == "boss@example.com" and "/confirm-email#token=" in confirm_url
    token = {"token": confirm_url.split("#token=", 1)[1]}
    assert (await client.post("/api/v1/auth/email-change/confirm", json=token)).status_code == 204
    # The change ended every login session of the account; the new address signs in.
    assert (await client.get("/api/v1/users/me")).status_code == 401
    relogin = await client.post("/api/v1/auth/login", json={"email": "boss@example.com", "password": PASSWORD})
    assert relogin.status_code == 200


def test_mail_requires_an_encryption_key() -> None:
    mail = {"smtp_host": "127.0.0.1", "sender": "a13n@example.com"}
    with pytest.raises(ValidationError, match="requires an encryption key"):
        Settings.model_validate({"auth": {"mail": mail}})
    for encryption in ({"active_key_id": "k", "keys": {"k": "a" * 44}}, {"key_file": "/app/var/encryption.key"}):
        assert Settings.model_validate({"auth": {"mail": mail}, "encryption": encryption}).auth.mail.smtp_host


async def test_mail_delivery_settles_and_dead_letters(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    config = MailSettings(smtp_host="127.0.0.1", sender="a13n@example.com")
    async with transaction(runtime.storage) as session:
        for purpose in ("sent", "failing"):
            queue_mail(
                session,
                runtime.keys,
                config,
                Mail("to@example.com", purpose, f"body of {purpose}"),
                organization_id=tenant.organization_id,
                workspace_id=None,
                purpose=purpose,
            )

    class Recorder:
        def __init__(self) -> None:
            self.sent: list[Mail] = []

        async def send(self, mail: Mail) -> None:
            if mail.subject == "failing":
                raise OSError("refused")
            self.sent.append(mail)

    recorder = Recorder()
    handlers: dict[OutboxKind, Handler] = {"email": partial(deliver_mail, runtime.storage, runtime.keys, recorder)}
    await Delivery(runtime.storage, handlers, owner="test", limit=10, lease_seconds=60, max_attempts=1)()
    assert [mail.text for mail in recorder.sent] == ["body of sent"]
    async with short_session(runtime.storage) as session:
        rows = (await session.scalars(select(OutboxRow))).all()
    assert {row.target["purpose"]: (row.status, row.last_error) for row in rows} == {
        "sent": ("delivered", None),
        "failing": ("dead", "OSError"),
    }


async def test_service_accounts_and_workspace_keys(service) -> None:  # type: ignore[no-untyped-def]
    client, accounts = service.client, f"{service.workspace}/service-accounts"
    created = await client.post(accounts, json={"name": "ci"})
    assert created.status_code == 201 and created.json()["role"] == "runner"
    account = created.json()
    path = f"{accounts}/{account['id']}"
    key = await client.post(f"{path}/keys", json={"name": "deploy"})
    assert key.status_code == 201
    bearer = {"authorization": "Bearer " + key.json()["secret"]}
    me = (await client.get("/api/v1/users/me", headers=bearer)).json()
    assert (me["kind"], me["email"]) == ("service_account", None)
    assert (await client.get(service.workspace, headers=bearer)).json()["permissions"] == ["read", "run"]
    assert (await client.post(accounts, headers=bearer, json={"name": "escalate"})).status_code == 403
    other = await client.post(f"{service.organization}/workspaces", json={"key": "other", "name": "Other"})
    outside = await client.post(
        f"/api/v1/workspaces/{other.json()['id']}/grants", json={"principal_id": account["id"], "role": "viewer"}
    )
    assert outside.status_code == 400
    assert (
        await client.post(
            f"/api/v1/workspaces/{other.json()['id']}/service-accounts/{account['id']}/keys", json={"name": "x"}
        )
    ).status_code == 404

    promoted = await client.patch(path, headers=if_match(account), json={"role": "builder", "name": "ci-bot"})
    assert promoted.status_code == 200 and (promoted.json()["role"], promoted.json()["name"]) == ("builder", "ci-bot")
    assert (await client.get(service.workspace, headers=bearer)).json()["permissions"] == ["read", "run", "write"]
    paused = await client.patch(path, headers=if_match(promoted.json()), json={"status": "disabled"})
    assert paused.status_code == 200 and (await client.get(service.workspace, headers=bearer)).status_code == 401
    resumed = await client.patch(path, headers=if_match(paused.json()), json={"status": "active"})
    assert resumed.status_code == 200 and (await client.get(service.workspace, headers=bearer)).status_code == 200

    personal = await client.post(
        "/api/v1/users/me/keys", json={"workspace_id": service.tenant.workspace_id, "name": "me"}
    )
    # An API key never issues keys, not even an administrator's for a service account of its own workspace.
    for credential in (personal.json()["secret"], key.json()["secret"]):
        minted = await client.post(
            f"{path}/keys", headers={"authorization": "Bearer " + credential}, json={"name": "x"}
        )
        assert (minted.status_code, minted.json()["error"]["message"]) == (
            403,
            "This operation requires a login session",
        )
    keys = (await client.get(f"{service.workspace}/keys")).json()["items"]
    assert {(item["principal"]["kind"], item["name"]) for item in keys} == {
        ("service_account", "deploy"),
        ("user", "me"),
    }
    assert [item["name"] for item in (await client.get(f"{path}/keys")).json()["items"]] == ["deploy"]
    revoked = await client.delete(
        f"{service.workspace}/keys/{personal.json()['key']['id']}", headers=if_match(personal.json()["key"])
    )
    assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None

    # Removing the last grant retires the account: disabled, keys revoked, identity kept.
    [grant] = [
        g
        for g in (await client.get(f"{service.workspace}/grants")).json()["items"]
        if g["principal"]["id"] == account["id"]
    ]
    assert (await client.delete(f"{service.workspace}/grants/{grant['id']}")).status_code == 204
    retired = (await client.get(path)).json()
    assert (retired["status"], retired["role"]) == ("disabled", None)
    assert (await client.get(service.workspace, headers=bearer)).status_code == 401
    assert (await client.patch(path, headers=if_match(retired), json={"status": "active"})).status_code == 409
    restored = await client.patch(path, headers=if_match(retired), json={"role": "runner", "status": "active"})
    assert restored.status_code == 200 and restored.json()["status"] == "active"
    fresh = await client.post(f"{path}/keys", json={"name": "again"})
    deleted = await client.delete(path, headers=if_match(restored.json()))
    assert deleted.status_code == 200 and (deleted.json()["status"], deleted.json()["role"]) == ("disabled", None)
    assert (
        await client.get(service.workspace, headers={"authorization": "Bearer " + fresh.json()["secret"]})
    ).status_code == 401
    listed = (await client.get(accounts)).json()["items"]
    assert [(item["id"], item["status"]) for item in listed] == [(account["id"], "disabled")]
    assert "service_account.disable" in await audit_actions(service, target_id=account["id"])


async def test_service_account_description(service) -> None:  # type: ignore[no-untyped-def]
    client, accounts = service.client, f"{service.workspace}/service-accounts"
    described = await client.post(accounts, json={"name": "ci", "description": "Deploys from CI"})
    assert described.status_code == 201 and described.json()["description"] == "Deploys from CI"
    assert (await client.post(accounts, json={"name": "bot"})).json()["description"] == ""
    path = f"{accounts}/{described.json()['id']}"
    assert (
        await client.patch(path, headers=if_match(described.json()), json={"description": "x" * 2049})
    ).status_code == 400
    cleared = await client.patch(path, headers=if_match(described.json()), json={"description": ""})
    assert cleared.status_code == 200 and cleared.json()["description"] == ""
    assert (await client.get(accounts)).json()["items"][0]["description"] == ""
    assert "service_account.update" in await audit_actions(service, target_id=described.json()["id"])


async def test_archived_workspaces_allow_only_offboarding(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    lab = (await client.post(f"{service.organization}/workspaces", json={"key": "lab", "name": "Lab"})).json()
    path = f"/api/v1/workspaces/{lab['id']}"
    async with AsyncExitStack() as stack:
        member = await join(service, stack, "member@example.com", "builder", scope=path)
        disabling = (await client.post(f"{path}/service-accounts", json={"name": "ci"})).json()
        retiring = (await client.post(f"{path}/service-accounts", json={"name": "bot"})).json()
        [grant] = [g for g in (await client.get(f"{path}/grants")).json()["items"] if g["principal"]["kind"] == "user"]
        assert (await client.post(f"{path}/archive", headers=if_match(lab))).status_code == 200
        # Issuing a key is a change, which the archived workspace refuses for users and service accounts alike.
        for issued in (
            await member.post("/api/v1/users/me/keys", json={"workspace_id": lab["id"], "name": "k"}),
            await client.post(f"{path}/service-accounts/{disabling['id']}/keys", json={"name": "k"}),
        ):
            assert issued.status_code == 422 and issued.json()["error"]["code"] == "disabled"
        account = f"{path}/service-accounts/{disabling['id']}"
        assert (await client.patch(account, headers=if_match(disabling), json={"name": "x"})).status_code == 422
        assert (await client.patch(f"{path}/grants/{grant['id']}", json={"role": "viewer"})).status_code == 422
        # Removing access is offboarding, like revoking a key, and stays possible.
        paused = await client.patch(account, headers=if_match(disabling), json={"status": "disabled"})
        assert paused.status_code == 200 and paused.json()["status"] == "disabled"
        retired = await client.delete(f"{path}/service-accounts/{retiring['id']}", headers=if_match(retiring))
        assert retired.status_code == 200 and retired.json()["status"] == "disabled"
        assert (await client.delete(f"{path}/grants/{grant['id']}")).status_code == 204
        assert (await member.get(path)).status_code == 404


async def test_profile_password_and_login_sessions(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    profile = await client.get("/api/v1/users/me")
    assert profile.headers["etag"] == etag(profile.json()["id"], profile.json()["version"])
    renamed = await client.patch("/api/v1/users/me", headers=if_match(profile.json()), json={"name": "Ada"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Ada"
    unavailable = await client.patch(
        "/api/v1/users/me",
        headers=if_match(renamed.json()),
        json={"email": "x@example.com", "current_password": PASSWORD},
    )
    assert unavailable.status_code == 503
    assert (await client.get("/api/v1/auth/configuration")).json() == {"email_delivery": False, "initialized": True}
    async with new_client(service) as anonymous:
        assert (await anonymous.post("/api/v1/auth/password-reset", json={"email": ADMIN})).status_code == 204
    async with new_client(service) as other:
        login = await other.post("/api/v1/auth/login", json={"email": ADMIN, "password": PASSWORD})
        other.headers["x-csrf-token"] = login.json()["csrf_token"]
        sessions = (await client.get("/api/v1/users/me/login-sessions")).json()["items"]
        assert len(sessions) == 2 and sum(item["current"] for item in sessions) == 1
        wrong = {"current_password": "not-my-password", "password": "brand-new-password-1"}
        assert (await client.post("/api/v1/users/me/password", json=wrong)).status_code == 400
        changed = {"current_password": PASSWORD, "password": "brand-new-password-1"}
        assert (await client.post("/api/v1/users/me/password", json=changed)).status_code == 204
        # Changing the password ends every other login session and keeps this one.
        assert (await other.get("/api/v1/users/me")).status_code == 401
        [current] = (await client.get("/api/v1/users/me/login-sessions")).json()["items"]
        assert current["current"] is True
        assert (await client.delete(f"/api/v1/users/me/login-sessions/{current['id']}")).status_code == 204
        assert (await client.get("/api/v1/users/me")).status_code == 401
    assert await audit_actions(service, target_kind="user") == ["user.update", "user.password.change"]


async def test_expire_credentials_keeps_history(service) -> None:  # type: ignore[no-untyped-def]
    storage, client = service.runtime.storage, service.client
    await client.post(f"{service.workspace}/invitations", json={"email": "stale@example.com", "role": "viewer"})
    async with AsyncExitStack() as stack:
        await join(service, stack, "kept@example.com", "viewer")
    past = datetime.now(UTC) - timedelta(seconds=1)
    async with transaction(storage) as session:
        await session.execute(update(InvitationRow).where(InvitationRow.accepted_at.is_(None)).values(expires_at=past))
        await session.execute(
            update(TokenRow).where(TokenRow.principal_id != service.tenant.principal_id).values(expires_at=past)
        )
        session.add(
            TokenRow(
                id=new_object_id("prt"),
                principal_id=service.tenant.principal_id,
                kind="password_reset",
                secret_hash="1" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                revoked_at=past,
            )
        )
    assert await expire_credentials(storage, limit=100) == 3
    async with short_session(storage) as session:
        assert [row.email for row in (await session.scalars(select(InvitationRow))).all()] == ["kept@example.com"]
        tokens = (await session.scalars(select(TokenRow))).all()
        assert [(token.principal_id, token.kind) for token in tokens] == [(service.tenant.principal_id, "session")]
    assert await expire_credentials(storage, limit=100) == 0
    assert (await client.get("/api/v1/users/me")).status_code == 200


async def test_paths_resolve_only_within_membership(service) -> None:  # type: ignore[no-untyped-def]
    client, tenant = service.client, service.tenant
    second, foreign = new_object_id("org"), new_object_id("org")
    shared, closed, hidden = new_object_id("ws"), new_object_id("ws"), new_object_id("ws")
    outsider = new_object_id("usr")
    async with transaction(service.runtime.storage) as session:
        session.add_all(
            [
                OrganizationRow(id=second, key="second", name="Second"),
                OrganizationRow(id=foreign, key="far", name="Far"),
            ]
        )
        await session.flush()
        # The tenant's own workspace key, `default`, recurs in a second organization it can read.
        session.add_all(
            [
                WorkspaceRow(id=shared, organization_id=second, key="default", name="Default"),
                WorkspaceRow(id=closed, organization_id=second, key="closed", name="Closed"),
                WorkspaceRow(id=hidden, organization_id=foreign, key="hidden", name="Hidden"),
                PrincipalRow(id=outsider, kind="user", name="outsider", email="outsider@example.com"),
            ]
        )
        await session.flush()
        for principal_id, organization_id, workspace_id in (
            (tenant.principal_id, second, shared),
            (outsider, foreign, hidden),
        ):
            session.add(
                GrantRow(
                    id=new_object_id("rb"),
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    principal_id=principal_id,
                    role="viewer",
                    created_by_id=principal_id,
                )
            )
    ambiguous = await client.get("/api/v1/workspaces/default")
    assert ambiguous.status_code == 409 and ambiguous.json()["error"]["details"]["reason"] == "ambiguous_key"
    assert (await client.get(f"/api/v1/workspaces/{shared}")).status_code == 200
    # Inside an organization it belongs to, the caller learns a workspace exists by ID but is refused; keys
    # resolve only among workspaces it can read.
    assert (await client.get(f"/api/v1/workspaces/{closed}")).status_code == 403
    assert (await client.get("/api/v1/workspaces/closed")).status_code == 404
    assert (await client.get(f"/api/v1/workspaces/{shared}/grants")).status_code == 403
    # Outside its organizations nothing is revealed, by ID or key, and nothing is recorded there.
    for path in (
        f"/api/v1/workspaces/{hidden}",
        "/api/v1/workspaces/hidden",
        f"/api/v1/workspaces/{hidden}/grants",
        f"/api/v1/organizations/{foreign}",
        f"/api/v1/organizations/{foreign}/grants",
    ):
        assert (await client.get(path)).status_code == 404, path
    assert await audit_actions(service, outcome="denied") == ["grant.list"]
    assert await audit_actions(service, organization_id=foreign) == []
    # A grant goes only to a principal already in the organization; any other is indistinguishable from none.
    for principal_id in (outsider, new_object_id("usr")):
        refused = await client.post(
            f"{service.organization}/grants", json={"principal_id": principal_id, "role": "viewer"}
        )
        assert refused.status_code == 404 and refused.json()["error"]["details"] == {
            "kind": "principal",
            "id": principal_id,
        }


async def test_invitation_needs_the_inviters_current_authority(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    async with AsyncExitStack() as stack:
        deputy = await join(service, stack, "deputy@example.com", "admin", scope=service.organization)
        receipt = await deputy.post(
            f"{service.workspace}/invitations", json={"email": "late@example.com", "role": "builder"}
        )
        assert receipt.status_code == 201
        [grant] = [
            g
            for g in (await client.get(f"{service.organization}/grants")).json()["items"]
            if g["principal"]["email"] == "deputy@example.com"
        ]
        assert (await client.delete(f"{service.organization}/grants/{grant['id']}")).status_code == 204
        async with new_client(service) as invitee:
            refused = await accept(invitee, receipt.json()["invitation_url"], MEMBER_PASSWORD)
        assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "inviter_lost_authority"
        async with short_session(service.runtime.storage) as session:
            assert await session.scalar(select(PrincipalRow).where(PrincipalRow.email == "late@example.com")) is None
        # An accepted grant is the acceptor's act, carrying who invited them and through which invitation.
        joined = await join(service, stack, "joined@example.com", "runner")
        joined_id = (await joined.get("/api/v1/users/me")).json()["id"]
    async with short_session(service.runtime.storage) as session:
        event = await session.scalar(
            select(AuditEventRow).where(AuditEventRow.action == "grant.create", AuditEventRow.actor_id == joined_id)
        )
    assert event is not None and event.details["invited_by_id"] == service.tenant.principal_id
    assert event.details["principal_id"] == joined_id and event.details["invitation_id"]


async def test_administration_rechecks_authority_inside_its_transaction(service) -> None:  # type: ignore[no-untyped-def]
    storage, access, organization_id = service.runtime.storage, service.runtime.access, service.tenant.organization_id
    async with AsyncExitStack() as stack:
        deputy = await join(service, stack, "deputy@example.com", "admin", scope=service.organization)
        deputy_id = (await deputy.get("/api/v1/users/me")).json()["id"]
    async with short_session(storage) as session:
        authenticated = await principal_for(session, access, deputy_id)
    # A revocation holds the organization while the deputy's change, authenticated as an admin, starts.
    async with transaction(storage) as session:
        await lock_organization(session, organization_id)
        change = asyncio.create_task(
            create_workspace(storage, access, authenticated, organization_id, WorkspaceCreate(key="late", name="Late"))
        )
        await asyncio.sleep(0.2)
        assert not change.done()
        await session.execute(delete(GrantRow).where(GrantRow.principal_id == deputy_id))
    with pytest.raises(ServiceError, match="cannot perform"):
        await change
    assert await audit_actions(service, outcome="denied") == ["workspace.create"]


async def test_admin_reads_do_not_wait_for_administration(service) -> None:  # type: ignore[no-untyped-def]
    storage, access, organization_id = service.runtime.storage, service.runtime.access, service.tenant.organization_id
    async with short_session(storage) as session:
        admin = await principal_for(session, access, service.tenant.principal_id)
    # A change holds the organization; reading its audit trail neither waits for it nor blocks it.
    async with transaction(storage) as session:
        await lock_organization(session, organization_id)
        async with asyncio.timeout(2):
            page = await list_audit_events(
                storage, access, admin, OrganizationPath(organization_id), limit=10, cursor=None
            )
    assert [event.action for event in page.items] == ["organization.bootstrap"]


async def test_users_disable_their_own_account(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    assert (
        await client.post("/api/v1/users/me/disable", json={"current_password": "not-my-password"})
    ).status_code == 400
    last = await client.post("/api/v1/users/me/disable", json={"current_password": PASSWORD})
    assert last.status_code == 409 and last.json()["error"]["details"]["reason"] == "last_organization_admin"
    proof = {"current_password": MEMBER_PASSWORD}
    async with AsyncExitStack() as stack:
        member = await join(service, stack, "leaving@example.com", "builder")
        key = await member.post(
            "/api/v1/users/me/keys", json={"workspace_id": service.tenant.workspace_id, "name": "k"}
        )
        bearer = {"authorization": "Bearer " + key.json()["secret"]}
        assert (await member.post("/api/v1/users/me/disable", headers=bearer, json=proof)).status_code == 403
        assert (await member.post("/api/v1/users/me/disable", json=proof)).status_code == 204
        assert (await member.get("/api/v1/users/me")).status_code == 401
        assert (await member.get(service.workspace, headers=bearer)).status_code == 401
        async with new_client(service) as again:
            relogin = await again.post(
                "/api/v1/auth/login", json={"email": "leaving@example.com", "password": MEMBER_PASSWORD}
            )
            assert relogin.status_code == 401
        # Grants and keys stay, so re-enabling the account restores them, but never a login session from before.
        await set_account_status(service.runtime.storage, "leaving@example.com", "active")
        assert (await member.get("/api/v1/users/me")).status_code == 401
        assert (await member.get(service.workspace, headers=bearer)).status_code == 200
    grants = (await client.get(f"{service.workspace}/grants")).json()["items"]
    assert [(g["principal"]["email"], g["role"]) for g in grants] == [("leaving@example.com", "builder")]
    assert "user.disable" in await audit_actions(service, target_kind="user")


async def test_archiving_revokes_pending_invitations(service) -> None:  # type: ignore[no-untyped-def]
    client = service.client
    lab = (await client.post(f"{service.organization}/workspaces", json={"key": "lab", "name": "Lab"})).json()
    path = f"/api/v1/workspaces/{lab['id']}"
    pending = await client.post(f"{path}/invitations", json={"email": "late@example.com", "role": "viewer"})
    organization_wide = await client.post(
        f"{service.organization}/invitations", json={"email": "org@example.com", "role": "viewer"}
    )
    assert (await client.post(f"{path}/archive", headers=if_match(lab))).status_code == 200
    [revoked] = (await client.get(f"{path}/invitations")).json()["items"]
    assert revoked["revoked_at"] is not None
    async with new_client(service) as invitee:
        refused = await accept(invitee, pending.json()["invitation_url"], MEMBER_PASSWORD)
    assert refused.status_code == 409 and refused.json()["error"]["details"]["reason"] == "revoked"
    # Withdrawing an invitation is offboarding, which an archived workspace allows: this one is simply done.
    again = await client.post(f"{path}/invitations/{revoked['id']}/revoke", headers=if_match(revoked))
    assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == "revoked"
    [kept] = (await client.get(f"{service.organization}/invitations")).json()["items"]
    assert kept["id"] == organization_wide.json()["invitation"]["id"] and kept["revoked_at"] is None
    async with short_session(service.runtime.storage) as session:
        event = await session.scalar(select(AuditEventRow).where(AuditEventRow.action == "workspace.archive"))
    assert event is not None and event.details == {"revoked_invitations": 1}


async def test_disabling_an_account_ends_its_sessions_and_links(service) -> None:  # type: ignore[no-untyped-def]
    storage = service.runtime.storage
    async with AsyncExitStack() as stack:
        member = await join(service, stack, "member@example.com", "viewer")
        member_id = (await member.get("/api/v1/users/me")).json()["id"]
        key = await member.post(
            "/api/v1/users/me/keys", json={"workspace_id": service.tenant.workspace_id, "name": "k"}
        )
        bearer = {"authorization": "Bearer " + key.json()["secret"]}
        async with transaction(storage) as session:
            reset = await issue_link(session, member_id, "password_reset", seconds=600)
        await set_account_status(storage, "member@example.com", "disabled")
        await set_account_status(storage, "member@example.com", "active")
        # Enabling restores grants and keys, never a login session or a link issued before the disable.
        assert (await member.get("/api/v1/users/me")).status_code == 401
        assert (await member.get(service.workspace, headers=bearer)).status_code == 200
        async with new_client(service) as anonymous:
            confirm = {"token": reset, "password": "a-new-password-1234"}
            assert (await anonymous.post("/api/v1/auth/password-reset/confirm", json=confirm)).status_code == 400
            relogin = await anonymous.post(
                "/api/v1/auth/login", json={"email": "member@example.com", "password": MEMBER_PASSWORD}
            )
            assert relogin.status_code == 200
    async with short_session(storage) as session:
        live = await session.scalar(
            select(func.count())
            .select_from(TokenRow)
            .where(TokenRow.principal_id == member_id, TokenRow.revoked_at.is_(None))
        )
    assert live == 1  # the new login's session only


async def test_administrators_change_each_others_grants_across_organizations(service, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An authority check locks its actor so that neither a grant change of that actor in another organization
    waits for it, nor two such changes deadlock."""
    client, tenant = service.client, service.tenant
    async with AsyncExitStack() as stack:
        peer = await join(service, stack, "peer@example.com", "viewer", scope=service.organization)
        peer_id = (await peer.get("/api/v1/users/me")).json()["id"]
        other = new_object_id("org")
        async with transaction(service.runtime.storage) as session:
            session.add(OrganizationRow(id=other, key="other", name="Other"))
            await session.flush()
            for principal_id, role in ((peer_id, "admin"), (tenant.principal_id, "viewer")):
                session.add(
                    GrantRow(
                        id=new_object_id("rb"),
                        organization_id=other,
                        principal_id=principal_id,
                        role=role,
                        created_by_id=peer_id,
                    )
                )
        theirs = f"{service.organization}/grants/" + next(
            g["id"]
            for g in (await client.get(f"{service.organization}/grants")).json()["items"]
            if g["principal"]["id"] == peer_id
        )
        mine = f"/api/v1/organizations/{other}/grants/" + next(
            g["id"]
            for g in (await peer.get(f"/api/v1/organizations/{other}/grants")).json()["items"]
            if g["principal"]["id"] == tenant.principal_id
        )
        # Both changes pass their authority checks before either locks the member it changes.
        checked = asyncio.Barrier(2)
        grant_at = grant_changes._grant_at

        async def after_both_checks(*args):  # type: ignore[no-untyped-def]
            async with asyncio.timeout(5):
                await checked.wait()
            return await grant_at(*args)

        monkeypatch.setattr(grant_changes, "_grant_at", after_both_checks)
        changed = await asyncio.gather(
            client.patch(theirs, json={"role": "runner"}), peer.patch(mine, json={"role": "runner"})
        )
    assert [(response.status_code, response.json()["role"]) for response in changed] == [(200, "runner")] * 2


async def test_switching_between_admin_roles_keeps_the_organization_administered(serve) -> None:  # type: ignore[no-untyped-def]
    distribution = OSS.extend(Distribution(name="owners", roles={"owner": VERBS}))
    async with serve(distribution=distribution) as service:
        client, grants = service.client, f"{service.organization}/grants"
        [mine] = (await client.get(grants)).json()["items"]
        owner = await client.patch(f"{grants}/{mine['id']}", json={"role": "owner"})
        assert owner.status_code == 200 and owner.json()["role"] == "owner"
        demoted = await client.patch(f"{grants}/{owner.json()['id']}", json={"role": "viewer"})
        assert demoted.status_code == 409 and demoted.json()["error"]["details"]["reason"] == "last_organization_admin"


async def test_outbox_ends_claims_that_never_settle_and_keeps_deferred_ones(runtime, tenant) -> None:  # type: ignore[no-untyped-def]
    config = MailSettings(smtp_host="127.0.0.1", sender="a13n@example.com")
    async with transaction(runtime.storage) as session:
        for purpose in ("cancelled", "deferred"):
            queue_mail(
                session,
                runtime.keys,
                config,
                Mail("to@example.com", purpose, purpose),
                organization_id=tenant.organization_id,
                workspace_id=None,
                purpose=purpose,
            )

    async def handle(claimed: Claim) -> None:
        if claimed.target["purpose"] == "deferred":
            async with transaction(runtime.storage) as session:
                await settle(session, claimed, "deferred", error="not_yet")
            return
        await asyncio.sleep(3600)  # cancelled with the pass, as a sweep deadline or a shutdown would

    delivery = Delivery(runtime.storage, {"email": handle}, owner="test", limit=10, lease_seconds=60, max_attempts=2)
    for _ in range(3):
        with anyio.move_on_after(0.5):
            await delivery()
        async with transaction(runtime.storage) as session:
            await session.execute(update(OutboxRow).values(lease_expires_at=func.now(), available_at=func.now()))
    async with short_session(runtime.storage) as session:
        rows = (await session.scalars(select(OutboxRow))).all()
    # Every claim of the cancelled delivery used an attempt; a deferral gave its attempt back each time.
    assert {row.target["purpose"]: (row.status, row.attempts, row.last_error) for row in rows} == {
        "cancelled": ("dead", 2, "unsettled"),
        "deferred": ("pending", 0, "not_yet"),
    }


async def test_smtp_sends_are_bounded_as_a_whole() -> None:
    """A server that answers every read in time but never finishes is cut off at the mail timeout."""
    closed = asyncio.Event()

    async def trickle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"220 ready\r\n")
        await reader.readline()
        try:
            while True:
                writer.write(b"250-still answering\r\n")
                await writer.drain()
                await asyncio.sleep(0.05)
        except ConnectionError:
            closed.set()
        finally:
            writer.close()

    server = await asyncio.start_server(trickle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    mailer = SmtpMailer(MailSettings(smtp_host="127.0.0.1", smtp_port=port, sender="a13n@example.com", timeout=0.5))
    started = time.monotonic()
    async with server:
        with pytest.raises(OSError):
            await mailer.send(Mail("to@example.com", "subject", "text"))
        assert time.monotonic() - started < 1.5
        # The exchange ended too, rather than finishing the mail after the outbox gave up on this attempt.
        async with asyncio.timeout(2):
            await closed.wait()
