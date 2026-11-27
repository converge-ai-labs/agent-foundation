"""RP-initiated and back-channel browser-session logout."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import time
from urllib.parse import parse_qs, urlsplit

import httpx2
import jwt
import pytest
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.settings import OIDC, Authentication, OIDCGrantSpec, OIDCProvider
from a13n_service.tenancy import oidc
from a13n_service.tenancy.authenticate import authenticate_secret, session_cookie
from a13n_service.tenancy.tables import PrincipalRow, TokenRow
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from pydantic import SecretStr
from sqlalchemy import select, update

pytestmark = pytest.mark.anyio

PROVIDER = OIDCProvider(
    label="Team",
    issuer="https://idp.example.test/realm",
    client_id="console",
    client_secret=SecretStr("test-secret"),
)
EVENT = "http://schemas.openid.net/event/backchannel-logout"
BACKCHANNEL = "/api/v1/auth/oidc/team/backchannel-logout"


@pytest.fixture(scope="module", params=["RS256", "ES256"])
def signing(request):
    algorithm = request.param
    if algorithm == "RS256":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        public = jwt.algorithms.ECAlgorithm.to_jwk(key.public_key())
    return key, algorithm, {**json.loads(public), "kid": "fixture", "use": "sig", "alg": algorithm}


def logout_token(signing, *, omit=(), **overrides):
    key, algorithm, _ = signing
    claims = {
        "iss": PROVIDER.issuer,
        "aud": PROVIDER.client_id,
        "iat": int(time()),
        "jti": "logout-1",
        "events": {EVENT: {}},
        **overrides,
    }
    for claim in omit:
        claims.pop(claim)
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": "fixture"})


def upstream(monkeypatch, *, endpoint=None, jwk=None, status=200, discovery_check=None):
    discovery_url = f"{PROVIDER.issuer}/.well-known/openid-configuration"
    jwks_url = f"{PROVIDER.issuer}/jwks"
    calls = []

    @asynccontextmanager
    async def open_http(policy, **_):
        class ProviderHTTP:
            async def get(self, url):
                calls.append(url)
                policy.validate_syntax(url)
                if url == discovery_url:
                    if discovery_check is not None:
                        await discovery_check()
                    return httpx2.Response(
                        status,
                        request=httpx2.Request("GET", url),
                        json={
                            "issuer": PROVIDER.issuer,
                            "authorization_endpoint": f"{PROVIDER.issuer}/authorize",
                            "token_endpoint": f"{PROVIDER.issuer}/token",
                            "jwks_uri": jwks_url,
                            **({"end_session_endpoint": endpoint} if endpoint is not None else {}),
                        },
                    )
                assert url == jwks_url
                return httpx2.Response(200, request=httpx2.Request("GET", url), json={"keys": [jwk]})

        yield ProviderHTTP()

    monkeypatch.setattr(oidc, "open_http", open_http)
    return calls


@pytest.mark.parametrize("valid", ["sid", "sub", "both"])
async def test_verify_logout_token(monkeypatch, signing, valid):
    upstream(monkeypatch, jwk=signing[2])
    claims = {"aud": ["other", PROVIDER.client_id], "iat": int(time()) - 120}
    if valid != "sub":
        claims["sid"] = "idp-session"
    if valid != "sid":
        claims.update(sub="subject", exp=int(time()) + 60)
    target = await oidc.verify_logout_token(PROVIDER, logout_token(signing, **claims))
    assert (target.subject, target.sid) == (claims.get("sub"), claims.get("sid"))


@pytest.mark.parametrize(
    "claims,omit",
    [
        ({"iss": "https://other.example.test"}, ()),
        ({"aud": ["other"]}, ()),
        ({"iat": 2**32}, ()),
        ({"iat": True}, ()),
        ({"iat": "123"}, ()),
        ({"exp": 0}, ()),
        ({"exp": None}, ()),
        ({"events": {}}, ()),
        ({"events": {EVENT: True}}, ()),
        ({"events": [EVENT]}, ()),
        ({"nonce": None}, ()),
        ({"nonce": "id-token-nonce"}, ()),
        ({"sid": ""}, ()),
        ({"sid": 17}, ()),
        ({}, ("sid",)),
        ({}, ("jti",)),
        ({}, ("iat",)),
    ],
)
async def test_reject_invalid_logout_claims(monkeypatch, signing, claims, omit):
    upstream(monkeypatch, jwk=signing[2])
    with pytest.raises(oidc.Failure):
        await oidc.verify_logout_token(PROVIDER, logout_token(signing, omit=omit, **{"sid": "idp-session", **claims}))


async def test_reject_untrusted_logout_signature_and_algorithm(monkeypatch, signing):
    upstream(monkeypatch, jwk=signing[2])
    _, algorithm, jwk = signing
    other = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if algorithm == "RS256"
        else ec.generate_private_key(ec.SECP256R1())
    )
    with pytest.raises(oidc.Failure):
        await oidc.verify_logout_token(PROVIDER, logout_token((other, algorithm, jwk), sid="idp-session"))
    with pytest.raises(oidc.Failure):
        await oidc.verify_logout_token(PROVIDER, jwt.encode({"sid": "idp-session"}, key=None, algorithm="none"))


@pytest.fixture
async def sso_service(settings, serve):
    other = PROVIDER.model_copy(update={"client_id": "other-console"})
    # The operator can configure an empty fallback when a provider has a role rule.
    from a13n_service.settings import OIDCRoleRule

    provider = PROVIDER.model_copy(
        update={"role_rules": [OIDCRoleRule(organization_id="unused", role="runner", match={"groups": "team"})]}
    )
    other = other.model_copy(update={"role_rules": provider.role_rules})
    auth = Authentication(oidc=OIDC(providers={"team": provider, "other": other}))
    async with serve(settings=settings.model_copy(update={"auth": auth})) as service:
        yield service


async def sign_in(service, *, name="team", subject="subject", sid=None):
    runtime = service.runtime
    configured = runtime.settings.auth.oidc.providers[name]
    provider = configured.model_copy(update={"role_rules": []})
    defaults = [OIDCGrantSpec(workspace_id=service.tenant.workspace_id, role="runner")]
    config = runtime.settings.auth.oidc.model_copy(update={"defaults": defaults})
    settings = runtime.settings.model_copy(update={"auth": runtime.settings.auth.model_copy(update={"oidc": config})})
    return await oidc.sign_in(
        runtime.storage, runtime.access, settings, name, provider, oidc.Identity(subject, "Alex", None, sid=sid)
    )


async def authenticate(service, login):
    settings = service.runtime.settings
    return await authenticate_secret(
        service.runtime.storage,
        service.runtime.access,
        login.secret,
        "session",
        session_seconds=settings.auth.session_seconds,
        auth=settings.auth,
    )


@pytest.mark.parametrize(
    "endpoint,status",
    [
        ("https://idp.example.test/end?tenant=team&client_id=replace-me", 200),
        (None, 204),
        (37, 204),
        ("http://idp.example.test/end", 204),
        ("https://idp.example.test/end#fragment", 204),
    ],
)
async def test_rp_logout_commits_before_discovery(sso_service, monkeypatch, endpoint, status):
    service = sso_service
    login = await sign_in(service, sid="upstream-session")
    credential = await authenticate(service, login)

    async def session_is_revoked():
        async with short_session(service.runtime.storage) as session:
            token = await session.get(TokenRow, credential.credential_id)
            assert token.revoked_at is not None
            assert token.data == {
                "provider": "team",
                "issuer": PROVIDER.issuer,
                "client_id": PROVIDER.client_id,
                "sid": "upstream-session",
            }

    upstream(monkeypatch, endpoint=endpoint, discovery_check=session_is_revoked)
    settings = service.runtime.settings
    service.client.cookies.set(session_cookie(settings), login.secret, domain="service.test", path="/")
    reply = await service.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": login.csrf_token})
    assert reply.status_code == status, reply.text
    assert reply.headers["cache-control"] == "no-store"
    assert any("Max-Age=0" in cookie for cookie in reply.headers.get_list("set-cookie"))
    if status == 200:
        url = urlsplit(reply.json()["logout_url"])
        assert (url.scheme, url.netloc, url.path) == ("https", "idp.example.test", "/end")
        assert parse_qs(url.query) == {
            "tenant": ["team"],
            "client_id": [PROVIDER.client_id],
            "post_logout_redirect_uri": [f"{settings.server.public_origin}/login"],
        }
    else:
        assert not reply.content


async def test_password_logout_does_not_use_provider(sso_service, monkeypatch):
    calls = upstream(monkeypatch)
    reply = await sso_service.client.post("/api/v1/auth/logout")
    assert reply.status_code == 204
    assert any("Max-Age=0" in cookie for cookie in reply.headers.get_list("set-cookie"))
    assert not calls


async def test_backchannel_revokes_only_matching_sessions(sso_service, monkeypatch, signing, runs_kit):
    service = sso_service
    # Inspect which sessions logout revokes before credential expiry can delete them.
    await runs_kit.pause_sweeps(service)
    upstream(monkeypatch, jwk=signing[2])
    same_sid = [await sign_in(service, sid="target") for _ in range(2)]
    other_sid = await sign_in(service, sid="other-device")
    legacy = await sign_in(service)
    other_provider = await sign_in(service, name="other", sid="target")
    other_subject = await sign_in(service, subject="another-user", sid="target")
    delayed_iat = int(time()) - 120
    expired = await sign_in(service, sid="target")
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(TokenRow)
            .where(TokenRow.secret_hash.in_([secret_hash(other_sid.secret), secret_hash(legacy.secret)]))
            .values(created_at=datetime.fromtimestamp(delayed_iat - 1, UTC))
        )
        expired_id = await session.scalar(
            update(TokenRow)
            .where(TokenRow.secret_hash == secret_hash(expired.secret))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=60))
            .returning(TokenRow.id)
        )
    # The IdP calls the endpoint without any session cookie or CSRF token.
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test"
    ) as idp:
        for _ in range(2):
            reply = await idp.post(
                BACKCHANNEL,
                data={"logout_token": logout_token(signing, sub="subject", sid="target"), "ignored": "extra"},
            )
            assert reply.status_code == 200, reply.text
            assert reply.headers["cache-control"] == "no-store"
        for login in same_sid:
            with pytest.raises(ServiceError, match="Authentication is required"):
                await authenticate(service, login)
        for login in (other_sid, legacy, other_provider, other_subject):
            assert await authenticate(service, login)
        recent = await sign_in(service, sid="new-sign-in")
        reply = await idp.post(
            BACKCHANNEL, data={"logout_token": logout_token(signing, sub="subject", iat=delayed_iat)}
        )
        assert reply.status_code == 200
        assert await authenticate(service, recent)
    for login in (other_sid, legacy):
        with pytest.raises(ServiceError, match="Authentication is required"):
            await authenticate(service, login)
    for login in (other_provider, other_subject):
        assert await authenticate(service, login)
    assert (await service.client.get("/api/v1/auth/session")).status_code == 200
    async with short_session(service.runtime.storage) as session:
        assert (await session.get(TokenRow, expired_id)).revoked_at is None
        assert (await session.get(PrincipalRow, legacy.principal.id)).status == "active"
        events = list(
            await session.scalars(select(AuditEventRow).where(AuditEventRow.action == "login_session.revoke"))
        )
        assert len(events) == 4
        assert all(
            event.actor_id is None and event.details == {"provider": "team", "source": "oidc_backchannel"}
            for event in events
        )


@pytest.mark.parametrize(
    "path,content_type,body",
    [
        (BACKCHANNEL, "application/json", '{"logout_token":"token"}'),
        (BACKCHANNEL, "application/x-www-form-urlencoded", "none=1"),
        (BACKCHANNEL, "application/x-www-form-urlencoded", "logout_token=one&logout_token=two"),
        (BACKCHANNEL, "application/x-www-form-urlencoded", "logout_token=not-a-JWT"),
        (BACKCHANNEL.replace("/team/", "/unknown/"), "application/x-www-form-urlencoded", "logout_token=ignored"),
    ],
)
async def test_backchannel_rejects_invalid_requests(sso_service, monkeypatch, path, content_type, body):
    calls = upstream(monkeypatch)
    reply = await sso_service.client.post(path, content=body, headers={"Content-Type": content_type})
    assert reply.status_code == 400
    assert reply.headers["cache-control"] == "no-store"
    assert not calls
