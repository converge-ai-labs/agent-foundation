"""Withhold known credential-bearing configuration fields from model context."""

import re
from collections.abc import Mapping
from typing import Annotated
from urllib.parse import parse_qsl, urlsplit

from pydantic import AfterValidator, Field, JsonValue, StringConstraints
from pydantic_ai import ModelRetry

PROTECTED = "[protected configuration value; preserve with a bounded patch]"
_CREDENTIAL_FIELDS = frozenset(
    {
        "apikey",
        "authorization",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "password",
        "clientsecret",
        "credential",
        "credentials",
        "secretvalue",
        "cookie",
        "cookies",
        "headers",
        "extraheaders",
    }
)


def protected_field(name: str) -> bool:
    return name.lower().replace("_", "").replace("-", "") in _CREDENTIAL_FIELDS


def credential_url(value: str) -> bool:
    if not value.startswith(("https://", "http://")):
        return False
    try:
        url = urlsplit(value)
        return bool(url.username or url.password or any(protected_field(key) for key, _ in parse_qsl(url.query)))
    except ValueError:
        return True


def model_safe(value: JsonValue, *, depth: int = 0) -> JsonValue:
    """This is a known-field ceiling, not a promise to detect secrets in prose."""
    if depth > 32:
        return PROTECTED
    if isinstance(value, dict):
        result = {
            key: PROTECTED if protected_field(key) else model_safe(item, depth=depth + 1) for key, item in value.items()
        }
        # Diff values may omit the field name, so use their complete authoring path.
        path = value.get("path")
        if isinstance(path, list) and any(isinstance(part, str) and protected_field(part) for part in path):
            for key in ("before", "after"):
                if key in result:
                    result[key] = PROTECTED
        return result
    if isinstance(value, list):
        return [model_safe(item, depth=depth + 1) for item in value]
    if isinstance(value, str) and credential_url(value):
        return PROTECTED
    return value


def contains_protected_input(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(protected_field(str(key)) or contains_protected_input(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_protected_input(item) for item in value)
    return isinstance(value, str) and (PROTECTED in value or credential_url(value))


# Quoted keys escape only their quote or a backslash. No expression evaluation.
_PATH_KEY = re.compile(
    r"(?P<bare>[A-Za-z_][A-Za-z0-9_-]*)"
    r"|\['(?P<single>(?:[^'\\\x00-\x1f]|\\['\\])*)'\]"
    r'|\["(?P<double>(?:[^"\\\x00-\x1f]|\\["\\])*)"\]'
)


def read_path_parts(path: str) -> tuple[str, ...]:
    """Parse a bounded JSONPath subset with an optional root prefix."""
    if path.startswith("$."):
        path = path[2:]
    elif path.startswith("$["):
        path = path[1:]
    parts: list[str] = []
    offset = 0
    while offset < len(path):
        if parts and path[offset] == ".":
            offset += 1
            if offset == len(path) or path[offset] == "[":
                break
        elif parts and path[offset] != "[":
            break
        match = _PATH_KEY.match(path, offset)
        if match is None:
            break
        key = match.group("bare")
        if key is None:
            key = re.sub(r"\\(.)", r"\1", match.group("single") or match.group("double") or "")
        if not 1 <= len(key) <= 128 or len(parts) == 16:
            break
        parts.append(key)
        offset = match.end()
    else:
        if parts:
            return tuple(parts)
    raise ValueError(
        "Use an object path such as config.instructions or config['key.with.dots']; "
        "the $. prefix is optional. Paths contain 1-16 keys of 1-128 characters. "
        "Array indices, wildcards, recursive descent and filters are unsupported."
    )


def _validate_read_path(path: str) -> str:
    read_path_parts(path)
    return path


ReadPath = Annotated[str, StringConstraints(min_length=1, max_length=8192), AfterValidator(_validate_read_path)]
ReadFields = Annotated[tuple[ReadPath, ...], Field(max_length=32)]


def select_fields(
    safe: dict[str, JsonValue], fields: ReadFields | None, *, required: tuple[str, ...] = ()
) -> dict[str, JsonValue]:
    """Select object paths only after the complete response has been made model-safe."""
    if fields is None:
        return safe
    selected: dict[tuple[str, ...], JsonValue] = {}
    for field in (*fields, *required):
        try:
            path = read_path_parts(field)
        except ValueError as error:
            raise ModelRetry(str(error)) from error
        value: JsonValue = safe
        for part in path:
            if not isinstance(value, dict) or part not in value:
                raise ModelRetry(
                    "Unknown or unavailable field path. Select existing object fields from the safe response; "
                    "arrays, nulls and protected values cannot be traversed."
                )
            value = value[part]
        selected[path] = value
    result: dict[str, JsonValue] = {}
    for path in sorted(selected, key=len):
        if any(path[:size] in selected for size in range(1, len(path))):
            continue
        target = result
        for part in path[:-1]:
            child = target.setdefault(part, {})
            assert isinstance(child, dict)
            target = child
        target[path[-1]] = selected[path]
    return result
