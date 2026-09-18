"""Process-scoped Grok coordination for embedding hosts without durable coordination."""

from typing import Protocol

from anyio import Lock

from .models import (
    CredentialPersistenceError,
    GrokCredentials,
    GrokRefresh,
    ModelAuthenticationError,
    RefreshNotDispatched,
)


class GrokCredentialStore(Protocol):
    async def load(self) -> GrokCredentials: ...
    async def save(self, credentials: GrokCredentials) -> None: ...


def require_same_account(expected: GrokCredentials, actual: GrokCredentials) -> None:
    if (expected.account_id, expected.issuer, expected.client_id) != (
        actual.account_id,
        actual.issuer,
        actual.client_id,
    ):
        raise ModelAuthenticationError("grok", "The active Model account changed during the request.")


class ProcessGrokCredentialSource:
    """Share this instance across Models. Exclusion/uncertainty lasts only its process lifetime."""

    def __init__(self, store: GrokCredentialStore):
        self._store = store
        self._lock = Lock()
        self._uncertain: set[str | None] = set()

    async def load(self) -> GrokCredentials:
        async with self._lock:
            current = await self._store.load()
            self._check(current)
            return current

    def _check(self, current: GrokCredentials) -> None:
        if current.refresh_token in self._uncertain:
            raise ModelAuthenticationError(
                "grok", "The previous credential refresh outcome is unknown. Reauthenticate before retrying."
            )

    async def rotate(self, expected: GrokCredentials, exchange: GrokRefresh) -> GrokCredentials:
        async with self._lock:
            current = await self._store.load()
            self._check(current)
            require_same_account(expected, current)
            if current != expected:
                return current
            self._uncertain.add(current.refresh_token)
            try:
                rotated = await exchange(current)
            except RefreshNotDispatched:
                self._uncertain.discard(current.refresh_token)
                raise
            require_same_account(current, rotated)
            try:
                await self._store.save(rotated)
            except Exception:
                raise CredentialPersistenceError(
                    "grok", "The refreshed Model credentials could not be persisted."
                ) from None
            # A successful durable save establishes whether this grant remained reusable.
            if rotated.refresh_token == current.refresh_token:
                self._uncertain.discard(current.refresh_token)
            return rotated
