"""Shared declarations for the finite trusted Provider adapter registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class CredentialFormat(StrEnum):
    api_key = "api_key"
    aws_credentials_json = "aws_credentials_json"
    google_service_account_json = "google_service_account_json"


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyProviderConfig(ProviderConfig):
    pass


class ValidatedProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config: dict[str, object]
    endpoint: str | None


EndpointResolver = Callable[[Mapping[str, object]], str | None]
CredentialValidator = Callable[[Mapping[str, object], bool], None]


@dataclass(frozen=True, slots=True)
class ProviderType:
    """One trusted Provider type's configuration and capability declaration."""

    key: str
    display_name: str
    config_model: type[ProviderConfig]
    supported_model_apis: tuple[str, ...]
    credential_format: CredentialFormat | None = CredentialFormat.api_key
    credential_required: bool = True
    endpoint: str | EndpointResolver | None = None
    endpoint_config_field: str | None = None
    credential_validator: CredentialValidator | None = None
    supports_model_discovery: bool = False

    def validate_config(self, config: Mapping[str, object], *, credential_configured: bool) -> ValidatedProviderConfig:
        if self.credential_required and not credential_configured:
            raise ValueError("the provider credential is required")
        if self.credential_format is None and credential_configured:
            raise ValueError("the provider does not accept a credential")
        normalized = self.config_model.model_validate(dict(config)).model_dump(mode="json", exclude_none=True)
        if self.credential_validator is not None:
            self.credential_validator(normalized, credential_configured)
        endpoint = self.endpoint(normalized) if callable(self.endpoint) else self.endpoint
        return ValidatedProviderConfig(config=normalized, endpoint=endpoint)

    def with_validated_endpoint(self, validated: ValidatedProviderConfig, endpoint: str) -> ValidatedProviderConfig:
        normalized = dict(validated.config)
        if self.endpoint_config_field is not None:
            normalized[self.endpoint_config_field] = endpoint
        return ValidatedProviderConfig(config=normalized, endpoint=endpoint)


@dataclass(frozen=True, slots=True)
class RuntimeProvider:
    type: str
    config: dict[str, object]
    endpoint: str | None
    credential: str | None
