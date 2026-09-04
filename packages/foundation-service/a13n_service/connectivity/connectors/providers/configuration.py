"""Shared value constraints for built-in Connector Provider configurations."""

from typing import Annotated

from pydantic import AfterValidator, Field

from ..domain import ConnectorKey, StrictModel
from ..validation import normalized_endpoint


def unique_connector_keys(value: tuple[str, ...]) -> tuple[str, ...]:
    if len(set(value)) != len(value):
        raise ValueError("Connector keys must be unique")
    return tuple(sorted(value))


Endpoint = Annotated[str, Field(min_length=1, max_length=2048), AfterValidator(normalized_endpoint)]
ConnectorKeys = Annotated[
    tuple[ConnectorKey, ...],
    Field(min_length=1, max_length=256, json_schema_extra={"uniqueItems": True}),
    AfterValidator(unique_connector_keys),
]


class ApiKeyCredentials(StrictModel):
    api_key: str = Field(
        min_length=1, max_length=4096, repr=False, json_schema_extra={"writeOnly": True, "format": "password"}
    )
