"""Private OAuth account storage and single-flight credential refresh."""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx2
from a13n_harness.configuration import RunConfiguration
from anyio import CancelScope, fail_after
from pydantic import ValidationError

from a13n_harness_ui.model_accounts._common import expiry_status, read_json_snapshot, write_json_if_unchanged
from a13n_harness_ui.model_accounts.coordination import store_lock
from a13n_harness_ui.model_accounts.models import (
    AccountProjection,
    AccountStoreConflictError,
    AccountStoreError,
    Availability,
    ExpiryStatus,
    RequiredAction,
    StoreKind,
)

from .catalog import Registration
from .sdk import AuthorizedAccount, OAuthContext, https_url, origin
from .transport import provider_http_client


@dataclass(frozen=True, slots=True)
class _CredentialSnapshot:
    account: AuthorizedAccount | None
    digest: str | None
    generation: str | None = None


class PluginAccountStore:
    def __init__(self, oauth_root: Path, *, provider: str, account_id: str) -> None:
        if re.fullmatch(r"acct-[a-zA-Z0-9_-]{1,123}", account_id) is None:
            raise ValueError("Invalid managed account identity")
        self.path = oauth_root / "accounts" / f"{account_id}.json"
        self.provider = provider
        self.account_id = account_id

    async def read(self) -> _CredentialSnapshot:
        snapshot = await read_json_snapshot(self.path, provider=self.provider)
        document = snapshot.document
        try:
            if not document:
                return _CredentialSnapshot(None, snapshot.digest)
            if document.get("provider") != self.provider or document.get("version") != 1:
                raise ValueError
            return _CredentialSnapshot(
                AuthorizedAccount.model_validate(document["account"], strict=False),
                snapshot.digest,
                document["generation"],
            )
        except (ValidationError, KeyError, ValueError):
            raise AccountStoreError(
                "The Model Provider account store is incompatible.",
                code="account_store_incompatible",
                provider=self.provider,
            ) from None

    async def write(self, account: AuthorizedAccount, digest: str | None, *, generation: str | None = None) -> None:
        try:
            account = AuthorizedAccount.model_validate(account.model_dump())
        except (AttributeError, ValidationError, TypeError, ValueError):
            raise AccountStoreError(
                "The Model Provider returned invalid account credentials.",
                code="account_update_invalid",
                provider=self.provider,
            ) from None
        await write_json_if_unchanged(
            self.path,
            {
                "provider": self.provider,
                "version": 1,
                "generation": generation or uuid4().hex,
                "revision": uuid4().hex,
                "account": account.model_dump(mode="json"),
            },
            digest,
            provider=self.provider,
        )

    async def inspect(self) -> AccountProjection:
        snapshot = await self.read()
        account = snapshot.account
        if account is None:
            return AccountProjection(
                provider=self.provider,
                availability=Availability.ABSENT,
                source=StoreKind.FILE,
                usable=False,
                expiry=ExpiryStatus.NOT_APPLICABLE,
                required_action=RequiredAction.LOGIN,
                account_id=self.account_id,
            )
        return AccountProjection(
            provider=self.provider,
            availability=Availability.AVAILABLE,
            source=StoreKind.FILE,
            usable=True,
            expiry=expiry_status(account.expires_at, datetime.now(UTC), timedelta(minutes=1)),
            expires_at=account.expires_at,
            account_id=self.account_id,
        )

    async def logout(self) -> bool:
        async with store_lock(self.path):
            snapshot = await self.read()
            if snapshot.account is None:
                return False
            await write_json_if_unchanged(self.path, {}, snapshot.digest, provider=self.provider)
            return True


class PluginCredentialSource:
    """Use a shared store lock and adopt a newer grant before refreshing."""

    def __init__(
        self,
        store: PluginAccountStore,
        registration: Registration,
        configuration: RunConfiguration | None = None,
        *,
        expected_generation: str | None = None,
        client_factory: Callable[[], httpx2.AsyncClient] | None = None,
    ) -> None:
        self.store = store
        self.registration = registration
        self.expected_generation = expected_generation
        self.client_factory = client_factory or (
            lambda: provider_http_client(registration.declaration, configuration, oauth=True)
        )

    async def _read_authorized(self) -> tuple[AuthorizedAccount, str, str]:
        snapshot = await self.store.read()
        if snapshot.account is None or snapshot.digest is None or snapshot.generation is None:
            raise AccountStoreError(
                "Sign in to the Model Provider account before making requests.",
                code="oauth_login_required",
                provider=self.store.provider,
            )
        if self.expected_generation is not None and snapshot.generation != self.expected_generation:
            raise AccountStoreConflictError(
                "The selected account changed during the Run.",
                code="oauth_account_switch",
                provider=self.store.provider,
            )
        return snapshot.account, snapshot.digest, snapshot.generation

    async def current(self) -> tuple[AuthorizedAccount, str]:
        account, digest, _ = await self._read_authorized()
        if account.expired(datetime.now(UTC)):
            return await self.refresh(digest)
        return account, digest

    async def refresh(self, rejected_digest: str) -> tuple[AuthorizedAccount, str]:
        async with store_lock(self.store.path):
            current, digest, generation = await self._read_authorized()
            if digest != rejected_digest and not current.expired(datetime.now(UTC)):
                return current, digest
            try:
                async with self.client_factory() as client:
                    with fail_after(30):
                        refreshed = await self.registration.plugin.refresh_credentials(OAuthContext(client), current)
                if not isinstance(refreshed, AuthorizedAccount) or refreshed.expired(datetime.now(UTC)):
                    raise ValueError
            except Exception:
                raise AccountStoreError(
                    "The Model Provider could not refresh credentials. Sign in again.",
                    code="oauth_refresh_failed",
                    provider=self.store.provider,
                ) from None
            if refreshed.upstream_account_id != current.upstream_account_id:
                raise AccountStoreConflictError(
                    "The Model Provider returned a different account during refresh.",
                    code="oauth_account_switch",
                    provider=self.store.provider,
                )
            with CancelScope(shield=True):
                await self.store.write(refreshed, digest, generation=generation)
            account, latest_digest, _ = await self._read_authorized()
            return account, latest_digest

    def bearer_auth(self, extra_headers: Mapping[str, str]) -> httpx2.Auth:
        return _BearerAuth(self, extra_headers)


class _BearerAuth(httpx2.Auth):
    def __init__(self, source: PluginCredentialSource, headers: Mapping[str, str]) -> None:
        self.source = source
        self.headers = httpx2.Headers(headers)
        if {"authorization", "host", "proxy-authorization"} & set(self.headers):
            raise ValueError("Bearer auth extras cannot override credential or routing headers")
        self.origins = frozenset(origin(https_url(value)) for value in source.registration.declaration.allowed_origins)

    def _set_headers(self, request: httpx2.Request, account: AuthorizedAccount) -> None:
        value = account.credentials.get("access_token")
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9._~+/=-]+", value) is None:
            raise AccountStoreError(
                "The Model Provider account lacks a valid bearer access token.",
                code="oauth_token_invalid",
                provider=self.source.store.provider,
            )
        request.headers.update(self.headers)
        request.headers["Authorization"] = f"Bearer {value}"

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        if origin(request.url) not in self.origins or request.url.userinfo:
            raise ValueError("Model Provider credentials require an exact declared HTTPS origin")
        account, digest = await self.source.current()
        self._set_headers(request, account)
        response = yield request
        if response.status_code != 401:
            return
        # The native Model clients send buffered JSON. Do not replay a one-shot stream.
        try:
            _ = request.content
        except httpx2.RequestNotRead:
            return
        refreshed, _ = await self.source.refresh(digest)
        self._set_headers(request, refreshed)
        yield request
