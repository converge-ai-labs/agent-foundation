"""Safe Search Provider resources and durable Agent selections."""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Annotated, Literal

from a13n_harness.toolsets.domains import DomainRestrictions
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr, model_validator

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import ObjectId
from a13n_service.names import DisplayName


def normalize_domain(value: str) -> str:
    try:
        domain = value.removesuffix(".").encode("idna").decode("ascii").lower()
        if len(domain) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", part) for part in domain.split(".")
        ):
            raise ValueError("invalid DNS hostname")
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            return domain
        raise ValueError("IP addresses are not DNS hostnames")
    except UnicodeError as error:
        raise ValueError("invalid DNS hostname") from error


class SearchSelection(DomainRestrictions):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ObjectId
    max_results: int = Field(default=5, ge=1, le=10)
    include_domains: tuple[Annotated[str, AfterValidator(normalize_domain)], ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def unique_domains(self) -> SearchSelection:
        if len(set(self.include_domains)) != len(self.include_domains):
            raise ValueError("include_domains must be distinct after normalization")
        return self


def validate_credential(value: SecretStr) -> SecretStr:
    raw = value.get_secret_value()
    if not raw.strip() or len(raw.encode("utf-8")) > 4096:
        raise ValueError("credential must be nonblank and at most 4096 UTF-8 bytes")
    return value


Credential = Annotated[SecretStr, AfterValidator(validate_credential)]


class SearchConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreateSearchProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["brave", "exa"]
    name: DisplayName
    configuration: SearchConfiguration = Field(default_factory=SearchConfiguration)
    credential: Credential = Field(json_schema_extra={"writeOnly": True})
    enabled: bool = True


class UpdateSearchProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DisplayName | None = None
    configuration: SearchConfiguration | None = None
    credential: Credential | None = Field(default=None, json_schema_extra={"writeOnly": True})
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> UpdateSearchProviderRequest:
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("supplied fields cannot be null")
        return self


class SearchProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ObjectId
    organization_id: ObjectId
    workspace_id: ObjectId | None
    type: str
    name: str
    configuration: dict[str, object]
    credential_configured: bool
    enabled: bool
    created_by: PrincipalRef
    updated_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class SearchProviderCollection(BaseModel):
    items: tuple[SearchProvider, ...]
    next_cursor: str | None = None


class SearchProviderDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    display_name: str
    configuration_schema: dict[str, object]
    credential_schema: dict[str, object]
    credential_required: bool = True
    setup_url: str


class SearchProviderDefinitionCollection(BaseModel):
    items: tuple[SearchProviderDefinition, ...]


class SearchProviderTestResult(BaseModel):
    success: bool
    code: str | None
    checked_at: datetime


class SearchProviderReference(BaseModel):
    agent_id: ObjectId
    agent_revision_id: ObjectId
    version: int
    is_current: bool


class SearchProviderReferenceCollection(BaseModel):
    items: tuple[SearchProviderReference, ...]
    next_cursor: str | None = None


def definitions() -> tuple[SearchProviderDefinition, ...]:
    return tuple(
        SearchProviderDefinition(
            type=key,
            display_name=name,
            configuration_schema=SearchConfiguration.model_json_schema(),
            credential_schema={"type": "string", "writeOnly": True, "minLength": 1, "maxLength": 4096},
            setup_url=url,
        )
        for key, name, url in (
            ("brave", "Brave Search", "https://api-dashboard.search.brave.com/"),
            ("exa", "Exa", "https://dashboard.exa.ai/api-keys"),
        )
    )
