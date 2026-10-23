"""Request-boundary account selection over a Run's captured subscription group."""

from __future__ import annotations

import math
import random
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import Any

from a13n_harness.errors import ModelResolutionError
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError
from anyio import CancelScope
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestContext, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from a13n_harness_ui.configuration.models import SubscriptionGroupResource

from ._common import read_json_snapshot
from .managed import SubscriptionAccount, SubscriptionAccounts
from .models import AccountStoreError, Availability, MainAccountSelection, Provider
from .provenance import main_request
from .usage_cache import SubscriptionUsage


@dataclass(frozen=True, slots=True)
class AccountSelectionEvent:
    group_id: str
    account_id: str
    reason: str


def account_failure(error: Exception) -> str | None:
    from pydantic_ai.providers.openai_codex import CredentialsPersistenceError, CredentialsRefreshError

    if isinstance(
        error, (CredentialsRefreshError, CredentialsPersistenceError, ModelAuthenticationError, AccountStoreError)
    ):
        return "authentication_unavailable"
    if isinstance(error, ModelHTTPError):
        if error.status_code == 401:
            return "authentication_rejected"
        body = error.body
        if isinstance(body, dict):
            detail = body.get("error", body)
            code = detail.get("code") or detail.get("type") if isinstance(detail, dict) else None
            if code in {
                "usage_limit_reached",
                "usage_not_included",
                "insufficient_quota",
                "credit_balance_exhausted",
                "rate_limit_exceeded",
            }:
                return "quota_exhausted"
    return None


def account_order(
    group: SubscriptionGroupResource, random_value: Callable[[], float] = random.random
) -> tuple[str, ...]:
    members = list(group.accounts)
    if group.strategy == "weighted":
        members.sort(key=lambda member: -math.log(max(random_value(), 1e-15)) / member.weight)
    return tuple(member.account for member in members)


async def choose_account(
    group: SubscriptionGroupResource,
    accounts: SubscriptionAccounts,
    usage: SubscriptionUsage,
    model_name: str,
    order: tuple[str, ...],
    *,
    attempted: set[str] | None = None,
    active_id: str | None = None,
) -> SubscriptionAccount:
    attempted = attempted or set()
    candidates: list[tuple[int, SubscriptionAccount]] = []
    # Inspect the sticky candidate first; a tier-zero account cannot be outranked.
    for account_id in sorted(order, key=lambda account_id: account_id != active_id):
        if account_id in attempted:
            continue
        try:
            account = await accounts.get(account_id, available=True)
            if account.provider != group.provider:
                continue
            projection = await accounts.store(account).inspect()
            if projection.availability != Availability.AVAILABLE:
                continue
            tier = await usage.tier(account, model_name, allow_credits=group.allow_credits)
            if tier == 0:
                return account
            if tier is not None:
                candidates.append((tier, account))
        except AccountStoreError:
            continue
    if not candidates:
        raise ModelResolutionError(
            "No eligible subscription account remains. Refresh usage, sign in, or update the group.",
            code="subscription_group_unavailable",
            details={"group_id": group.id},
        )
    candidates.sort(key=lambda item: (item[0], 0 if item[1].id == active_id else 1))
    return candidates[0][1]


class _RequestScope(AsyncExitStack):
    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> bool | None:
        # Provider exit acquires async locks and closes sockets even when the caller is cancelled.
        with CancelScope(shield=True):
            return await super().__aexit__(exc_type, exc_val, exc_tb)


class SubscriptionGroupModel(WrapperModel):
    """Try each eligible account once, only before a response reaches the caller."""

    def __init__(
        self,
        group: SubscriptionGroupResource,
        accounts: SubscriptionAccounts,
        usage: SubscriptionUsage,
        factory: Callable[[SubscriptionAccount], Model],
        *,
        selected: Callable[[AccountSelectionEvent], None] | None = None,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        if not group.accounts:
            raise ModelResolutionError("The subscription group is empty.", code="subscription_group_empty")
        self.group = group.model_copy(deep=True)
        self.accounts = accounts
        self.usage = usage
        self._factory = factory
        self._selected = selected
        self._order = account_order(group, random_value)
        # A real provider model supplies profile/settings before the first request.
        template = SubscriptionAccount(id=self._order[0], provider=group.provider, name="Subscription account")
        super().__init__(factory(template))
        self._models: dict[str, tuple[int, str | None, Model]] = {}
        self._active_id: str | None = None
        self._reported: str | None = None

    async def _model(self, account: SubscriptionAccount) -> Model:
        store = self.accounts.store(account)
        assert store.policy.path is not None
        snapshot = await read_json_snapshot(store.policy.path, provider=Provider(account.provider))
        cached = self._models.get(account.id)
        if cached is None or cached[:2] != (account.login_revision, snapshot.digest):
            # Keep only the latest generation. In-flight requests retain their own reference
            # and provider entry; replacing the cache never closes another request's client.
            cached = (account.login_revision, snapshot.digest, self._factory(account))
            self._models[account.id] = cached
        return cached[2]

    async def _choose(self, attempted: set[str], last_error: Exception | None) -> SubscriptionAccount:
        try:
            return await choose_account(
                self.group,
                self.accounts,
                self.usage,
                self.model_name,
                self._order,
                attempted=attempted,
                active_id=self._active_id,
            )
        except ModelResolutionError as error:
            raise error from last_error

    async def _report(self, account: SubscriptionAccount, reason: str) -> None:
        if self._reported is not None and self._reported != account.id and reason == "initial_selection":
            reason = "account_unavailable_or_allowance_preferred"
        if self._reported != account.id and self._selected is not None:
            self._selected(AccountSelectionEvent(self.group.id, account.id, reason))
        self._reported = account.id
        binding = main_request.get()
        if binding is not None:
            model_id, selected = binding
            await selected(
                MainAccountSelection(
                    model_id=model_id,
                    group_id=self.group.id,
                    account_id=account.id,
                    account_name=account.name,
                    reason=reason,
                )
            )

    async def _request[T](self, operation: Callable[[Model], Awaitable[T]], *, report: bool) -> T:
        attempted: set[str] = set()
        last_error: Exception | None = None
        reason = "initial_selection"
        while True:
            account = await self._choose(attempted, last_error)
            attempted.add(account.id)
            async with _RequestScope() as request_scope:
                try:
                    model = await self._model(account)
                    await request_scope.enter_async_context(model)
                    response = await operation(model)
                except Exception as error:
                    failure = account_failure(error)
                    if failure is None:
                        raise
                    reason, last_error = failure, error
                    await self.usage.reject(account, self.model_name)
                    continue
                self._active_id = account.id
                if report:
                    await self._report(account, reason)
                return response

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        return await self._request(
            lambda model: model.request(messages, model_settings, model_request_parameters), report=True
        )

    async def compact_messages(
        self, request_context: ModelRequestContext, *, instructions: str | None = None
    ) -> ModelResponse:
        return await self._request(
            lambda model: model.compact_messages(request_context, instructions=instructions), report=False
        )

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        return await self._request(
            lambda model: model.count_tokens(messages, model_settings, model_request_parameters), report=False
        )

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        attempted: set[str] = set()
        last_error: Exception | None = None
        reason = "initial_selection"
        while True:
            account = await self._choose(attempted, last_error)
            attempted.add(account.id)
            # Each request owns its client through stream exit, including cancellation.
            async with _RequestScope() as response_scope:
                try:
                    model = await self._model(account)
                    await response_scope.enter_async_context(model)
                    response = await response_scope.enter_async_context(
                        model.request_stream(messages, model_settings, model_request_parameters, run_context)
                    )
                except Exception as error:
                    failure = account_failure(error)
                    if failure is None:
                        raise
                    reason, last_error = failure, error
                    await self.usage.reject(account, self.model_name)
                    continue
                # Stream iteration, cleanup and caller exceptions must never enter the retry loop.
                self._active_id = account.id
                await self._report(account, reason)
                yield response
                return
