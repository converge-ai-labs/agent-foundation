"""Shared Provider configuration and runtime values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class CredentialFormat(StrEnum):
    api_key = "api_key"
    aws_credentials_json = "aws_credentials_json"
    google_service_account_json = "google_service_account_json"


class ProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyProviderConfiguration(ProviderConfiguration):
    pass


class ValidatedProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configuration: dict[str, object]
    endpoint: str | None


@dataclass(frozen=True, slots=True)
class RuntimeProvider:
    type: str
    configuration: dict[str, object]
    endpoint: str | None
    credential: str | None
