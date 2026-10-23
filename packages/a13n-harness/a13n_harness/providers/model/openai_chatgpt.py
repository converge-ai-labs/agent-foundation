"""Catalog glue for Pydantic AI's ChatGPT Provider and Responses Model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

import httpx2
from pydantic import Field, model_validator

from ..authentication import Authentication, CredentialMode
from .definition import DiscoveredModel, ModelOAuth, ModelProviderDefinition, ProviderOperationError
from .types import ModelConnection, ProviderConfiguration

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.providers import Provider
    from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentialSource


class Config(ProviderConfiguration):
    base_url: str | None = Field(
        default=None, exclude=True, json_schema_extra={"enum": [None, "https://api.openai.com/v1"]}
    )
    client_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        title="OAuth client ID",
        description="Leave blank to use deployment defaults or register a public client.",
    )
    redirect_uri: str | None = Field(
        default=None,
        max_length=2048,
        title="Loopback callback URL",
        description="http://127.0.0.1:PORT/auth/callback. Leave blank to use deployment defaults.",
    )

    @model_validator(mode="before")
    @classmethod
    def legacy_public_client(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("token_endpoint_auth_method") == "none":
            return {key: item for key, item in value.items() if key != "token_endpoint_auth_method"}
        return value

    @model_validator(mode="after")
    def registration(self) -> Self:
        if self.client_id is not None and (not self.client_id.strip() or self.client_id == "dynamic_agent_client"):
            raise ValueError("Use an issued public OAuth client ID")
        if self.redirect_uri is not None:
            from pydantic_ai.exceptions import UserError
            from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTOAuthFlow

            try:
                OpenAIChatGPTOAuthFlow(app_name="Agent Foundation OSS", redirect_uri=self.redirect_uri)
            except UserError as error:
                raise ValueError(str(error)) from None
        if self.base_url not in (None, "https://api.openai.com/v1"):
            raise ValueError("ChatGPT plan usage requires the public OpenAI endpoint")
        return self


def _provider(
    source: OpenAIChatGPTCredentialSource, client: httpx2.AsyncClient, headers: dict[str, str]
) -> Provider[Any]:
    from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTProvider

    client.headers.update(headers)
    provider = OpenAIChatGPTProvider(credential_source=source, http_client=client)
    provider.client.max_retries = 0
    return provider


def _model(name: str, provider: Provider[Any]) -> Model[Any]:
    from pydantic_ai.models.openai import OpenAIResponsesModel

    return OpenAIResponsesModel(name, provider=provider)


async def _discover(
    connection: ModelConnection[Config, Any],
    source: OpenAIChatGPTCredentialSource | None,
    client: httpx2.AsyncClient,
) -> tuple[DiscoveredModel, ...]:
    from openai import OpenAIError
    from pydantic_ai.exceptions import ModelAPIError, UserError

    from .chatgpt import discover_chatgpt_models
    from .oauth.models import ModelAuthenticationError

    assert source is not None
    try:
        models = await discover_chatgpt_models(
            credential_source=source, http_client=client, extra_headers=connection.extra_headers
        )
    except (OpenAIError, ModelAPIError, UserError, ModelAuthenticationError):
        raise ProviderOperationError("ChatGPT model discovery failed; check authorization and retry") from None
    return tuple(DiscoveredModel(model_name=model.slug, display_name=model.display_name) for model in models)


DEFINITION = ModelProviderDefinition(
    type="openai_chatgpt",
    display_name="ChatGPT",
    configuration_model=Config,
    credential_model=None,
    authentication=Authentication(mode=CredentialMode.forbidden),
    supported_model_apis=("openai.responses",),
    oauth=ModelOAuth(scheme="openai-chatgpt", build_provider=_provider),
    build_model=_model,
    model_discovery=_discover,
    catalog_providers=("openai",),
    endpoint="https://api.openai.com/v1",
    setup_url="https://chatgpt.com/settings/usage",
    setup_label="ChatGPT plan usage",
)
