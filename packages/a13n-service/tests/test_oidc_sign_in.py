"""OIDC verification and local account/session boundaries."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import httpx2
import jwt
import pytest
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.settings import OIDC, Authentication, OIDCGrantSpec, OIDCProvider, OIDCRoleRule, Settings
from a13n_service.tenancy import oidc, routes
from a13n_service.tenancy.access import principals_for
from a13n_service.tenancy.authenticate import authenticate_secret, session_cookie
from a13n_service.tenancy.authorize import WorkspaceScope, allowed_verbs
from a13n_service.tenancy.credentials import new_secret
from a13n_service.tenancy.tables import GrantRow, OIDCGrantRow, OrganizationRow, PrincipalRow, TokenRow, WorkspaceRow
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
            "auth": {
                "oidc": {"defaults": [{"workspace_id": "ws_test", "role": "runner"}], "providers": {"team": PROVIDER}}
            },
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
    state, browser = "fixture-state", "fixture-browser"
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
                    defaults=[OIDCGrantSpec(workspace_id=service.tenant.workspace_id, role="runner")],
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


async def test_password_recovery_respects_sign_in_mode_and_identity(service, monkeypatch) -> None:
    runtime, tenant = service.runtime, service.tenant
    auth = Authentication(
        oidc=OIDC(
            providers={"team": PROVIDER},
            defaults=[OIDCGrantSpec(organization_id=tenant.organization_id, role="admin")],
        )
    )
    mixed = runtime.settings.model_copy(update={"auth": auth})
    oidc_admin = await oidc.sign_in(
        runtime.storage,
        runtime.access,
        mixed,
        "team",
        PROVIDER,
        oidc.Identity("recovery-admin", "SSO Admin", "admin@example.com"),
    )
    monkeypatch.setattr(service.app.state, "runtime", replace(runtime, settings=mixed))
    oidc_target = f"{service.organization}/members/{oidc_admin.principal.id}/password-reset"
    assert (await service.client.post(oidc_target)).status_code == 404
    local_target = f"{service.organization}/members/{tenant.principal_id}/password-reset"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as client:
        client.cookies.set(session_cookie(mixed), oidc_admin.secret)
        client.headers["x-csrf-token"] = oidc_admin.csrf_token
        link = await client.post(local_target)
        assert link.status_code == 200, link.text
        token = link.json()["reset_url"].split("#token=", 1)[1]
        disabled = mixed.model_copy(update={"auth": auth.model_copy(update={"password_enabled": False})})
        monkeypatch.setattr(service.app.state, "runtime", replace(runtime, settings=disabled))
        configuration = (await client.get("/api/v1/auth/configuration")).json()
        assert not configuration["password_enabled"] and not configuration["manual_password_reset"]
        assert (await client.post(local_target)).status_code == 403
        confirm = await client.post(
            "/api/v1/auth/password-reset/confirm", json={"token": token, "password": "new-password"}
        )
        assert confirm.status_code == 403


async def test_same_oidc_subject_from_two_issuers_is_two_accounts(service) -> None:
    runtime = service.runtime
    other = PROVIDER.model_copy(update={"issuer": "https://other.example.test/realm"})
    settings = runtime.settings.model_copy(
        update={
            "auth": Authentication(
                oidc=OIDC(
                    providers={"team": PROVIDER, "other": other},
                    defaults=[OIDCGrantSpec(workspace_id=service.tenant.workspace_id, role="runner")],
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
    oidc_settings = OIDC(
        providers={"team": PROVIDER}, defaults=[OIDCGrantSpec(workspace_id=service.tenant.workspace_id, role="runner")]
    )
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


async def test_callback_diagnostics_are_bounded(service, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=oidc.__name__)
    sentinel = "private-sentinel"
    runtime = service.runtime
    settings = runtime.settings.model_copy(
        update={
            "auth": Authentication(
                oidc=OIDC(
                    providers={"team": PROVIDER},
                    defaults=[OIDCGrantSpec(workspace_id=service.tenant.workspace_id, role="runner")],
                )
            )
        }
    )
    oidc_runtime = SimpleNamespace(settings=settings, redis=runtime.redis)

    def explode(*args, **kwargs):
        raise ValueError(f"{sentinel}-token-client-secret")

    monkeypatch.setattr(oidc, "open_http", explode)
    cookie = routes._oidc_cookie_name(settings, "team")
    redirect = routes._oidc_callback_url(settings, "team")
    cases = [
        ("unknown_provider", "callback_state", "unknown_provider", None),
        ("missing_cookie", "callback_state", "missing_cookie", None),
        ("duplicate_state", "callback_state", "invalid_state", None),
        ("expired_state", "callback_state", "state_not_found", None),
        ("known_error", "callback_error", "provider_error", "access_denied"),
        ("extension_error", "callback_error", "provider_error", "provider.policy-02"),
        ("duplicate_error", "callback_error", "provider_error", "unknown"),
        ("unsafe_error", "callback_error", "provider_error", "unknown"),
        ("issuer_mismatch", "callback_error", "issuer_mismatch", None),
        ("missing_code", "callback_error", "invalid_code", None),
        ("exception", "token_exchange", "internal", None),
    ]
    for case, stage, reason, idp_error in cases:
        provider_id = sentinel if case == "unknown_provider" else "team"
        state, browser = new_secret(), new_secret()
        params = [
            ("state", state),
            ("code", f"{sentinel}-code"),
            ("error_description", f"{sentinel}-email@example.test"),
        ]
        if case == "duplicate_state":
            params.append(("state", f"{sentinel}-other-state"))
        if case == "known_error":
            params.append(("error", "access_denied"))
        if case == "extension_error":
            params.append(("error", "provider.policy-02"))
        if case == "duplicate_error":
            params.extend([("error", "access_denied"), ("error", "server_error")])
        if case == "unsafe_error":
            params.append(("error", f"{sentinel}\n"))
        if case == "issuer_mismatch":
            params.append(("iss", f"https://{sentinel}.example.test"))
        if case == "missing_code":
            params = [(key, value) for key, value in params if key != "code"]
        request = Request(
            {
                "type": "http",
                "query_string": urlencode(params).encode(),
                "headers": ([] if case == "missing_cookie" else [(b"cookie", f"{cookie}={browser}".encode())]),
            }
        )
        if case not in {"unknown_provider", "missing_cookie", "duplicate_state"}:
            key = oidc._flow_key("team", PROVIDER, redirect, state, browser)
            await runtime.redis.set(
                key, json.dumps([f"{sentinel}-nonce", f"{sentinel}-verifier", "auth", "token", "jwks"])
            )
            if case == "expired_state":
                await runtime.redis.expire(key, 0)
        caplog.clear()
        response = await routes.finish_oidc_sign_in(request, provider_id, oidc_runtime)
        assert response.status_code == 303 and response.headers["location"] == "/login?oidc=failed"
        assert ("set-cookie" in response.headers) == (
            case
            in {
                "known_error",
                "extension_error",
                "duplicate_error",
                "unsafe_error",
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
        if case == "exception":
            assert record.levelno == logging.ERROR
            assert any(frame["function"] == "explode" for frame in record.exception_details[0]["frames"])


async def test_oidc_start_budget_is_per_peer_and_separate(service, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=oidc.__name__)
    runtime = service.runtime
    settings = runtime.settings.model_copy(
        update={
            "auth": Authentication(
                oidc=OIDC(
                    providers={"team": PROVIDER, "other": PROVIDER},
                    defaults=[OIDCGrantSpec(workspace_id=service.tenant.workspace_id, role="runner")],
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
            {
                "type": "http",
                "query_string": b"",
                "client": (address, 2020),
                "headers": [(b"x-forwarded-for", forwarded.encode())],
            }
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


@pytest.mark.parametrize("value", ["", "A", "é", "access_denied\n", "a" * 65])
def test_callback_error_codes_reject_unsafe_or_long_values(value):
    assert oidc.callback_error_code([value]) == "unknown"
    assert oidc.callback_error_code(["a" * 64]) == "a" * 64


async def test_redis_outage_blocks_persisting_the_flow_after_discovery(service, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=oidc.__name__)
    runtime = service.runtime
    settings = runtime.settings.model_copy(
        update={
            "auth": Authentication(
                oidc=OIDC(
                    defaults=[OIDCGrantSpec(workspace_id=service.tenant.workspace_id, role="runner")],
                    providers={"team": PROVIDER},
                )
            )
        }
    )
    sequence = []

    async def get(url):
        sequence.append("discovery")
        return httpx2.Response(
            200,
            request=httpx2.Request("GET", url),
            json={
                "issuer": PROVIDER.issuer,
                "authorization_endpoint": f"{PROVIDER.issuer}/authorize",
                "token_endpoint": f"{PROVIDER.issuer}/token",
                "jwks_uri": f"{PROVIDER.issuer}/keys",
            },
        )

    @asynccontextmanager
    async def http(*args, **kwargs):
        yield SimpleNamespace(get=get)

    async def unavailable(operation, *args, **kwargs):
        sequence.append(operation)
        raise RedisError("private-sentinel")

    monkeypatch.setattr(oidc, "open_http", http)
    broken = SimpleNamespace(
        eval=lambda *args: unavailable("eval", *args), set=lambda *args, **kwargs: unavailable("set", *args, **kwargs)
    )
    response = await routes.start_oidc_sign_in(
        Request({"type": "http", "query_string": b"", "headers": []}),
        "team",
        replace(runtime, settings=settings, redis=broken),
    )
    assert response.headers["location"] == "/login?oidc=failed" and "set-cookie" not in response.headers
    assert sequence == ["eval", "discovery", "set"]
    assert caplog.records[-1].reason == "dependency_unavailable"
    assert "private-sentinel" not in repr([record.__dict__ for record in caplog.records])


async def test_first_sign_in_requires_active_default_workspace(service):
    runtime = service.runtime
    for state, reason in (("missing", "workspace_missing"), ("archived", "workspace_archived")):
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
                        defaults=[OIDCGrantSpec(workspace_id=workspace_id, role="runner")],
                    )
                )
            }
        )
        with pytest.raises(oidc.Failure) as failure:
            await oidc.sign_in(
                runtime.storage, runtime.access, settings, "team", PROVIDER, oidc.Identity("fresh", "User", None)
            )
        assert failure.value.stage == "provision" and failure.value.reason == reason


@pytest.mark.parametrize(
    ("claims", "matched"),
    [
        ({"team_roles": "ConsoleAdmins"}, True),
        ({"team_roles": ["member", "ConsoleAdmins"]}, True),
        ({"team_roles": "consoleadmins"}, False),
        ({"team_roles": ["ConsoleAdmins", 1]}, False),
        ({"team_roles": "ConsoleAdmins", "_claim_names": {"team_roles": "src1"}}, False),
        ({}, False),
    ],
)
def test_id_token_admin_mapping_is_opt_in_and_exact(signing_key, claims, matched) -> None:
    key, jwks = signing_key
    mapping = OIDCRoleRule(workspace_id="ws_test", role="admin", match={"team_roles": "ConsoleAdmins"})
    provider = PROVIDER.model_copy(update={"role_rules": [mapping]})
    encoded = id_token(key, **claims)
    assert not oidc._identity(encoded, jwks, PROVIDER, NONCE).matched_grants
    assert bool(oidc._identity(encoded, jwks, provider, NONCE).matched_grants) is matched


@pytest.mark.parametrize(
    ("claims", "matched"),
    [
        ({"groups": ["Admins"], "hasgroups": True, "realm_access": {"roles": ["member"]}}, True),
        ({"hasgroups": True, "realm_access": {"roles": ["member"]}}, False),
        ({"groups": ["Admins"], "realm_access": {"roles": ["member"]}, "realm_access.roles": "stranger"}, False),
        ({"groups": ["Admins"], "realm_access.roles": ["operator"]}, True),
        ({"groups": ["Admins"], "realm_access.roles": ["operator"], "_claim_names": {"groups": "external"}}, False),
    ],
)
def test_rule_matches_all_claims_and_accepts_complete_inline_groups(signing_key, claims, matched):
    key, jwks = signing_key
    rule = OIDCRoleRule(
        role="admin",
        organization_id="org_test",
        match={"groups": "Admins", "realm_access.roles": ["member", "operator"]},
    )
    provider = PROVIDER.model_copy(update={"role_rules": [rule]})
    assert bool(oidc._identity(id_token(key, **claims), jwks, provider, NONCE).matched_grants) is matched


@pytest.mark.parametrize("destination", ["https://else.example/a", "//else.example", "/\\else.example", "/\t/other"])
def test_oidc_return_to_requires_an_origin_relative_path(destination):
    assert oidc.console_destination(destination) == "/"


async def test_oidc_syncs_roles_without_reissuing_credentials(service, signing_key, monkeypatch):
    runtime, tenant = service.runtime, service.tenant
    provider = OIDCProvider(
        label="Team",
        issuer=PROVIDER.issuer,
        client_id=PROVIDER.client_id,
        client_secret=PROVIDER.client_secret,
        role_rules=[
            OIDCRoleRule(organization_id=tenant.organization_id, role="admin", match={"team_roles": "ConsoleAdmins"}),
            OIDCRoleRule(workspace_id=tenant.workspace_id, role="admin", match={"team_roles": "ConsoleAdmins"}),
        ],
        extra_scopes=["email", "groups", "profile", "groups"],
    )
    assert tuple(provider.scopes) == ("openid", "profile", "email", "groups")
    settings = runtime.settings.model_copy(
        update={
            "auth": Authentication(
                oidc=OIDC(
                    providers={"team": provider},
                    defaults=[OIDCGrantSpec(workspace_id=tenant.workspace_id, role="runner")],
                )
            )
        }
    )
    monkeypatch.setattr(service.app.state, "runtime", replace(runtime, settings=settings))
    key, jwks = signing_key

    async def sign_in(value):
        identity = oidc._identity(id_token(key, team_roles=value), jwks, provider, NONCE)
        return await oidc.sign_in(runtime.storage, runtime.access, settings, "team", provider, identity)

    mapped = await sign_in(["ConsoleAdmins"])
    subject = mapped.principal.id
    scope = WorkspaceScope(tenant.organization_id, tenant.workspace_id)
    assert "admin" in allowed_verbs(mapped.principal, scope)
    assert not allowed_verbs(mapped.principal, WorkspaceScope("other_org", "other_workspace"))
    grants_url = f"{service.organization}/grants"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as client:
        client.cookies.set(session_cookie(settings), mapped.secret)
        client.headers["x-csrf-token"] = mapped.csrf_token
        account_url = f"{service.workspace}/service-accounts"
        account = await client.post(account_url, json={"name": "edge"})
        assert account.status_code == 201, account.text
        issued = await client.post(f"{account_url}/{account.json()['id']}/keys", json={"name": "deploy"})
        assert issued.status_code == 201, issued.text
        account_key = {"authorization": "Bearer " + issued.json()["secret"]}
        personal = await client.post("/api/v1/users/me/keys", json={"workspace_id": tenant.workspace_id, "name": "me"})
        assert personal.status_code == 201, personal.text
        human_key = {"authorization": "Bearer " + personal.json()["secret"]}
        assert "admin" in (await client.get(service.workspace, headers=human_key)).json()["permissions"]

        first = (await client.get(grants_url, params={"limit": 1})).json()
        (sso,) = first["items"]
        assert (sso["source"], sso["principal"]["id"], sso["role"]) == ("oidc", subject, "admin")
        second = (await client.get(grants_url, params={"limit": 1, "cursor": first["next_cursor"]})).json()
        (manual_admin,) = second["items"]
        assert manual_admin["source"] == "manual" and second["next_cursor"] is None
        assert (await client.patch(f"{grants_url}/{sso['id']}", json={"role": "viewer"})).status_code == 404
        assert (await client.delete(f"{grants_url}/{sso['id']}")).status_code == 404
        protected = await client.delete(f"{grants_url}/{manual_admin['id']}")
        assert (
            protected.status_code == 409 and protected.json()["error"]["details"]["reason"] == "last_organization_admin"
        )

        # An independent workspace role is unaffected by the organization override.
        workspace_grants = (await service.client.get(f"{service.workspace}/grants")).json()["items"]
        runner = next(item for item in workspace_grants if item["principal"]["id"] == subject)
        assert runner["source"] == "oidc" and runner["role"] == "admin" and not runner["overrides_sso"]
        workspace_role = await service.client.post(
            f"{service.workspace}/grants", json={"role": "builder", "principal_id": subject}
        )
        assert workspace_role.status_code == 201, workspace_role.text
        assert workspace_role.json()["overrides_sso"]

        async def set_manual_role():
            response = await service.client.post(grants_url, json={"role": "viewer", "principal_id": subject})
            assert response.status_code == 201, response.text
            assert response.json()["overrides_sso"]
            return f"{grants_url}/{response.json()['id']}"

        async def effective_role():
            items = (await service.client.get(grants_url)).json()["items"]
            return [
                (item["source"], item["role"], item["overrides_sso"])
                for item in items
                if item["principal"]["id"] == subject
            ]

        async def has_worker_admin():
            async with short_session(runtime.storage) as db:
                worker = (await principals_for(db, runtime.access, [(subject, scope)]))[subject, scope]
                assert not isinstance(worker, ServiceError)
                return "admin" in allowed_verbs(worker, scope)

        assert not sso["overrides_sso"] and not manual_admin["overrides_sso"]
        assert "uses_oidc" not in sso["principal"]
        assert await has_worker_admin()
        override = await set_manual_role()
        assert await effective_role() == [("manual", "viewer", True)]
        assert (await client.get(grants_url)).status_code == 403
        assert (await client.get(service.workspace, headers=human_key)).json()["permissions"] == [
            "read",
            "run",
            "write",
        ]
        assert (await client.get(service.workspace, headers=account_key)).json()["permissions"] == ["read", "run"]
        assert not await has_worker_admin()

        assert (await sign_in("ConsoleAdmins")).principal.id == subject
        assert await effective_role() == [("manual", "viewer", True)]
        assert (await service.client.delete(override)).status_code == 204
        assert await effective_role() == [("oidc", "admin", False)]
        assert (await client.get(grants_url)).status_code == 200
        assert await has_worker_admin()
        assert "admin" in (await client.get(service.workspace, headers=human_key)).json()["permissions"]

        # Losing the default revokes it for existing sessions once the override is absent.
        override = await set_manual_role()
        mismatch = await sign_in(["consoleadmins"])
        assert mismatch.principal.id == subject
        assert await effective_role() == [("manual", "viewer", False)]
        assert (await service.client.delete(override)).status_code == 204
        assert await effective_role() == []
        assert (await client.get(grants_url)).status_code == 403
        assert (await client.get(service.workspace, headers=human_key)).json()["permissions"] == [
            "read",
            "run",
            "write",
        ]
        assert (await client.get(service.workspace, headers=account_key)).json()["permissions"] == ["read", "run"]
        assert not await has_worker_admin()

        etag = (await service.client.get(service.workspace)).headers["etag"]
        archived = await service.client.post(f"{service.workspace}/archive", headers={"If-Match": etag})
        assert archived.status_code == 200, archived.text
        assert (await sign_in("ConsoleAdmins")).principal.id == subject
        assert (await client.get(grants_url)).status_code == 200  # archive leaves its organization intact
        archived_write = await client.post(account_url, json={"name": "another"})
        assert archived_write.status_code == 422 and archived_write.json()["error"]["code"] == "disabled"


def oidc_configuration(runtime, providers, defaults=()):
    return runtime.settings.model_copy(
        update={"auth": Authentication(oidc=OIDC(providers=providers, defaults=list(defaults)))}
    )


async def test_failed_mapping_preserves_grants_audit_and_sessions(service, monkeypatch, caplog):
    runtime, tenant = service.runtime, service.tenant
    rule = OIDCRoleRule(organization_id=tenant.organization_id, role="admin", match={"groups": "admin"})
    provider = PROVIDER.model_copy(update={"role_rules": [rule], "defaults": []})
    config = oidc_configuration(
        runtime, {"team": provider}, [OIDCGrantSpec(workspace_id=tenant.workspace_id, role="runner")]
    )
    prior = await oidc.sign_in(
        runtime.storage, runtime.access, config, "team", provider, oidc.Identity("same", "User", None, (rule,))
    )
    principal_id = prior.principal.id

    async def snapshot():
        async with short_session(runtime.storage) as db:
            return (
                set(
                    (row.id, row.role)
                    for row in await db.scalars(select(OIDCGrantRow).where(OIDCGrantRow.principal_id == principal_id))
                ),
                set(await db.scalars(select(AuditEventRow.id).where(AuditEventRow.actor_id == principal_id))),
                set(await db.scalars(select(TokenRow.id).where(TokenRow.principal_id == principal_id))),
            )

    before = await snapshot()
    caplog.set_level(logging.INFO, logger=oidc.__name__)
    callback = routes._oidc_callback_url(config, "team")
    cookie = routes._oidc_cookie_name(config, "team")
    for target, broken in (
        ("strict", ()),
        ("workspace", (rule, OIDCGrantSpec(workspace_id="ws_missing", role="runner"))),
        ("organization", (rule, OIDCGrantSpec(organization_id="org_missing", role="runner"))),
    ):

        async def exchange(*args, _broken=broken, **kwargs):
            return oidc.Identity("same", "User", "different@example.test", _broken)

        monkeypatch.setattr(oidc, "exchange", exchange)
        caplog.clear()
        key = oidc._flow_key("team", provider, callback, f"state-{target}", "fixture-browser")
        await runtime.redis.set(key, json.dumps(["nonce", "verifier", "auth", "token", "jwks"]), ex=60)
        response = await routes.finish_oidc_sign_in(
            Request(
                {
                    "type": "http",
                    "query_string": f"state=state-{target}&code=fixture-code".encode(),
                    "headers": [(b"cookie", f"{cookie}=fixture-browser".encode())],
                }
            ),
            "team",
            replace(runtime, settings=config),
        )
        expected = "access_denied" if target == "strict" else f"{target}_missing"
        assert response.headers["location"] == "/login?oidc=failed"
        assert all(
            not value.startswith(f"{session_cookie(config)}=") for value in response.headers.getlist("set-cookie")
        )
        assert caplog.records[-1].reason == expected and caplog.records[-1].stage == "provision"
        if target != "strict":
            assert caplog.records[-1].levelno == logging.WARNING
        assert await snapshot() == before
        result = await authenticate_secret(
            runtime.storage, runtime.access, prior.secret, "session", session_seconds=43200, auth=config.auth
        )
        assert "admin" in allowed_verbs(result.principal, WorkspaceScope(tenant.organization_id, tenant.workspace_id))


async def test_sso_winner_and_avatar_are_scoped_across_providers(service, monkeypatch):
    from a13n_service.tenancy.authenticate import open_session

    runtime, tenant = service.runtime, service.tenant
    roles = {**runtime.access.roles, "writer": frozenset({"write"}), "runner_alias": frozenset({"read", "run"})}
    access = replace(runtime.access, roles=roles)
    base = OIDCRoleRule(organization_id=tenant.organization_id, role="writer", match={"groups": "one"})
    higher = base.model_copy(update={"role": "runner_alias"})
    first = PROVIDER.model_copy(update={"role_rules": [base]})
    second = PROVIDER.model_copy(update={"role_rules": [higher]})
    config = oidc_configuration(runtime, {"team": first, "other": second})
    monkeypatch.setattr(service.app.state, "runtime", replace(runtime, access=access, settings=config))
    person = await oidc.sign_in(
        runtime.storage, access, config, "team", first, oidc.Identity("same", "User", None, (base,))
    )
    subject = person.principal.id
    scope = WorkspaceScope(tenant.organization_id, tenant.workspace_id)
    assert allowed_verbs(person.principal, scope) == {"write"}
    person = await oidc.sign_in(
        runtime.storage, access, config, "other", second, oidc.Identity("same", "User", None, (higher,))
    )
    assert person.principal.id == subject and allowed_verbs(person.principal, scope) == {"read", "run"}
    async with transaction(runtime.storage) as db:
        db.add_all(
            [
                OrganizationRow(id="org_other", name="Other"),
                PrincipalRow(id="usr_other", kind="user", name="Other", email="other@example.test"),
            ]
        )
        await db.flush()
        db.add(
            GrantRow(
                id="rb_other",
                organization_id="org_other",
                principal_id="usr_other",
                role="admin",
                created_by_id="usr_other",
            )
        )
        outside_secret = await open_session(db, "usr_other", seconds=600)
    transport = httpx2.ASGITransport(app=service.app)
    async with httpx2.AsyncClient(transport=transport, base_url="https://service.test") as member:
        member.cookies.set(session_cookie(config), person.secret)
        member.headers["x-csrf-token"] = person.csrf_token
        profile = await member.get("/api/v1/users/me")
        png = b"\x89PNG\r\n\x1a\n" + bytes(24)
        image = await member.put(
            "/api/v1/users/me/avatar",
            content=png,
            headers={"content-type": "image/png", "if-match": profile.headers["etag"]},
        )
        assert image.status_code == 200, image.text
        avatar = image.json()["image_url"]
        same_org = await service.client.get(avatar)
        assert same_org.status_code == 200 and same_org.content == png
        member.cookies.set(session_cookie(config), outside_secret, domain="service.test", path="/")
        member.cookies.delete(session_cookie(config), domain="", path="/")
        assert (await member.get(avatar)).status_code == 404
    grants = (await service.client.get(f"{service.organization}/grants")).json()["items"]
    winners = [row for row in grants if row["principal"]["id"] == subject]
    assert [(row["source"], row["role"]) for row in winners] == [("oidc", "runner_alias")]
    async with short_session(runtime.storage) as db:
        worker = (await principals_for(db, access, [(subject, scope)]))[subject, scope]
        assert not isinstance(worker, ServiceError) and allowed_verbs(worker, scope) == {"read", "run"}
    # Switching one provider to simple clears only its automatic grant and does not add defaults on re-login.
    simple = second.model_copy(
        update={"role_rules": [], "defaults": [OIDCGrantSpec(workspace_id=tenant.workspace_id, role="admin")]}
    )
    simple_config = oidc_configuration(runtime, {"team": first, "other": simple})
    after = await oidc.sign_in(
        runtime.storage, access, simple_config, "other", simple, oidc.Identity("same", "User", None)
    )
    assert allowed_verbs(after.principal, scope) == {"write"}


async def test_simple_grants_can_span_scopes_and_resolve_duplicate_roles(service):
    runtime, tenant = service.runtime, service.tenant
    defaults = [
        OIDCGrantSpec(organization_id=tenant.organization_id, role="viewer"),
        OIDCGrantSpec(workspace_id=tenant.workspace_id, role="builder"),
        OIDCGrantSpec(workspace_id=tenant.workspace_id, role="runner"),
    ]
    config = oidc_configuration(runtime, {"team": PROVIDER}, defaults)
    user = await oidc.sign_in(
        runtime.storage, runtime.access, config, "team", PROVIDER, oidc.Identity("simple", "Simple", None)
    )
    async with short_session(runtime.storage) as db:
        manual = await db.scalars(select(GrantRow).where(GrantRow.principal_id == user.principal.id))
        assert {row.workspace_id: row.role for row in manual} == {None: "viewer", tenant.workspace_id: "builder"}
        assert not list(await db.scalars(select(OIDCGrantRow).where(OIDCGrantRow.principal_id == user.principal.id)))


@pytest.mark.parametrize(
    "body",
    [
        {"role": "runner"},
        {"role": "runner", "workspace_id": "ws_test", "organization_id": "org_test"},
        {"role": "runner", "workspace_id": "ws_test", "match": {}},
        {"role": "runner", "workspace_id": "ws_test", "match": {"groups": []}},
    ],
)
def test_oidc_grant_specs_validate_scope_and_match(body):
    schema = OIDCRoleRule if "match" in body else OIDCGrantSpec
    with pytest.raises(ValidationError):
        schema.model_validate(body)


def test_oidc_policy_requires_roles_from_the_distribution(settings):
    from a13n_service.app import build_app
    from a13n_service.distribution import OSS

    config = settings.model_copy(
        update={
            "auth": Authentication(
                oidc=OIDC(
                    providers={"team": PROVIDER},
                    defaults=[
                        OIDCGrantSpec(workspace_id="ws_test", role="reviewer"),
                    ],
                )
            )
        }
    )
    with pytest.raises(ValueError, match="unregistered role 'reviewer'"):
        build_app(settings=config, apps=())
    build_app(replace(OSS, roles={**OSS.roles, "reviewer": frozenset({"read", "write"})}), settings=config, apps=())
