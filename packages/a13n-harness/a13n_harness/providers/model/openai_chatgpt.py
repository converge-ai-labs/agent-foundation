"""Catalog glue for the separately portable ChatGPT OAuth Provider and Model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

import httpx2
from pydantic import Field, model_validator

from .definition import ModelOAuth, ModelProviderDefinition
from .types import ProviderConfiguration

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.providers import Provider

    from .oauth.chatgpt import OpenAIChatGPTCredentialSource


class Config(ProviderConfiguration):
    # ChatGPT plan tokens must never be sent to a gateway or Codex backend.
    base_url: str | None = Field(
        default=None, exclude=True, json_schema_extra={"enum": [None, "https://api.openai.com/v1"]}
    )

    @model_validator(mode="after")
    def fixed_endpoint(self) -> Self:
        if self.base_url not in (None, "https://api.openai.com/v1"):
            raise ValueError("ChatGPT plan usage requires the public OpenAI endpoint")
        return self


def _provider(
    source: OpenAIChatGPTCredentialSource, client: httpx2.AsyncClient, headers: dict[str, str]
) -> Provider[Any]:
    from .chatgpt import OpenAIChatGPTProvider

    return OpenAIChatGPTProvider(credential_source=source, http_client=client, extra_headers=headers)


def _model(name: str, provider: Provider[Any]) -> Model[Any]:
    from ...models.chatgpt import OpenAIChatGPTResponsesModel

    return OpenAIChatGPTResponsesModel(name, provider=provider)


DEFINITION = ModelProviderDefinition(
    type="openai_chatgpt",
    display_name="ChatGPT",
    configuration_model=Config,
    supported_model_apis=("openai.responses",),
    oauth=ModelOAuth(scheme="openai-chatgpt", build_provider=_provider),
    build_model=_model,
    endpoint="https://api.openai.com/v1",
    setup_url="https://chatgpt.com/settings/usage",
    setup_label="ChatGPT plan usage",
)
