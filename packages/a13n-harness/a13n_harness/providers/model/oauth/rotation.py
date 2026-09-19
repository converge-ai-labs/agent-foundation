"""One Grok grant-rotation protocol shared by in-process and durable Host stores."""

from contextlib import AbstractAsyncContextManager
from hashlib import sha256
from typing import Protocol

from anyio import CancelScope

from .models import (
    CredentialPersistenceError,
    GrokCredentials,
    GrokRefresh,
    ModelAuthenticationError,
    RefreshNotDispatched,
)


def grant_fingerprint(refresh_token: str | None) -> str:
    """Only a one-way fingerprint of a high-entropy grant enters coordination state."""

    return sha256((refresh_token or "").encode()).hexdigest()


def same_account(expected: GrokCredentials, actual: GrokCredentials) -> bool:
    return (expected.account_id, expected.issuer, expected.client_id) == (
        actual.account_id,
        actual.issuer,
        actual.client_id,
    )


def require_same_account(expected: GrokCredentials, actual: GrokCredentials) -> None:
    if not same_account(expected, actual):
        raise ModelAuthenticationError("grok", "The active Model account changed during the request.")


class GrokGrantStore[StateT](Protocol):
    """Host-owned exclusion, durable credential state, and uncertain-grant evidence."""

    def exclusive(self) -> AbstractAsyncContextManager[None]:
        """Cover one complete read, exchange and publication interval."""
        ...

    async def read(self) -> tuple[GrokCredentials, StateT]:
        """Return the current credentials with whatever the publication needs to detect a change."""
        ...

    async def publish(self, state: StateT, credentials: GrokCredentials) -> None: ...

    async def blocked(self, grant: str) -> bool: ...

    async def set_blocked(self, grant: str, blocked: bool) -> None: ...


async def load_grok_credentials[StateT](store: GrokGrantStore[StateT]) -> GrokCredentials:
    async with store.exclusive():
        current, _ = await store.read()
        await _require_usable(store, current)
        return current


async def rotate_grok_grant[StateT](
    store: GrokGrantStore[StateT], expected: GrokCredentials, exchange: GrokRefresh
) -> GrokCredentials:
    """Reread, authorize one grant spend, exchange, and publish under one host lock."""

    async with store.exclusive():
        current, state = await store.read()
        await _require_usable(store, current)
        require_same_account(expected, current)
        if current != expected:
            return current
        grant = grant_fingerprint(current.refresh_token)
        # Record the marker before dispatch; process death cannot turn lock release into replay permission.
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
                "grok", "The refreshed Model credentials could not be persisted."
            ) from None
        # A successful durable publication establishes whether this grant remained reusable.
        if rotated.refresh_token == current.refresh_token:
            await store.set_blocked(grant, False)
        return rotated


async def _require_usable[StateT](store: GrokGrantStore[StateT], current: GrokCredentials) -> None:
    if await store.blocked(grant_fingerprint(current.refresh_token)):
        raise ModelAuthenticationError(
            "grok", "The previous credential refresh outcome is unknown. Reauthenticate before retrying."
        )
