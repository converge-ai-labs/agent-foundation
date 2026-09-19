"""Shared value constraints for built-in Connector Provider configurations."""

from pydantic import Field, SecretStr

from .contracts import StrictModel


class ApiKeyCredentials(StrictModel):
    # `SecretStr` already redacts the repr and emits `writeOnly` plus `format: password`.
    api_key: SecretStr = Field(title="API Key", min_length=1, max_length=4096)
