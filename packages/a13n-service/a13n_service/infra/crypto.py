"""One authenticated credential envelope, bound to its exact tenant and column."""

import base64
import hashlib
import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from a13n_service.infra.errors import ServiceError

# The key ID of the one key an `encryption.key_file` holds.
FILE_KEY_ID = "key_file"


def file_key(path: Path) -> SecretStr:
    """The base64 key kept at `path`, generated there first when missing. A complete private draft is linked into
    place, so concurrent first starts all read the one key that won and none replaces it."""
    if not path.exists():
        draft = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
        descriptor = os.open(draft, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, base64.b64encode(secrets.token_bytes(32)))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(draft, path)
        except FileExistsError:
            pass
        finally:
            draft.unlink()
    return SecretStr(path.read_text().strip())


def secret_hash(secret: str) -> str:
    """Lookup digest of a high-entropy random secret (tokens, leases); not suitable for passwords."""
    return hashlib.sha256(secret.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SecretLocation:
    # None only for account-wide values, such as account mail, that belong to no organization.
    organization_id: str | None
    table: str
    column: str
    row_id: str

    def aad(self) -> bytes:
        return json.dumps([self.organization_id, self.table, self.column, self.row_id], separators=(",", ":")).encode()


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key_id: str = Field(min_length=1, max_length=128)
    nonce: str = Field(min_length=16, max_length=16)
    ciphertext: str = Field(min_length=24, max_length=90000)


class KeyRing:
    def __init__(self, *, active_key_id: str | None, keys: Mapping[str, SecretStr]):
        self.active_key_id = active_key_id
        self._keys: dict[str, AESGCM] = {}
        for key_id, encoded in keys.items():
            try:
                key = base64.b64decode(encoded.get_secret_value(), validate=True)
            except ValueError:
                raise ValueError("Encryption keys must be base64-encoded 32-byte keys") from None
            if len(key) != 32 or not 1 <= len(key_id) <= 128:
                raise ValueError("Encryption keys require a bounded ID and 32 bytes")
            self._keys[key_id] = AESGCM(key)
        if active_key_id is not None and active_key_id not in self._keys:
            raise ValueError("The active encryption key must be in the key ring")

    def protect(self, plaintext: bytes, location: SecretLocation) -> Envelope:
        if len(plaintext) > 65536:
            raise ServiceError(
                "invalid_argument",
                "Credential exceeds its byte limit",
                {"field": location.column, "reason": "too_long", "limit": 65536},
            )
        if self.active_key_id is None:
            raise ServiceError("unavailable", "Credential encryption is not configured", {"dependency": "encryption"})
        nonce = secrets.token_bytes(12)
        encrypted = self._keys[self.active_key_id].encrypt(nonce, plaintext, location.aad())
        return Envelope(
            key_id=self.active_key_id,
            nonce=base64.b64encode(nonce).decode(),
            ciphertext=base64.b64encode(encrypted).decode(),
        )

    def reveal(self, envelope: Envelope, location: SecretLocation) -> bytes:
        try:
            return self._keys[envelope.key_id].decrypt(
                base64.b64decode(envelope.nonce, validate=True),
                base64.b64decode(envelope.ciphertext, validate=True),
                location.aad(),
            )
        except (KeyError, ValueError, InvalidTag):
            raise ServiceError("unavailable", "Credential cannot be decrypted", {"dependency": "encryption"}) from None
