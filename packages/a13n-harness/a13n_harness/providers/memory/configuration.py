"""Pure typed Mem0 connection inputs."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from a13n_harness._urls import require_http_url


class Mem0OSSConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = Field(min_length=1, max_length=2048)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = require_http_url(value)
        if parsed.query or parsed.fragment or value != value.strip():
            raise ValueError("Memory base URLs must not contain queries, fragments, or surrounding whitespace")
        return value.rstrip("/")


class Mem0PlatformConfiguration(Mem0OSSConfiguration):
    base_url: str = "https://api.mem0.ai"


class Mem0Credential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: SecretStr = Field(min_length=1)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("The API key cannot be blank")
        return value
