"""Typed Connector construction with explicit resource ownership."""

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
from .contracts import ConnectorProviderRuntime, JsonObject
from .http import ConnectorHttpClient


@dataclass(frozen=True, slots=True, kw_only=True)
class ConnectorProviderDefinition[C: BaseModel, K: BaseModel](ProviderDefinition[C, K]):
    DOMAIN: ClassVar[str] = "Connector"

    setup_validator: Callable[[C, str, object], JsonObject]
    open_provider: Callable[[C, K | None, ConnectorHttpClient], AbstractAsyncContextManager[ConnectorProviderRuntime]]

    def validate_setup(self, value: object, *, connector_key: str, configuration: JsonObject) -> JsonObject:
        return self.setup_validator(self.configuration_model.model_validate(configuration), connector_key, value)

    def open(
        self, configuration: object, credential: object = None, *, http: ConnectorHttpClient | None = None
    ) -> AbstractAsyncContextManager[ConnectorProviderRuntime]:
        """Validate inputs now; acquire native resources only when entering the context."""

        parsed = self.configuration_model.model_validate(configuration)
        return self._open(parsed, self.parse_credential(parsed, credential), http)

    @asynccontextmanager
    async def _open(
        self, parsed: C, secret: K | None, http: ConnectorHttpClient | None
    ) -> AsyncIterator[ConnectorProviderRuntime]:
        if http is not None:
            async with self.open_provider(parsed, secret, http) as provider:
                yield provider
            return
        client = httpx2.AsyncClient(verify=outbound_tls_verify(), timeout=30, follow_redirects=False)
        try:
            transport = ConnectorHttpClient(
                client, EndpointPolicy(require_https=True), response_max_bytes=8 * 1024 * 1024
            )
            async with self.open_provider(parsed, secret, transport) as provider:
                yield provider
        finally:
            with move_on_after(1, shield=True) as cleanup:
                await client.aclose()
            if cleanup.cancel_called:
                get_logger(__name__).warning("Connector transport cleanup timed out")
