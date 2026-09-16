"""Shared contracts and mechanics for Provider adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx2
from a13n_harness.errors import ModelResolutionError
from pydantic_ai.providers import Provider

from ..headers import validate_header_names
from .types import CredentialFormat, ProviderConfiguration, RuntimeProvider, ValidatedProviderConfiguration


class ProviderOperationError(ValueError):
    """An expected failure of provider connection testing."""


class ProviderOperationUnsupported(ProviderOperationError):
    """The integration has no safe native operation for this command."""


NativeProviderBuilder = Callable[[RuntimeProvider, httpx2.AsyncClient, str], Provider[Any]]
EndpointResolver = Callable[[Mapping[str, object]], str | None]
CredentialValidator = Callable[[Mapping[str, object], bool], None]


@dataclass(frozen=True, slots=True)
class ConnectionProbeRequest:
    url: str
    headers: Mapping[str, str]


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
    connection_probe: Callable[[RuntimeProvider], ConnectionProbeRequest] | None = None
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


def bearer_models_request(provider: RuntimeProvider) -> ConnectionProbeRequest:
    return ConnectionProbeRequest(
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
