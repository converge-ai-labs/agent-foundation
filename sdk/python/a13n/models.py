"""Native Search Provider values and lossless Agent search configuration."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr


class Resource(BaseModel):
    # Ignore additive response fields, including any accidental credential field.
    model_config = ConfigDict(extra="ignore")


class SearchSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    provider_id: str
    max_results: int = Field(default=5, ge=1, le=10)
    include_domains: list[str] = Field(default_factory=list, max_length=20)


class AgentConfig(BaseModel):
    """Lossless Agent configuration; non-search fields remain Service-owned JSON.

    Other Agent fields are preserved verbatim and validated by Service. This SDK
    currently supplies typed validation for the search surface only.
    """

    model_config = ConfigDict(extra="allow")
    search: SearchSelection | None = None

    def to_wire(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_unset=True)


class AgentRunOverride(AgentConfig):
    """Omitted search inherits; explicit None disables; an object replaces."""


class PrincipalRef(Resource):
    principal_type: str
    principal_id: str


class SearchProvider(Resource):
    id: str
    organization_id: str
    workspace_id: str | None
    name: str
    type: str
    configuration: dict[str, JsonValue]
    enabled: bool
    credential_configured: bool
    created_at: datetime
    updated_at: datetime
    created_by: PrincipalRef
    updated_by: PrincipalRef


class SearchProviderDefinition(Resource):
    type: str
    display_name: str
    configuration_schema: dict[str, JsonValue]
    credential_schema: dict[str, JsonValue]
    credential_required: bool = True
    setup_url: str


class SearchProviderReference(Resource):
    agent_id: str
    agent_revision_id: str
    version: int
    is_current: bool


class SearchProviderTestResult(Resource):
    success: bool
    code: str | None
    checked_at: datetime


class Page[T](Resource):
    items: list[T]
    next_cursor: str | None = None


class Representation[T](Resource):
    value: T
    etag: str | None
    request_id: str | None


class CreateSearchProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    type: Literal["brave", "exa"]
    name: str
    credential: SecretStr = Field(repr=False)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    enabled: bool = True


class UpdateSearchProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str | None = None
    configuration: dict[str, JsonValue] | None = None
    enabled: bool | None = None
    credential: SecretStr | None = Field(default=None, repr=False)
