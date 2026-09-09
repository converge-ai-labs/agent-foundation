"""Optional private configuration for additional real-provider HTTP journeys."""

import os
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

DEFAULT_PATH = Path(__file__).with_name("providers.local.toml")


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True, hide_input_in_errors=True)


class EnvironmentSettings(Settings):
    type: Literal["a13n.e2b"]
    api_key: SecretStr = Field(min_length=1)
    template: str = Field(default="base", min_length=1)


class ComposioSettings(Settings):
    provider: Literal["composio"]
    api_key: SecretStr = Field(min_length=1)
    toolkits: list[str] = Field(default_factory=lambda: ["github"], min_length=1)


class OpenConnectorSettings(Settings):
    provider: Literal["openconnector"]
    project_api_key: SecretStr = Field(min_length=1)
    catalog_api_key: SecretStr = Field(min_length=1)
    services: list[str] = Field(default_factory=lambda: ["slack"], min_length=1)


class ModelSettings(Settings):
    provider: Literal["openrouter", "openai_compatible"]
    api_key: SecretStr = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str = ""

    @model_validator(mode="after")
    def endpoint(self):
        if self.provider == "openrouter":
            if self.base_url:
                raise ValueError("OpenRouter uses its built-in endpoint; leave base_url empty")
            return self
        parts = urlsplit(self.base_url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError("base_url must be an HTTPS URL without credentials, query or fragment")
        return self


class ProviderSettings(Settings):
    environment: EnvironmentSettings | None = None
    connector: ComposioSettings | OpenConnectorSettings | None = Field(default=None, discriminator="provider")
    model: ModelSettings | None = None


def load_provider_settings(path: Path | None = None) -> ProviderSettings:
    """Missing default/all-empty sections disable integration; explicit bad paths fail."""
    override = os.environ.get("LIVE_TEST_PROVIDERS_CONFIG")
    selected = path or (Path(override).expanduser() if override else DEFAULT_PATH)
    if not selected.exists() and path is None and not override:
        return ProviderSettings()
    try:
        raw = tomllib.loads(selected.read_text())
        normalized = {}
        for section, values in raw.items():
            if isinstance(values, dict):
                values = {
                    key: value.strip() if isinstance(value, str) else value
                    for key, value in values.items()
                    if not (isinstance(value, str) and not value.strip())
                }
                values = values or None
            normalized[section] = values
        return ProviderSettings.model_validate(normalized)
    except (OSError, ValueError) as error:
        # Never include TOML source, validation input, or upstream exception chains.
        detail = "cannot read or parse TOML"
        if isinstance(error, ValidationError):
            known = {"environment", "connector", "model"}
            sections = sorted({str(item["loc"][0]) for item in error.errors() if item["loc"]} & known)
            detail = "invalid settings" + (" in " + ", ".join(sections) if sections else "")
        raise ValueError(
            f"Provider configuration: {detail}; see dev/live_tests/providers.example.toml and README"
        ) from None
