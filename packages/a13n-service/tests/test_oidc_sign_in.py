"""OIDC verification and local account/session boundaries."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.settings import OIDC, Authentication, OIDCProvider, Settings
from a13n_service.tenancy import oidc
from a13n_service.tenancy.authenticate import authenticate_secret
from a13n_service.tenancy.credentials import new_secret
from a13n_service.tenancy.tables import GrantRow, PrincipalRow, TokenRow
from a13n_service.tenancy.users import set_account_status
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, select, update

pytestmark = pytest.mark.anyio

PROVIDER = OIDCProvider(
    label="Team",
    issuer="https://identity.example.test/realm",
    client_id="console",
    client_secret=SecretStr("test-secret"),
)
NONCE = "fixture-nonce"


@pytest.fixture(scope="module")
def signing_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return key, {"keys": [{**public, "kid": "fixture", "use": "sig", "alg": "RS256"}]}


def id_token(key: Any, **overrides: object) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": PROVIDER.issuer,
            "aud": PROVIDER.client_id,
            "sub": "opaque-subject",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "nonce": NONCE,
            "email": "test@example.com",
            **overrides,
        },
        key,
        algorithm="RS256",
        headers={"kid": "fixture"},
    )


def test_token_returns_only_verified_email(signing_key) -> None:
    key, jwks = signing_key
    assert oidc._identity(id_token(key, email_verified="true"), jwks, PROVIDER, NONCE).email is None
    assert oidc._identity(id_token(key, email_verified=True), jwks, PROVIDER, NONCE).email == "test@example.com"


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://another.example.test"},
        {"aud": "different-client"},
        {"aud": ["console", "other"]},
        {"nonce": "another-attempt"},
        {"azp": "another-client"},
        {"exp": 0},
        {"sub": ""},
    ],
)
def test_token_rejects_wrong_claims(signing_key, claims) -> None:
    key, jwks = signing_key
    with pytest.raises(jwt.PyJWTError):
        oidc._identity(id_token(key, **claims), jwks, PROVIDER, NONCE)


def test_token_rejects_untrusted_signature(signing_key) -> None:
    _, jwks = signing_key
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(jwt.InvalidSignatureError):
        oidc._identity(id_token(other), jwks, PROVIDER, NONCE)


def test_provider_settings_require_sign_in_and_https() -> None:
    with pytest.raises(ValidationError):
        Authentication(password_enabled=False)
    with pytest.raises(ValidationError):
        OIDC(providers={"team": PROVIDER})
    with pytest.raises(ValidationError):
        OIDCProvider(
            label="Untrusted", issuer="http://idp.example.test", client_id="client", client_secret=SecretStr("test")
        )
    settings = Settings.model_validate(
        {
            "server": {"public_url": "http://checkout.localhost:8400"},
            "auth": {"oidc": {"default_workspace_id": "ws_test", "providers": {"team": PROVIDER}}},
        }
    )
    assert settings.auth.password_enabled


async def test_state_is_bound_and_consumed_once(service) -> None:
    redis = service.runtime.redis
    redirect = "https://console.example.test/api/v1/auth/oidc/team/callback"
    state, browser = new_secret(), new_secret()
    key = oidc._flow_key("team", PROVIDER, redirect, state, browser)
    await redis.set(key, json.dumps(["nonce", "verifier", "authorization", "token", "jwks"]), ex=60)

    async def consume(name: str, browser_secret: str):
        return await oidc.consume(redis, name, PROVIDER, redirect_uri=redirect, state=state, browser=browser_secret)

    with pytest.raises(ServiceError):
        await consume("other-provider", browser)
    with pytest.raises(ServiceError):
        await consume("team", new_secret())
    attempts = await asyncio.gather(consume("team", browser), consume("team", browser), return_exceptions=True)
    assert sum(isinstance(result, oidc.Flow) for result in attempts) == 1
    assert sum(isinstance(result, ServiceError) for result in attempts) == 1


async def test_oidc_account_is_provisioned_once_and_sessions_never_roll(service) -> None:
    runtime = service.runtime
    settings = runtime.settings.model_copy(
        update={
            "auth": Authentication(
                oidc=OIDC(
                    providers={"team": PROVIDER},
                    default_workspace_id=service.tenant.workspace_id,
                    session_seconds=60,
                )
            )
        }
    )
    storage, access = runtime.storage, runtime.access
    identity = oidc.Identity("subject-1", "Alex", "admin@example.com")

    async def sign_in():
        return await oidc.sign_in(storage, access, settings, "team", PROVIDER, identity)

    first, second = await asyncio.gather(sign_in(), sign_in())
    assert first.principal.id == second.principal.id != service.tenant.principal_id
    principal_id = first.principal.id
    async with transaction(storage) as session:
        row = await session.get(PrincipalRow, principal_id)
        assert row is not None and row.email is None and row.oidc_email == identity.email
        grant = await session.scalar(select(GrantRow).where(GrantRow.principal_id == principal_id))
        assert grant and grant.role == "runner" and grant.workspace_id == service.tenant.workspace_id
        await session.execute(delete(GrantRow).where(GrantRow.principal_id == principal_id))
        expiry = datetime.now(UTC) + timedelta(seconds=25)
        await session.execute(update(TokenRow).where(TokenRow.principal_id == principal_id).values(expires_at=expiry))
    result = await authenticate_secret(
        storage, access, first.secret, "session", session_seconds=43200, auth=settings.auth
    )
    assert not result.rolling_session
    assert not (await sign_in()).principal.grants
    async with short_session(storage) as session:
        token = await session.get(TokenRow, result.credential_id)
        assert token is not None and token.expires_at == expiry
    await set_account_status(storage, None, "disabled", principal_id=principal_id)
    with pytest.raises(ServiceError):
        await sign_in()
    with pytest.raises(ServiceError):
        await authenticate_secret(storage, access, first.secret, "session", session_seconds=43200, auth=settings.auth)
