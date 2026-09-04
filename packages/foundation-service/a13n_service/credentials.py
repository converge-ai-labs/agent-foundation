"""Resource-owned encrypted material; authorization stays with the owning domain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy import BigInteger, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.secrets.crypto import SecretProtectionError, SecretProtector


@dataclass(frozen=True, slots=True, repr=False)
class CredentialSnapshot:
    resource_id: str
    owner_type: str
    owner_id: str
    organization_id: str
    workspace_id: str
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


class ResourceCredential:
    """Columns and protection only; callers own transactions, locks, and use policy."""

    credential_owner_type: ClassVar[str]
    id: Mapped[str]
    organization_id: Mapped[str]
    workspace_id: Mapped[str]
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
