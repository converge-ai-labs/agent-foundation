"""Shared usage snapshots from responses, request-time reads, and idle fallback polling."""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Literal

import httpx2
from a13n_harness.http import outbound_tls_verify
from anyio import fail_after, sleep
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from a13n_harness_ui.model_providers import AccountContext, PluginUsageSnapshot
from a13n_harness_ui.model_providers.catalog import ModelProviderCatalog
from a13n_harness_ui.model_providers.credentials import PluginAccountStore, PluginCredentialSource
from a13n_harness_ui.model_providers.transport import provider_http_client

from ._common import read_json_snapshot, write_json_if_unchanged
from .codex import CodexAccountStore
from .coordination import store_lock
from .grok import GrokAccountStore
from .managed import SubscriptionAccount, SubscriptionAccounts
from .models import CODEX_SIGN_IN_REQUIRED, AccountProjection, AccountStoreError, Availability, RequiredAction
from .provider_usage import GrokUsage, read_grok_usage
from .usage import CodexUsage, CodexUsageClient, PaidCredits, UsageLimits, UsageSignInRequired, UsageWindow

POLL_SECONDS = 600
REQUEST_REFRESH_SECONDS = 60
REJECTION_SECONDS = 180


class UsageSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    login_revision: int = 0
    login_generation: str | None = Field(default=None, exclude_if=lambda value: value is None)
    checked_at: float = 0
    observed_at: float = 0
    usage: CodexUsage | GrokUsage | PluginUsageSnapshot | None = None
    error: str | None = None
    required_action: Literal["reauthenticate", "refresh"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    # A rejected request suppresses this model briefly, not unrelated model buckets.
    rejected: dict[str, float] = Field(default_factory=dict)


class SubscriptionUsage:
    def __init__(
        self,
        accounts: SubscriptionAccounts,
        *,
        model_providers: ModelProviderCatalog | None = None,
        reader: Callable[[SubscriptionAccount], Awaitable[CodexUsage | GrokUsage | PluginUsageSnapshot | None]]
        | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.accounts = accounts
        self.model_providers = model_providers
        self.clock = clock
        self._reader = reader or self._read_provider

    def _path(self, account: SubscriptionAccount):
        return self.accounts.root / "state" / f"{account.id}.json"

    async def read(self, account: SubscriptionAccount, *, projection: AccountProjection | None = None) -> UsageSnapshot:
        raw = await read_json_snapshot(self._path(account), provider=account.provider)
        try:
            value = UsageSnapshot.model_validate(raw.document or {})
        except ValidationError:
            value = UsageSnapshot()
        if (value.login_revision, value.login_generation) == (account.login_revision, account.login_generation):
            return value
        fresh = UsageSnapshot(login_revision=account.login_revision, login_generation=account.login_generation)
        if (
            value.required_action == "reauthenticate"
            and value.login_revision + 1 == account.login_revision
            and isinstance(value.usage, CodexUsage)
            and value.usage.account_id == account.upstream_account_id
        ):
            # A successful same-account sign-in may show the last observation while
            # fetching new usage. Logout, switches and old cooldowns never carry over.
            if projection is None:
                projection = await self.accounts.store(account).inspect()
            if (
                projection.availability is Availability.AVAILABLE
                and projection.account_id == account.upstream_account_id
            ):
                return fresh.model_copy(
                    update={
                        "usage": value.usage,
                        "observed_at": value.observed_at,
                        "required_action": "refresh",
                    }
                )
        return fresh

    async def inspect(self, account: SubscriptionAccount) -> tuple[AccountProjection, UsageSnapshot]:
        projection = await self.accounts.store(account).inspect()
        snapshot = await self.read(account, projection=projection)
        provider = account.provider
        adoption = (
            (await self.accounts.adoption()).get(provider)
            if provider in ("codex", "grok")
            and account.id == f"acct-{provider}-default"
            and account.login_revision == 0
            else None
        )
        if snapshot.required_action == "reauthenticate" and projection.availability is Availability.AVAILABLE:
            projection = replace(
                projection,
                usable=False,
                required_action=RequiredAction.REAUTHENTICATE,
                message=(
                    "This login was imported from the CLI and its shared refresh grant was rejected. "
                    "Sign in here to create an independent Harness UI session without affecting the CLI."
                    if adoption is not None and adoption.status == "adopted"
                    else CODEX_SIGN_IN_REQUIRED
                ),
            )
        elif (
            adoption is not None
            and adoption.status == "adopted"
            and (projection.usable or projection.required_action is RequiredAction.REFRESH)
            and projection.message is None
        ):
            projection = replace(projection, message=adoption.message)
        return projection, snapshot

    async def _read_provider(self, account: SubscriptionAccount) -> CodexUsage | GrokUsage | PluginUsageSnapshot | None:
        store = self.accounts.store(account)
        if isinstance(store, PluginAccountStore):
            registration = self.model_providers.get(account.provider) if self.model_providers is not None else None
            if registration is None or not registration.declaration.supports_usage:
                return None
            source = PluginCredentialSource(store, registration, expected_generation=account.login_generation)
            try:
                with fail_after(10):
                    async with provider_http_client(registration.declaration) as client:
                        usage = await registration.plugin.fetch_usage(AccountContext(client, source.bearer_auth))
                        return PluginUsageSnapshot.model_validate(usage)
            except Exception:
                raise ValueError("Model Provider usage refresh failed") from None
        async with httpx2.AsyncClient(verify=outbound_tls_verify()) as client:
            if isinstance(store, GrokAccountStore):
                return await read_grok_usage(store, client, expected_account_id=account.upstream_account_id)
            assert isinstance(store, CodexAccountStore)
            return await CodexUsageClient(store, client, expected_account_id=account.upstream_account_id).read()

    async def refresh(self, account_id: str, *, force: bool = False, max_age: float = POLL_SECONDS) -> UsageSnapshot:
        account = await self.accounts.get(account_id)
        previous = await self.read(account)
        started = self.clock()
        if not force and (
            previous.required_action == "reauthenticate"
            or (previous.checked_at > 0 and started - previous.checked_at < max_age)
        ):
            return previous
        usage = None
        error = None
        grant_rejection = None
        try:
            usage = await self._reader(account)
        except UsageSignInRequired as exc:
            grant_rejection = exc
            error = str(exc)
        except (ValueError, AccountStoreError):
            error = "Usage refresh failed. The last successful snapshot is retained."
        # Only the short publication is serialized. Duplicate reads from two TUIs are harmless.
        async with store_lock(self._path(account)):
            current_account = await self.accounts.get(account_id)
            if (current_account.login_revision, current_account.login_generation) != (
                account.login_revision,
                account.login_generation,
            ):
                return await self.read(current_account)
            raw = await read_json_snapshot(self._path(account), provider=account.provider)
            current = await self.read(account)
            if grant_rejection is not None and not await self._grant_is_current(account, grant_rejection.fingerprint):
                return current
            grant_rejected = grant_rejection is not None
            newer_observation = max(current.checked_at, current.observed_at) > started
            # Response headers can advance allowance without settling credentials.
            recovered_with_newer_headers = (
                force
                and usage is not None
                and previous.required_action == current.required_action == "reauthenticate"
                and current.checked_at <= started < current.observed_at
            )
            if newer_observation and not (grant_rejected or recovered_with_newer_headers):
                return current
            updates = {}
            if not newer_observation:
                updates = {
                    "checked_at": started,
                    "observed_at": started if usage is not None else current.observed_at,
                    "usage": usage if usage is not None else current.usage,
                    "error": error,
                    "required_action": "refresh" if error else None,
                    "rejected": {key: stamp for key, stamp in current.rejected.items() if stamp > started}
                    if usage is not None
                    else current.rejected,
                }
            if grant_rejected:
                updates["required_action"] = "reauthenticate"
                updates["error"] = error
            elif recovered_with_newer_headers:
                updates.update(
                    checked_at=started,
                    required_action=None,
                    error=None,
                    rejected={key: stamp for key, stamp in current.rejected.items() if stamp > started},
                )
            elif current.required_action == "reauthenticate" and (usage is None or not force):
                updates["required_action"] = "reauthenticate"
                updates["error"] = current.error
            updated = current.model_copy(update=updates)
            await write_json_if_unchanged(
                self._path(account), updated.model_dump(mode="json"), raw.digest, provider=account.provider
            )
            return updated

    async def observe_headers(self, account: SubscriptionAccount, headers: Mapping[str, str]) -> None:
        """Merge the default Codex bucket only; named buckets need an API feature mapping."""
        if account.provider != "codex":
            return
        headers = {key.lower(): value for key, value in headers.items()}
        windows: dict[str, UsageWindow] = {}
        for name in ("primary", "secondary"):
            prefix = f"x-codex-{name}"
            try:
                window = UsageWindow(
                    used_percent=float(headers[f"{prefix}-used-percent"]),
                    limit_window_seconds=int(headers[f"{prefix}-window-minutes"]) * 60,
                    reset_at=int(headers[f"{prefix}-reset-at"]),
                )
                if not math.isfinite(window.used_percent):
                    continue
            except (KeyError, ValueError):
                continue
            windows[f"{name}_window"] = window
        credits = None
        if all(headers.get(f"x-codex-credits-{key}") in {"true", "false"} for key in ("has-credits", "unlimited")):
            credits = PaidCredits(
                has_credits=headers["x-codex-credits-has-credits"] == "true",
                unlimited=headers["x-codex-credits-unlimited"] == "true",
                balance=headers.get("x-codex-credits-balance"),
            )
        if not windows and credits is None:
            return
        observed = self.clock()
        async with store_lock(self._path(account)):
            latest = await self.accounts.get(account.id)
            if (latest.login_revision, latest.login_generation) != (account.login_revision, account.login_generation):
                return
            raw = await read_json_snapshot(self._path(account), provider=account.provider)
            current = await self.read(account)
            if max(current.checked_at, current.observed_at) > observed:
                return
            usage = current.usage or CodexUsage(
                account_id=account.upstream_account_id or account.id, plan_type="unknown"
            )
            if not isinstance(usage, CodexUsage):
                return
            limits = usage.rate_limit
            if windows:
                limits = (limits or UsageLimits(allowed=True, limit_reached=False)).model_copy(update=windows)
                reached = any(
                    window is not None and window.used_percent >= 100
                    for window in (limits.primary_window, limits.secondary_window)
                )
                limits = limits.model_copy(update={"allowed": not reached, "limit_reached": reached})
            updated = current.model_copy(
                update={
                    "observed_at": observed,
                    "usage": usage.model_copy(update={"rate_limit": limits, "credits": credits or usage.credits}),
                    "error": current.error if current.required_action == "reauthenticate" else None,
                    "required_action": None if current.required_action == "refresh" else current.required_action,
                }
            )
            await write_json_if_unchanged(
                self._path(account), updated.model_dump(mode="json"), raw.digest, provider=account.provider
            )

    async def _grant_is_current(self, account: SubscriptionAccount, fingerprint: str | None) -> bool:
        if fingerprint is None:
            return True
        from .codex_transport import grant_fingerprint

        store = self.accounts.store(account)
        if not isinstance(store, CodexAccountStore):
            return False
        try:
            credentials = await store.load()
        except AccountStoreError:
            return False
        return grant_fingerprint(credentials.refresh_token) == fingerprint

    async def reject(
        self,
        account: SubscriptionAccount,
        model_name: str,
        *,
        grant_rejected: bool = False,
        fingerprint: str | None = None,
    ) -> None:
        await self._record_rejection(
            account, model_name, CODEX_SIGN_IN_REQUIRED if grant_rejected else None, fingerprint
        )

    async def mark_reauthenticate(self, account: SubscriptionAccount, rejection: UsageSignInRequired) -> None:
        await self._record_rejection(account, None, str(rejection), rejection.fingerprint)

    async def _record_rejection(
        self, account: SubscriptionAccount, model_name: str | None, grant_error: str | None, fingerprint: str | None
    ) -> None:
        async with store_lock(self._path(account)):
            latest = await self.accounts.get(account.id)
            if (latest.login_revision, latest.login_generation) != (account.login_revision, account.login_generation):
                return
            raw = await read_json_snapshot(self._path(account), provider=account.provider)
            current = await self.read(account)
            if grant_error is not None and not await self._grant_is_current(account, fingerprint):
                return
            now = self.clock()
            rejected = {key: stamp for key, stamp in current.rejected.items() if now - stamp < REJECTION_SECONDS}
            if model_name is not None:
                rejected[model_name] = now
            await write_json_if_unchanged(
                self._path(account),
                current.model_copy(
                    update={
                        "rejected": rejected,
                        "required_action": "reauthenticate" if grant_error is not None else current.required_action,
                        "error": grant_error if grant_error is not None else current.error,
                    }
                ).model_dump(mode="json"),
                raw.digest,
                provider=account.provider,
            )

    async def poll(self) -> None:
        while True:
            for account in await self.accounts.list():
                if account.enabled:
                    try:
                        await self.refresh(account.id)
                    except AccountStoreError:
                        # A concurrent delete/logout is observed on the next pass.
                        continue
            await sleep(POLL_SECONDS)

    async def tier(self, account: SubscriptionAccount, model_name: str, *, allow_credits: bool) -> int | None:
        snapshot = await self.refresh(account.id, max_age=REQUEST_REFRESH_SECONDS)
        if snapshot.required_action == "reauthenticate":
            return None
        rejected = snapshot.rejected.get(model_name)
        if rejected is not None and self.clock() - rejected < REJECTION_SECONDS:
            return None
        usage = snapshot.usage
        if not isinstance(usage, CodexUsage):
            return 0  # Unknown usage remains eligible; the provider is authoritative.
        limits = usage.rate_limit
        # Only an exact metered-feature match can gate this model.
        for additional in usage.additional_rate_limits or ():
            if additional.metered_feature == model_name:
                limits = additional.rate_limit
                break
        if not exhausted(limits):
            return 0
        credits = usage.credits
        return 1 if allow_credits and credits is not None and (credits.has_credits or credits.unlimited) else None


def exhausted(limits: UsageLimits | None) -> bool:
    if limits is None:
        return False
    return (
        limits.limit_reached
        or not limits.allowed
        or any(
            window is not None and window.used_percent >= 100
            for window in (limits.primary_window, limits.secondary_window)
        )
    )
