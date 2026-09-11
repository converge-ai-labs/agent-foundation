"""Provider-specific credential validation and parsing."""

from __future__ import annotations

import json

from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from pydantic import BaseModel, ConfigDict, Field

from .headers import ExtraHeaders
from .provider_adapters.types import CredentialFormat

_GOOGLE_OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"


class ProviderCredentialError(ValueError):
    """A Provider credential does not satisfy its configured format."""


class AwsCredentialValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: str | None = None


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


class ProviderSecrets(BaseModel):
    """One encrypted bundle; public presence metadata never contains its values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    credential: str | None = Field(default=None, repr=False)
    extra_headers: ExtraHeaders = Field(default_factory=dict, repr=False)

    def encrypted_value(self) -> str | None:
        return self.model_dump_json() if self.credential is not None or self.extra_headers else None
