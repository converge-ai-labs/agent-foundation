"""Process-scoped Grok coordination for embedding hosts without durable coordination."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from anyio import Lock

from .models import GrokCredentials, GrokRefresh
from .rotation import load_grok_credentials, rotate_grok_grant


class GrokCredentialStore(Protocol):
    async def load(self) -> GrokCredentials: ...
    async def save(self, credentials: GrokCredentials) -> None: ...


class ProcessGrokCredentialSource:
    """Share this instance across Models. Exclusion/uncertainty lasts only its process lifetime."""

    def __init__(self, store: GrokCredentialStore):
        self._store = store
        self._lock = Lock()
        self._uncertain: set[str] = set()

    async def load(self) -> GrokCredentials:
        return await load_grok_credentials(self)

    async def rotate(self, expected: GrokCredentials, exchange: GrokRefresh) -> GrokCredentials:
        return await rotate_grok_grant(self, expected, exchange)

    def exclusive(self) -> AbstractAsyncContextManager[None]:
        return self._lock

    async def read(self) -> tuple[GrokCredentials, None]:
        return await self._store.load(), None

    async def publish(self, state: None, credentials: GrokCredentials) -> None:
        del state
        await self._store.save(credentials)

    async def blocked(self, grant: str) -> bool:
        return grant in self._uncertain

    async def set_blocked(self, grant: str, blocked: bool) -> None:
        if blocked:
            self._uncertain.add(grant)
        else:
            self._uncertain.discard(grant)
