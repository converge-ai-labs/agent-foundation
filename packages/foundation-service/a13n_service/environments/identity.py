"""Backend scope and physical target identity, independent of resource ownership."""

import hashlib
import json
import socket
from collections.abc import Mapping

from pydantic import JsonValue
from sqlalchemy import or_

from .models import EnvironmentProviderRecord


def target_identity(provider_type: str, configuration: Mapping[str, JsonValue], native_id: str | None) -> str | None:
    if native_id is None:
        return None
    payload = [provider_type, dict(configuration), native_id]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def local_backend_eligible():
    host = EnvironmentProviderRecord.configuration["host_id"].as_string()
    return or_(host.is_(None), host == socket.gethostname())
