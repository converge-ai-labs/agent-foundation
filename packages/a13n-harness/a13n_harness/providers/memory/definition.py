"""Typed Memory construction with explicit host-owned backend lifetimes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

from pydantic import BaseModel

from ..authentication import Authentication
from ..validation import validate_definition
from .contracts import MemoryBackend, MemoryDocumentBackend


@dataclass(frozen=True, slots=True)
class MemoryProviderDefinition[C: BaseModel, K: BaseModel]:
    type: str
    display_name: str
    configuration_model: type[C]
    credential_model: type[K]
    open_backend: Callable[[C, K | None], AbstractAsyncContextManager[MemoryBackend]]
    authentication: Authentication = field(default_factory=Authentication)
    setup_url: str | None = None
    setup_label: str | None = None
    supports_documents: bool = False

    def __post_init__(self) -> None:
        validate_definition(
            self.type,
            self.display_name,
            self.setup_url,
            self.configuration_model,
            self.credential_model,
            domain="Memory",
            setup_label=self.setup_label,
        )
        self.authentication.validate_configuration_model(self.configuration_model)
        if not callable(self.open_backend):
            raise TypeError("Memory Provider must supply backend construction")
        if type(self.supports_documents) is not bool:
            raise TypeError("Memory document support must be a boolean")

    @asynccontextmanager
    async def open(self, configuration: object, credential: object = None) -> AsyncIterator[MemoryBackend]:
        parsed = self.configuration_model.model_validate(configuration)
        self.authentication.validate_presence(parsed, credential is not None)
        secret = self.credential_model.model_validate(credential) if credential is not None else None
        async with self.open_backend(parsed, secret) as backend:
            if not isinstance(backend, MemoryBackend):
                raise TypeError("Memory Provider must open a MemoryBackend")
            if self.supports_documents and not isinstance(backend, MemoryDocumentBackend):
                raise TypeError("Memory Provider declared unsupported document operations")
            yield backend
