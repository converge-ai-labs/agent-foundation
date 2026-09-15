"""Shared contracts and mechanics for Provider adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx2
from a13n_harness.errors import ModelResolutionError
from pydantic_ai.providers import Provider

from ..descriptions import default_candidate
from ..domain import ModelCandidate
from ..headers import validate_header_names
from .types import CredentialFormat, ProviderConfiguration, RuntimeProvider, ValidatedProviderConfiguration


class ProviderOperationError(ValueError):
    """An expected failure of provider discovery or connection testing."""


class ProviderOperationUnsupported(ProviderOperationError):
    """The integration has no safe native operation for this command."""


@dataclass(frozen=True, slots=True)
class DiscoveredModelIdentity:
    upstream_model: str
    display_name: str | None
    metadata: Mapping[str, Any]


NativeProviderBuilder = Callable[[RuntimeProvider, httpx2.AsyncClient, str], Provider[Any]]
EndpointResolver = Callable[[Mapping[str, object]], str | None]
CredentialValidator = Callable[[Mapping[str, object], bool], None]


@dataclass(frozen=True, slots=True)
class ModelListRequest:
    url: str
    headers: Mapping[str, str]


class ModelDiscoveryAdapter(Protocol):
    def request(self, provider: RuntimeProvider) -> ModelListRequest: ...

    def parse(self, payload: Any) -> list[DiscoveredModelIdentity]: ...

    def next_page(self, payload: Mapping[str, Any]) -> dict[str, str]: ...

    def describe(
        self, model_api: str, upstream_model: str, display_name: str | None, metadata: Mapping[str, Any]
    ) -> ModelCandidate: ...


@dataclass(frozen=True, slots=True)
class ModelListSchema:
    collection_field: str
    identifier_field: str
    display_name_fields: tuple[str, ...] = ()
    identifier_prefix: str = ""


@dataclass(frozen=True, slots=True)
class JsonModelDiscoveryAdapter:
    """Describe one Provider's bounded JSON model-list operation."""

    request_builder: Callable[[RuntimeProvider], ModelListRequest]
    schema: ModelListSchema

    def request(self, provider: RuntimeProvider) -> ModelListRequest:
        return self.request_builder(provider)

    def parse(self, payload: Any) -> list[DiscoveredModelIdentity]:
        if not isinstance(payload, Mapping):
            raise ProviderOperationError("the Provider model-list response is invalid")
        values = payload.get(self.schema.collection_field)
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ProviderOperationError("the Provider model-list response is invalid")

        parsed: list[DiscoveredModelIdentity] = []
        for value in values:
            if not isinstance(value, Mapping):
                continue
            raw_id = value.get(self.schema.identifier_field)
            if not isinstance(raw_id, str):
                continue
            model_id = raw_id.removeprefix(self.schema.identifier_prefix).strip()
            if not 1 <= len(model_id) <= 256:
                continue
            parsed.append(
                DiscoveredModelIdentity(
                    model_id, _display_name(value, self.schema.display_name_fields, model_id), dict(value)
                )
            )
        return parsed

    def next_page(self, payload: Mapping[str, Any]) -> dict[str, str]:
        if any(payload.get(key) for key in ("has_more", "nextPageToken", "next", "next_cursor", "nextLink")):
            raise ProviderOperationError("the Provider returned an unsupported continuation format")
        return {}

    def describe(
        self, model_api: str, upstream_model: str, display_name: str | None, metadata: Mapping[str, Any]
    ) -> ModelCandidate:
        return default_candidate(model_api, upstream_model, display_name)


@dataclass(frozen=True, slots=True)
class ProviderIntegration:
    """One trusted Provider type's metadata and connection behavior."""

    type: str
    display_name: str
    configuration_model: type[ProviderConfiguration]
    supported_model_apis: tuple[str, ...]  # First API is the authoring default.
    build_provider: NativeProviderBuilder
    credential_format: CredentialFormat | None = CredentialFormat.api_key
    credential_required: bool = True
    endpoint: str | EndpointResolver | None = None
    endpoint_configuration_field: str | None = None
    credential_validator: CredentialValidator | None = None
    model_discovery: ModelDiscoveryAdapter | None = None
    reserved_headers: tuple[str, ...] = ("authorization",)
    additional_endpoint_fields: tuple[str, ...] = ()

    def validate_configuration(
        self,
        configuration: Mapping[str, object],
        *,
        credential_configured: bool,
        header_names: Sequence[str] = (),
    ) -> ValidatedProviderConfiguration:
        if self.credential_required and not credential_configured:
            raise ValueError("the provider credential is required")
        if self.credential_format is None and credential_configured:
            raise ValueError("the provider does not accept a credential")
        parsed = self.configuration_model.model_validate(dict(configuration))
        reserved = (*self.reserved_headers, *parsed.authentication_headers)
        affinity_header = parsed.session_affinity_header
        if affinity_header is not None:
            validate_header_names((affinity_header,), reserved=reserved)
            reserved = (*reserved, affinity_header)
        validate_header_names(header_names, reserved=reserved)
        normalized = parsed.model_dump(mode="json", by_alias=True, exclude_none=False, exclude_defaults=True)
        if self.credential_validator is not None:
            self.credential_validator(normalized, credential_configured)
        endpoint = parsed.base_url or (self.endpoint(normalized) if callable(self.endpoint) else self.endpoint)
        return ValidatedProviderConfiguration(configuration=normalized, endpoint=endpoint)

    def with_validated_endpoint(
        self, validated: ValidatedProviderConfiguration, endpoint: str
    ) -> ValidatedProviderConfiguration:
        normalized = dict(validated.configuration)
        if "base_url" in normalized:
            normalized["base_url"] = endpoint
        elif self.endpoint_configuration_field is not None:
            normalized[self.endpoint_configuration_field] = endpoint
        return ValidatedProviderConfiguration(configuration=normalized, endpoint=endpoint)


def bearer_models_request(provider: RuntimeProvider) -> ModelListRequest:
    return ModelListRequest(
        url=join_url(require_endpoint(provider), "models"),
        headers={"authorization": f"Bearer {require_credential(provider)}"},
    )


def join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def require_endpoint(provider: RuntimeProvider) -> str:
    if provider.endpoint is None:
        raise ValueError("the Model Provider endpoint is missing")
    return provider.endpoint


def require_credential(provider: RuntimeProvider) -> str:
    if not provider.credential:
        raise ModelResolutionError(
            "The Model Provider credential is unavailable.",
            code="model_provider_credential_unavailable",
            details={"provider_type": provider.type},
        )
    return provider.credential


def _display_name(value: Mapping[object, object], fields: tuple[str, ...], model_id: str) -> str | None:
    for field in fields:
        raw_display_name = value.get(field)
        if isinstance(raw_display_name, str):
            display_name = raw_display_name.strip()
            if display_name != model_id and 1 <= len(display_name) <= 128:
                return display_name
    return None
