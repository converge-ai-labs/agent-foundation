"""Typed Provider account inputs: selected implementation, configuration, and credentials."""

from __future__ import annotations

import json

from a13n_harness.providers.environment.catalog import EnvironmentProviderCatalog
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from pydantic import BaseModel, ValidationError

from a13n_service.credentials import credential_payload

from .errors import invalid_environment, provider_unavailable


def provider_implementation(catalog: EnvironmentProviderCatalog, provider_type: str) -> EnvironmentProviderDefinition:
    """A stored type this deployment no longer selects is a safe error, never a crash."""
    try:
        return catalog.require(provider_type)
    except EnvironmentProviderError as error:
        raise provider_unavailable() from error


def provider_configuration(catalog: EnvironmentProviderCatalog, provider_type: str, value: object) -> BaseModel:
    return provider_implementation(catalog, provider_type).configuration_model.model_validate(value)


def provider_credential(
    catalog: EnvironmentProviderCatalog, provider_type: str, configuration: BaseModel, value: dict | None
) -> str | None:
    """Enforce the declared credential requirement, then encrypt the revealed secret payload."""
    definition = provider_implementation(catalog, provider_type)
    try:
        definition.authentication.validate_presence(configuration, value is not None)
        if value is None:
            return None
        if definition.credential_model is None:
            raise ValueError("this Provider does not accept credentials")
        return json.dumps(credential_payload(definition.credential_model.model_validate(value)))
    except (ValidationError, ValueError) as error:
        raise invalid_environment("Provider credential is invalid") from error
