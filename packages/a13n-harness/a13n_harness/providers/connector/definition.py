"""Typed Connector construction with explicit resource ownership."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

import httpx2
from a13n_logging import get_logger
from anyio import move_on_after
from pydantic import BaseModel

from ..authentication import Authentication
from ..endpoint_policy import EndpointPolicy
from ..validation import validate_definition
from .contracts import ConnectorProviderRuntime, JsonObject
from .http import ConnectorHttpClient


@dataclass(frozen=True, slots=True)
class ConnectorProviderDefinition[C: BaseModel, K: BaseModel]:
    type: str
    display_name: str
    configuration_model: type[C]
    credential_model: type[K]
    setup_validator: Callable[[C, str, object], JsonObject]
    open_provider: Callable[[C, K | None, ConnectorHttpClient], AbstractAsyncContextManager[ConnectorProviderRuntime]]
    authentication: Authentication = field(default_factory=Authentication)
    setup_url: str | None = None
    setup_label: str | None = None

    def __post_init__(self) -> None:
        validate_definition(
            self.type,
            self.display_name,
            self.setup_url,
            self.configuration_model,
            self.credential_model,
            domain="Connector",
            setup_label=self.setup_label,
        )
        self.authentication.validate_configuration_model(self.configuration_model)
        if not callable(self.setup_validator) or not callable(self.open_provider):
            raise TypeError("Connector Provider must supply setup validation and construction")

    def validate_setup(self, value: object, *, connector_key: str, configuration: JsonObject) -> JsonObject:
        return self.setup_validator(self.configuration_model.model_validate(configuration), connector_key, value)

    def open(
        self, configuration: object, credential: object = None, *, http: ConnectorHttpClient | None = None
    ) -> AbstractAsyncContextManager[ConnectorProviderRuntime]:
        """Validate inputs now; acquire native resources only when entering the context."""

        parsed = self.configuration_model.model_validate(configuration)
        self.authentication.validate_presence(parsed, credential is not None)
        secret = self.credential_model.model_validate(credential) if credential is not None else None
        return self._open(parsed, secret, http)

    @asynccontextmanager
    async def _open(
        self, parsed: C, secret: K | None, http: ConnectorHttpClient | None
    ) -> AsyncIterator[ConnectorProviderRuntime]:
        if http is not None:
            async with self.open_provider(parsed, secret, http) as provider:
                yield provider
            return
        client = httpx2.AsyncClient(timeout=30, follow_redirects=False, trust_env=False)
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
