"""Pure bounded metadata and schema validation for Provider definitions."""

import json
import re
from urllib.parse import urlsplit

from pydantic import BaseModel

_PROVIDER_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def validate_definition(
    provider_type: str,
    display_name: str,
    setup_url: str,
    configuration_model: type[BaseModel],
    credential_model: type[BaseModel],
) -> None:
    _validate_type("Web", provider_type)
    _validate_display_name("Web", provider_type, display_name)
    _validate_setup_url(provider_type, setup_url)
    _validate_schema("Web configuration", provider_type, configuration_model)
    _validate_schema("Web credential", provider_type, credential_model)


def _validate_type(label: str, provider_type: str) -> None:
    if _PROVIDER_TYPE.fullmatch(provider_type) is None:
        raise ValueError(f"{label} Provider type {provider_type!r} is invalid")


def _validate_schema(label: str, provider_type: str, model: object) -> None:
    try:
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise TypeError("not a Pydantic model")
        schema = model.model_json_schema()
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise TypeError("schema must describe an object")
        encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise ValueError("schema is too large")
        pending: list[object] = [schema]
        visited = 0
        while pending:
            value = pending.pop()
            visited += 1
            if visited > 10_000:
                raise ValueError("schema is too complex")
            if isinstance(value, dict):
                reference = value.get("$ref")
                if reference is not None and (not isinstance(reference, str) or not reference.startswith("#/$defs/")):
                    raise ValueError("schema contains a remote reference")
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
    except Exception as error:
        raise ValueError(f"{label} Provider {provider_type!r} has an invalid schema") from error


def _validate_display_name(label: str, provider_type: str, display_name: str) -> None:
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 128:
        raise ValueError(f"{label} Provider {provider_type!r} has an invalid display name")


def _validate_setup_url(provider_type: str, setup_url: str) -> None:
    parsed = urlsplit(setup_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"Web Provider {provider_type!r} has an invalid setup URL")
