"""Independent ingestion and historical query capabilities; credentials are write-only."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from a13n_service.providers.metrics import Endpoint
from a13n_service.resources.connections.headers import normalize_headers

type Collection = Literal["native", "pull", "proxy", "external"]
type Collector = Literal["vmagent", "alloy"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
LabelName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$", max_length=128)]
LabelValue = Annotated[str, StringConstraints(max_length=256)]
Labels = Annotated[dict[LabelName, LabelValue], Field(max_length=16)]


class Export(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    endpoint: Endpoint
    protocol: Literal["http/protobuf", "grpc"] = "http/protobuf"
    interval_seconds: int = Field(default=30, ge=5, le=300)


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # API base, including an optional gateway prefix, before /api/v1/query[_range].
    endpoint: Endpoint
    scope_labels: Labels = Field(default_factory=dict)


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    collection: Collection
    export: Export | None = None
    query: Query | None = None
    collector: Collector | None = None

    @model_validator(mode="after")
    def capabilities(self) -> Self:
        if self.collection == "native" and self.export is None:
            raise ValueError("Native collection requires an OTLP export endpoint")
        if self.collection == "proxy" and self.export is not None and self.export.protocol != "http/protobuf":
            raise ValueError("Proxy export supports OTLP HTTP/protobuf")
        if self.collection == "external" and (self.query is None or self.export is not None):
            raise ValueError("External collection requires a query endpoint and has no managed export")
        if (self.collection == "pull") != (self.collector is not None):
            raise ValueError("Exactly pull collection selects a deployment collector")
        if self.collection == "pull" and self.export is not None:
            raise ValueError("Pull delivery uses the collector's operator-owned pipeline")
        return self


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    headers: dict[str, SecretStr] = Field(default_factory=dict)

    @field_validator("headers")
    @classmethod
    def valid_headers(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        normalized = normalize_headers(
            {name: secret.get_secret_value() for name, secret in value.items()}, authentication=True
        )
        return {name: SecretStr(secret) for name, secret in normalized.items()}

    def plaintext(self) -> dict[str, str]:
        return {name: value.get_secret_value() for name, value in self.headers.items()}


class DataSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    config: Configuration
    enabled: bool = True
    export_credentials: Credentials | None = None
    query_credentials: Credentials | None = None

    deployment_preset_revision: str | None = Field(default=None, min_length=64, max_length=64)
    use_deployment_query_credentials: bool = False

    @model_validator(mode="after")
    def credential_destinations(self) -> Self:
        if self.use_deployment_query_credentials:
            if self.deployment_preset_revision is None:
                raise ValueError("Deployment credentials require the displayed preset revision")
            if self.query_credentials is not None:
                raise ValueError("Choose deployment or supplied query credentials, not both")
        for channel in ("export", "query"):
            credentials = self.export_credentials if channel == "export" else self.query_credentials
            destination = self.config.export if channel == "export" else self.config.query
            if credentials is not None and destination is None:
                raise ValueError(f"{channel} credentials require its endpoint")
        return self


class DataSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    config: Configuration | None = None
    enabled: bool | None = None
    # Omitted preserves; null clears; an object replaces the whole credential.
    export_credentials: Credentials | None = None
    query_credentials: Credentials | None = None


class DataSource(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str
    name: str
    config: Configuration
    enabled: bool
    builtin: bool
    has_export_credentials: bool
    has_query_credentials: bool
    version: int
    created_by_id: str
    updated_by_id: str
    created_at: datetime
    updated_at: datetime


class DeploymentPreset(BaseModel):
    name: str
    config: Configuration
    has_query_credentials: bool
    revision: str


class CollectorCapability(BaseModel):
    kind: Collector
    available: bool
    reason: Literal["not_configured", "monitoring_disabled"] | None = None


class Capabilities(BaseModel):
    proxy: bool
    collectors: list[CollectorCapability]


class DataSourcePage(BaseModel):
    items: list[DataSource]
    next_cursor: str | None
