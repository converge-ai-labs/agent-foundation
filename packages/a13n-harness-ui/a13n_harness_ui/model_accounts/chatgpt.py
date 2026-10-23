"""Host-owned ChatGPT registrations; Pydantic AI owns token refresh."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from a13n_harness.providers.model.oauth.chatgpt import (
    ChatGPTAuthorization,
    StoredChatGPTCredentials,
    credential_metadata,
)
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError
from anyio import CancelScope
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials, OpenAIChatGPTCredentialSource

from ._common import expiry_status
from .auth import HostAuthDocument
from .coordination import store_lock
from .models import (
    AccountCandidate,
    AccountProjection,
    AccountSelection,
    AccountStoreError,
    Availability,
    ExpiryStatus,
    Provider,
    RequiredAction,
    StoreKind,
)


class _Registration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    client_id: str
    subject: str | None = None
    email: str | None = None
    credentials: StoredChatGPTCredentials | None = Field(default=None, repr=False)


class _State(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    host_id: str = Field(default_factory=lambda: uuid4().urn)
    selected: str | None = None
    registrations: dict[str, _Registration] = Field(default_factory=dict, repr=False)
    pending: ChatGPTAuthorization | None = Field(default=None, repr=False)
    attempt_id: str | None = None
    message: str | None = None


def _state(document: dict[str, Any]) -> _State:
    value = dict(document.get("openai_chatgpt", {}))
    value.pop("blocked_grants", None)  # Retired custom refresh coordination.
    return _State.model_validate(value)


class ChatGPTAccountStore:
    def __init__(self, path: Path) -> None:
        self.document = HostAuthDocument(path)

    def exclusive(self):
        return store_lock(self.document.path)

    def source(self) -> ChatGPTCredentialSource:
        return ChatGPTCredentialSource(self)

    async def _read(self) -> _State:
        return _state(await self.document.read())

    async def _update[T](self, operation: Callable[[_State], T]) -> T:
        def change(document: dict[str, Any]) -> T:
            state = _state(document)
            result = operation(state)
            document["openai_chatgpt"] = state.model_dump(mode="json")
            return result

        return await self.document.update(change)

    async def begin(
        self, attempt_id: str, redirect_uri: str, *, new_registration: bool = False
    ) -> ChatGPTAuthorization:
        async with self.exclusive():

            def change(state: _State) -> ChatGPTAuthorization:
                if not state.registrations and state.host_id.startswith("host-"):
                    state.host_id = uuid4().urn
                selected = state.registrations.get(state.selected or "") if not new_registration else None
                state.pending = ChatGPTAuthorization.start(
                    host_id=state.host_id,
                    redirect_uri=redirect_uri,
                    client_id=selected.client_id if selected else None,
                    credentials=selected.credentials if selected else None,
                )
                state.attempt_id = attempt_id
                return state.pending

            return await self._update(change)

    async def complete(self, attempt_id: str, callback_url: str) -> None:
        async with self.exclusive():
            state = await self._read()
            if state.attempt_id != attempt_id or state.pending is None:
                raise AccountStoreError(
                    "This ChatGPT sign-in is no longer pending.", code="login_not_found", provider=Provider.CHATGPT
                )
            pending = state.pending
            pending.validate_callback(callback_url)
            await self._update(lambda value: self._clear_attempt(value, attempt_id))
            credentials = await pending.exchange_callback(callback_url)
            subject, email, _ = credential_metadata(credentials)

            def publish(value: _State) -> None:
                key = credentials.client_id
                value.registrations[key] = _Registration(
                    client_id=key, subject=subject, email=email, credentials=credentials
                )
                value.selected = key
                value.message = (
                    None if credentials.plan_usage_granted else "Sign in again and approve ChatGPT plan usage."
                )

            with CancelScope(shield=True):
                await self._update(publish)

    @staticmethod
    def _clear_attempt(state: _State, attempt_id: str) -> None:
        if state.attempt_id == attempt_id:
            state.pending = None
            state.attempt_id = None

    async def cancel(self, attempt_id: str) -> None:
        async with self.exclusive():
            await self._update(lambda state: self._clear_attempt(state, attempt_id))

    async def load(self) -> OpenAIChatGPTCredentials:
        state = await self._read()
        registration = state.registrations.get(state.selected or "")
        if registration is None or registration.credentials is None:
            raise ModelAuthenticationError("openai-chatgpt", "Sign in with ChatGPT before using this Model.")
        return registration.credentials

    async def inspect(self) -> AccountProjection:
        state = await self._read()
        registration = state.registrations.get(state.selected or "")
        credentials = registration.credentials if registration else None
        expires_at = credential_metadata(credentials)[2] if credentials else None
        usable = credentials is not None and credentials.plan_usage_granted
        return AccountProjection(
            provider=Provider.CHATGPT,
            availability=Availability.AVAILABLE if credentials else Availability.ABSENT,
            source=StoreKind.FILE,
            usable=usable,
            expiry=expiry_status(expires_at, now=datetime.now(UTC), refresh_window=timedelta(minutes=5))
            if expires_at
            else ExpiryStatus.UNKNOWN,
            expires_at=expires_at,
            account_id=registration.subject if registration else None,
            source_id=state.selected,
            required_action=RequiredAction.NONE
            if usable
            else RequiredAction.REAUTHENTICATE
            if credentials
            else RequiredAction.LOGIN,
            message=state.message or "Host-managed ChatGPT registration; independent of Codex.",
        )

    async def candidates(self) -> tuple[AccountCandidate, ...]:
        state = await self._read()
        return tuple(
            AccountCandidate(
                selection=AccountSelection(source="native", account_id=key),
                label=registration.email or registration.subject or key,
                selected=key == state.selected,
            )
            for key, registration in state.registrations.items()
        )

    async def select(self, selection: AccountSelection) -> AccountProjection:
        async with self.exclusive():

            def change(state: _State) -> None:
                if selection.source != "native" or selection.account_id not in state.registrations:
                    raise AccountStoreError(
                        "ChatGPT registration is unavailable.", code="account_not_found", provider=Provider.CHATGPT
                    )
                state.selected = selection.account_id

            await self._update(change)
        return await self.inspect()

    async def logout(self) -> bool:
        async with self.exclusive():

            def clear(value: _State) -> bool:
                selected = value.registrations.get(value.selected or "")
                existed = selected is not None and selected.credentials is not None
                if selected:
                    selected.credentials = None
                value.pending = None
                value.attempt_id = None
                value.message = "Local tokens cleared. Manage remote access in ChatGPT settings."
                return existed

            with CancelScope(shield=True):
                return await self._update(clear)
        raise RuntimeError("ChatGPT logout did not complete")


class ChatGPTCredentialSource(OpenAIChatGPTCredentialSource):
    """One provider's load/save adapter; never restore a disconnected or replaced grant."""

    def __init__(self, store: ChatGPTAccountStore):
        self.store = store
        self._loaded: OpenAIChatGPTCredentials | None = None

    async def load(self) -> OpenAIChatGPTCredentials:
        current = await self.store.load()
        if self._loaded is not None and current.client_id != self._loaded.client_id:
            raise ModelAuthenticationError("openai-chatgpt", "The selected ChatGPT registration changed.")
        self._loaded = current
        return current

    async def save(self, credentials: OpenAIChatGPTCredentials) -> None:
        async with self.store.exclusive():

            def change(state: _State) -> None:
                selected = state.registrations.get(state.selected or "")
                if (
                    self._loaded is None
                    or selected is None
                    or selected.credentials != self._loaded
                    or credentials.client_id != selected.client_id
                ):
                    raise ModelAuthenticationError("openai-chatgpt", "ChatGPT authorization changed during refresh.")
                selected.credentials = credentials

            with CancelScope(shield=True):
                await self.store._update(change)
                self._loaded = credentials
