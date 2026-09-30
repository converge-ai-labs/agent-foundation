"""Grant exclusion and publication shared by Grok and Copilot sources.

This is a storage coordination primitive, not a provider-neutral OAuth protocol.
Provider exchanges, validation and credential formats remain provider-owned.
"""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from hashlib import sha256
from typing import Protocol

from anyio import CancelScope

from .chatgpt import OpenAIChatGPTCredentials
from .copilot import CopilotCredentials
from .models import (
    CredentialPersistenceError,
    GrokCredentials,
    GrokRefresh,
    ModelAuthenticationError,
    RefreshNotDispatched,
)

type RotatingCredentials = GrokCredentials | CopilotCredentials | OpenAIChatGPTCredentials


def grant_fingerprint(refresh_token: str | None) -> str:
    """Only a one-way fingerprint of a high-entropy grant enters coordination state."""
    return sha256((refresh_token or "").encode()).hexdigest()


def same_account(expected: RotatingCredentials, actual: RotatingCredentials) -> bool:
    if isinstance(expected, OpenAIChatGPTCredentials) and isinstance(actual, OpenAIChatGPTCredentials):
        if expected.ext_agent_host_id != actual.ext_agent_host_id:
            return False
    if isinstance(expected, CopilotCredentials) and isinstance(actual, CopilotCredentials):
        if expected.source_id != actual.source_id:
            return False
    return (expected.provider, expected.account_id, expected.issuer, expected.client_id) == (
        actual.provider,
        actual.account_id,
        actual.issuer,
        actual.client_id,
    )


def require_same_account(expected: RotatingCredentials, actual: RotatingCredentials) -> None:
    if not same_account(expected, actual):
        raise ModelAuthenticationError(expected.provider, "The active Model account changed during the request.")


class GrantStore[CredentialT: RotatingCredentials, StateT](Protocol):
    """Host-owned exclusion, durable credential state, and uncertain-grant evidence."""

    def exclusive(self) -> AbstractAsyncContextManager[None]: ...

    async def read(self) -> tuple[CredentialT, StateT]: ...

    async def publish(self, state: StateT, credentials: CredentialT) -> None: ...

    async def blocked(self, grant: str) -> bool: ...

    async def set_blocked(self, grant: str, blocked: bool) -> None: ...


async def load_credentials[CredentialT: RotatingCredentials, StateT](
    store: GrantStore[CredentialT, StateT],
) -> CredentialT:
    async with store.exclusive():
        current, _ = await store.read()
        await _require_usable(store, current)
        return current


async def rotate_grant[CredentialT: RotatingCredentials, StateT](
    store: GrantStore[CredentialT, StateT],
    expected: CredentialT,
    exchange: Callable[[CredentialT], Awaitable[CredentialT]],
) -> CredentialT:
    """Reread, authorize one grant spend, exchange, and publish under one host lock."""
    async with store.exclusive():
        current, state = await store.read()
        await _require_usable(store, current)
        require_same_account(expected, current)
        if current != expected:
            return current
        grant = grant_fingerprint(current.refresh_token)
        # Process death must not turn lock release into permission to replay a rotating grant.
        await store.set_blocked(grant, True)
        try:
            rotated = await exchange(current)
        except RefreshNotDispatched:
            with CancelScope(shield=True):
                await store.set_blocked(grant, False)
            raise
        require_same_account(current, rotated)
        try:
            await store.publish(state, rotated)
        except Exception:
            raise CredentialPersistenceError(
                current.provider, "The refreshed Model credentials could not be persisted."
            ) from None
        if rotated.refresh_token == current.refresh_token:
            await store.set_blocked(grant, False)
        return rotated


async def _require_usable[CredentialT: RotatingCredentials, StateT](
    store: GrantStore[CredentialT, StateT],
    current: CredentialT,
) -> None:
    if await store.blocked(grant_fingerprint(current.refresh_token)):
        raise ModelAuthenticationError(
            current.provider, "The previous credential refresh outcome is unknown. Reauthenticate before retrying."
        )


# Preserve the existing Grok source boundary for embedding Hosts.
type GrokGrantStore[StateT] = GrantStore[GrokCredentials, StateT]


async def load_grok_credentials[StateT](store: GrokGrantStore[StateT]) -> GrokCredentials:
    return await load_credentials(store)


async def rotate_grok_grant[StateT](
    store: GrokGrantStore[StateT],
    expected: GrokCredentials,
    exchange: GrokRefresh,
) -> GrokCredentials:
    return await rotate_grant(store, expected, exchange)
