"""Host-owned ChatGPT registrations in auth.json, separate from Codex stores."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from a13n_harness.providers.model.oauth.chatgpt import (
    ChatGPTAuthorization,
    OpenAIChatGPTCredentials,
    OpenAIChatGPTOAuthFlow,
    OpenAIChatGPTRefresh,
    revoke_chatgpt_credentials,
)
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError
from a13n_harness.providers.model.oauth.rotation import grant_fingerprint, load_credentials, rotate_grant
from anyio import CancelScope
from pydantic import BaseModel, ConfigDict, Field

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
    subject: str
    email: str | None = None
    credentials: OpenAIChatGPTCredentials | None = Field(default=None, repr=False)


class _State(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    host_id: str = Field(default_factory=lambda: "host-" + secrets.token_hex(16))
    selected: str | None = None
    registrations: dict[str, _Registration] = Field(default_factory=dict, repr=False)
    pending: ChatGPTAuthorization | None = Field(default=None, repr=False)
    attempt_id: str | None = None
    message: str | None = None
    blocked_grants: set[str] = Field(default_factory=set)


def _state(document: dict[str, Any]) -> _State:
    return _State.model_validate(document.get("openai_chatgpt", {}))


class ChatGPTAccountStore:
    def __init__(self, path: Path) -> None:
        self.document = HostAuthDocument(path)

    def exclusive(self):
        return store_lock(self.document.path)

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
    ) -> OpenAIChatGPTOAuthFlow:
        async with self.exclusive():

            def change(state: _State) -> ChatGPTAuthorization:
                selected = state.registrations.get(state.selected or "") if not new_registration else None
                flow = OpenAIChatGPTOAuthFlow.start(
                    ext_agent_host_id=state.host_id,
                    agent_name="Harness UI",
                    redirect_uri=redirect_uri,
                    credentials=selected.credentials if selected else None,
                )
                pending = flow.authorization
                if selected and selected.credentials is None:
                    from dataclasses import replace

                    pending = replace(
                        pending, client_id=selected.client_id, subject=selected.subject, login_hint=selected.email
                    )
                state.pending = pending
                state.attempt_id = attempt_id
                return pending

            return OpenAIChatGPTOAuthFlow(await self._update(change))

    async def complete(self, attempt_id: str, callback_url: str) -> None:
        async with self.exclusive():
            state = await self._read()
            if state.attempt_id != attempt_id or state.pending is None:
                raise AccountStoreError(
                    "This ChatGPT sign-in is no longer pending.", code="login_not_found", provider=Provider.CHATGPT
                )
            flow = OpenAIChatGPTOAuthFlow(state.pending)
            flow.validate_callback(callback_url)
            # Persist consumption before external I/O: restored processes cannot replay a code.
            await self._update(lambda value: self._clear_attempt(value, attempt_id))
            credentials = await flow.exchange_callback(callback_url)

            def publish(value: _State) -> None:
                key = credentials.client_id
                value.registrations[key] = _Registration(
                    client_id=key, subject=credentials.subject, email=credentials.email, credentials=credentials
                )
                value.selected = key
                value.message = None

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

    async def read(self) -> tuple[OpenAIChatGPTCredentials, str]:
        state = await self._read()
        registration = state.registrations.get(state.selected or "")
        if registration is None or registration.credentials is None:
            raise ModelAuthenticationError("openai-chatgpt", "Sign in with ChatGPT before using this Model.")
        return registration.credentials, registration.client_id

    async def load(self) -> OpenAIChatGPTCredentials:
        return await load_credentials(self)

    async def rotate(
        self, expected: OpenAIChatGPTCredentials, exchange: OpenAIChatGPTRefresh
    ) -> OpenAIChatGPTCredentials:
        return await rotate_grant(self, expected, exchange)

    async def publish(self, state: str, credentials: OpenAIChatGPTCredentials) -> None:
        def change(value: _State) -> None:
            value.registrations[state].credentials = credentials

        await self._update(change)

    async def blocked(self, grant: str) -> bool:
        return grant in (await self._read()).blocked_grants

    async def set_blocked(self, grant: str, blocked: bool) -> None:
        def change(state: _State) -> None:
            if blocked:
                state.blocked_grants.add(grant)
            else:
                state.blocked_grants.discard(grant)

        await self._update(change)

    async def inspect(self) -> AccountProjection:
        state = await self._read()
        registration = state.registrations.get(state.selected or "")
        credentials = registration.credentials if registration else None
        expiry = (
            expiry_status(credentials.expires_at, now=datetime.now(UTC), refresh_window=timedelta(minutes=5))
            if credentials
            else ExpiryStatus.UNKNOWN
        )
        blocked = credentials is not None and await self.blocked(grant_fingerprint(credentials.refresh_token))
        return AccountProjection(
            provider=Provider.CHATGPT,
            availability=Availability.AVAILABLE if credentials else Availability.ABSENT,
            source=StoreKind.FILE,
            usable=credentials is not None and not blocked,
            expiry=expiry,
            expires_at=credentials.expires_at if credentials else None,
            account_id=registration.subject if registration else None,
            source_id=state.selected,
            required_action=RequiredAction.REAUTHENTICATE
            if blocked
            else RequiredAction.NONE
            if credentials
            else RequiredAction.LOGIN,
            message=state.message or "Host-managed ChatGPT registration; independent of Codex.",
        )

    async def candidates(self) -> tuple[AccountCandidate, ...]:
        state = await self._read()
        return tuple(
            AccountCandidate(
                selection=AccountSelection(source="native", account_id=key),
                label=registration.email or registration.subject,
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
            state = await self._read()
            registration = state.registrations.get(state.selected or "")
            credentials = registration.credentials if registration else None

            def clear(value: _State) -> None:
                selected = value.registrations.get(value.selected or "")
                if selected:
                    selected.credentials = None
                value.pending = None
                value.attempt_id = None

            existed = credentials is not None
            with CancelScope(shield=True):
                await self._update(clear)
            if credentials:
                try:
                    await revoke_chatgpt_credentials(credentials)
                except Exception:
                    await self._update(
                        lambda value: setattr(
                            value, "message", "Local tokens cleared; remote revocation was not confirmed."
                        )
                    )
        return existed
