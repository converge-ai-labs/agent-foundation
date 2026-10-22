"""Deterministic keyword ranking over current tool definitions."""

import re
from typing import Any


def words(value: str) -> list[str]:
    # Split identifier boundaries before case folding, including HTTPServer -> HTTP Server.
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return re.findall(r"[^\W_]+", value.casefold())


def keyword_rank(tokens: set[str], fields: tuple[str, ...]) -> tuple[int, tuple[int, ...]]:
    """Count each keyword in its strongest whole-word/substring field bucket."""
    best: dict[str, int] = {}
    for field, text in enumerate(fields):
        folded = text.casefold()
        hits = {token for token in tokens if token in folded}
        if not hits:
            continue
        field_words = set(words(text))
        for token in hits:
            bucket = field if token in field_words else len(fields) + field
            best[token] = min(best.get(token, bucket), bucket)
    counts = [0] * (2 * len(fields))
    for bucket in best.values():
        counts[bucket] -= 1
    return len(best), tuple(counts)


def parameter_search_text(schema: dict[str, Any]) -> tuple[str, str]:
    """Read input property names/descriptions at physical schema nodes, without resolving refs."""
    names: list[str] = []
    descriptions: list[str] = []
    pending = [schema]
    while pending:
        node = pending.pop()
        description = node.get("description")
        if isinstance(description, str):
            descriptions.append(description)
        for key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
            children = node.get(key)
            if isinstance(children, dict):
                if key == "properties":
                    names.extend(children)
                pending.extend(child for child in children.values() if isinstance(child, dict))
        for key in (
            "items",
            "additionalItems",
            "additionalProperties",
            "contains",
            "propertyNames",
            "unevaluatedItems",
            "unevaluatedProperties",
            "not",
            "if",
            "then",
            "else",
            "allOf",
            "anyOf",
            "oneOf",
            "prefixItems",
        ):
            child = node.get(key)
            if isinstance(child, dict):
                pending.append(child)
            elif isinstance(child, list):
                pending.extend(item for item in child if isinstance(item, dict))
    return " ".join(names), " ".join(descriptions)
