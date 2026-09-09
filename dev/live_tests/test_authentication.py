"""Offline checks for the explicit local bearer authentication boundary."""

import pytest
from a13n_service.iam import AuthenticationError
from starlette.requests import Request

from .host import bearer_authenticator


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"authorization", b"Bearer wrong")],
        [(b"authorization", b"Basic private")],
        [(b"authorization", b"Bearer private"), (b"authorization", b"Bearer private")],
    ],
)
async def test_local_authentication_rejects_missing_wrong_and_ambiguous_credentials(headers):
    authenticate = bearer_authenticator(
        {"token": "private", "user_id": "usr_1234567890abcdef", "workspace_id": "ws_1234567890abcdef"}
    )
    with pytest.raises(AuthenticationError):
        await authenticate(Request({"type": "http", "headers": headers}))


@pytest.mark.anyio
async def test_local_authentication_binds_the_configured_identity_and_workspace():
    authenticate = bearer_authenticator(
        {"token": "private", "user_id": "usr_1234567890abcdef", "workspace_id": "ws_1234567890abcdef"}
    )
    actor = await authenticate(
        Request({"type": "http", "headers": [(b"authorization", b"Bearer private"), (b"x-workspace-id", b"ws_other")]})
    )
    assert actor.principal.principal_id == "usr_1234567890abcdef"
    assert actor.workspace_id == "ws_1234567890abcdef"


@pytest.mark.anyio
async def test_inbox_evidence_rejects_another_authenticated_workspace_before_storage_access():
    import httpx2
    from fastapi import FastAPI

    from .fixture_inbox import inbox_router

    config = {"token": "first", "user_id": "usr_" + "a" * 24, "workspace_id": "ws_" + "a" * 24}
    config["other_identity"] = {"token": "second", "user_id": "usr_" + "b" * 24, "workspace_id": "ws_" + "b" * 24}
    app = FastAPI()
    app.include_router(inbox_router(config, bearer_authenticator(config)))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://fixture") as client:
        response = await client.get("/__live__/threads/thread_owned/inbox", headers={"Authorization": "Bearer second"})
    assert response.status_code == 403
