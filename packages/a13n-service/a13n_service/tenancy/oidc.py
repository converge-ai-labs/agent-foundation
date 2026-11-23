"""OIDC authorization code exchange and first-login provisioning for the Service browser session."""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from urllib.parse import quote_plus

import httpx2
import jwt
from a13n_logging import get_logger
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select

from a13n_service.infra.audit import record
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import Storage, advisory_lock, lock, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbound import open_http
from a13n_service.settings import OIDCProvider, Settings, oidc_endpoint_policy
from a13n_service.tenancy.access import Access, principal_for, unauthenticated
from a13n_service.tenancy.authenticate import Login, oidc_session_data, open_session, session_csrf
from a13n_service.tenancy.credentials import new_secret
from a13n_service.tenancy.tables import GrantRow, PrincipalRow, WorkspaceRow

FLOW_SECONDS = 600
_SIGNING_ALGORITHMS = ("RS256", "ES256")


class _Discovery(BaseModel):
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    code_challenge_methods_supported: list[str] | None = None


class _TokenResponse(BaseModel):
    id_token: str


@dataclass(frozen=True, slots=True)
class Flow:
    nonce: str
    verifier: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True, slots=True)
class Identity:
    subject: str
    name: str
    email: str | None


def _upstream_failed(error: Exception) -> ServiceError:
    # Provider bodies, token values and callback URLs can contain credentials or personal information.
    get_logger(__name__).info("OIDC provider request failed", extra={"failure": type(error).__name__})
    return ServiceError("unavailable", "The identity provider could not complete sign-in")


def _flow_key(name: str, provider: OIDCProvider, redirect_uri: str, state: str, browser: str) -> str:
    binding = json.dumps([name, provider.issuer, provider.client_id, redirect_uri, state, browser])
    return f"a13n:auth:oidc:{secret_hash(binding)}"


async def start(redis: Redis, name: str, provider: OIDCProvider, *, redirect_uri: str) -> tuple[str, str]:
    policy = oidc_endpoint_policy(provider.issuer)
    try:
        async with open_http(policy, timeout=5, max_bytes=128 * 1024) as http:
            response = await http.get(f"{provider.issuer.rstrip('/')}/.well-known/openid-configuration")
            discovery = _Discovery.model_validate_json(response.raise_for_status().content)
        for endpoint in (discovery.authorization_endpoint, discovery.token_endpoint, discovery.jwks_uri):
            # The authorization endpoint is visited by the browser, not by open_http.
            if any(c.isspace() or c in "\\#" for c in endpoint):
                raise ValueError("invalid OIDC endpoint")
            policy.validate_syntax(endpoint)
        methods = discovery.code_challenge_methods_supported
        if discovery.issuer != provider.issuer or (methods is not None and "S256" not in methods):
            raise ValueError("OIDC Discovery issuer or PKCE methods do not match")
    except (ValueError, httpx2.HTTPError) as error:
        raise _upstream_failed(error) from None

    state, browser, nonce, verifier = (new_secret() for _ in range(4))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    params = {
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    target = str(httpx2.URL(discovery.authorization_endpoint).copy_merge_params(params))
    key = _flow_key(name, provider, redirect_uri, state, browser)
    await redis.set(
        key,
        json.dumps([nonce, verifier, discovery.authorization_endpoint, discovery.token_endpoint, discovery.jwks_uri]),
        ex=FLOW_SECONDS,
    )
    return target, browser


async def consume(
    redis: Redis, name: str, provider: OIDCProvider, *, redirect_uri: str, state: str, browser: str
) -> Flow:
    if len(state) != 43 or len(browser) != 43:
        raise unauthenticated()
    # The key binds the browser before consuming state; a mismatched browser or provider cannot consume it.
    value = await redis.getdel(_flow_key(name, provider, redirect_uri, state, browser))
    if value is None:
        raise unauthenticated()
    return Flow(*json.loads(value))


def _identity(token: str, jwks: dict, provider: OIDCProvider, nonce: str) -> Identity:
    header = jwt.get_unverified_header(token)
    algorithm = header.get("alg")
    kid = header.get("kid")
    if algorithm not in _SIGNING_ALGORITHMS or (kid is not None and not isinstance(kid, str)):
        raise jwt.InvalidTokenError("unsupported OIDC signing algorithm or key")
    keys = jwt.PyJWKSet.from_dict(jwks).keys
    matches = [
        key
        for key in keys
        if key.algorithm_name == algorithm
        and key.public_key_use in (None, "sig")
        and (kid is None or key.key_id == kid)
    ]
    if len(matches) != 1:
        raise jwt.InvalidTokenError("no unique OIDC signing key")
    claims = jwt.decode(
        token,
        matches[0],
        algorithms=[algorithm],
        audience=provider.client_id,
        issuer=provider.issuer,
        options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
    )
    if (
        claims["aud"] not in (provider.client_id, [provider.client_id])
        or claims.get("azp", provider.client_id) != provider.client_id
    ):
        raise jwt.InvalidTokenError("unexpected ID token audience or authorized party")
    received = claims["nonce"]
    if not isinstance(received, str) or not hmac.compare_digest(received.encode(), nonce.encode()):
        raise jwt.InvalidTokenError("ID token nonce does not match")
    subject = claims["sub"]
    if (
        not isinstance(subject, str)
        or not 0 < len(subject) <= 255
        or not subject.isascii()
        or not subject.isprintable()
    ):
        raise jwt.InvalidTokenError("invalid ID token subject")
    email = claims.get("email")
    email = (
        email
        if claims.get("email_verified") is True and isinstance(email, str) and len(email) <= 320 and email.isprintable()
        else None
    )
    name = claims.get("name")
    name = name.strip()[:128] if isinstance(name, str) and name.isprintable() else ""
    return Identity(subject, name or email or f"{provider.label} user"[:128], email)


async def exchange(provider: OIDCProvider, flow: Flow, *, redirect_uri: str, code: str) -> Identity:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": flow.verifier,
    }
    secret = provider.client_secret.get_secret_value()
    auth = None
    if provider.token_endpoint_auth_method == "client_secret_basic":
        auth = httpx2.BasicAuth(quote_plus(provider.client_id), quote_plus(secret))
    else:
        data.update(client_id=provider.client_id, client_secret=secret)
    policy = oidc_endpoint_policy(provider.issuer)
    try:
        async with open_http(policy, timeout=5, max_bytes=128 * 1024) as http:
            response = (
                await http.post(flow.token_endpoint, data=data)
                if auth is None
                else await http.post(flow.token_endpoint, data=data, auth=auth)
            )
            tokens = _TokenResponse.model_validate_json(response.raise_for_status().content)
            keys = (await http.get(flow.jwks_uri)).raise_for_status().json()
        if not isinstance(keys, dict):
            raise ValueError("invalid OIDC JWKS")
        return _identity(tokens.id_token, keys, provider, flow.nonce)
    except (jwt.PyJWTError, ValueError, TypeError, httpx2.HTTPError) as error:
        raise _upstream_failed(error) from None


async def sign_in(
    storage: Storage, access: Access, settings: Settings, name: str, provider: OIDCProvider, identity: Identity
) -> Login:
    """Provision once in the session-creation transaction; returning users keep their current grants."""
    async with transaction(storage) as session:
        await advisory_lock(session, "oidc-identity", provider.issuer, identity.subject)
        row = await session.scalar(
            select(PrincipalRow)
            .where(PrincipalRow.oidc_issuer == provider.issuer, PrincipalRow.oidc_subject == identity.subject)
            .with_for_update()
        )
        if row is None:
            workspace_id = settings.auth.oidc.default_workspace_id
            workspace = await lock(session, WorkspaceRow, workspace_id) if workspace_id else None
            if workspace is None or workspace.archived_at is not None:
                raise ServiceError("unavailable", "The default sign-in workspace is unavailable")
            row = PrincipalRow(
                id=new_object_id("usr"),
                kind="user",
                name=identity.name,
                oidc_issuer=provider.issuer,
                oidc_subject=identity.subject,
                oidc_email=identity.email,
            )
            session.add(row)
            await session.flush()
            session.add(
                GrantRow(
                    id=new_object_id("rb"),
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.id,
                    principal_id=row.id,
                    role="runner",
                    created_by_id=row.id,
                )
            )
            record(
                session,
                None,
                actor_id=row.id,
                action="user.oidc_provision",
                target_kind="user",
                target_id=row.id,
                details={"provider": name, "workspace_id": workspace.id},
            )
            await session.flush()
        elif row.status == "active" and row.oidc_email != identity.email:
            row.oidc_email = identity.email
        principal = await principal_for(session, access, row.id)
        secret = await open_session(
            session, row.id, seconds=settings.auth.oidc.session_seconds, oidc=oidc_session_data(name, provider)
        )
    return Login(principal, secret, session_csrf(secret))
