"""Ordinary JSON request hashing and SHA-256 wire values.

Domain-specific package and RFC 8785 digests retain their own encodings.
"""

import hashlib
import json
from typing import Annotated

from pydantic import BaseModel, StringConstraints

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def digest_request(value: object) -> str:
    """Hash normalized ordinary input; domains own semantic normalization."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
