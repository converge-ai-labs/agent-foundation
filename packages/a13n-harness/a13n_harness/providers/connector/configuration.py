"""Shared value constraints for built-in Connector Provider configurations."""

from pydantic import Field

from .contracts import StrictModel


class ApiKeyCredentials(StrictModel):
    api_key: str = Field(
        title="API Key",
        min_length=1,
        max_length=4096,
        repr=False,
        json_schema_extra={"writeOnly": True, "format": "password"},
    )
