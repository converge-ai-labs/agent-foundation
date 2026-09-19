"""Structured vendor credentials; SDK conversion happens only at construction."""

from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class ApiKeyCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    api_key: SecretStr = Field(min_length=1, max_length=65536, repr=False)

    @field_validator("api_key")
    @classmethod
    def nonblank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("API key must not be blank")
        return value


class EmptyCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AwsSdkCredentials(TypedDict):
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: str | None


class AwsCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    aws_access_key_id: SecretStr = Field(min_length=1, max_length=256, repr=False)
    aws_secret_access_key: SecretStr = Field(min_length=1, max_length=4096, repr=False)
    aws_session_token: SecretStr | None = Field(default=None, max_length=65536, repr=False)

    def native_values(self) -> AwsSdkCredentials:
        return {
            "aws_access_key_id": self.aws_access_key_id.get_secret_value(),
            "aws_secret_access_key": self.aws_secret_access_key.get_secret_value(),
            "aws_session_token": self.aws_session_token.get_secret_value() if self.aws_session_token else None,
        }


class GoogleServiceAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["service_account"] = "service_account"
    project_id: str = Field(min_length=1, max_length=256)
    private_key_id: str | None = Field(default=None, max_length=256)
    private_key: SecretStr = Field(
        min_length=1, max_length=65536, repr=False, json_schema_extra={"contentMediaType": "application/x-pem-file"}
    )
    client_email: str = Field(min_length=1, max_length=512)
    client_id: str | None = Field(default=None, max_length=256)
    auth_uri: Literal["https://accounts.google.com/o/oauth2/auth"] = "https://accounts.google.com/o/oauth2/auth"
    token_uri: Literal["https://oauth2.googleapis.com/token"] = "https://oauth2.googleapis.com/token"
    auth_provider_x509_cert_url: str | None = None
    client_x509_cert_url: str | None = None
    universe_domain: Literal["googleapis.com"] = "googleapis.com"

    @model_validator(mode="after")
    def validate_service_account(self):
        try:
            self.native_credentials()
        except (TypeError, ValueError):
            raise ValueError("Google credentials require a valid service-account key") from None
        return self

    def native_credentials(self):
        from google.oauth2.service_account import Credentials

        values = self.model_dump(exclude_none=True)
        values["private_key"] = self.private_key.get_secret_value()
        return Credentials.from_service_account_info(values)
