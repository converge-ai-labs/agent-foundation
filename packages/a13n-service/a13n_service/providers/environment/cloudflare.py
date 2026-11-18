"""Configuration for the Cloudflare Containers gateway endpoint."""

from urllib.parse import urlsplit

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator


class Account(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint: str = Field(title="Gateway HTTPS origin", min_length=1, max_length=2048)
    _endpoint_policy: EndpointPolicy = PrivateAttr(default_factory=EndpointPolicy)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.port not in {None, 443}
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or any(character.isspace() for character in value)
        ):
            raise ValueError("Expected an HTTPS gateway origin")
        return f"https://{url.hostname.lower()}" if ":" not in url.hostname else f"https://[{url.hostname.lower()}]"
