"""Typed Memory construction with explicit host-owned backend lifetimes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel

from ..definition import ProviderDefinition
from .contracts import MemoryBackend, MemoryDocumentBackend
from .filesystem.configuration import FilesystemMemoryConfiguration


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryProviderDefinition[C: BaseModel, K: BaseModel](ProviderDefinition[C, K]):
    DOMAIN: ClassVar[str] = "Memory"

    # Document-only storage opens through its own Host binding, never a record backend.
    open_backend: Callable[[C, K | None], AbstractAsyncContextManager[MemoryBackend]] | None = None
    supports_documents: bool = False
    supports_revisions: bool = False
    supports_changes: bool = False

    @property
    def supports_records(self) -> bool:
        return self.open_backend is not None

    def validate_domain(self) -> None:
        if self.supports_records:
            return
        if not self.supports_documents:
            raise ValueError("Memory Provider must support records or documents")
        # Hosts bind document-only storage as their own files, so its inputs are the file store's.
        if self.configuration_model is not FilesystemMemoryConfiguration:
            raise ValueError("Document-only Memory Providers must use FilesystemMemoryConfiguration")

    @asynccontextmanager
    async def open(self, configuration: object, credential: object = None) -> AsyncIterator[MemoryBackend]:
        if self.open_backend is None:
            raise TypeError(f"Memory Provider {self.type!r} has no record backend")
        parsed = self.configuration_model.model_validate(configuration)
        async with self.open_backend(parsed, self.parse_credential(parsed, credential)) as backend:
            if not isinstance(backend, MemoryBackend):
                raise TypeError("Memory Provider must open a MemoryBackend")
            if self.supports_documents and not isinstance(backend, MemoryDocumentBackend):
                raise TypeError("Memory Provider declared unsupported document operations")
            yield backend
