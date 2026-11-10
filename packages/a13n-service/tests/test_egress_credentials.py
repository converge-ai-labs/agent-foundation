"""Credential secrecy, tenant authority, optimistic updates and database invariants."""

import asyncio
import json

import pytest
from a13n_service.distribution import OSS
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.crypto import Envelope, SecretLocation
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.egress_credentials import service as credentials
from a13n_service.resources.egress_credentials.schemas import CredentialCreate, CredentialRotation
from a13n_service.resources.egress_credentials.tables import CredentialRow
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.anyio


def payload(value: str = "fixture-token-first") -> dict:
    return {"name": "GitHub", "allowed_hosts": ["API.GitHub.COM.", "api.github.com"], "value": value}


async def test_credential_lifecycle_is_encrypted_write_only_and_versioned(service):
    base = f"{service.api}/egress-credentials"
    created = await service.client.post(base, json=payload())
    assert created.status_code == 201, created.text
    credential = created.json()
    assert credential["allowed_hosts"] == ["api.github.com"]
    assert credential["credential_version"] == credential["version"] == 1
    assert "value" not in credential and payload()["value"] not in created.text
    path = f"{base}/{credential['id']}"
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(CredentialRow, credential["id"])
        assert payload()["value"] not in json.dumps(row.value)
        assert credentials.reveal(service.runtime.keys, row).get_secret_value() == payload()["value"]
        with pytest.raises(ServiceError, match="cannot be decrypted"):
            service.runtime.keys.reveal(
                Envelope.model_validate(row.value),
                SecretLocation(row.organization_id, row.__tablename__, "value", "ecred_another"),
            )

    assert (await service.client.put(f"{path}/value", json={"value": "replacement"})).status_code == 428
    renamed = await service.client.patch(path, json={"name": "Renamed"}, headers={"if-match": created.headers["etag"]})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["credential_version"] == 1
    assert renamed.json()["version"] == 2
    rotated = await service.client.put(
        f"{path}/value", json={"value": "fixture-token-second"}, headers={"if-match": renamed.headers["etag"]}
    )
    assert rotated.status_code == 202, rotated.text
    assert rotated.json()["credential_version"] == 2
    assert rotated.json()["version"] == 3
    assert "fixture-token" not in rotated.text
    read = await service.client.get(path)
    assert read.json() == rotated.json()
    listing = await service.client.get(base)
    assert listing.json()["items"] == [rotated.json()]
    assert "fixture-token" not in listing.text

    stale = await service.client.post(f"{path}/revoke", headers={"if-match": renamed.headers["etag"]})
    assert stale.status_code == 412
    revoked = await service.client.post(f"{path}/revoke", headers={"if-match": rotated.headers["etag"]})
    assert revoked.status_code == 202, revoked.text
    assert revoked.json()["state"] == "revoked" and revoked.json()["credential_version"] == 3
    refused = await service.client.put(
        f"{path}/value", json={"value": "cannot-restore"}, headers={"if-match": revoked.headers["etag"]}
    )
    assert refused.status_code == 409
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(CredentialRow, credential["id"])
        assert row.value is None and row.revoked_at is not None
        with pytest.raises(ServiceError):
            credentials.reveal(service.runtime.keys, row)
        events = (await session.scalars(select(AuditEventRow).where(AuditEventRow.target_id == row.id))).all()
        assert [event.action for event in sorted(events, key=lambda event: event.occurred_at)] == [
            "egress_credential.create",
            "egress_credential.update",
            "egress_credential.rotate",
            "egress_credential.revoke",
        ]
        assert "fixture-token" not in json.dumps([event.details for event in events])


async def test_concurrent_rotations_have_one_winner(service):
    base = f"{service.api}/egress-credentials"
    created = await service.client.post(base, json=payload())
    path = f"{base}/{created.json()['id']}/value"
    results = await asyncio.gather(
        *[
            service.client.put(path, json={"value": value}, headers={"if-match": created.headers["etag"]})
            for value in ("first-racer", "second-racer")
        ]
    )
    assert sorted(response.status_code for response in results) == [202, 412]
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(CredentialRow, created.json()["id"])
        assert row.credential_version == 2
        assert credentials.reveal(service.runtime.keys, row).get_secret_value() in {"first-racer", "second-racer"}


async def test_scope_permissions_and_read_pagination(service):
    tenant, runtime = service.tenant, service.runtime
    runner = Principal(
        tenant.principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["runner"]),)
    )
    with pytest.raises(ServiceError) as error:
        await credentials.create_credential(
            runtime.storage, runtime.keys, runner, tenant.workspace_id, CredentialCreate.model_validate(payload())
        )
    assert error.value.code == "forbidden"
    base = f"{service.api}/egress-credentials"
    created = [await service.client.post(base, json=payload()) for _ in range(2)]
    first = await credentials.list_credentials(runtime.storage, runner, tenant.workspace_id, limit=1, cursor=None)
    second = await credentials.list_credentials(
        runtime.storage, runner, tenant.workspace_id, limit=1, cursor=first.next_cursor
    )
    assert {first.items[0].id, second.items[0].id} == {response.json()["id"] for response in created}
    assert second.next_cursor is None
    other = await service.client.post(f"{service.organization}/workspaces", json={"name": "Other"})
    assert other.status_code == 201, other.text
    hidden = await service.client.get(
        f"{base}/{created[0].json()['id']}", headers={"x-workspace-id": other.json()["id"]}
    )
    assert hidden.status_code == 404


async def test_hosts_are_fixed_and_database_guards_final_revocation(service):
    base = f"{service.api}/egress-credentials"
    created = await service.client.post(base, json=payload())
    path = f"{base}/{created.json()['id']}"
    rejected = await service.client.patch(
        path, json={"allowed_hosts": ["other.example"]}, headers={"if-match": created.headers["etag"]}
    )
    assert rejected.status_code == 400
    assert "fixture-token" not in rejected.text
    with pytest.raises(DBAPIError, match="injection hosts are immutable"):
        async with transaction(service.runtime.storage) as session:
            await session.execute(
                update(CredentialRow)
                .where(CredentialRow.id == created.json()["id"])
                .values(allowed_hosts=["other.example"])
            )
    revoked = await service.client.post(f"{path}/revoke", headers={"if-match": created.headers["etag"]})
    assert revoked.status_code == 202
    with pytest.raises(DBAPIError, match="revocation is final"):
        async with transaction(service.runtime.storage) as session:
            await session.execute(
                update(CredentialRow)
                .where(CredentialRow.id == created.json()["id"])
                .values(state="active", value={}, revoked_at=None)
            )


@pytest.mark.parametrize(
    "host",
    [
        "*.example.com",
        "https://example.com",
        "127.0.0.1",
        "::1",
        "bad..example",
        "-bad.example",
        "\u212a.example",
        "a" * 64 + ".example",
    ],
)
def test_invalid_injection_hosts_are_rejected(host):
    with pytest.raises(ValidationError):
        CredentialCreate.model_validate({**payload(), "allowed_hosts": [host]})


@pytest.mark.parametrize("value", ["", "secret\nheader", "secret\x00", "é" * 4097])
def test_invalid_values_never_enter_model_repr(value):
    with pytest.raises(ValidationError):
        CredentialRotation(value=value)
    valid = CredentialRotation(value="fixture-token-private")
    assert "fixture-token-private" not in repr(valid)


def test_secret_byte_limit_is_inclusive():
    assert len(CredentialRotation(value="é" * 4096).value.get_secret_value().encode()) == 8192


async def test_invalid_secret_is_absent_from_http_validation_errors(service):
    secret = "private-fixture\ninvalid-header"
    response = await service.client.post(f"{service.api}/egress-credentials", json=payload(secret))
    assert response.status_code == 400
    assert "private-fixture" not in response.text


def test_credential_writes_are_not_admitted_to_management_mcp_or_platform_tools():
    routes = [
        route
        for router in OSS.routers
        for route in router.routes
        if route.path.startswith("/api/v1/egress-credentials")
    ]
    assert routes
    for route in routes:
        metadata = route.openapi_extra or {}
        assert "x-a13n-platform" not in metadata
        assert bool(metadata.get("x-a13n-mcp")) == (route.methods == {"GET"})
