"""OIDC sign-in and logout for Service browser sessions."""

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from time import time
from urllib.parse import quote_plus

import httpx2
import jwt
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from a13n_logging import exception_details, get_logger
from pydantic import BaseModel, ValidationError, field_validator
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.audit import record
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import Storage, advisory_lock, lock, transaction
from a13n_service.infra.errors import ErrorCode, ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbound import open_http
from a13n_service.settings import OIDCGrantSpec, OIDCProvider, Settings, oidc_endpoint_policy
from a13n_service.tenancy.access import Access, lock_organization, principal_for
from a13n_service.tenancy.authenticate import Login, oidc_session_data, open_session, session_csrf
from a13n_service.tenancy.authorize import Scope, WorkspaceScope
from a13n_service.tenancy.credentials import new_secret
from a13n_service.tenancy.grants import add_grant
from a13n_service.tenancy.tables import OIDCGrantRow, OrganizationRow, PrincipalRow, TokenRow, WorkspaceRow

FLOW_SECONDS = 600
_TOKEN_CLOCK_SKEW_SECONDS = 30
_SIGNING_ALGORITHMS = ("RS256", "ES256")
_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
_PROVIDER_ERROR = re.compile(r"[a-z0-9_.-]{1,64}")


class _Discovery(BaseModel):
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    code_challenge_methods_supported: list[str] | None = None
    end_session_endpoint: str | None = None

    @field_validator("end_session_endpoint", mode="before")
    @classmethod
    def optional_logout_endpoint(cls, value: object) -> str | None:
        return value if isinstance(value, str) else None


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
    matched_grants: tuple[OIDCGrantSpec, ...] = ()
    sid: str | None = None


class Stage(StrEnum):
    START = "start"
    DISCOVERY = "discovery"
    CALLBACK_STATE = "callback_state"
    CALLBACK_ERROR = "callback_error"
    TOKEN_EXCHANGE = "token_exchange"
    JWKS = "jwks"
    ID_TOKEN = "id_token"
    LOGOUT_TOKEN = "logout_token"
    PROVISION = "provision"


class Reason(StrEnum):
    UNKNOWN_PROVIDER = "unknown_provider"
    NOT_INITIALIZED = "not_initialized"
    RATE_LIMITED = "rate_limited"
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
    ACCESS_DENIED = "access_denied"
    ORGANIZATION_MISSING = "organization_missing"
    WORKSPACE_MISSING = "workspace_missing"
    WORKSPACE_ARCHIVED = "workspace_archived"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    INTERNAL = "internal"


def callback_error_code(errors: list[str]) -> str:
    return errors[0] if len(errors) == 1 and _PROVIDER_ERROR.fullmatch(errors[0]) else "unknown"


class Failure(ServiceError):
    """A browser sign-in refusal with diagnostics safe to log verbatim."""

    def __init__(
        self,
        stage: Stage,
        reason: Reason,
        *,
        code: ErrorCode = "unauthenticated",
        status: int | None = None,
        provider_error: str | None = None,
    ) -> None:
        super().__init__(code, "OIDC sign-in failed")
        self.stage = stage
        self.reason = reason
        self.status = status
        self.provider_error = provider_error


def log_failure(provider_id: str | None, stage: Stage, error: Exception) -> None:
    # Only the configured provider key and bounded diagnostics cross the logging boundary.
    if isinstance(error, Failure):
        reason = error.reason
        stage = error.stage
    elif stage is Stage.START and isinstance(error, ServiceError) and error.code == "rate_limited":
        reason = Reason.RATE_LIMITED
    elif isinstance(error, (RedisError, SQLAlchemyError, TimeoutError)):
        reason = Reason.DEPENDENCY_UNAVAILABLE
    else:
        reason = Reason.INTERNAL
    fields = {"provider_id": provider_id, "stage": stage.value, "reason": reason.value}
    if isinstance(error, Failure):
        if error.status is not None:
            fields["http_status"] = error.status
        if error.provider_error is not None:
            fields["provider_error"] = error.provider_error
    if reason is Reason.INTERNAL:
        get_logger(__name__).error(
            "OIDC sign-in failed", extra={**fields, "exception_details": exception_details(error)}
        )
    elif reason in {Reason.ORGANIZATION_MISSING, Reason.WORKSPACE_MISSING}:
        get_logger(__name__).warning("OIDC sign-in failed", extra=fields)
    else:
        get_logger(__name__).info("OIDC sign-in failed", extra=fields)


@contextmanager
def _upstream(stage: Stage) -> Iterator[None]:
    try:
        yield
    except httpx2.HTTPStatusError as error:
        raise Failure(stage, Reason.HTTP_STATUS, code="unavailable", status=error.response.status_code) from None
    except (httpx2.TimeoutException, TimeoutError):
        raise Failure(stage, Reason.UPSTREAM_TIMEOUT, code="unavailable") from None
    except httpx2.DecodingError:
        raise Failure(stage, Reason.INVALID_RESPONSE, code="unavailable") from None
    except (EndpointPolicyError, httpx2.InvalidURL):
        raise Failure(stage, Reason.INVALID_ENDPOINT, code="unavailable") from None
    except httpx2.HTTPError:
        raise Failure(stage, Reason.UPSTREAM_TRANSPORT, code="unavailable") from None
    except ServiceError as error:
        if error.code != "payload_too_large":
            raise
        raise Failure(stage, Reason.RESPONSE_TOO_LARGE, code="unavailable") from None
    except (jwt.PyJWTError, RecursionError) as error:
        if isinstance(error, jwt.ExpiredSignatureError):
            reason = Reason.TOKEN_EXPIRED
        elif isinstance(error, jwt.ImmatureSignatureError):
            reason = Reason.TOKEN_NOT_YET_VALID
        elif isinstance(error, jwt.InvalidIssuerError):
            reason = Reason.ISSUER_MISMATCH
        elif isinstance(error, jwt.InvalidAudienceError):
            reason = Reason.AUDIENCE_MISMATCH
        elif isinstance(error, jwt.InvalidSignatureError):
            reason = Reason.INVALID_SIGNATURE
        else:
            reason = Reason.INVALID_TOKEN if stage in {Stage.ID_TOKEN, Stage.LOGOUT_TOKEN} else Reason.INVALID_RESPONSE
        raise Failure(stage, reason, code="unavailable") from None


def _parse_response[T: BaseModel](model: type[T], response: httpx2.Response, stage: Stage) -> T:
    data = response.raise_for_status().content
    try:
        return model.model_validate_json(data)
    except ValidationError:
        raise Failure(stage, Reason.INVALID_RESPONSE, code="unavailable") from None


def _parse_jwks(response: httpx2.Response) -> list[jwt.PyJWK]:
    response.raise_for_status()
    try:
        data = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Failure(Stage.JWKS, Reason.INVALID_RESPONSE, code="unavailable") from None
    if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
        raise Failure(Stage.JWKS, Reason.INVALID_RESPONSE, code="unavailable")
    try:
        return jwt.PyJWKSet.from_dict(data).keys
    except (TypeError, ValueError):
        # PyJWT key decoders also raise these for malformed key material.
        raise Failure(Stage.JWKS, Reason.INVALID_RESPONSE, code="unavailable") from None


def _flow_key(name: str, provider: OIDCProvider, redirect_uri: str, state: str, browser: str) -> str:
    binding = json.dumps([name, provider.issuer, provider.client_id, redirect_uri, state, browser])
    return f"a13n:auth:oidc:{secret_hash(binding)}"


def console_destination(value: str) -> str:
    # An origin-relative path only. Browsers normalize backslashes and strip ASCII control characters.
    if not value.startswith("/") or value.startswith("//") or any(ord(c) <= 32 or c == "\\" for c in value):
        return "/"
    return value


async def _discover(provider: OIDCProvider) -> _Discovery:
    with _upstream(Stage.DISCOVERY):
        async with open_http(oidc_endpoint_policy(provider.issuer), timeout=5, max_bytes=128 * 1024) as http:
            response = await http.get(f"{provider.issuer.rstrip('/')}/.well-known/openid-configuration")
            discovery = _parse_response(_Discovery, response, Stage.DISCOVERY)
        if discovery.issuer != provider.issuer:
            raise Failure(Stage.DISCOVERY, Reason.ISSUER_MISMATCH, code="unavailable")
        return discovery


def _browser_endpoint(provider: OIDCProvider, endpoint: str) -> httpx2.URL:
    # The browser visits these URLs directly, outside of open_http.
    if any(not c.isprintable() or c.isspace() or c in "\\#" for c in endpoint):
        raise EndpointPolicyError("invalid OIDC browser endpoint")
    oidc_endpoint_policy(provider.issuer).validate_syntax(endpoint)
    return httpx2.URL(endpoint)


async def start(
    redis: Redis, name: str, provider: OIDCProvider, *, redirect_uri: str, return_to: str = "/"
) -> tuple[str, str]:
    discovery = await _discover(provider)
    with _upstream(Stage.DISCOVERY):
        authorization = _browser_endpoint(provider, discovery.authorization_endpoint)
        policy = oidc_endpoint_policy(provider.issuer)
        for endpoint in (discovery.token_endpoint, discovery.jwks_uri):
            if any(c.isspace() or c in "\\#" for c in endpoint):
                raise Failure(Stage.DISCOVERY, Reason.INVALID_ENDPOINT, code="unavailable")
            policy.validate_syntax(endpoint)
        methods = discovery.code_challenge_methods_supported
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
    target = str(authorization.copy_merge_params(params))
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
    # The key binds the browser before consuming state; a mismatched browser or provider cannot consume it.
    value = await redis.getdel(_flow_key(name, provider, redirect_uri, state, browser))
    if value is None:
        # Expired, replayed and mismatched values all look like a missing Redis key.
        raise Failure(Stage.CALLBACK_STATE, Reason.STATE_NOT_FOUND)
    return Flow(*json.loads(value))


def _match_claim(claim: str, expected: str | list[str], claims: dict[str, object]) -> bool:
    distributed = claims.get("_claim_names")
    if isinstance(distributed, dict) and claim in distributed:
        return False
    if claim in claims:
        value = claims[claim]
    else:
        root, *path = claim.split(".")
        if isinstance(distributed, dict) and root in distributed:
            return False
        value = claims.get(root)
        for part in path:
            if not isinstance(value, dict) or part not in value:
                return False
            value = value[part]
    alternatives = (expected,) if isinstance(expected, str) else expected
    if isinstance(value, str):
        return value in alternatives
    return (
        isinstance(value, list)
        and all(isinstance(item, str) for item in value)
        and any(item in alternatives for item in value)
    )


def _signing_header(token: str) -> tuple[str, str | None]:
    header = jwt.get_unverified_header(token)
    algorithm = header.get("alg")
    kid = header.get("kid")
    if (
        not isinstance(algorithm, str)
        or algorithm not in _SIGNING_ALGORITHMS
        or (kid is not None and not isinstance(kid, str))
    ):
        raise jwt.InvalidTokenError("unsupported OIDC signing algorithm or key")
    return algorithm, kid


def _decode_token(
    token: str, header: tuple[str, str | None], keys: list[jwt.PyJWK], provider: OIDCProvider, *, required: list[str]
) -> dict[str, object]:
    algorithm, kid = header
    matches = [
        key
        for key in keys
        if key.algorithm_name == algorithm
        and key.public_key_use in (None, "sig")
        and (kid is None or key.key_id == kid)
    ]
    if len(matches) != 1:
        raise jwt.InvalidTokenError("no unique OIDC signing key")
    try:
        return jwt.decode(
            token,
            matches[0],
            algorithms=[algorithm],
            audience=provider.client_id,
            issuer=provider.issuer,
            leeway=_TOKEN_CLOCK_SKEW_SECONDS,
            options={"require": required},
        )
    except (TypeError, OverflowError):
        # PyJWT's NumericDate conversion can raise these on invalid signed claim types or values.
        raise jwt.InvalidTokenError("invalid OIDC token claim type") from None


def _subject_id(subject: object) -> str | None:
    return (
        subject
        if isinstance(subject, str) and 0 < len(subject) <= 255 and subject.isascii() and subject.isprintable()
        else None
    )


def _session_id(sid: object) -> str | None:
    return sid if isinstance(sid, str) and sid and sid.isascii() and sid.isprintable() else None


def _identity(token: str, keys: list[jwt.PyJWK], provider: OIDCProvider, nonce: str) -> Identity:
    claims = _decode_token(
        token, _signing_header(token), keys, provider, required=["iss", "sub", "aud", "exp", "iat", "nonce"]
    )
    if (
        claims["aud"] not in (provider.client_id, [provider.client_id])
        or claims.get("azp", provider.client_id) != provider.client_id
    ):
        raise jwt.InvalidAudienceError("unexpected ID token audience or authorized party")
    received = claims["nonce"]
    if not isinstance(received, str) or not received.isascii() or not hmac.compare_digest(received, nonce):
        raise jwt.InvalidTokenError("ID token nonce does not match")
    subject = _subject_id(claims["sub"])
    if subject is None:
        raise jwt.InvalidTokenError("invalid ID token subject")
    email_claim = claims.get("email")
    email = email_claim.strip() if isinstance(email_claim, str) else ""
    email = (
        email
        if (not provider.require_email_verified or claims.get("email_verified") is True)
        and 0 < len(email) <= 320
        and email.isprintable()
        else None
    )
    name = claims.get("name")
    name = name.strip() if isinstance(name, str) and name.isprintable() else ""
    matched = tuple(
        rule
        for rule in provider.role_rules
        if all(_match_claim(claim, values, claims) for claim, values in rule.match.items())
    )
    return Identity(
        subject, (name or email or f"{provider.label} user")[:128], email, matched, _session_id(claims.get("sid"))
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
            tokens = _parse_response(_TokenResponse, response, Stage.TOKEN_EXCHANGE)
            with _upstream(Stage.JWKS):
                keys = _parse_jwks(await http.get(flow.jwks_uri))
    with _upstream(Stage.ID_TOKEN):
        return _identity(tokens.id_token, keys, provider, flow.nonce)


async def logout_redirect(provider: OIDCProvider, *, post_logout_redirect_uri: str) -> str | None:
    try:
        endpoint = (await _discover(provider)).end_session_endpoint
        if endpoint is None:
            return None
        with _upstream(Stage.DISCOVERY):
            return str(
                _browser_endpoint(provider, endpoint).copy_merge_params(
                    {
                        "client_id": provider.client_id,
                        "post_logout_redirect_uri": post_logout_redirect_uri,
                    }
                )
            )
    except Failure:
        # Discovery is best-effort; the local session has already ended.
        return None


@dataclass(frozen=True, slots=True)
class LogoutTarget:
    subject: str | None
    sid: str | None


def _logout_target(claims: dict[str, object]) -> LogoutTarget:
    issued, expires, current = claims["iat"], claims.get("exp"), time()
    if (
        not isinstance(issued, (int, float))
        or isinstance(issued, bool)
        or not current - _TOKEN_CLOCK_SKEW_SECONDS <= issued <= current + _TOKEN_CLOCK_SKEW_SECONDS
    ):
        raise jwt.InvalidTokenError("logout token outside the issued-at window")
    if "exp" in claims and (
        not isinstance(expires, (int, float)) or isinstance(expires, bool) or not expires > current
    ):
        raise jwt.InvalidTokenError("logout token expired or invalid")
    events = claims.get("events")
    if "nonce" in claims or not isinstance(events, dict) or not isinstance(events.get(_LOGOUT_EVENT), dict):
        raise jwt.InvalidTokenError("invalid logout event or nonce")
    subject, sid = _subject_id(claims.get("sub")), _session_id(claims.get("sid"))
    if (
        (subject is None and sid is None)
        or ("sub" in claims and subject is None)
        or ("sid" in claims and sid is None)
        or not claims["jti"]
    ):
        raise jwt.InvalidTokenError("invalid logout token identifiers")
    return LogoutTarget(subject, sid)


async def verify_logout_token(provider: OIDCProvider, token: str) -> LogoutTarget:
    with _upstream(Stage.LOGOUT_TOKEN):
        header = _signing_header(token)
    discovery = await _discover(provider)
    with _upstream(Stage.JWKS):
        async with open_http(oidc_endpoint_policy(provider.issuer), timeout=5, max_bytes=128 * 1024) as http:
            keys = _parse_jwks(await http.get(discovery.jwks_uri))
    with _upstream(Stage.LOGOUT_TOKEN):
        claims = _decode_token(token, header, keys, provider, required=["iss", "aud", "iat", "jti"])
        return _logout_target(claims)


async def revoke_logout_sessions(storage: Storage, name: str, provider: OIDCProvider, target: LogoutTarget) -> None:
    binding = oidc_session_data(name, provider)
    if target.sid is not None:
        binding["sid"] = target.sid
        match = TokenRow.data.contains(binding)
    else:
        principal_id = (
            select(PrincipalRow.id)
            .where(PrincipalRow.oidc_issuer == provider.issuer, PrincipalRow.oidc_subject == target.subject)
            .scalar_subquery()
        )
        match = TokenRow.data.contains(binding) & (TokenRow.principal_id == principal_id)
    async with transaction(storage) as session:
        revoked = await session.scalars(
            update(TokenRow)
            .where(
                TokenRow.kind == "session",
                TokenRow.revoked_at.is_(None),
                TokenRow.expires_at > func.clock_timestamp(),
                match,
            )
            .values(revoked_at=func.clock_timestamp())
            .returning(TokenRow.id)
        )
        for session_id in revoked:
            record(
                session,
                None,
                actor_id=None,
                action="login_session.revoke",
                target_kind="login_session",
                target_id=session_id,
                details={"provider": name, "source": "oidc_backchannel"},
            )


async def _resolve_grants(
    session: AsyncSession, access: Access, requested: tuple[OIDCGrantSpec, ...] | list[OIDCGrantSpec]
) -> tuple[dict[Scope | WorkspaceScope, str], dict[str, WorkspaceRow]]:
    workspaces = {spec.workspace_id for spec in requested if spec.workspace_id is not None}
    organizations = {spec.organization_id for spec in requested if spec.organization_id is not None}
    workspace_rows = (
        {row.id: row for row in await session.scalars(select(WorkspaceRow).where(WorkspaceRow.id.in_(workspaces)))}
        if workspaces
        else {}
    )
    if workspaces != workspace_rows.keys():
        raise Failure(Stage.PROVISION, Reason.WORKSPACE_MISSING, code="unavailable")
    if organizations and organizations != set(
        await session.scalars(select(OrganizationRow.id).where(OrganizationRow.id.in_(organizations)))
    ):
        raise Failure(Stage.PROVISION, Reason.ORGANIZATION_MISSING, code="unavailable")
    result: dict[Scope | WorkspaceScope, str] = {}
    for spec in requested:
        access.check_role(spec.role)
        scope: Scope | WorkspaceScope
        if spec.workspace_id is not None:
            workspace = workspace_rows[spec.workspace_id]
            scope = WorkspaceScope(workspace.organization_id, workspace.id)
        else:
            assert spec.organization_id is not None
            scope = Scope(spec.organization_id)
        previous = result.get(scope)
        if previous is None or (-len(access.roles[spec.role]), spec.role) < (-len(access.roles[previous]), previous):
            result[scope] = spec.role
    return result, workspace_rows


async def _sync_grants(
    session: AsyncSession,
    principal_id: str,
    provider_id: str,
    current: list[OIDCGrantRow],
    desired: dict[Scope | WorkspaceScope, str],
) -> None:
    remaining = desired.copy()
    for row in current:
        scope = (
            WorkspaceScope(row.organization_id, row.workspace_id) if row.workspace_id else Scope(row.organization_id)
        )
        if (role := remaining.pop(scope, None)) is None:
            await session.delete(row)
            action = "revoked"
        elif role != row.role:
            row.role = role
            action = "changed"
        else:
            continue
        record(
            session,
            scope,
            actor_id=principal_id,
            action=f"user.oidc_grant_{action}",
            target_kind="grant",
            target_id=row.id,
            details={"provider": provider_id, "role": row.role},
        )
    for scope, role in remaining.items():
        grant_id = new_object_id("oidcrb")
        session.add(
            OIDCGrantRow(
                id=grant_id,
                principal_id=principal_id,
                provider_id=provider_id,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                role=role,
            )
        )
        record(
            session,
            scope,
            actor_id=principal_id,
            action="user.oidc_grant_granted",
            target_kind="grant",
            target_id=grant_id,
            details={"provider": provider_id, "role": role},
        )
    await session.flush()


async def sign_in(
    storage: Storage, access: Access, settings: Settings, name: str, provider: OIDCProvider, identity: Identity
) -> Login:
    """Provision or synchronize the provider's grants and open the session in one transaction."""
    async with transaction(storage) as session:
        await advisory_lock(session, "oidc-identity", provider.issuer, identity.subject)
        principal_id = await session.scalar(
            select(PrincipalRow.id).where(
                PrincipalRow.oidc_issuer == provider.issuer,
                PrincipalRow.oidc_subject == identity.subject,
            )
        )
        current = (
            list(
                await session.scalars(
                    select(OIDCGrantRow).where(
                        OIDCGrantRow.principal_id == principal_id,
                        OIDCGrantRow.provider_id == name,
                    )
                )
            )
            if principal_id is not None
            else []
        )
        defaults = settings.auth.oidc.defaults_for(provider)
        synchronized = bool(provider.role_rules)
        if synchronized:
            requested = identity.matched_grants or defaults
            if not requested:
                raise Failure(Stage.PROVISION, Reason.ACCESS_DENIED)
        else:
            requested = defaults if principal_id is None else []
        desired, workspaces = await _resolve_grants(session, access, requested)
        # Match IAM's lock order, including every old and new organization on reassignment.
        organizations = {row.organization_id for row in current} | {scope.organization_id for scope in desired}
        for organization_id in sorted(organizations):
            await lock_organization(session, organization_id)
        row = await lock(session, PrincipalRow, principal_id) if principal_id is not None else None
        if row is None:
            for workspace in workspaces.values():
                await session.refresh(workspace)
                if workspace.archived_at is not None:
                    raise Failure(Stage.PROVISION, Reason.WORKSPACE_ARCHIVED, code="unavailable")
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
            if not synchronized:
                for scope, role in desired.items():
                    await add_grant(session, row, scope, role, actor_id=row.id, details={"provider": name})
            record(
                session,
                None,
                actor_id=row.id,
                action="user.oidc_provision",
                target_kind="user",
                target_id=row.id,
                details={"provider": name},
            )
        elif row.status != "active":
            raise Failure(Stage.PROVISION, Reason.ACCOUNT_DISABLED)
        elif row.oidc_email != identity.email:
            row.oidc_email = identity.email
        await _sync_grants(session, row.id, name, current, desired if synchronized else {})
        principal = await principal_for(session, access, row.id)
        data = oidc_session_data(name, provider)
        if identity.sid is not None:
            data["sid"] = identity.sid
        secret = await open_session(session, row.id, seconds=settings.auth.oidc.session_seconds, oidc=data)
    return Login(principal, secret, session_csrf(secret))
