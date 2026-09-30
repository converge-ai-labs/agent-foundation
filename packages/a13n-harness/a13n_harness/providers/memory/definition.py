"""Typed Memory Provider construction with explicit resource ownership."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import ClassVar

import httpx2
from a13n_logging import get_logger
from anyio import move_on_after
from pydantic import BaseModel

from a13n_harness.http import outbound_tls_verify

from ..definition import ProviderDefinition
from ..endpoint_policy import EndpointPolicy
from .contracts import RecordStore

# Hosts store these types themselves; no Provider may claim them.
RESERVED_MEMORY_TYPES = frozenset({"postgres", "directory"})

type OpenStore[C: BaseModel, K: BaseModel] = Callable[
    [C, K | None, str, httpx2.AsyncClient], AbstractAsyncContextManager[RecordStore]
]


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryProviderDefinition[C: BaseModel, K: BaseModel](ProviderDefinition[C, K]):
    """A record memory backend. `open_store` binds one namespace of it to a store."""

    DOMAIN: ClassVar[str] = "Memory"

    open_store: OpenStore[C, K]

    def validate_domain(self) -> None:
        if self.type in RESERVED_MEMORY_TYPES:
            raise ValueError(f"Memory Provider type {self.type!r} is reserved")

    def open(
        self,
        configuration: object,
        credential: object = None,
        *,
        namespace: str,
        http: httpx2.AsyncClient | None = None,
    ) -> AbstractAsyncContextManager[RecordStore]:
        """Validate inputs now; acquire native resources only when entering the context.

        Without `http`, the store owns a client that reaches public HTTPS endpoints only.
        """
        parsed = self.configuration_model.model_validate(configuration)
        return self._open(parsed, self.parse_credential(parsed, credential), namespace, http)

    @asynccontextmanager
    async def _open(
        self, parsed: C, secret: K | None, namespace: str, http: httpx2.AsyncClient | None
    ) -> AsyncIterator[RecordStore]:
        if http is not None:
            async with self.open_store(parsed, secret, namespace, http) as store:
                yield store
            return
        policy = EndpointPolicy(require_https=True)

        async def check_request(request: httpx2.Request) -> None:
            await policy.validate(str(request.url))

        client = httpx2.AsyncClient(
            verify=outbound_tls_verify(), timeout=30, follow_redirects=False, event_hooks={"request": [check_request]}
        )
        try:
            async with self.open_store(parsed, secret, namespace, client) as store:
                yield store
        finally:
            with move_on_after(1, shield=True) as cleanup:
                await client.aclose()
            if cleanup.cancel_called:
                get_logger(__name__).warning("Memory transport cleanup timed out")
