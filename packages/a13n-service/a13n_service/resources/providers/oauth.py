"""Workspace-shared Model Provider OAuth, with short claims around external I/O.

Pydantic AI owns the OpenAI protocol and refresh. Service owns encrypted state,
authorization, pending-attempt consumption and credential publication.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import uuid4

from a13n_harness.providers.model.oauth.chatgpt import (
    ChatGPTAuthorization,
    StoredChatGPTCredentials,
    credential_metadata,
)
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError
from a13n_harness.providers.model.openai_chatgpt import Config
from anyio import CancelScope
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials, OpenAIChatGPTCredentialSource
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.providers.tables import ModelProviderOAuthRow, ModelProviderRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.settings import Providers
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, Verb

_CREDENTIALS = TypeAdapter(StoredChatGPTCredentials)
_PENDING = TypeAdapter(ChatGPTAuthorization)


class ProviderAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Clear retained account hints; a configured client remains selected.
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
    state: Literal["disconnected", "connected", "reauthentication_required"]
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


def _view(row: ModelProviderOAuthRow | None, provider_id: str, keys: KeyRing) -> AuthorizationStatus:
    if row is None:
        return AuthorizationStatus(provider_id=provider_id, state="disconnected")
    state: Literal["disconnected", "connected", "reauthentication_required"] = "disconnected"
    if row.tokens is not None:
        state = "connected" if _credentials(row, keys).plan_usage_granted else "reauthentication_required"
    return AuthorizationStatus(
        provider_id=provider_id,
        state=state,
        subject=row.subject,
        client_id=row.client_id,
        email=row.email,
        expires_at=row.expires_at,
        pending=row.pending is not None or row.login_claim is not None,
        message="Sign in again and approve ChatGPT plan usage."
        if state == "reauthentication_required"
        else "ChatGPT sign-in is completing. Restart authorization if it does not finish."
        if row.login_claim is not None
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


async def status(
    storage: Storage, actor: Principal, workspace_id: str, provider_id: str, *, keys: KeyRing
) -> AuthorizationStatus:
    async with short_session(storage) as session:
        await authorized_provider(session, actor, workspace_id, provider_id, "read")
        row = await session.get(ModelProviderOAuthRow, provider_id)
        return _view(row, provider_id, keys)


async def authorize(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    provider_id: str,
    body: ProviderAuthorizationRequest,
    *,
    keys: KeyRing,
    settings: Providers,
) -> AuthorizationStart:
    async with transaction(storage) as session:
        provider = await authorized_provider(session, actor, workspace_id, provider_id, "write", lock=True)
        config = Config.model_validate(provider.config)
        selected_client = config.client_id or settings.chatgpt_client_id
        redirect_uri = config.redirect_uri or settings.chatgpt_redirect_uri
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
        retained_client = selected_client is None or row.client_id == selected_client
        reuse_registration = retained_client and not body.new_registration
        credentials = _credentials(row, keys) if row.tokens is not None and reuse_registration else None
        pending = ChatGPTAuthorization.start(
            host_id=row.host_id,
            redirect_uri=redirect_uri,
            client_id=selected_client or (row.client_id if reuse_registration else None),
            credentials=credentials,
        )
        attempt_id = new_object_id("oauth")
        row.pending_id = attempt_id
        row.pending = _protect(row, keys, "pending", pending)
        row.login_claim = None
        audit_row(session, actor, provider, "oauth_authorize")
        return AuthorizationStart(
            attempt_id=attempt_id,
            authorization_url=pending.authorization_url(),
            expires_at=pending.expires_at,
        )


async def complete(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    provider_id: str,
    body: AuthorizationCallback,
    *,
    keys: KeyRing,
) -> AuthorizationStatus:
    async with transaction(storage) as session:
        provider = await authorized_provider(session, actor, workspace_id, provider_id, "write", lock=True)
        row = await session.get(ModelProviderOAuthRow, provider_id, with_for_update=True)
        if row is None or row.pending_id != body.attempt_id or row.pending is None:
            raise conflict("model_provider", provider_id, "authorization_not_pending")
        pending = _PENDING.validate_json(keys.reveal(Envelope.model_validate(row.pending), _location(row, "pending")))
        try:
            pending.validate_callback(body.callback_url.get_secret_value())
        except UserError:
            raise invalid(
                "callback_url", "the complete callback URL does not match this authorization attempt"
            ) from None
        organization_id = provider.organization_id
        row.pending = None
        row.pending_id = None
        row.login_claim = body.attempt_id
    # The official flow exchanges the code without holding a database session.
    try:
        credentials = await pending.exchange_callback(body.callback_url.get_secret_value())
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
            row.client_id = credentials.client_id
            row.subject, row.email, row.expires_at = credential_metadata(credentials)
            row.login_claim = None
            row.refresh_claim = None
            row.refresh_started_at = None
            row.refresh_blocked = False
            audit_row(session, actor, provider, "oauth_tokens")
            return _view(row, provider_id, keys)
    raise RuntimeError("OAuth credential publication did not complete")


async def disconnect(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    provider_id: str,
    *,
    keys: KeyRing,
) -> AuthorizationDisconnect:
    async with transaction(storage) as session:
        provider = await authorized_provider(session, actor, workspace_id, provider_id, "write", lock=True)
        row = await session.get(ModelProviderOAuthRow, provider_id, with_for_update=True)
        if row is None:
            return AuthorizationDisconnect()
        row.tokens = None
        row.pending = None
        row.pending_id = None
        row.login_claim = None
        row.refresh_claim = None
        row.refresh_started_at = None
        row.refresh_blocked = False
        audit_row(session, actor, provider, "oauth_tokens")
    return AuthorizationDisconnect()


class ChatGPTCredentialSource(OpenAIChatGPTCredentialSource):
    """Encrypted load/save storage; Pydantic AI owns refresh and retries."""

    def __init__(self, storage: Storage, keys: KeyRing, provider_id: str, organization_id: str):
        self.storage, self.keys = storage, keys
        self.provider_id, self.organization_id = provider_id, organization_id
        self._loaded: OpenAIChatGPTCredentials | None = None

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
        async with short_session(self.storage) as session:
            current = _credentials(await self._row(session), self.keys)
        if self._loaded is not None and current.client_id != self._loaded.client_id:
            raise ModelAuthenticationError("openai-chatgpt", "The ChatGPT registration changed.")
        self._loaded = current
        return current

    async def save(self, credentials: OpenAIChatGPTCredentials) -> None:
        with CancelScope(shield=True):
            async with transaction(self.storage) as session:
                row = await self._row(session, lock=True)
                if (
                    self._loaded is None
                    or _credentials(row, self.keys) != self._loaded
                    or credentials.client_id != self._loaded.client_id
                ):
                    raise ModelAuthenticationError("openai-chatgpt", "ChatGPT authorization changed during refresh.")
                row.tokens = _protect(row, self.keys, "tokens", credentials)
                row.subject, row.email, row.expires_at = credential_metadata(credentials)
            self._loaded = credentials
