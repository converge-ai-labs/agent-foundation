"""Service-owned PATCH intent for encrypted Provider headers."""

from collections.abc import Mapping
from typing import Annotated

from a13n_harness.providers.model.headers import HeaderName, normalize_header_names
from pydantic import AfterValidator, BeforeValidator, Field, SecretStr


def _validate_secret_values(value: dict[str, SecretStr | None]) -> dict[str, SecretStr | None]:
    for secret in value.values():
        if secret is not None:
            text = secret.get_secret_value()
            if len(text) > 2048 or any(not 32 <= ord(char) <= 126 for char in text):
                raise ValueError("header values must be bounded printable ASCII strings")
    return value


# Up to 32 removals plus 32 replacements; the resulting account permits at most 32 headers.
HeaderUpdates = Annotated[
    dict[HeaderName, SecretStr | None],
    Field(max_length=64, json_schema_extra={"writeOnly": True}),
    BeforeValidator(normalize_header_names),
    AfterValidator(_validate_secret_values),
]

__all__ = ["HeaderUpdates", "apply_header_updates"]


def apply_header_updates[T](current: Mapping[str, T], updates: Mapping[str, T | None]) -> dict[str, T]:
    result = dict(current)
    for name, value in updates.items():
        if value is None:
            result.pop(name, None)
        else:
            result[name] = value
    return result
