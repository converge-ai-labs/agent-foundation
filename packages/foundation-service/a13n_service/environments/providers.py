"""Foundation-owned attach-only Environment provider boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from a13n_environment_provider import Environment
from pydantic import BaseModel

from .domain import JsonObject


@runtime_checkable
class FoundationEnvironmentAttachProvider(Protocol):
    """Trusted provider capability that can only attach to an exact target."""

    @property
    def provider_key(self) -> str: ...

    @property
    def connection_versions(self) -> frozenset[str]: ...

    def validate_connection(
        self,
        *,
        schema_version: str,
        parameters: JsonObject,
    ) -> BaseModel: ...

    def target_key(self, *, connection: BaseModel) -> str: ...

    def create_attachment_environment(
        self,
        *,
        connection: BaseModel,
        runtime: object,
    ) -> Environment: ...


__all__ = ["FoundationEnvironmentAttachProvider"]
