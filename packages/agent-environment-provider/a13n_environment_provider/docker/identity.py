"""Deterministic private identities for Docker target recovery."""

import hashlib
import json

from .configuration import DockerTargetConfiguration


def configuration_fingerprint(configuration: DockerTargetConfiguration) -> str:
    payload = json.dumps(
        configuration.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def correlation(prefix: str, *values: str) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.encode())
        digest.update(b"\0")
    return f"{prefix}-{digest.hexdigest()[:24]}"
