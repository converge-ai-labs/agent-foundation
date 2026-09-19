"""One projection of the shared Provider definition core behind every type endpoint."""

from typing import TypedDict

from a13n_harness.providers.authentication import Authentication
from a13n_harness.providers.definition import ProviderDefinition
from pydantic import BaseModel, ConfigDict


class ProviderMetadataCore(TypedDict):
    """The fields every domain projects identically; each domain DTO adds its own."""

    type: str
    display_name: str
    configuration_schema: dict[str, object]
    credential_schema: dict[str, object] | None
    authentication: Authentication
    setup_url: str | None
    setup_label: str | None


class ProviderMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    display_name: str
    configuration_schema: dict[str, object]
    credential_schema: dict[str, object] | None
    authentication: Authentication
    setup_url: str | None = None
    setup_label: str | None = None


class ProviderMetadataCollection[M: ProviderMetadata](BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[M, ...]


def provider_metadata_core(definition: ProviderDefinition) -> ProviderMetadataCore:
    """Credential schemas are write-only here, so no domain has to remember it."""
    credential_model = definition.credential_model
    return ProviderMetadataCore(
        type=definition.type,
        display_name=definition.display_name,
        configuration_schema=definition.configuration_model.model_json_schema(),
        credential_schema=(
            {**credential_model.model_json_schema(), "writeOnly": True} if credential_model is not None else None
        ),
        authentication=definition.authentication,
        setup_url=definition.setup_url,
        setup_label=definition.setup_label,
    )
