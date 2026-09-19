"""Typed Provider account inputs: selected implementation, configuration, and credentials."""

from __future__ import annotations

import json

from a13n_harness.providers.catalog import ProviderCatalog, ProviderNotSelected
from a13n_harness.providers.environment import EnvironmentProviderDefinition
from pydantic import BaseModel, ValidationError

from a13n_service.credentials import provider_credential_payload

from .errors import invalid_environment, provider_unavailable


def provider_implementation(
    catalog: ProviderCatalog[EnvironmentProviderDefinition], provider_type: str
) -> EnvironmentProviderDefinition:
    """A stored type this deployment no longer selects is a safe error, never a crash."""
    try:
        return catalog.require(provider_type)
    except ProviderNotSelected as error:
        raise provider_unavailable() from error


def provider_configuration(
    catalog: ProviderCatalog[EnvironmentProviderDefinition], provider_type: str, value: object
) -> BaseModel:
    return provider_implementation(catalog, provider_type).configuration_model.model_validate(value)


def provider_credential(
    catalog: ProviderCatalog[EnvironmentProviderDefinition],
    provider_type: str,
    configuration: BaseModel,
    value: dict | None,
) -> str | None:
    """Enforce the declared credential requirement, then encrypt the revealed secret payload."""
    definition = provider_implementation(catalog, provider_type)
    try:
        payload = provider_credential_payload(definition, configuration, value)
    except (ValidationError, ValueError) as error:
        raise invalid_environment("Provider credential is invalid") from error
    return None if payload is None else json.dumps(payload)
