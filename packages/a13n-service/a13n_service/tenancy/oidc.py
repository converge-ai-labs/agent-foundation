"""OIDC authorization code exchange and first-login provisioning for the Service browser session."""

import base64
import hashlib
import hmac
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from urllib.parse import quote_plus

import httpx2
import jwt
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from a13n_logging import exception_details, get_logger
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import Storage, advisory_lock, lock, now, transaction
from a13n_service.infra.errors import ErrorCode, ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbound import open_http
from a13n_service.settings import OIDCOrgAdmin, OIDCProvider, Settings, oidc_endpoint_policy
from a13n_service.tenancy.access import Access, lock_organization, principal_for
from a13n_service.tenancy.authenticate import Login, oidc_session_data, open_session, session_csrf
from a13n_service.tenancy.authorize import Scope
from a13n_service.tenancy.credentials import new_secret
from a13n_service.tenancy.tables import GrantRow, OIDCOrgAdminRow, PrincipalRow, WorkspaceRow

FLOW_SECONDS = 600
_TOKEN_CLOCK_SKEW_SECONDS = 30
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
    return_to: str = "/"


@dataclass(frozen=True, slots=True)
class Identity:
    subject: str
    name: str
    email: str | None
    org_admin: bool = False


class Stage(StrEnum):
    START = "start"
    DISCOVERY = "discovery"
    CALLBACK_STATE = "callback_state"
    CALLBACK_ERROR = "callback_error"
    TOKEN_EXCHANGE = "token_exchange"
    JWKS = "jwks"
    ID_TOKEN = "id_token"
    PROVISION = "provision"


class Reason(StrEnum):
    UNKNOWN_PROVIDER = "unknown_provider"
    NOT_INITIALIZED = "not_initialized"
    RATE_LIMITED = "rate_limited"
    RATE_LIMIT_UNAVAILABLE = "rate_limit_unavailable"
    MISSING_COOKIE = "missing_cookie"
    INVALID_STATE = "invalid_state"
    STATE_NOT_FOUND = "state_not_found"
    INVALID_CODE = "invalid_code"
    ISSUER_MISMATCH = "issuer_mismatch"
    PROVIDER_ERROR = "provider_error"
    INVALID_ENDPOINT = "invalid_endpoint"
    PKCE_UNSUPPORTED = "pkce_unsupported"
    INVALID_RESPONSE = "invalid_response"
    HTTP_STATUS = "http_status"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_TRANSPORT = "upstream_transport"
    RESPONSE_TOO_LARGE = "response_too_large"
    INVALID_TOKEN = "invalid_token"
    TOKEN_EXPIRED = "token_expired"
    TOKEN_NOT_YET_VALID = "token_not_yet_valid"
    AUDIENCE_MISMATCH = "audience_mismatch"
    INVALID_SIGNATURE = "invalid_signature"
    ACCOUNT_DISABLED = "account_disabled"
    WORKSPACE_MISSING = "workspace_missing"
    WORKSPACE_ARCHIVED = "workspace_archived"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    INTERNAL = "internal"


class ProviderError(StrEnum):
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED_CLIENT = "unauthorized_client"
    ACCESS_DENIED = "access_denied"
    UNSUPPORTED_RESPONSE_TYPE = "unsupported_response_type"
    INVALID_SCOPE = "invalid_scope"
    SERVER_ERROR = "server_error"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    INTERACTION_REQUIRED = "interaction_required"
    LOGIN_REQUIRED = "login_required"
    ACCOUNT_SELECTION_REQUIRED = "account_selection_required"
    CONSENT_REQUIRED = "consent_required"
    INVALID_REQUEST_URI = "invalid_request_uri"
    INVALID_REQUEST_OBJECT = "invalid_request_object"
    REQUEST_NOT_SUPPORTED = "request_not_supported"
    REQUEST_URI_NOT_SUPPORTED = "request_uri_not_supported"
    REGISTRATION_NOT_SUPPORTED = "registration_not_supported"
    UNKNOWN = "unknown"

    @classmethod
    def from_callback(cls, errors: list[str]) -> "ProviderError":
        if len(errors) == 1:
            try:
                return cls(errors[0])
            except ValueError:
                pass
        return cls.UNKNOWN


class Failure(ServiceError):
    """A browser sign-in refusal with diagnostics safe to log verbatim."""

    def __init__(
        self,
        stage: Stage,
        reason: Reason,
        *,
        code: ErrorCode = "unauthenticated",
        status: int | None = None,
        provider_error: ProviderError | None = None,
    ) -> None:
        super().__init__(code, "OIDC sign-in failed")
        self.stage = stage
        self.reason = reason
        self.status = status
        self.provider_error = provider_error


def log_failure(provider_id: str | None, stage: Stage, error: Exception) -> None:
    # Only the configured provider key and bounded enums cross the logging boundary.
    if isinstance(error, Failure):
        reason = error.reason
        stage = error.stage
    elif isinstance(error, (RedisError, SQLAlchemyError, TimeoutError)):
        reason = Reason.DEPENDENCY_UNAVAILABLE
    else:
        reason = Reason.INTERNAL
    fields = {"provider_id": provider_id, "stage": stage.value, "reason": reason.value}
    if isinstance(error, Failure):
        if error.status is not None:
            fields["http_status"] = error.status
        if error.provider_error is not None:
            fields["provider_error"] = error.provider_error.value
    if reason is Reason.INTERNAL:
        get_logger(__name__).error(
            "OIDC sign-in failed", extra={**fields, "exception_details": exception_details(error)}
        )
    else:
        get_logger(__name__).info("OIDC sign-in failed", extra=fields)


_outbound_sensitive: ContextVar[bool] = ContextVar("oidc_outbound_sensitive", default=False)


def _allow_transport_log(record: logging.LogRecord) -> bool:
    return not _outbound_sensitive.get()


@cache
def _filter_transport_logs() -> None:
    # HTTPX2 logs full URLs and status text; HTTPCore2 DEBUG traces log response headers and exceptions.
    # Logger filters run before any handlers and affect only the OIDC task context.
    for name in (
        "httpx2",
        "httpcore2.connection",
        "httpcore2.http11",
        "httpcore2.http2",
        "httpcore2.proxy",
        "httpcore2.socks",
    ):
        get_logger(name).addFilter(_allow_transport_log)


@contextmanager
def _upstream(stage: Stage) -> Iterator[None]:
    _filter_transport_logs()
    context = _outbound_sensitive.set(True)
    try:
        yield
    except Failure:
        raise
    except Exception as error:
        status = None
        if isinstance(error, httpx2.HTTPStatusError):
            reason = Reason.HTTP_STATUS
            status = error.response.status_code
        elif isinstance(error, (httpx2.TimeoutException, TimeoutError)):
            reason = Reason.UPSTREAM_TIMEOUT
        elif isinstance(error, httpx2.DecodingError):
            reason = Reason.INVALID_RESPONSE
        elif isinstance(error, httpx2.HTTPError):
            reason = Reason.UPSTREAM_TRANSPORT
        elif isinstance(error, (EndpointPolicyError, httpx2.InvalidURL)):
            reason = Reason.INVALID_ENDPOINT
        elif isinstance(error, ServiceError) and error.code == "payload_too_large":
            reason = Reason.RESPONSE_TOO_LARGE
        elif isinstance(error, jwt.ExpiredSignatureError):
            reason = Reason.TOKEN_EXPIRED
        elif isinstance(error, jwt.ImmatureSignatureError):
            reason = Reason.TOKEN_NOT_YET_VALID
        elif isinstance(error, jwt.InvalidIssuerError):
            reason = Reason.ISSUER_MISMATCH
        elif isinstance(error, jwt.InvalidAudienceError):
            reason = Reason.AUDIENCE_MISMATCH
        elif isinstance(error, jwt.InvalidSignatureError):
            reason = Reason.INVALID_SIGNATURE
        elif isinstance(error, (jwt.PyJWTError, ValueError, TypeError, OverflowError)):
            reason = Reason.INVALID_TOKEN if stage is Stage.ID_TOKEN else Reason.INVALID_RESPONSE
        else:
            reason = Reason.INTERNAL
        raise Failure(stage, reason, code="unavailable", status=status) from None
    finally:
        _outbound_sensitive.reset(context)


def _flow_key(name: str, provider: OIDCProvider, redirect_uri: str, state: str, browser: str) -> str:
    binding = json.dumps([name, provider.issuer, provider.client_id, redirect_uri, state, browser])
    return f"a13n:auth:oidc:{secret_hash(binding)}"


def console_destination(value: str) -> str:
    # An origin-relative path only. Browsers normalize backslashes and strip ASCII control characters.
    if not value.startswith("/") or value.startswith("//") or any(ord(c) <= 32 or c == "\\" for c in value):
        return "/"
    return value


async def start(
    redis: Redis, name: str, provider: OIDCProvider, *, redirect_uri: str, return_to: str = "/"
) -> tuple[str, str]:
    with _upstream(Stage.DISCOVERY):
        policy = oidc_endpoint_policy(provider.issuer)
        async with open_http(policy, timeout=5, max_bytes=128 * 1024) as http:
            response = await http.get(f"{provider.issuer.rstrip('/')}/.well-known/openid-configuration")
            discovery = _Discovery.model_validate_json(response.raise_for_status().content)
        for endpoint in (discovery.authorization_endpoint, discovery.token_endpoint, discovery.jwks_uri):
            # The authorization endpoint is visited by the browser, not by open_http.
            if any(c.isspace() or c in "\\#" for c in endpoint):
                raise Failure(Stage.DISCOVERY, Reason.INVALID_ENDPOINT, code="unavailable")
            policy.validate_syntax(endpoint)
        methods = discovery.code_challenge_methods_supported
        if discovery.issuer != provider.issuer:
            raise Failure(Stage.DISCOVERY, Reason.ISSUER_MISMATCH, code="unavailable")
        if methods is not None and "S256" not in methods:
            raise Failure(Stage.DISCOVERY, Reason.PKCE_UNSUPPORTED, code="unavailable")

    state, browser, nonce, verifier = (new_secret() for _ in range(4))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    params = {
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(provider.scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    target = str(httpx2.URL(discovery.authorization_endpoint).copy_merge_params(params))
    key = _flow_key(name, provider, redirect_uri, state, browser)
    await redis.set(
        key,
        json.dumps(
            [
                nonce,
                verifier,
                discovery.authorization_endpoint,
                discovery.token_endpoint,
                discovery.jwks_uri,
                console_destination(return_to),
            ]
        ),
        ex=FLOW_SECONDS,
    )
    return target, browser


async def consume(
    redis: Redis, name: str, provider: OIDCProvider, *, redirect_uri: str, state: str, browser: str
) -> Flow:
    if len(state) != 43 or len(browser) != 43:
        raise Failure(Stage.CALLBACK_STATE, Reason.INVALID_STATE)
    # The key binds the browser before consuming state; a mismatched browser or provider cannot consume it.
    value = await redis.getdel(_flow_key(name, provider, redirect_uri, state, browser))
    if value is None:
        # Expired, replayed and mismatched values all look like a missing Redis key.
        raise Failure(Stage.CALLBACK_STATE, Reason.STATE_NOT_FOUND)
    return Flow(*json.loads(value))


def _matches_org_admin(rule: OIDCOrgAdmin | None, claims: dict[str, object]) -> bool:
    if rule is None:
        return False
    # Trust a complete inline claim; distributed claims and Entra group overage need a separate lookup.
    distributed = claims.get("_claim_names")
    if (isinstance(distributed, dict) and rule.claim in distributed) or (
        rule.claim == "groups" and "hasgroups" in claims
    ):
        return False
    value = claims.get(rule.claim)
    if isinstance(value, str):
        return value == rule.value
    return isinstance(value, list) and all(isinstance(item, str) for item in value) and rule.value in value


def _identity(token: str, keys: list[jwt.PyJWK], provider: OIDCProvider, nonce: str) -> Identity:
    header = jwt.get_unverified_header(token)
    algorithm = header.get("alg")
    kid = header.get("kid")
    if algorithm not in _SIGNING_ALGORITHMS or (kid is not None and not isinstance(kid, str)):
        raise jwt.InvalidTokenError("unsupported OIDC signing algorithm or key")
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
        leeway=_TOKEN_CLOCK_SKEW_SECONDS,
        options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
    )
    if (
        claims["aud"] not in (provider.client_id, [provider.client_id])
        or claims.get("azp", provider.client_id) != provider.client_id
    ):
        raise jwt.InvalidAudienceError("unexpected ID token audience or authorized party")
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
    return Identity(
        subject, name or email or f"{provider.label} user"[:128], email, _matches_org_admin(provider.org_admin, claims)
    )


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
    with _upstream(Stage.TOKEN_EXCHANGE):
        policy = oidc_endpoint_policy(provider.issuer)
        async with open_http(policy, timeout=5, max_bytes=128 * 1024) as http:
            response = (
                await http.post(flow.token_endpoint, data=data)
                if auth is None
                else await http.post(flow.token_endpoint, data=data, auth=auth)
            )
            tokens = _TokenResponse.model_validate_json(response.raise_for_status().content)
            with _upstream(Stage.JWKS):
                jwks = (await http.get(flow.jwks_uri)).raise_for_status().json()
                if not isinstance(jwks, dict):
                    raise ValueError("invalid OIDC JWKS")
                keys = jwt.PyJWKSet.from_dict(jwks).keys
    with _upstream(Stage.ID_TOKEN):
        return _identity(tokens.id_token, keys, provider, flow.nonce)


async def _sync_org_admin(
    session: AsyncSession,
    principal_id: str,
    name: str,
    current: OIDCOrgAdminRow | None,
    organization_id: str | None,
) -> None:
    previous = current.organization_id if current is not None else None
    if organization_id == previous:
        return
    grant_id = current.id if current is not None else new_object_id("oidcrb")
    if current is not None:
        if organization_id is None:
            await session.delete(current)
        else:
            current.organization_id = organization_id
            current.created_at = await now(session)
    elif organization_id is not None:
        session.add(
            OIDCOrgAdminRow(id=grant_id, principal_id=principal_id, provider_id=name, organization_id=organization_id)
        )
    for scope, action in ((previous, "revoked"), (organization_id, "granted")):
        if scope is not None:
            record(
                session,
                Scope(scope),
                actor_id=principal_id,
                action=f"user.oidc_admin_{action}",
                target_kind="grant",
                target_id=grant_id,
                details={"provider": name},
            )
    await session.flush()


async def sign_in(
    storage: Storage, access: Access, settings: Settings, name: str, provider: OIDCProvider, identity: Identity
) -> Login:
    """Synchronize one provider's role and issue the login session in the same transaction."""
    async with transaction(storage) as session:
        await advisory_lock(session, "oidc-identity", provider.issuer, identity.subject)
        principal_id = await session.scalar(
            select(PrincipalRow.id).where(
                PrincipalRow.oidc_issuer == provider.issuer, PrincipalRow.oidc_subject == identity.subject
            )
        )
        current = (
            await session.scalar(
                select(OIDCOrgAdminRow).where(
                    OIDCOrgAdminRow.principal_id == principal_id, OIDCOrgAdminRow.provider_id == name
                )
            )
            if principal_id is not None
            else None
        )
        workspace_id = settings.auth.oidc.default_workspace_id
        mapped_admin = provider.org_admin is not None and identity.org_admin
        workspace = (
            await session.get(WorkspaceRow, workspace_id)
            if workspace_id is not None and (principal_id is None or mapped_admin)
            else None
        )
        # Match administration's organization → principal lock order. Serialize moves under both orgs.
        for organization_id in sorted({item.organization_id for item in (current, workspace) if item is not None}):
            await lock_organization(session, organization_id)
        if workspace is not None:
            await session.refresh(workspace)
        row = await lock(session, PrincipalRow, principal_id) if principal_id is not None else None
        if row is None:
            if workspace is None or workspace.archived_at is not None:
                reason = Reason.WORKSPACE_MISSING if workspace is None else Reason.WORKSPACE_ARCHIVED
                raise Failure(Stage.PROVISION, reason, code="unavailable")
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
        elif row.status != "active":
            raise Failure(Stage.PROVISION, Reason.ACCOUNT_DISABLED)
        elif row.oidc_email != identity.email:
            row.oidc_email = identity.email
        mapped_organization = workspace.organization_id if mapped_admin and workspace is not None else None
        await _sync_org_admin(session, row.id, name, current, mapped_organization)
        principal = await principal_for(session, access, row.id)
        secret = await open_session(
            session, row.id, seconds=settings.auth.oidc.session_seconds, oidc=oidc_session_data(name, provider)
        )
    return Login(principal, secret, session_csrf(secret))
