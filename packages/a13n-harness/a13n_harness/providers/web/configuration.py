"""Built-in Web inputs."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class EmptyConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiKeyCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key: SecretStr = Field(
        title="API Key", min_length=1, max_length=4096, repr=False, json_schema_extra={"writeOnly": True}
    )

    @field_validator("api_key")
    @classmethod
    def bounded_nonblank_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip() or len(value.get_secret_value().encode("utf-8")) > 4096:
            raise ValueError("api_key must be nonblank and at most 4096 UTF-8 bytes")
        return value


def require_api_key(credential: ApiKeyCredential | None) -> str:
    """State the declared `required` credential contract where Python cannot type it."""
    if credential is None:
        raise ValueError("this Web Provider requires an API key credential")
    return credential.api_key.get_secret_value()
