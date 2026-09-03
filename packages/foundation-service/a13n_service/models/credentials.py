"""Encrypted Provider credential persistence and fresh resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass

from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from pydantic import BaseModel, ConfigDict

from a13n_service.secrets.crypto import SecretProtectionError, SecretProtector

from .models import ModelProviderRecord
from .provider_adapters.types import CredentialFormat

_OWNER_TYPE = "model_provider"
_CREDENTIAL_KEY = "credential"
_GOOGLE_OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"


class ProviderCredentialError(ValueError):
    """A Provider credential cannot be protected or recovered."""


class AwsCredentialValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: str | None = None


@dataclass(frozen=True, slots=True)
class EncryptedProviderCredential:
    provider_id: str
    organization_id: str
    workspace_id: str
    version: int
    ciphertext: bytes
    nonce: bytes
    encryption_key_id: str

    @classmethod
    def from_record(cls, record: ModelProviderRecord) -> EncryptedProviderCredential | None:
        if record.ciphertext is None:
            return None
        if record.nonce is None or record.encryption_key_id is None:
            raise ProviderCredentialError("the Provider credential is unavailable")
        return cls(
            provider_id=record.id,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            version=record.credential_version,
            ciphertext=bytes(record.ciphertext),
            nonce=bytes(record.nonce),
            encryption_key_id=record.encryption_key_id,
        )


def replace_provider_credential(
    record: ModelProviderRecord,
    value: str | None,
    protector: SecretProtector,
) -> None:
    record.credential_version += 1
    if value is None:
        record.ciphertext = None
        record.nonce = None
        record.encryption_key_id = None
        return
    try:
        encrypted = protector.encrypt(
            value,
            secret_id=record.id,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            owner_type=_OWNER_TYPE,
            owner_id=record.id,
            key=_CREDENTIAL_KEY,
            version=record.credential_version,
        )
    except SecretProtectionError as error:
        raise ProviderCredentialError("the Provider credential could not be protected") from error
    record.ciphertext = encrypted.ciphertext
    record.nonce = encrypted.nonce
    record.encryption_key_id = encrypted.encryption_key_id


def decrypt_provider_credential(
    encrypted: EncryptedProviderCredential | None,
    protector: SecretProtector,
) -> str | None:
    if encrypted is None:
        return None
    try:
        return protector.decrypt(
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            encryption_key_id=encrypted.encryption_key_id,
            secret_id=encrypted.provider_id,
            organization_id=encrypted.organization_id,
            workspace_id=encrypted.workspace_id,
            owner_type=_OWNER_TYPE,
            owner_id=encrypted.provider_id,
            key=_CREDENTIAL_KEY,
            version=encrypted.version,
        )
    except SecretProtectionError as error:
        raise ProviderCredentialError("the Provider credential is unavailable") from error


def validate_provider_credential(credential_format: CredentialFormat | None, value: str | None) -> None:
    if value is None:
        return
    if not value.strip():
        raise ProviderCredentialError("the Provider credential cannot be empty")
    try:
        if credential_format is CredentialFormat.aws_credentials_json:
            parse_aws_credentials(value)
        elif credential_format is CredentialFormat.google_service_account_json:
            parse_google_service_account(value)
    except ValueError as error:
        raise ProviderCredentialError(str(error)) from error


def parse_aws_credentials(value: str) -> AwsCredentialValue:
    try:
        return AwsCredentialValue.model_validate(json.loads(value))
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError("AWS credential must be a valid credentials JSON object") from error


def parse_google_service_account(value: str) -> ServiceAccountCredentials:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("Google Vertex credential must be a service-account JSON object") from error
    if not isinstance(decoded, dict) or decoded.get("token_uri") != _GOOGLE_OAUTH_TOKEN_URI:
        raise ValueError("Google Vertex credential must use the official Google OAuth token endpoint")
    trusted_info = dict(decoded)
    trusted_info["token_uri"] = _GOOGLE_OAUTH_TOKEN_URI
    try:
        return ServiceAccountCredentials.from_service_account_info(trusted_info)
    except (TypeError, ValueError) as error:
        raise ValueError("Google Vertex credential must be a service-account JSON object") from error
