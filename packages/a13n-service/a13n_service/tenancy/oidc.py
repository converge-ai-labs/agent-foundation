"""OIDC authorization code exchange and first-login provisioning for the Service browser session."""

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote_plus

import httpx2
import jwt
from a13n_harness.providers.endpoint_policy import EndpointPolicyError
from a13n_logging import exception_details, get_logger
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
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
from a13n_service.tenancy.tables import OIDCGrantRow, OrganizationRow, PrincipalRow, WorkspaceRow

FLOW_SECONDS = 600
_TOKEN_CLOCK_SKEW_SECONDS = 30
_SIGNING_ALGORITHMS = ("RS256", "ES256")
_PROVIDER_ERROR = re.compile(r"[a-z0-9_.-]{1,64}")


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
    matched_grants: tuple[OIDCGrantSpec, ...] = ()


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
    except jwt.PyJWTError as error:
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
            reason = Reason.INVALID_TOKEN if stage is Stage.ID_TOKEN else Reason.INVALID_RESPONSE
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


async def start(
    redis: Redis, name: str, provider: OIDCProvider, *, redirect_uri: str, return_to: str = "/"
) -> tuple[str, str]:
    with _upstream(Stage.DISCOVERY):
        policy = oidc_endpoint_policy(provider.issuer)
        async with open_http(policy, timeout=5, max_bytes=128 * 1024) as http:
            response = await http.get(f"{provider.issuer.rstrip('/')}/.well-known/openid-configuration")
            discovery = _parse_response(_Discovery, response, Stage.DISCOVERY)
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
    try:
        claims = jwt.decode(
            token,
            matches[0],
            algorithms=[algorithm],
            audience=provider.client_id,
            issuer=provider.issuer,
            leeway=_TOKEN_CLOCK_SKEW_SECONDS,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
        )
    except (TypeError, OverflowError):
        # PyJWT's NumericDate conversion can raise these on invalid signed claim types or values.
        raise jwt.InvalidTokenError("invalid ID token claim type") from None
    if (
        claims["aud"] not in (provider.client_id, [provider.client_id])
        or claims.get("azp", provider.client_id) != provider.client_id
    ):
        raise jwt.InvalidAudienceError("unexpected ID token audience or authorized party")
    received = claims["nonce"]
    if not isinstance(received, str) or not received.isascii() or not hmac.compare_digest(received, nonce):
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
    matched = tuple(
        rule
        for rule in provider.role_rules
        if all(_match_claim(claim, values, claims) for claim, values in rule.match.items())
    )
    return Identity(subject, name or email or f"{provider.label} user"[:128], email, matched)


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
        secret = await open_session(
            session, row.id, seconds=settings.auth.oidc.session_seconds, oidc=oidc_session_data(name, provider)
        )
    return Login(principal, secret, session_csrf(secret))
