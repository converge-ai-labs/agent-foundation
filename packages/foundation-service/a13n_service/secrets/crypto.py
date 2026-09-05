"""AES-256-GCM protection for durable managed Secret values."""

from __future__ import annotations

import base64
import binascii
import os
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


class SecretProtectionError(ValueError):
    """Secret material cannot be safely protected or recovered."""


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    encryption_key_id: str


class SecretProtector:
    """One configured master key implementing the durable aes_256_gcm_v1 profile."""

    def __init__(self, *, key: bytes, encryption_key_id: str) -> None:
        if len(key) != 32:
            raise SecretProtectionError("the managed Secret master key must be exactly 256 bits")
        if not encryption_key_id or len(encryption_key_id) > 128:
            raise SecretProtectionError("the managed Secret encryption key identifier is invalid")
        self._cipher = AESGCM(key)
        self.encryption_key_id = encryption_key_id

    @classmethod
    def from_base64(cls, *, encoded_key: str, encryption_key_id: str) -> SecretProtector:
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except (ValueError, binascii.Error) as error:
            raise SecretProtectionError("the managed Secret master key is not valid base64") from error
        return cls(key=key, encryption_key_id=encryption_key_id)

    def encrypt(
        self,
        value: str,
        *,
        secret_id: str,
        organization_id: str,
        workspace_id: str | None,
        owner_type: str,
        owner_id: str,
        key: str,
        version: int,
    ) -> EncryptedSecret:
        plaintext = value.encode("utf-8")
        if not plaintext or len(plaintext) > 65_536:
            raise SecretProtectionError("the managed Secret value must contain 1 through 65,536 UTF-8 bytes")
        nonce = os.urandom(_NONCE_BYTES)
        aad = _additional_data(
            secret_id,
            organization_id,
            workspace_id,
            owner_type,
            owner_id,
            key,
            str(version),
            self.encryption_key_id,
        )
        return EncryptedSecret(
            ciphertext=self._cipher.encrypt(nonce, plaintext, aad),
            nonce=nonce,
            encryption_key_id=self.encryption_key_id,
        )

    def decrypt(
        self,
        *,
        ciphertext: bytes,
        nonce: bytes,
        encryption_key_id: str,
        secret_id: str,
        organization_id: str,
        workspace_id: str | None,
        owner_type: str,
        owner_id: str,
        key: str,
        version: int,
    ) -> str:
        if encryption_key_id != self.encryption_key_id:
            raise SecretProtectionError("the managed Secret encryption key is unavailable")
        if len(nonce) != _NONCE_BYTES:
            raise SecretProtectionError("the managed Secret nonce is invalid")
        aad = _additional_data(
            secret_id,
            organization_id,
            workspace_id,
            owner_type,
            owner_id,
            key,
            str(version),
            encryption_key_id,
        )
        try:
            return self._cipher.decrypt(nonce, ciphertext, aad).decode("utf-8")
        except (InvalidTag, ValueError, UnicodeDecodeError) as error:
            raise SecretProtectionError("the managed Secret value could not be authenticated") from error


def _additional_data(*values: str | None) -> bytes:
    encoded = bytearray()
    for value in values:
        if value is None:
            encoded.extend(struct.pack(">I", 0xFFFFFFFF))
            continue
        item = value.encode("utf-8")
        encoded.extend(struct.pack(">I", len(item)))
        encoded.extend(item)
    return bytes(encoded)
