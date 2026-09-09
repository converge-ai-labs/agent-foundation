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
