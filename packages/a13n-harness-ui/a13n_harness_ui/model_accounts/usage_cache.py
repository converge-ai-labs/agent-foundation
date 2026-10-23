"""Shared usage snapshots from responses, request-time reads, and idle fallback polling."""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable, Mapping

import httpx2
from a13n_harness.http import outbound_tls_verify
from anyio import sleep
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ._common import read_json_snapshot, write_json_if_unchanged
from .codex import CodexAccountStore
from .coordination import store_lock
from .grok import GrokAccountStore
from .managed import SubscriptionAccount, SubscriptionAccounts
from .models import AccountStoreError, Provider
from .provider_usage import GrokUsage, read_grok_usage
from .usage import CodexUsage, CodexUsageClient, PaidCredits, UsageLimits, UsageWindow

POLL_SECONDS = 600
REQUEST_REFRESH_SECONDS = 60
REJECTION_SECONDS = 180


class UsageSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    login_revision: int = 0
    checked_at: float = 0
    observed_at: float = 0
    usage: CodexUsage | GrokUsage | None = None
    error: str | None = None
    # A rejected request suppresses this model briefly, not unrelated model buckets.
    rejected: dict[str, float] = Field(default_factory=dict)


class SubscriptionUsage:
    def __init__(
        self,
        accounts: SubscriptionAccounts,
        *,
        reader: Callable[[SubscriptionAccount], Awaitable[CodexUsage | GrokUsage]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.accounts = accounts
        self.clock = clock
        self._reader = reader or self._read_provider

    def _path(self, account: SubscriptionAccount):
        return self.accounts.root / "state" / f"{account.id}.json"

    async def read(self, account: SubscriptionAccount) -> UsageSnapshot:
        raw = await read_json_snapshot(self._path(account), provider=Provider(account.provider))
        try:
            value = UsageSnapshot.model_validate(raw.document or {})
        except ValidationError:
            value = UsageSnapshot()
        if value.login_revision != account.login_revision:
            return UsageSnapshot(login_revision=account.login_revision)
        return value

    async def _read_provider(self, account: SubscriptionAccount) -> CodexUsage | GrokUsage:
        store = self.accounts.store(account)
        async with httpx2.AsyncClient(verify=outbound_tls_verify()) as client:
            if isinstance(store, GrokAccountStore):
                return await read_grok_usage(store, client, expected_account_id=account.upstream_account_id)
            assert isinstance(store, CodexAccountStore)
            return await CodexUsageClient(store, client, expected_account_id=account.upstream_account_id).read()

    async def refresh(self, account_id: str, *, force: bool = False, max_age: float = POLL_SECONDS) -> UsageSnapshot:
        account = await self.accounts.get(account_id)
        previous = await self.read(account)
        started = self.clock()
        if not force and previous.checked_at > 0 and started - previous.checked_at < max_age:
            return previous
        usage = None
        error = None
        try:
            usage = await self._reader(account)
        except (ValueError, AccountStoreError):
            error = "Usage refresh failed. The last successful snapshot is retained."
        # Only the short publication is serialized. Duplicate reads from two TUIs are harmless.
        async with store_lock(self._path(account)):
            current_account = await self.accounts.get(account_id)
            if current_account.login_revision != account.login_revision:
                return await self.read(current_account)
            raw = await read_json_snapshot(self._path(account), provider=Provider(account.provider))
            current = await self.read(account)
            if max(current.checked_at, current.observed_at) > started:
                return current
            updated = current.model_copy(
                update={
                    "checked_at": started,
                    "observed_at": started if usage is not None else current.observed_at,
                    "usage": usage if usage is not None else current.usage,
                    "error": error,
                    "rejected": {key: stamp for key, stamp in current.rejected.items() if stamp > started}
                    if usage is not None
                    else current.rejected,
                }
            )
            await write_json_if_unchanged(
                self._path(account), updated.model_dump(mode="json"), raw.digest, provider=Provider(account.provider)
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
            if latest.login_revision != account.login_revision:
                return
            raw = await read_json_snapshot(self._path(account), provider=Provider(account.provider))
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
                }
            )
            await write_json_if_unchanged(
                self._path(account), updated.model_dump(mode="json"), raw.digest, provider=Provider(account.provider)
            )

    async def reject(self, account: SubscriptionAccount, model_name: str) -> None:
        async with store_lock(self._path(account)):
            latest = await self.accounts.get(account.id)
            if latest.login_revision != account.login_revision:
                return
            raw = await read_json_snapshot(self._path(account), provider=Provider(account.provider))
            current = await self.read(account)
            now = self.clock()
            rejected = {key: stamp for key, stamp in current.rejected.items() if now - stamp < REJECTION_SECONDS}
            rejected[model_name] = now
            await write_json_if_unchanged(
                self._path(account),
                current.model_copy(update={"rejected": rejected}).model_dump(mode="json"),
                raw.digest,
                provider=Provider(account.provider),
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
