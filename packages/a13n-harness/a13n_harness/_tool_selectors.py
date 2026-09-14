"""Stable tool-selector validation and precedence shared by permission and risk policy."""

from collections.abc import Mapping


def validate_selector(selector: str) -> str:
    """Accept exact IDs, a trailing source prefix wildcard, or the global wildcard."""
    if not selector or selector != selector.strip() or len(selector) > 1024:
        raise ValueError("Permission selectors must be bounded non-blank strings")
    if "*" in selector and selector != "*" and not (selector.endswith(("/*", ".*")) and selector.count("*") == 1):
        raise ValueError("Selectors support only exact IDs, trailing '/*' or '.*', and '*'")
    return selector


def match_selector[T](rules: Mapping[str, T], tool_id: str) -> T | None:
    """Choose exact, then longest namespace prefix, then global default."""
    if tool_id in rules:
        return rules[tool_id]
    matches = [key for key in rules if key != "*" and key.endswith("*") and tool_id.startswith(key[:-1])]
    if matches:
        return rules[max(matches, key=len)]
    return rules.get("*")
