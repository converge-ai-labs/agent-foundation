"""The API shapes shared by every provider kind; `config` and `credential` follow the selected type's schemas."""

from datetime import datetime
from typing import Annotated, Literal

from a13n_harness.providers.authentication import Authentication
from a13n_harness.providers.model.headers import ExtraHeaders, HeaderName, HeaderValue, normalize_header_names
from a13n_harness.spec import HarnessModelCharacteristics
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue

from a13n_service.providers.registry import WebOperation

Credential = dict[str, JsonValue]
# A value sets a header, `null` removes one, and names left out keep their stored values.
HeaderUpdates = Annotated[
    dict[HeaderName, HeaderValue | None], Field(max_length=64), BeforeValidator(normalize_header_names)
]


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    config: dict[str, JsonValue] = Field(default_factory=dict)
    credential: Credential | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})
    # Headers a model provider adds to every request; their values are secrets, like the credential.
    extra_headers: ExtraHeaders = Field(default_factory=dict, repr=False, json_schema_extra={"writeOnly": True})
    enabled: bool = True


class ProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    # Replaces the whole configuration.
    config: dict[str, JsonValue] | None = None
    # Replaced whole when present; `null` removes it; omitted leaves it unchanged.
    credential: Credential | None = Field(default=None, repr=False, json_schema_extra={"writeOnly": True})
    extra_headers: HeaderUpdates = Field(default_factory=dict, repr=False, json_schema_extra={"writeOnly": True})
    enabled: bool | None = None


class Provider(BaseModel):
    id: str
    organization_id: str
    workspace_id: str
    type: str
    name: str
    config: dict[str, JsonValue]
    credential_configured: bool
    # The extra headers a model provider sends; their values are never returned.
    header_names: list[str]
    enabled: bool
    version: int
    created_by_id: str | None
    updated_by_id: str | None
    created_at: datetime
    updated_at: datetime


class ProviderPage(BaseModel):
    items: list[Provider]
    next_cursor: str | None


class ProviderTest(BaseModel):
    provider_id: str
    provider_version: int
    # `unsupported`: the type has no non-billable probe, so nothing was called.
    status: Literal["succeeded", "failed", "unsupported"]
    message: str | None


class ProviderModel(BaseModel):
    """An upstream choice; wire names retain the original account-discovery contract."""

    slug: str
    display_name: str
    # Optional public metadata, not an account entitlement or saved configuration.
    characteristics: HarnessModelCharacteristics | None = None


class ProviderType(BaseModel):
    type: str
    display_name: str
    configuration_schema: dict[str, JsonValue]
    credential_schema: dict[str, JsonValue] | None
    authentication: Authentication
    setup_url: str | None
    setup_label: str | None
    supports_test: bool
    supports_model_discovery: bool = False
    # Model types: the calling APIs a model may select, the default first, with their display names and native
    # settings schemas; and the model catalog channels that list the type's own model IDs.
    model_apis: list[str] | None = None
    default_model_api: str | None = None
    model_api_labels: dict[str, str] | None = None
    settings_schemas: dict[str, dict[str, JsonValue]] | None = None
    catalog_providers: list[str] | None = None
    # Web types: the tool operations they serve.
    oauth_scheme: str | None = None
    operations: list[WebOperation] | None = None
    # Environment types: the template schema, and whether its instances can be stopped and destroyed.
    environment_schema: dict[str, JsonValue] | None = None
    supports_stop: bool | None = None
    supports_destroy: bool | None = None


class ProviderTypePage(BaseModel):
    items: list[ProviderType]
    next_cursor: str | None
