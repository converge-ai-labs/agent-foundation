"""Native Web Provider values and lossless Agent Web configuration."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr


class Resource(BaseModel):
    # Ignore additive response fields, including any accidental credential field.
    model_config = ConfigDict(extra="ignore")


class SearchToolConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    provider_id: str | None = None
    max_results: int = Field(default=5, ge=1, le=10)
    allow_domains: list[str] = Field(default_factory=list, max_length=256)
    deny_domains: list[str] = Field(default_factory=list, max_length=256)


class ScrapeToolConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    provider_id: str | None = None
    max_content_bytes: int = Field(default=512 * 1024, ge=1, le=4 * 1024 * 1024)
    allow_domains: list[str] = Field(default_factory=list, max_length=256)
    deny_domains: list[str] = Field(default_factory=list, max_length=256)


class FetchToolConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    max_content_bytes: int = Field(default=256 * 1024, ge=1, le=256 * 1024)
    allow_domains: list[str] = Field(default_factory=list, max_length=256)
    deny_domains: list[str] = Field(default_factory=list, max_length=256)


class DownloadToolConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    allow_domains: list[str] = Field(default_factory=list, max_length=256)
    deny_domains: list[str] = Field(default_factory=list, max_length=256)


class ToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    enabled: bool = True
    permission: Literal["auto", "allow", "ask", "deny", "review"] = "auto"
    config: dict[str, JsonValue] = Field(default_factory=dict)


class ToolsetSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    enabled: bool = True
    config: dict[str, JsonValue] = Field(default_factory=dict)
    tools: dict[str, ToolSelection] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    """Lossless Agent configuration with typed built-in Toolset selections.

    Other Agent fields are preserved verbatim and validated by Service.
    """

    model_config = ConfigDict(extra="allow")
    toolsets: dict[Literal["files", "shell", "web", "assets"], ToolsetSelection] = Field(default_factory=dict)

    def to_wire(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", exclude_unset=True)


class AgentRunOverride(AgentConfig):
    """Each submitted Toolset entry replaces that complete inherited entry."""


class PrincipalRef(Resource):
    principal_type: str
    principal_id: str


class WebProvider(Resource):
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


class WebProviderDefinition(Resource):
    type: str
    display_name: str
    configuration_schema: dict[str, JsonValue]
    credential_schema: dict[str, JsonValue]
    credential_required: bool = True
    setup_url: str
    operations: list[Literal["search", "scrape"]]
    supports_restricted_scrape: bool = False


class WebProviderReference(Resource):
    agent_id: str
    agent_revision_id: str
    version: int
    is_current: bool


class WebProviderTestResult(Resource):
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


class CreateWebProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    type: Literal["brave", "exa"]
    name: str
    credential: SecretStr = Field(repr=False)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    enabled: bool = True


class UpdateWebProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str | None = None
    configuration: dict[str, JsonValue] | None = None
    enabled: bool | None = None
    credential: SecretStr | None = Field(default=None, repr=False)
