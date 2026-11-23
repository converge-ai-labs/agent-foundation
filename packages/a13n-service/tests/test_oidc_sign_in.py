"""OIDC verification and local account/session boundaries."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import jwt
import pytest
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.settings import OIDC, Authentication, OIDCProvider, Settings
from a13n_service.tenancy import oidc, routes
from a13n_service.tenancy.authenticate import authenticate_secret
from a13n_service.tenancy.credentials import new_secret
from a13n_service.tenancy.tables import GrantRow, PrincipalRow, TokenRow, WorkspaceRow
from a13n_service.tenancy.users import set_account_status
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr, ValidationError
from redis.exceptions import RedisError
from sqlalchemy import delete, select, update
from starlette.requests import Request

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
    return key, jwt.PyJWKSet.from_dict({"keys": [{**public, "kid": "fixture", "use": "sig", "alg": "RS256"}]}).keys


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


@pytest.mark.parametrize("claim,direction", [("iat", 1), ("nbf", 1), ("exp", -1)])
def test_token_clock_skew_is_bounded(signing_key, claim, direction) -> None:
    key, jwks = signing_key
    current = datetime.now(UTC)
    within = id_token(key, **{claim: current + timedelta(seconds=direction * 25)})
    outside = id_token(key, **{claim: current + timedelta(seconds=direction * 35)})
    assert oidc._identity(within, jwks, PROVIDER, NONCE).subject == "opaque-subject"
    expected = jwt.ExpiredSignatureError if claim == "exp" else jwt.ImmatureSignatureError
    with pytest.raises(expected):
        oidc._identity(outside, jwks, PROVIDER, NONCE)


def test_token_accepts_only_the_configured_audience(signing_key) -> None:
    key, jwks = signing_key
    assert oidc._identity(id_token(key, aud=[PROVIDER.client_id]), jwks, PROVIDER, NONCE).subject == "opaque-subject"


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


@pytest.mark.parametrize(
    ("issuer", "override", "expected"),
    [
        ("https://accounts.google.com", None, "google"),
        ("https://accounts.google.com", "generic", "generic"),
        ("https://accounts.google.com.example.test", None, "generic"),
        ("https://accounts.google.com/custom", None, "generic"),
        ("https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0", None, "microsoft"),
        ("https://login.microsoftonline.com/common/v2.0", None, "generic"),
        ("https://identity.example.test", None, "generic"),
        ("https://identity.example.test", "microsoft", "microsoft"),
    ],
)
def test_sign_in_brand_is_based_on_the_exact_issuer_or_explicit_override(issuer, override, expected) -> None:
    provider = OIDCProvider(
        label="Google", issuer=issuer, brand=override, client_id="console", client_secret=SecretStr("test")
    )
    assert provider.sign_in_brand == expected


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

    async with short_session(storage) as session:
        before = await now(session)
    first, second = await asyncio.gather(sign_in(), sign_in())
    assert first.principal.id == second.principal.id != service.tenant.principal_id
    principal_id = first.principal.id
    async with transaction(storage) as session:
        row = await session.get(PrincipalRow, principal_id)
        assert row is not None and row.email is None and row.oidc_email == identity.email
        grant = await session.scalar(select(GrantRow).where(GrantRow.principal_id == principal_id))
        assert grant and grant.role == "runner" and grant.workspace_id == service.tenant.workspace_id
        initial_token = await session.scalar(select(TokenRow).where(TokenRow.secret_hash == secret_hash(first.secret)))
        assert initial_token is not None
        duration = timedelta(seconds=settings.auth.oidc.session_seconds)
        assert before + duration <= initial_token.expires_at <= await now(session) + duration
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


async def test_same_oidc_subject_from_two_issuers_is_two_accounts(service) -> None:
    runtime = service.runtime
    other = PROVIDER.model_copy(update={"issuer": "https://other.example.test/realm"})
    settings = runtime.settings.model_copy(
        update={
            "auth": Authentication(
                oidc=OIDC(
                    providers={"team": PROVIDER, "other": other}, default_workspace_id=service.tenant.workspace_id
                )
            )
        }
    )
    identity = oidc.Identity("shared-subject", "Alex", "admin@example.com")
    first = await oidc.sign_in(runtime.storage, runtime.access, settings, "team", PROVIDER, identity)
    second = await oidc.sign_in(runtime.storage, runtime.access, settings, "other", other, identity)
    assert first.principal.id != second.principal.id


async def test_oidc_sessions_require_the_original_provider_and_client(service) -> None:
    runtime = service.runtime
    oidc_settings = OIDC(providers={"team": PROVIDER}, default_workspace_id=service.tenant.workspace_id)
    settings = runtime.settings.model_copy(update={"auth": Authentication(oidc=oidc_settings)})
    login = await oidc.sign_in(
        runtime.storage, runtime.access, settings, "team", PROVIDER, oidc.Identity("original-subject", "Alex", None)
    )

    async def authenticate(providers):
        auth = Authentication(oidc=oidc_settings.model_copy(update={"providers": providers}))
        return await authenticate_secret(
            runtime.storage, runtime.access, login.secret, "session", session_seconds=43200, auth=auth
        )

    assert (await authenticate({"team": PROVIDER})).principal.id == login.principal.id
    for providers in (
        {"renamed": PROVIDER},
        {"team": PROVIDER.model_copy(update={"issuer": "https://other.example.test/realm"})},
        {"team": PROVIDER.model_copy(update={"client_id": "new-client"})},
    ):
        with pytest.raises(ServiceError) as error:
            await authenticate(providers)
        assert error.value.code == "unauthenticated"


@pytest.mark.parametrize(
    "case,stage,reason,idp_error",
    [
        ("unknown_provider", "callback_state", "unknown_provider", None),
        ("missing_cookie", "callback_state", "missing_cookie", None),
        ("duplicate_state", "callback_state", "invalid_state", None),
        ("expired_state", "callback_state", "state_not_found", None),
        ("known_error", "callback_error", "provider_error", "access_denied"),
        ("unknown_error", "callback_error", "provider_error", "unknown"),
        ("issuer_mismatch", "callback_error", "issuer_mismatch", None),
        ("missing_code", "callback_error", "invalid_code", None),
        ("exception", "token_exchange", "internal", None),
    ],
)
async def test_callback_diagnostics_are_bounded(service, caplog, monkeypatch, case, stage, reason, idp_error):
    caplog.set_level(logging.INFO, logger=oidc.__name__)
    sentinel = "private-sentinel"
    runtime = service.runtime
    settings = runtime.settings.model_copy(
        update={
            "auth": Authentication(
                oidc=OIDC(
                    providers={"team": PROVIDER},
                    default_workspace_id=service.tenant.workspace_id,
                )
            )
        }
    )
    oidc_runtime = SimpleNamespace(settings=settings, redis=runtime.redis)
    provider_id = sentinel if case == "unknown_provider" else "team"
    state, browser = new_secret(), new_secret()
    params = [("state", state), ("code", f"{sentinel}-code"), ("error_description", f"{sentinel}-email@example.test")]
    if case == "duplicate_state":
        params.append(("state", f"{sentinel}-other-state"))
    if case in {"known_error", "unknown_error"}:
        params.append(("error", "access_denied" if case == "known_error" else sentinel))
    if case == "issuer_mismatch":
        params.append(("iss", f"https://{sentinel}.example.test"))
    if case == "missing_code":
        params = [(key, value) for key, value in params if key != "code"]
    cookie = routes._oidc_cookie_name(settings, "team")
    request = Request(
        {
            "type": "http",
            "query_string": urlencode(params).encode(),
            "headers": ([] if case == "missing_cookie" else [(b"cookie", f"{cookie}={browser}".encode())]),
        }
    )
    if case not in {"unknown_provider", "missing_cookie", "duplicate_state"}:
        redirect = routes._oidc_callback_url(settings, "team")
        key = oidc._flow_key("team", PROVIDER, redirect, state, browser)
        await runtime.redis.set(key, json.dumps([f"{sentinel}-nonce", f"{sentinel}-verifier", "auth", "token", "jwks"]))
        if case == "expired_state":
            await runtime.redis.expire(key, 0)

    async def explode(*args, **kwargs):
        raise ValueError(f"{sentinel}-token-client-secret")

    monkeypatch.setattr(oidc, "exchange", explode)
    caplog.clear()
    response = await routes.finish_oidc_sign_in(request, provider_id, oidc_runtime)
    assert response.status_code == 303 and response.headers["location"] == "/login?oidc=failed"
    assert ("set-cookie" in response.headers) == (
        case
        in {
            "known_error",
            "unknown_error",
            "issuer_mismatch",
            "missing_code",
            "exception",
        }
    )
    (record,) = [record for record in caplog.records if record.name == oidc.__name__]
    assert record.stage == stage and record.reason == reason
    assert record.provider_id == (None if case == "unknown_provider" else "team")
    assert record.__dict__.get("provider_error") == idp_error
    for secret in (sentinel, state, browser):
        assert secret not in repr(record.__dict__)
    assert record.exc_info is None


async def test_oidc_start_budget_is_per_peer_separate_and_fail_closed(service, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=oidc.__name__)
    runtime = service.runtime
    settings = runtime.settings.model_copy(
        update={
            "auth": Authentication(
                oidc=OIDC(
                    providers={"team": PROVIDER, "other": PROVIDER},
                    default_workspace_id=service.tenant.workspace_id,
                    start_limit=1,
                )
            )
        }
    )
    oidc_runtime = SimpleNamespace(settings=settings, redis=runtime.redis, storage=runtime.storage)
    starts = []

    async def start(redis, name, provider, **kwargs):
        starts.append(name)
        return "https://identity.example.test/authorize", new_secret()

    monkeypatch.setattr(oidc, "start", start)

    def browser(address, forwarded):
        return Request(
            {"type": "http", "client": (address, 2020), "headers": [(b"x-forwarded-for", forwarded.encode())]}
        )

    first = await routes.start_oidc_sign_in(browser("192.0.2.1", "192.0.2.99"), "team", oidc_runtime)
    assert first.headers["location"] == "https://identity.example.test/authorize"
    other_provider = await routes.start_oidc_sign_in(browser("192.0.2.1", "192.0.2.98"), "other", oidc_runtime)
    assert other_provider.headers["location"] == "/login?oidc=failed"
    assert caplog.records[-1].reason == "rate_limited"
    assert (
        await service.client.post(
            "/api/v1/auth/login",
            json={
                "email": "admin@example.com",
                "password": "test-password-1234",
            },
        )
    ).is_success
    await routes.start_oidc_sign_in(browser("192.0.2.2", "192.0.2.99"), "other", oidc_runtime)
    assert starts == ["team", "other"]

    async def unavailable(*args):
        raise RedisError("private-sentinel")

    oidc_runtime.redis = SimpleNamespace(eval=unavailable)
    failed = await routes.start_oidc_sign_in(browser("192.0.2.3", "192.0.2.99"), "other", oidc_runtime)
    assert failed.headers["location"] == "/login?oidc=failed" and starts == ["team", "other"]
    assert caplog.records[-1].reason == "rate_limit_unavailable"
    assert "private-sentinel" not in caplog.text


@pytest.mark.parametrize("state,reason", [("missing", "workspace_missing"), ("archived", "workspace_archived")])
async def test_first_sign_in_requires_active_default_workspace(service, state, reason):
    runtime = service.runtime
    workspace_id = service.tenant.workspace_id if state == "archived" else "ws_missing"
    if state == "archived":
        async with transaction(runtime.storage) as session:
            await session.execute(
                update(WorkspaceRow).where(WorkspaceRow.id == workspace_id).values(archived_at=await now(session))
            )
    settings = runtime.settings.model_copy(
        update={
            "auth": Authentication(
                oidc=OIDC(
                    providers={"team": PROVIDER},
                    default_workspace_id=workspace_id,
                )
            )
        }
    )
    with pytest.raises(oidc.Failure) as failure:
        await oidc.sign_in(
            runtime.storage, runtime.access, settings, "team", PROVIDER, oidc.Identity("fresh", "User", None)
        )
    assert failure.value.stage == "provision" and failure.value.reason == reason


async def test_http_transport_logs_do_not_expose_oidc_query_or_response_headers(caplog):
    sentinel = "private-sentinel"
    requests = []

    async def respond(reader, writer):
        requests.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write((f"HTTP/1.1 400 {sentinel}\r\nX-Fixture: {sentinel}\r\nContent-Length: 0\r\n\r\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(respond, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        provider = PROVIDER.model_copy(update={"issuer": f"http://127.0.0.1:{port}"})
        endpoint = f"{provider.issuer}/token?private={sentinel}"
        caplog.set_level(logging.DEBUG, logger="httpx2")
        caplog.set_level(logging.DEBUG, logger="httpcore2")
        flow = oidc.Flow("nonce", "verifier", "authorize", endpoint, endpoint)
        with pytest.raises(oidc.Failure) as failure:
            await oidc.exchange(provider, flow, redirect_uri="https://service.test/callback", code="fixture-code")
    assert requests and b"accept-encoding: identity" in requests[0].lower()
    assert failure.value.stage == "token_exchange" and failure.value.status == 400
    assert sentinel not in repr([record.__dict__ for record in caplog.records])
