"""Definition for Google's separately authorized AI Plan provider."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from pydantic import Field, model_validator

from ..authentication import Authentication, CredentialMode
from .definition import GoogleAIPlanModelOAuth, ModelProviderDefinition
from .types import ProviderConfiguration

if TYPE_CHECKING:
    import httpx2
    from pydantic_ai.providers import Provider

    from .oauth.google_ai_plan import GoogleAIPlanCredentialSource


BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
RESERVED_HEADERS = ("authorization", "dpop", "user-agent", "x-goog-api-key", "x-goog-user-project", "host")
MODEL_SUGGESTIONS = (
    ("gemini-4-hydrogen", "Gemini 4 Hydrogen"),
    ("gemini-4-neon", "Gemini 4 Neon"),
    ("gemini-4-carbon", "Gemini 4 Carbon"),
    ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
)


class Config(ProviderConfiguration):
    base_url: str | None = Field(default=None, exclude=True, json_schema_extra={"enum": [None]})
    forward_context_headers: bool = False

    @model_validator(mode="after")
    def fixed_endpoint(self) -> Self:
        if self.base_url is not None or self.session_affinity_header is not None:
            raise ValueError("Google AI Plan does not support endpoint or session affinity overrides.")
        return self


def _provider(
    source: GoogleAIPlanCredentialSource, client: httpx2.AsyncClient, headers: dict[str, str]
) -> Provider[Any]:
    from ...models.google_ai_plan import GoogleAIPlanProvider

    return GoogleAIPlanProvider(client, source=source, headers=headers)


DEFINITION = ModelProviderDefinition(
    type="google_ai_plan",
    display_name="Google AI Plan",
    configuration_model=Config,
    credential_model=None,
    authentication=Authentication(mode=CredentialMode.forbidden),
    supported_model_apis=("google.interactions",),
    oauth=GoogleAIPlanModelOAuth(build_provider=_provider),
    endpoint=BASE_URL,
    reserved_headers=RESERVED_HEADERS,
)
