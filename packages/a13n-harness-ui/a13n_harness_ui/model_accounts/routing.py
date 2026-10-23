"""Request-boundary account selection over a Run's captured subscription group."""

from __future__ import annotations

import math
import random
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

from a13n_harness.errors import ModelResolutionError
from a13n_harness.providers.model.oauth.models import ModelAuthenticationError
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestContext, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openai_codex import CredentialsPersistenceError, CredentialsRefreshError
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
    for account_id in order:
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
        self._stack = AsyncExitStack()
        self._active: SubscriptionAccount | None = None
        self._digest: str | None = None
        self._reason = "initial_selection"
        self._reported: str | None = None
        self._entered = False

    async def __aenter__(self) -> Self:
        await self._stack.enter_async_context(self.wrapped)
        self._entered = True
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> bool | None:
        self._entered = False
        return await self._stack.__aexit__(exc_type, exc_val, exc_tb)

    async def _choose(self, attempted: set[str]) -> SubscriptionAccount:
        return await choose_account(
            self.group,
            self.accounts,
            self.usage,
            self.model_name,
            self._order,
            attempted=attempted,
            active_id=self._active.id if self._active else None,
        )

    async def _activate(self, account: SubscriptionAccount) -> None:
        store = self.accounts.store(account)
        assert store.policy.path is not None
        snapshot = await read_json_snapshot(store.policy.path, provider=Provider(account.provider))
        changed = self._active is None or self._active.id != account.id
        if (
            self._active is None
            or changed
            or self._digest != snapshot.digest
            or self._active.login_revision != account.login_revision
        ):
            if not self._entered:
                await self.__aenter__()
            await self._stack.aclose()
            self.wrapped = self._factory(account)
            await self._stack.enter_async_context(self.wrapped)
            self._active = account
            self._digest = snapshot.digest

    async def _failed(self, account: SubscriptionAccount, error: Exception) -> bool:
        reason = account_failure(error)
        if reason is None:
            return False
        self._reason = reason
        await self.usage.reject(account, self.model_name)
        return True

    async def _report(self, account: SubscriptionAccount) -> None:
        reason = self._reason
        if self._reported is not None and self._reported != account.id and reason == "initial_selection":
            reason = "account_unavailable_or_allowance_preferred"
        if self._reported != account.id and self._selected is not None:
            self._selected(AccountSelectionEvent(self.group.id, account.id, reason))
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
        self._reported = account.id
        self._reason = "initial_selection"

    async def _request[T](self, operation: Callable[[], Awaitable[T]], *, report: bool) -> T:
        attempted: set[str] = set()
        while True:
            account = await self._choose(attempted)
            attempted.add(account.id)
            try:
                await self._activate(account)
                response = await operation()
            except Exception as error:
                if not await self._failed(account, error):
                    raise
                continue
            if report:
                await self._report(account)
            return response

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        return await self._request(
            lambda: self.wrapped.request(messages, model_settings, model_request_parameters), report=True
        )

    async def compact_messages(
        self, request_context: ModelRequestContext, *, instructions: str | None = None
    ) -> ModelResponse:
        return await self._request(
            lambda: self.wrapped.compact_messages(request_context, instructions=instructions), report=False
        )

    async def count_tokens(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> RequestUsage:
        return await self._request(
            lambda: self.wrapped.count_tokens(messages, model_settings, model_request_parameters), report=False
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
        while True:
            account = await self._choose(attempted)
            attempted.add(account.id)
            async with AsyncExitStack() as response_scope:
                try:
                    await self._activate(account)
                    response = await response_scope.enter_async_context(
                        self.wrapped.request_stream(messages, model_settings, model_request_parameters, run_context)
                    )
                except Exception as error:
                    if not await self._failed(account, error):
                        raise
                    continue
                # Stream iteration and caller exceptions must never enter the retry loop.
                await self._report(account)
                yield response
                return
