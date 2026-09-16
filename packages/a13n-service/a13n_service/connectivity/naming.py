"""Stable internal identities and bounded model-facing external tool names."""

import hashlib
import re
from collections import Counter

MODEL_TOOL_NAME_LIMIT = 64
CONNECTION_ALIAS_LIMIT = 29
TOOL_NAME_LIMIT = MODEL_TOOL_NAME_LIMIT - CONNECTION_ALIAS_LIMIT - 1
HASH_LENGTH = 16


def source_key(kind: str, identifier: str) -> str:
    return f"{kind}_{hashlib.sha256(identifier.encode()).hexdigest()[:HASH_LENGTH]}"


def connection_model_aliases(connections: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """Freeze readable, distinct capability names for one accepted tool surface."""
    stem_limit = CONNECTION_ALIAS_LIMIT - len("conn_")
    stems = {
        identifier: (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "connection")[:stem_limit]
        for identifier, name in connections
    }
    stems = {identifier: stem if stem[0].isalpha() else f"c_{stem}"[:stem_limit] for identifier, stem in stems.items()}
    counts = Counter(stems.values())
    collision_stem_limit = stem_limit - HASH_LENGTH - 1
    return {
        identifier: (
            f"conn_{stem}"
            if counts[stem] == 1
            else f"conn_{stem[:collision_stem_limit]}_{hashlib.sha256(identifier.encode()).hexdigest()[:HASH_LENGTH]}"
        )
        for identifier, stem in stems.items()
    }


def portable_tool_name(name: str) -> str:
    if len(name) <= TOOL_NAME_LIMIT and re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        return name
    stem = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[: TOOL_NAME_LIMIT - HASH_LENGTH - 1]
    return f"{stem}_{hashlib.sha256(name.encode()).hexdigest()[:HASH_LENGTH]}"
