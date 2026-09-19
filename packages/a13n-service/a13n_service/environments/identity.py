"""Backend scope and physical target identity, independent of resource ownership."""

import hashlib
import json
from collections.abc import Mapping

from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from pydantic import JsonValue


def target_identity(
    provider: EnvironmentProviderDefinition, configuration: Mapping[str, JsonValue], native_id: str | None
) -> str | None:
    if native_id is None:
        return None
    backend = provider.configuration_model.model_validate(configuration)
    payload = [provider.type, provider.backend_identity(backend), native_id]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def default_environment_name(environment_id: str) -> str:
    return f"Environment {environment_id[-8:]}"
