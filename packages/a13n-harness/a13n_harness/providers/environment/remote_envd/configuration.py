"""External daemon selection is state; backend access and credentials stay separate."""

from __future__ import annotations

from a13n_envd_client.eip.v1.models import AbsoluteEIPPath
from a13n_envd_client.http import normalize_http_endpoint
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class RemoteEnvdEnvironmentConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    working_directory: AbsoluteEIPPath | None = None
    required_methods: tuple[str, ...] = Field(default=(), max_length=128)

    @field_validator("required_methods")
    @classmethod
    def _methods(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not method or len(method) > 128 or method != method.strip() for method in value):
            raise ValueError("Required EIP methods must be bounded and nonblank")
        return tuple(sorted(set(value)))


class RemoteEnvdStateData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    device_id: str = Field(min_length=1, max_length=128)

    @field_validator("device_id")
    @classmethod
    def _identity(cls, value: str) -> str:
        if any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError("Device identity must contain no whitespace or control characters")
        return value


class RemoteEnvdConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    initialization_timeout: float = Field(default=10, gt=0, le=300, allow_inf_nan=False)
    request_timeout: float = Field(default=30, gt=0, le=3600, allow_inf_nan=False)
    max_in_flight: int = Field(default=32, ge=1, le=1024)


class HttpEnvdConnectionConfiguration(RemoteEnvdConnectionConfiguration):
    endpoint: str = Field(min_length=1, max_length=2048)
    allow_plaintext_private_link: bool = False

    @model_validator(mode="after")
    def _endpoint(self) -> HttpEnvdConnectionConfiguration:
        normalized = normalize_http_endpoint(
            self.endpoint, allow_plaintext_private_link=self.allow_plaintext_private_link
        )
        object.__setattr__(self, "endpoint", normalized)
        return self


class HttpEnvdCredential(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    token: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator("token")
    @classmethod
    def _token(cls, value: SecretStr) -> SecretStr:
        if any(character.isspace() or ord(character) < 32 for character in value.get_secret_value()):
            raise ValueError("EIP credential must contain no whitespace or control characters")
        return value


class WebSocketEnvdConnectionConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    connection_timeout: float = Field(default=10, gt=0, le=300, allow_inf_nan=False)
