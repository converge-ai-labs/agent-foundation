"""Shared Provider configuration and runtime values."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class CredentialFormat(StrEnum):
    api_key = "api_key"
    aws_credentials_json = "aws_credentials_json"
    google_service_account_json = "google_service_account_json"


class ProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)] | None = Field(
        default=None, title="Base URL", description="Leave empty to use the Provider's default endpoint."
    )

    @property
    def authentication_headers(self) -> tuple[str, ...]:
        return ()


class ValidatedProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configuration: dict[str, object]
    endpoint: str | None


@dataclass(frozen=True, slots=True)
class RuntimeProvider:
    type: str
    configuration: dict[str, object]
    endpoint: str | None
    credential: str | None = field(repr=False)
    extra_headers: dict[str, str] = field(default_factory=dict, repr=False)
