"""Process-scoped grant coordination for embedding Hosts without durable coordination."""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from anyio import Lock

from .copilot import CopilotCredentials
from .models import GrokCredentials
from .rotation import RotatingCredentials, load_credentials, rotate_grant


class CredentialStore[CredentialT: RotatingCredentials](Protocol):
    async def load(self) -> CredentialT: ...
    async def save(self, credentials: CredentialT) -> None: ...


type GrokCredentialStore = CredentialStore[GrokCredentials]
type CopilotCredentialStore = CredentialStore[CopilotCredentials]


class _ProcessCredentialSource[CredentialT: RotatingCredentials]:
    """Share this instance across Models. Exclusion/uncertainty lasts only its process lifetime."""

    def __init__(self, store: CredentialStore[CredentialT]):
        self._store = store
        self._lock = Lock()
        self._uncertain: set[str] = set()

    async def load(self) -> CredentialT:
        return await load_credentials(self)

    async def rotate(
        self, expected: CredentialT, exchange: Callable[[CredentialT], Awaitable[CredentialT]]
    ) -> CredentialT:
        return await rotate_grant(self, expected, exchange)

    def exclusive(self) -> AbstractAsyncContextManager[None]:
        return self._lock

    async def read(self) -> tuple[CredentialT, None]:
        return await self._store.load(), None

    async def publish(self, state: None, credentials: CredentialT) -> None:
        del state
        await self._store.save(credentials)

    async def blocked(self, grant: str) -> bool:
        return grant in self._uncertain

    async def set_blocked(self, grant: str, blocked: bool) -> None:
        if blocked:
            self._uncertain.add(grant)
        else:
            self._uncertain.discard(grant)


class ProcessGrokCredentialSource(_ProcessCredentialSource[GrokCredentials]):
    """One shared Grok source; process-local exclusion, no restart guarantee."""


class ProcessCopilotCredentialSource(_ProcessCredentialSource[CopilotCredentials]):
    """One shared Copilot source; process-local exclusion, no restart guarantee."""
