"""Backend scope and physical target identity, independent of resource ownership."""

import hashlib
import json
import socket
from collections.abc import Mapping

from a13n_environment import EnvironmentProvider
from pydantic import JsonValue
from sqlalchemy import or_

from .models import EnvironmentProviderRecord


def target_identity(
    provider: EnvironmentProvider, configuration: Mapping[str, JsonValue], native_id: str | None
) -> str | None:
    if native_id is None:
        return None
    backend = provider.provider_configuration_model.model_validate(configuration)
    payload = [provider.key, provider.backend_identity(backend), native_id]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def local_backend_eligible():
    host = EnvironmentProviderRecord.configuration["host_id"].as_string()
    return or_(host.is_(None), host == socket.gethostname())
