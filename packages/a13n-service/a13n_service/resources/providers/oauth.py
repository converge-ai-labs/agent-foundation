"""Workspace-shared Model Provider OAuth, with short claims around external I/O.

The Harness owns the OpenAI protocol. Service owns encrypted state, authorization,
consumption fences and renewable grant publication across Control and Workers.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Literal
from uuid import uuid4

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.model.oauth.chatgpt import (
    ChatGPTAuthorization,
    OpenAIChatGPTCredentials,
    OpenAIChatGPTOAuthFlow,
    OpenAIChatGPTRefresh,
    revoke_chatgpt_credentials,
)
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError, RefreshNotDispatched
from a13n_harness.providers.model.oauth.rotation import require_same_account
from anyio import CancelScope, sleep
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter
from pydantic_ai.exceptions import UserError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbound import open_http
from a13n_service.resources.providers.tables import ModelProviderOAuthRow, ModelProviderRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.settings import Providers
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, Verb

_CREDENTIALS = TypeAdapter(OpenAIChatGPTCredentials)
_PENDING = TypeAdapter(ChatGPTAuthorization)


class ProviderAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # ChatGPT requires a loopback redirect even when the browser cannot reach it.
    new_registration: bool = False


class AuthorizationCallback(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    attempt_id: str = Field(min_length=1, max_length=72)
    callback_url: SecretStr = Field(min_length=1, max_length=16384, repr=False, json_schema_extra={"writeOnly": True})


class AuthorizationStart(BaseModel):
    attempt_id: str
    authorization_url: str
    expires_at: datetime
    method: Literal["manual_callback"] = "manual_callback"


class AuthorizationStatus(BaseModel):
    provider_id: str
    state: Literal["disconnected", "connected", "refreshing", "reauthentication_required"]
    subject: str | None = None
    client_id: str | None = None
    email: str | None = None
    expires_at: datetime | None = None
    pending: bool = False
    message: str | None = None


class AuthorizationDisconnect(BaseModel):
    local_tokens_cleared: bool = True
    revocation_confirmed: bool | None = None


def _location(row: ModelProviderOAuthRow, column: str) -> SecretLocation:
    return SecretLocation(row.organization_id, row.__tablename__, column, row.provider_id)


def _credentials(row: ModelProviderOAuthRow, keys: KeyRing) -> OpenAIChatGPTCredentials:
    if row.tokens is None:
        raise ModelAuthenticationError("openai-chatgpt", "This Model Provider needs ChatGPT authorization.")
    return _CREDENTIALS.validate_json(keys.reveal(Envelope.model_validate(row.tokens), _location(row, "tokens")))


def _protect(
    row: ModelProviderOAuthRow, keys: KeyRing, column: str, value: OpenAIChatGPTCredentials | ChatGPTAuthorization
) -> dict:
    payload = (
        _CREDENTIALS.dump_json(value) if isinstance(value, OpenAIChatGPTCredentials) else _PENDING.dump_json(value)
    )
    return keys.protect(payload, _location(row, column)).model_dump(mode="json")


def _view(row: ModelProviderOAuthRow | None, provider_id: str) -> AuthorizationStatus:
    if row is None:
        return AuthorizationStatus(provider_id=provider_id, state="disconnected")
    state: Literal["disconnected", "connected", "refreshing", "reauthentication_required"] = "disconnected"
    interrupted = row.refresh_claim is not None and (
        row.refresh_started_at is None or datetime.now(UTC) >= row.refresh_started_at + timedelta(minutes=3)
    )
    if row.tokens is not None:
        state = (
            "reauthentication_required"
            if row.refresh_blocked or interrupted
            else "refreshing"
            if row.refresh_claim
            else "connected"
        )
    return AuthorizationStatus(
        provider_id=provider_id,
        state=state,
        subject=row.subject,
        client_id=row.client_id,
        email=row.email,
        expires_at=row.expires_at,
        pending=row.pending is not None,
        message="The previous token refresh outcome is unknown. Sign in again."
        if row.refresh_blocked or interrupted
        else None,
    )


async def authorized_provider(
    session: AsyncSession, actor: Principal, workspace_id: str, provider_id: str, verb: Verb, *, lock: bool = False
) -> ModelProviderRow:
    scope = await workspace_scope(session, actor, workspace_id, "read")
    row = await find_row(session, actor, ModelProviderRow, scope, provider_id, verb, lock=lock)
    if row.type != "openai_chatgpt":
        raise invalid("provider_id", "this Model Provider does not support ChatGPT authorization")
    return row


async def status(storage: Storage, actor: Principal, workspace_id: str, provider_id: str) -> AuthorizationStatus:
    async with short_session(storage) as session:
        await authorized_provider(session, actor, workspace_id, provider_id, "read")
        row = await session.get(ModelProviderOAuthRow, provider_id)
        return _view(row, provider_id)


async def authorize(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    provider_id: str,
    body: ProviderAuthorizationRequest,
    *,
    keys: KeyRing,
) -> AuthorizationStart:
    async with transaction(storage) as session:
        provider = await authorized_provider(session, actor, workspace_id, provider_id, "write", lock=True)
        row = await session.get(ModelProviderOAuthRow, provider_id, with_for_update=True)
        if row is None:
            row = ModelProviderOAuthRow(
                provider_id=provider_id,
                organization_id=provider.organization_id,
                workspace_id=workspace_id,
                host_id=uuid4().urn,
                refresh_blocked=False,
            )
            session.add(row)
        # Only an unregistered host can replace its rejected legacy identifier.
        if row.client_id is None and row.host_id.startswith("host_"):
            row.host_id = uuid4().urn
        credentials = _credentials(row, keys) if row.tokens is not None and not body.new_registration else None
        flow = OpenAIChatGPTOAuthFlow.start(
            ext_agent_host_id=row.host_id,
            agent_name="a13n Service",
            redirect_uri="http://127.0.0.1:1456/auth/callback",
            credentials=credentials,
        )
        if credentials is None and row.client_id and not body.new_registration:
            flow = OpenAIChatGPTOAuthFlow(
                replace(flow.authorization, client_id=row.client_id, subject=row.subject, login_hint=row.email)
            )
        attempt_id = new_object_id("oauth")
        row.pending_id = attempt_id
        row.pending = _protect(row, keys, "pending", flow.authorization)
        row.login_claim = None
        audit_row(session, actor, provider, "oauth_authorize")
        return AuthorizationStart(
            attempt_id=attempt_id,
            authorization_url=flow.authorization_url(),
            expires_at=flow.authorization.expires_at,
        )


async def complete(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    provider_id: str,
    body: AuthorizationCallback,
    *,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Providers,
) -> AuthorizationStatus:
    async with transaction(storage) as session:
        provider = await authorized_provider(session, actor, workspace_id, provider_id, "write", lock=True)
        row = await session.get(ModelProviderOAuthRow, provider_id, with_for_update=True)
        if row is None or row.pending_id != body.attempt_id or row.pending is None:
            raise conflict("model_provider", provider_id, "authorization_not_pending")
        pending = _PENDING.validate_json(keys.reveal(Envelope.model_validate(row.pending), _location(row, "pending")))
        flow = OpenAIChatGPTOAuthFlow(pending)
        try:
            flow.validate_callback(body.callback_url.get_secret_value())
        except UserError:
            raise invalid(
                "callback_url", "the complete callback URL does not match this authorization attempt"
            ) from None
        organization_id = provider.organization_id
        row.pending = None
        row.pending_id = None
        row.login_claim = body.attempt_id
    # No session survives discovery, token exchange or signing-key lookup.
    try:
        async with open_http(policy, timeout=settings.model_timeout, max_bytes=settings.response_bytes) as client:
            credentials = await OpenAIChatGPTOAuthFlow(pending, http_client=client).exchange_callback(
                body.callback_url.get_secret_value()
            )
    except Exception:
        raise ServiceError(
            "unavailable",
            "ChatGPT sign-in could not be completed. Start a new authorization; the code was consumed.",
            {"dependency": "model_oauth"},
        ) from None
    with CancelScope(shield=True):
        async with transaction(storage) as session:
            # Reuse admitted authority, but arbitrate against cancellation/replacement.
            provider = await session.get(ModelProviderRow, provider_id, with_for_update=True)
            row = await session.get(ModelProviderOAuthRow, provider_id, with_for_update=True)
            if (
                provider is None
                or row is None
                or row.organization_id != organization_id
                or row.login_claim != body.attempt_id
            ):
                raise conflict("model_provider", provider_id, "authorization_replaced")
            row.tokens = _protect(row, keys, "tokens", credentials)
            row.client_id, row.subject, row.email = credentials.client_id, credentials.subject, credentials.email
            row.expires_at = credentials.expires_at
            row.login_claim = None
            row.refresh_claim = None
            row.refresh_started_at = None
            row.refresh_blocked = False
            audit_row(session, actor, provider, "oauth_tokens")
            return _view(row, provider_id)
    raise RuntimeError("OAuth credential publication did not complete")


async def disconnect(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    provider_id: str,
    *,
    keys: KeyRing,
    policy: EndpointPolicy,
    settings: Providers,
) -> AuthorizationDisconnect:
    async with transaction(storage) as session:
        provider = await authorized_provider(session, actor, workspace_id, provider_id, "write", lock=True)
        row = await session.get(ModelProviderOAuthRow, provider_id, with_for_update=True)
        if row is None:
            return AuthorizationDisconnect()
        credentials = _credentials(row, keys) if row.tokens is not None else None
        row.tokens = None
        row.pending = None
        row.pending_id = None
        row.login_claim = None
        row.refresh_claim = None
        row.refresh_started_at = None
        row.refresh_blocked = False
        audit_row(session, actor, provider, "oauth_tokens")
    if credentials is None:
        return AuthorizationDisconnect()
    confirmed = False
    try:
        async with open_http(policy, timeout=settings.model_timeout, max_bytes=settings.response_bytes) as client:
            await revoke_chatgpt_credentials(credentials, http_client=client)
        confirmed = True
    except Exception:
        pass
    return AuthorizationDisconnect(revocation_confirmed=confirmed)


class ChatGPTCredentialSource:
    """Cross-worker rotating grants: claim/commit, exchange, then fenced publication."""

    def __init__(self, storage: Storage, keys: KeyRing, provider_id: str, organization_id: str):
        self.storage, self.keys = storage, keys
        self.provider_id, self.organization_id = provider_id, organization_id

    async def _row(self, session: AsyncSession, *, lock: bool = False) -> ModelProviderOAuthRow:
        statement = select(ModelProviderOAuthRow).where(
            ModelProviderOAuthRow.provider_id == self.provider_id,
            ModelProviderOAuthRow.organization_id == self.organization_id,
        )
        row = await session.scalar(statement.with_for_update() if lock else statement)
        if row is None:
            raise ModelAuthenticationError("openai-chatgpt", "This Model Provider needs ChatGPT authorization.")
        return row

    async def load(self) -> OpenAIChatGPTCredentials:
        deadline = monotonic() + 30
        while True:
            async with short_session(self.storage) as session:
                row = await self._row(session)
                if row.refresh_blocked:
                    raise ModelAuthenticationError(
                        "openai-chatgpt", "The previous refresh outcome is unknown. Sign in again."
                    )
                if row.refresh_claim is None:
                    return _credentials(row, self.keys)
                if row.refresh_started_at is None or datetime.now(UTC) >= row.refresh_started_at + timedelta(minutes=3):
                    raise ModelAuthenticationError(
                        "openai-chatgpt", "An interrupted refresh cannot be replayed. Sign in again."
                    )
            if monotonic() >= deadline:
                raise ModelAuthenticationError(
                    "openai-chatgpt", "Another worker is refreshing this Model Provider. Retry later."
                )
            await sleep(0.1)

    async def rotate(
        self, expected: OpenAIChatGPTCredentials, exchange: OpenAIChatGPTRefresh
    ) -> OpenAIChatGPTCredentials:
        while True:
            current = await self.load()
            require_same_account(expected, current)
            if current != expected:
                return current
            async with transaction(self.storage) as session:
                row = await self._row(session, lock=True)
                current = _credentials(row, self.keys)
                require_same_account(expected, current)
                if current != expected:
                    return current
                if row.refresh_claim is not None:
                    continue
                if row.refresh_blocked:
                    raise ModelAuthenticationError(
                        "openai-chatgpt", "The previous refresh outcome is unknown. Sign in again."
                    )
                claim = new_object_id("grant")
                row.refresh_claim = claim
                row.refresh_started_at = datetime.now(UTC)
            break
        try:
            rotated = await exchange(current)
            require_same_account(current, rotated)
        except BaseException as error:
            with CancelScope(shield=True):
                async with transaction(self.storage) as session:
                    row = await self._row(session, lock=True)
                    if row.refresh_claim == claim:
                        row.refresh_claim = None
                        row.refresh_started_at = None
                        row.refresh_blocked = not isinstance(error, RefreshNotDispatched)
            raise
        with CancelScope(shield=True):
            async with transaction(self.storage) as session:
                row = await self._row(session, lock=True)
                if row.refresh_claim != claim:
                    raise ModelAuthenticationError("openai-chatgpt", "The authorization changed during token refresh.")
                row.tokens = _protect(row, self.keys, "tokens", rotated)
                row.expires_at = rotated.expires_at
                row.refresh_claim = None
                row.refresh_started_at = None
                row.refresh_blocked = False
        return rotated
