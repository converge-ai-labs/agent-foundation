"""Safe Web Provider resources and phase-local Agent Web selections."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from a13n_harness.toolsets.domains import DomainRestrictions
from pydantic import BaseModel, ConfigDict, Field, model_validator

from a13n_service.iam.domain import PrincipalRef
from a13n_service.ids import ObjectId
from a13n_service.names import DisplayName

MAX_SCRAPE_CONTENT_BYTES = 4 * 1024 * 1024


class SearchSelection(DomainRestrictions):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ObjectId
    max_results: int = Field(default=5, ge=1, le=10)


class ScrapeSelection(DomainRestrictions):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ObjectId
    max_content_bytes: int = Field(default=512 * 1024, ge=1, le=MAX_SCRAPE_CONTENT_BYTES)


class FetchSelection(DomainRestrictions):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_content_bytes: int = Field(default=256 * 1024, ge=1, le=256 * 1024)


class DownloadSelection(DomainRestrictions):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WebSelection(BaseModel):
    """Direct four-tool selection until the keyed Toolset catalog replaces it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    search: SearchSelection | None = None
    scrape: ScrapeSelection | None = None
    fetch: FetchSelection | None = None
    download: DownloadSelection | None = None

    @model_validator(mode="after")
    def require_tool(self) -> WebSelection:
        if all(getattr(self, name) is None for name in ("search", "scrape", "fetch", "download")):
            raise ValueError("at least one Web tool must be selected")
        return self


class CreateWebProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: DisplayName
    configuration: dict[str, object] = Field(default_factory=dict)
    credential: dict[str, object] | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})
    enabled: bool = True


class UpdateWebProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DisplayName | None = None
    configuration: dict[str, object] | None = None
    credential: dict[str, object] | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> UpdateWebProviderRequest:
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("supplied fields cannot be null")
        return self


class WebProvider(BaseModel):
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


class WebProviderCollection(BaseModel):
    items: tuple[WebProvider, ...]
    next_cursor: str | None = None


class WebProviderDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    display_name: str
    configuration_schema: dict[str, object]
    credential_schema: dict[str, object]
    credential_required: bool = True
    setup_url: str
    operations: tuple[Literal["search", "scrape"], ...]
    supports_restricted_scrape: bool = False


class WebProviderDefinitionCollection(BaseModel):
    items: tuple[WebProviderDefinition, ...]


class WebProviderTestResult(BaseModel):
    success: bool
    code: str | None
    checked_at: datetime


class WebProviderReference(BaseModel):
    agent_id: ObjectId
    agent_revision_id: ObjectId
    version: int
    is_current: bool


class WebProviderReferenceCollection(BaseModel):
    items: tuple[WebProviderReference, ...]
    next_cursor: str | None = None


def provider_selections(
    selection: WebSelection | None,
) -> tuple[tuple[Literal["search", "scrape"], SearchSelection | ScrapeSelection], ...]:
    if selection is None:
        return ()
    values: list[tuple[Literal["search", "scrape"], SearchSelection | ScrapeSelection]] = []
    if selection.search is not None:
        values.append(("search", selection.search))
    if selection.scrape is not None:
        values.append(("scrape", selection.scrape))
    return tuple(values)
