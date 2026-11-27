"""Workspace-shared Model Provider OAuth, with short claims around external I/O.

Harness and Pydantic AI own the provider protocols. Service owns encrypted state,
authorization, pending-attempt consumption and credential publication.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

import httpx2
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from a13n_harness.providers.model.definition import ModelOAuthCredentialSource
from a13n_harness.providers.model.oauth.chatgpt import (
    ChatGPTAuthorization,
    StoredChatGPTCredentials,
    credential_metadata,
)
from a13n_harness.providers.model.oauth.google_ai_plan import (
    LOGIN_TIMEOUT,
    GoogleAIPlanClient,
    GoogleAIPlanCredentials,
    GoogleAIPlanFlow,
    callback_code,
    refresh_google_ai_plan,
)
from a13n_harness.providers.model.oauth.google_ai_plan import (
    SCHEME as GOOGLE_SCHEME,
)
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError
from a13n_harness.providers.model.openai_chatgpt import Config
from anyio import CancelScope, fail_after, sleep
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials, OpenAIChatGPTCredentialSource
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

_CREDENTIALS = TypeAdapter(StoredChatGPTCredentials)
_PENDING = TypeAdapter(ChatGPTAuthorization)
_REFRESH_SECONDS = 30

type OAuthScheme = Literal["openai-chatgpt", "google-ai-plan"]
_OAUTH_SCHEMES: dict[str, OAuthScheme] = {
    definition.type: definition.oauth.scheme for definition in BUILT_IN_MODEL_PROVIDERS if definition.oauth
}
type Credentials = OpenAIChatGPTCredentials | GoogleAIPlanCredentials


def oauth_scheme(provider_type: str) -> OAuthScheme | None:
    return _OAUTH_SCHEMES.get(provider_type)


class GoogleAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    client_id: str
    redirect_uri: str
    state: str = Field(repr=False)
    code_verifier: str = Field(repr=False)
    nonce: str = Field(repr=False)
    expires_at: datetime

    @classmethod
    def capture(cls, flow: GoogleAIPlanFlow, flow_seconds: int) -> GoogleAuthorization:
        return cls(
            client_id=flow.registration.client_id,
            redirect_uri=flow.redirect_uri,
            state=flow.state,
            code_verifier=flow.code_verifier,
            nonce=flow.nonce,
            expires_at=datetime.now(UTC) + timedelta(seconds=min(LOGIN_TIMEOUT, flow_seconds)),
        )

    def flow(self, registration: GoogleAIPlanClient, client: httpx2.AsyncClient) -> GoogleAIPlanFlow:
        return GoogleAIPlanFlow(
            registration,
            client,
            redirect_uri=self.redirect_uri,
            state=self.state,
            code_verifier=self.code_verifier,
            nonce=self.nonce,
        )


def _google_registration(settings: Providers) -> GoogleAIPlanClient:
    secret = settings.google_ai_plan_client_secret
    try:
        return GoogleAIPlanClient.configured(
            settings.google_ai_plan_client_id, secret.get_secret_value() if secret is not None else None
        )
    except ModelAuthenticationError:
        raise ServiceError(
            "unavailable",
            "Configure an approved Google AI Plan OAuth client ID and secret.",
            {"dependency": "model_oauth"},
        ) from None


def _refresh_stale(row: ModelProviderOAuthRow) -> bool:
    return row.refresh_started_at is not None and row.refresh_started_at.replace(tzinfo=UTC) <= (
        datetime.now(UTC) - timedelta(seconds=_REFRESH_SECONDS)
    )


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


def _google_credentials(row: ModelProviderOAuthRow, keys: KeyRing) -> GoogleAIPlanCredentials:
    if row.tokens is None:
        raise ModelAuthenticationError(GOOGLE_SCHEME, "This Model Provider needs Google AI Plan authorization.")
    return GoogleAIPlanCredentials.model_validate_json(
        keys.reveal(Envelope.model_validate(row.tokens), _location(row, "tokens"))
    )


def _protect(
    row: ModelProviderOAuthRow,
    keys: KeyRing,
    column: str,
    value: Credentials | ChatGPTAuthorization | GoogleAuthorization,
) -> dict:
    if isinstance(value, (GoogleAIPlanCredentials, GoogleAuthorization)):
        payload = value.model_dump_json().encode()
    elif isinstance(value, OpenAIChatGPTCredentials):
        payload = _CREDENTIALS.dump_json(value)
    else:
        payload = _PENDING.dump_json(value)
    return keys.protect(payload, _location(row, column)).model_dump(mode="json")


def _view(
    row: ModelProviderOAuthRow | None,
    provider_id: str,
    keys: KeyRing,
    scheme: OAuthScheme,
) -> AuthorizationStatus:
    if row is None:
        return AuthorizationStatus(provider_id=provider_id, state="disconnected")
    state: Literal["disconnected", "connected", "reauthentication_required"] = "disconnected"
    provider = "Google AI Plan" if scheme == GOOGLE_SCHEME else "ChatGPT"
    if row.tokens is not None:
        if scheme == GOOGLE_SCHEME:
            granted = not row.refresh_blocked and not _refresh_stale(row)
        else:
            granted = _credentials(row, keys).plan_usage_granted
        state = "connected" if granted else "reauthentication_required"
    return AuthorizationStatus(
        provider_id=provider_id,
        state=state,
        subject=row.subject,
        client_id=row.client_id,
        email=row.email,
        expires_at=row.expires_at,
        pending=row.pending is not None or row.login_claim is not None,
        message=f"Sign in again and approve {provider} plan usage."
        if state == "reauthentication_required"
        else f"{provider} sign-in is completing. Restart authorization if it does not finish."
        if row.login_claim is not None
        else None,
    )


async def authorized_provider(
    session: AsyncSession, actor: Principal, workspace_id: str, provider_id: str, verb: Verb, *, lock: bool = False
) -> tuple[ModelProviderRow, OAuthScheme]:
    scope = await workspace_scope(session, actor, workspace_id, "read")
    row = await find_row(session, actor, ModelProviderRow, scope, provider_id, verb, lock=lock)
    scheme = oauth_scheme(row.type)
    if scheme is None:
        raise invalid("provider_id", "this Model Provider does not support OAuth authorization")
    return row, scheme


async def status(
    storage: Storage, actor: Principal, workspace_id: str, provider_id: str, *, keys: KeyRing
) -> AuthorizationStatus:
    async with short_session(storage) as session:
        _, scheme = await authorized_provider(session, actor, workspace_id, provider_id, "read")
        row = await session.get(ModelProviderOAuthRow, provider_id)
        return _view(row, provider_id, keys, scheme)


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
        provider, scheme = await authorized_provider(session, actor, workspace_id, provider_id, "write", lock=True)
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
        if scheme == GOOGLE_SCHEME:
            flow = GoogleAIPlanFlow(
                _google_registration(settings), None, redirect_uri=settings.google_ai_plan_redirect_uri
            )
            pending = GoogleAuthorization.capture(flow, settings.flow_seconds)
            authorization_url = flow.authorization_url()
        else:
            config = Config.model_validate(provider.config)
            selected_client = config.client_id or settings.chatgpt_client_id
            redirect_uri = config.redirect_uri or settings.chatgpt_redirect_uri
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
            authorization_url = pending.authorization_url()
        attempt_id = new_object_id("oauth")
        row.pending_id = attempt_id
        row.pending = _protect(row, keys, "pending", pending)
        row.login_claim = None
        audit_row(session, actor, provider, "oauth_authorize")
        return AuthorizationStart(
            attempt_id=attempt_id,
            authorization_url=authorization_url,
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
    settings: Providers | None = None,
) -> AuthorizationStatus:
    callback_url = body.callback_url.get_secret_value()
    registration = None
    code = None
    async with transaction(storage) as session:
        provider, scheme = await authorized_provider(session, actor, workspace_id, provider_id, "write", lock=True)
        row = await session.get(ModelProviderOAuthRow, provider_id, with_for_update=True)
        if row is None or row.pending_id != body.attempt_id or row.pending is None:
            raise conflict("model_provider", provider_id, "authorization_not_pending")
        payload = keys.reveal(Envelope.model_validate(row.pending), _location(row, "pending"))
        try:
            if scheme == GOOGLE_SCHEME:
                pending = GoogleAuthorization.model_validate_json(payload)
                registration = _google_registration(settings or Providers())
                if (
                    pending.expires_at <= datetime.now(UTC)
                    or pending.client_id != registration.client_id
                    or settings is None
                    or pending.redirect_uri != settings.google_ai_plan_redirect_uri
                ):
                    raise conflict("model_provider", provider_id, "authorization_replaced")
                code = callback_code(callback_url, pending.redirect_uri, pending.state)
            else:
                pending = _PENDING.validate_json(payload)
                pending.validate_callback(callback_url)
        except (UserError, ModelAuthenticationError):
            raise invalid(
                "callback_url", "the complete callback URL does not match this authorization attempt"
            ) from None
        organization_id = provider.organization_id
        row.pending = None
        row.pending_id = None
        row.login_claim = body.attempt_id
    # The claim commits before opening a connection to an upstream authorization server.
    try:
        if isinstance(pending, GoogleAuthorization):
            assert registration is not None and settings is not None and code is not None
            with fail_after(settings.operation_seconds):
                async with open_http(
                    settings.endpoint_policy, timeout=settings.operation_seconds, max_bytes=settings.response_bytes
                ) as client:
                    credentials = await pending.flow(registration, client).exchange_code(code)
        else:
            credentials = await pending.exchange_callback(callback_url)
    except Exception:
        name = "Google AI Plan" if scheme == GOOGLE_SCHEME else "ChatGPT"
        raise ServiceError(
            "unavailable",
            f"{name} sign-in could not be completed. Start a new authorization; the callback was consumed.",
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
                or oauth_scheme(provider.type) != scheme
                or row.login_claim != body.attempt_id
            ):
                raise conflict("model_provider", provider_id, "authorization_replaced")
            row.tokens = _protect(row, keys, "tokens", credentials)
            row.client_id = credentials.client_id
            if isinstance(credentials, GoogleAIPlanCredentials):
                row.subject, row.email, row.expires_at = credentials.account_id, None, credentials.expires_at
            else:
                row.subject, row.email, row.expires_at = credential_metadata(credentials)
            row.login_claim = None
            row.refresh_claim = None
            row.refresh_started_at = None
            row.refresh_blocked = False
            audit_row(session, actor, provider, "oauth_tokens")
            return _view(row, provider_id, keys, scheme)
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
        provider, _ = await authorized_provider(session, actor, workspace_id, provider_id, "write", lock=True)
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


class GoogleAIPlanCredentialSource:
    """Persist refresh ownership before dispatch and fence publication against reauthorization."""

    def __init__(
        self,
        storage: Storage,
        keys: KeyRing,
        provider_id: str,
        organization_id: str,
        registration: GoogleAIPlanClient,
        client: httpx2.AsyncClient,
    ) -> None:
        self.storage, self.keys = storage, keys
        self.provider_id, self.organization_id = provider_id, organization_id
        self.registration, self.client = registration, client
        self._initial: GoogleAIPlanCredentials | None = None

    async def _row(self, session: AsyncSession, *, lock: bool = False) -> ModelProviderOAuthRow:
        statement = select(ModelProviderOAuthRow).where(
            ModelProviderOAuthRow.provider_id == self.provider_id,
            ModelProviderOAuthRow.organization_id == self.organization_id,
        )
        row = await session.scalar(statement.with_for_update() if lock else statement)
        if row is None:
            raise ModelAuthenticationError(GOOGLE_SCHEME, "This Model Provider needs Google AI Plan authorization.")
        return row

    def _bound(self, row: ModelProviderOAuthRow) -> GoogleAIPlanCredentials:
        current = _google_credentials(row, self.keys)
        if current.client_id != self.registration.client_id or (
            self._initial is not None
            and (self._initial.account_id != current.account_id or self._initial.dpop_jwk != current.dpop_jwk)
        ):
            raise ModelAuthenticationError(GOOGLE_SCHEME, "The Google AI Plan authorization changed.")
        self._initial = current
        return current

    async def current(self) -> GoogleAIPlanCredentials:
        async with short_session(self.storage) as session:
            row = await self._row(session)
            current = self._bound(row)
            if row.refresh_blocked or _refresh_stale(row):
                raise ModelAuthenticationError(GOOGLE_SCHEME, "Google AI Plan refresh needs a new sign-in.")
        if current.expires_at <= datetime.now(UTC) + timedelta(seconds=30):
            return await self.refresh(current)
        return current

    async def refresh(self, expected: GoogleAIPlanCredentials) -> GoogleAIPlanCredentials:
        claim = new_object_id("oauth")
        try:
            with fail_after(_REFRESH_SECONDS):
                while True:
                    async with transaction(self.storage) as session:
                        row = await self._row(session, lock=True)
                        current = self._bound(row)
                        if current.account_id != expected.account_id or current.dpop_jwk != expected.dpop_jwk:
                            raise ModelAuthenticationError(GOOGLE_SCHEME, "The Google AI Plan authorization changed.")
                        if row.refresh_blocked or _refresh_stale(row):
                            raise ModelAuthenticationError(GOOGLE_SCHEME, "Google AI Plan refresh needs a new sign-in.")
                        if current != expected:
                            return current
                        if row.refresh_claim is None:
                            row.refresh_claim = claim
                            row.refresh_started_at = datetime.now(UTC)
                            break
                    await sleep(0.1)
        except TimeoutError:
            raise ModelAuthenticationError(GOOGLE_SCHEME, "Google AI Plan refresh is still in progress.") from None
        updated = None
        try:
            with fail_after(_REFRESH_SECONDS - 1):
                updated = await refresh_google_ai_plan(self.registration, self.client, expected)
        except TimeoutError:
            raise ModelAuthenticationError(GOOGLE_SCHEME, "Google AI Plan refresh timed out. Sign in again.") from None
        finally:
            with CancelScope(shield=True):
                async with transaction(self.storage) as session:
                    row = await self._row(session, lock=True)
                    if row.refresh_claim == claim:
                        row.refresh_claim = None
                        row.refresh_started_at = None
                        row.refresh_blocked = updated is None
                        if updated is not None:
                            row.tokens = _protect(row, self.keys, "tokens", updated)
                            row.expires_at = updated.expires_at
                    elif updated is not None:
                        raise ModelAuthenticationError(
                            GOOGLE_SCHEME, "Google AI Plan authorization changed during refresh."
                        )
        assert updated is not None
        return updated


def model_credential_source(
    scheme: OAuthScheme,
    storage: Storage,
    keys: KeyRing,
    provider_id: str,
    organization_id: str,
    *,
    settings: Providers,
    client: httpx2.AsyncClient,
) -> ModelOAuthCredentialSource:
    if scheme == GOOGLE_SCHEME:
        return GoogleAIPlanCredentialSource(
            storage, keys, provider_id, organization_id, _google_registration(settings), client
        )
    return ChatGPTCredentialSource(storage, keys, provider_id, organization_id)
