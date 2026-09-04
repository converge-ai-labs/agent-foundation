"""Foundation-owned Environment attachment and bounded-retention boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from a13n_environment_provider import Environment
from pydantic import BaseModel

from .domain import JsonObject


@runtime_checkable
class AttachmentProvider(Protocol):
    """Trusted provider capability that can only attach to an exact target."""

    @property
    def provider_key(self) -> str: ...

    @property
    def connection_versions(self) -> frozenset[str]: ...

    @property
    def identity_schema_version(self) -> str: ...

    @property
    def retention_behavior(self) -> str: ...

    def validate_connection(
        self,
        *,
        schema_version: str,
        parameters: JsonObject,
    ) -> BaseModel: ...

    def target_identity(self, *, connection: BaseModel) -> object: ...

    def create_attachment_environment(
        self,
        *,
        connection: BaseModel,
        runtime: object,
    ) -> Environment: ...


@runtime_checkable
class RetentionProvider(Protocol):
    """Optional capability that can only extend an existing target's lifetime."""

    async def ensure_retained_until(
        self,
        *,
        connection: BaseModel,
        runtime: object,
        deadline: datetime,
        operation_id: str,
    ) -> datetime: ...


@runtime_checkable
class _LegacyNonRetainingAttachProvider(Protocol):
    """Pre-target-identity attach surface exposed by the shared built-ins."""

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


@dataclass(frozen=True, slots=True)
class BuiltinAttachmentProvider:
    """Foundation-owned target metadata for one non-retaining shared built-in."""

    provider: _LegacyNonRetainingAttachProvider

    @classmethod
    def from_provider(cls, provider: object) -> BuiltinAttachmentProvider:
        if not isinstance(provider, _LegacyNonRetainingAttachProvider):
            raise ValueError("Built-in Environment Provider has no Foundation attachment capability")
        return cls(provider)

    @property
    def provider_key(self) -> str:
        return self.provider.provider_key

    @property
    def connection_versions(self) -> frozenset[str]:
        return self.provider.connection_versions

    @property
    def identity_schema_version(self) -> str:
        return "1"

    @property
    def retention_behavior(self) -> str:
        return "none"

    def validate_connection(
        self,
        *,
        schema_version: str,
        parameters: JsonObject,
    ) -> BaseModel:
        return self.provider.validate_connection(schema_version=schema_version, parameters=parameters)

    def target_identity(self, *, connection: BaseModel) -> object:
        return {
            "namespace": {},
            "target_key": self.provider.target_key(connection=connection),
        }

    def create_attachment_environment(
        self,
        *,
        connection: BaseModel,
        runtime: object,
    ) -> Environment:
        return self.provider.create_attachment_environment(connection=connection, runtime=runtime)


__all__ = [
    "AttachmentProvider",
    "BuiltinAttachmentProvider",
    "RetentionProvider",
]
