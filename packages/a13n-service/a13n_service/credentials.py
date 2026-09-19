"""Resource-owned encrypted material; authorization stays with the owning domain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from a13n_harness.providers.definition import ProviderDefinition
from pydantic import BaseModel, JsonValue, SecretBytes, SecretStr, TypeAdapter
from sqlalchemy import BigInteger, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.secrets.crypto import SecretProtectionError, SecretProtector

_JSON_SERIALIZER = TypeAdapter(Any)


def credential_payload(credentials: BaseModel) -> dict[str, object]:
    """Reveal validated secret fields only for resource-owned encrypted persistence."""

    revealed = _reveal_secrets(credentials.model_dump(mode="python", by_alias=True))
    payload = _JSON_SERIALIZER.dump_python(revealed, mode="json")
    if not isinstance(payload, dict):
        raise TypeError("Resource credentials must serialize as an object")
    return payload


def provider_credential_payload(
    definition: ProviderDefinition,
    configuration: BaseModel | dict[str, JsonValue],
    credential: object,
) -> dict[str, object] | None:
    """Enforce the declared presence rule once, then reveal the secret for persistence."""

    parsed = definition.parse_credential(configuration, credential)
    return None if parsed is None else credential_payload(parsed)


def _reveal_secrets(value: object) -> object:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, SecretBytes):
        return value.get_secret_value().decode("utf-8")
    if isinstance(value, dict):
        return {key: _reveal_secrets(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_reveal_secrets(item) for item in value]
    return value


@dataclass(frozen=True, slots=True, repr=False)
class CredentialSnapshot:
    resource_id: str
    owner_type: str
    owner_id: str
    organization_id: str
    workspace_id: str | None
    generation: int
    ciphertext: bytes | None
    nonce: bytes | None
    encryption_key_id: str | None

    def decrypt(self, protector: SecretProtector) -> str:
        if self.ciphertext is None or self.nonce is None or self.encryption_key_id is None:
            raise SecretProtectionError("the resource credential is unavailable")
        return protector.decrypt(
            ciphertext=self.ciphertext,
            nonce=self.nonce,
            encryption_key_id=self.encryption_key_id,
            secret_id=self.resource_id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            owner_type=self.owner_type,
            owner_id=self.owner_id,
            key="credential",
            version=self.generation,
        )


class ResourceCredential[WorkspaceId: str | None]:
    """Columns and protection only; callers own transactions, locks, and use policy."""

    credential_owner_type: ClassVar[str]
    id: Mapped[str]
    organization_id: Mapped[str]
    workspace_id: Mapped[WorkspaceId]
    credential_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
    encryption_key_id: Mapped[str | None] = mapped_column(String(128))

    @property
    def credential_owner_id(self) -> str:
        return self.id

    def credential_snapshot(self) -> CredentialSnapshot:
        return CredentialSnapshot(
            resource_id=self.id,
            owner_type=self.credential_owner_type,
            owner_id=self.credential_owner_id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            generation=self.credential_generation,
            ciphertext=self.ciphertext,
            nonce=self.nonce,
            encryption_key_id=self.encryption_key_id,
        )

    def replace_credential(self, value: str | None, protector: SecretProtector) -> None:
        generation = self.credential_generation + 1
        if value is None:
            self.clear_credential()
            self.credential_generation = generation
            return
        encrypted = protector.encrypt(
            value,
            secret_id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            owner_type=self.credential_owner_type,
            owner_id=self.credential_owner_id,
            key="credential",
            version=generation,
        )
        self.ciphertext = encrypted.ciphertext
        self.nonce = encrypted.nonce
        self.encryption_key_id = encrypted.encryption_key_id
        self.credential_generation = generation

    def clear_credential(self) -> None:
        self.ciphertext = None
        self.nonce = None
        self.encryption_key_id = None
