"""Catalog glue for the separately portable ChatGPT OAuth Provider and Model."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Self

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from ..authentication import Authentication, AuthenticationCase, CredentialMode
from .definition import DiscoveredModel, ModelOAuth, ModelProviderDefinition, ProviderOperationError
from .types import ModelConnection, ProviderConfiguration

if TYPE_CHECKING:
    from pydantic_ai.models import Model
    from pydantic_ai.providers import Provider

    from .oauth.chatgpt import OpenAIChatGPTCredentialSource


class Config(ProviderConfiguration):
    # ChatGPT plan tokens must never be sent to a gateway or Codex backend.
    base_url: str | None = Field(
        default=None, exclude=True, json_schema_extra={"enum": [None, "https://api.openai.com/v1"]}
    )

    client_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        title="OAuth client ID",
        description="Leave blank to use deployment defaults or OSS registration.",
    )
    redirect_uri: str | None = Field(
        default=None,
        max_length=2048,
        title="Callback URL",
        description="Exact registered callback URL. Leave blank to use deployment defaults.",
    )
    token_endpoint_auth_method: Literal["none", "client_secret_basic"] = Field(
        default="none",
        title="Token endpoint authentication",
        description="Use the method provisioned for your OAuth client.",
    )

    @model_validator(mode="after")
    def registration(self) -> Self:
        if self.token_endpoint_auth_method != "none" and self.client_id is None:
            raise ValueError("A confidential OAuth client requires its client ID")
        if self.client_id is None and self.redirect_uri is None:
            return self

        from pydantic_ai.exceptions import UserError

        from .oauth.chatgpt import DYNAMIC_CLIENT_ID, validate_chatgpt_redirect_uri

        if self.client_id is not None and (not self.client_id.strip() or self.client_id == DYNAMIC_CLIENT_ID):
            raise ValueError("Use an explicitly provisioned OAuth client ID")
        if self.redirect_uri is not None:
            try:
                validate_chatgpt_redirect_uri(self.redirect_uri, preconfigured_client=True)
            except UserError as error:
                raise ValueError(str(error)) from None
        return self

    @model_validator(mode="after")
    def fixed_endpoint(self) -> Self:
        if self.base_url not in (None, "https://api.openai.com/v1"):
            raise ValueError("ChatGPT plan usage requires the public OpenAI endpoint")
        return self


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    client_secret: SecretStr = Field(
        min_length=1,
        max_length=4096,
        title="OAuth client secret",
        description="Stored encrypted; used only for server-side OAuth token requests.",
    )


def _provider(
    source: OpenAIChatGPTCredentialSource, client: httpx2.AsyncClient, headers: dict[str, str]
) -> Provider[Any]:
    from .chatgpt import OpenAIChatGPTProvider

    return OpenAIChatGPTProvider(credential_source=source, http_client=client, extra_headers=headers)


def _model(name: str, provider: Provider[Any]) -> Model[Any]:
    from ...models.chatgpt import OpenAIChatGPTResponsesModel

    return OpenAIChatGPTResponsesModel(name, provider=provider)


async def _discover(
    connection: ModelConnection[Config, Credential],
    source: OpenAIChatGPTCredentialSource | None,
    client: httpx2.AsyncClient,
) -> tuple[DiscoveredModel, ...]:
    from openai import OpenAIError
    from pydantic_ai.exceptions import UserError

    from .chatgpt import discover_chatgpt_models
    from .oauth.models import ModelAuthenticationError

    assert source is not None
    try:
        models = await discover_chatgpt_models(
            credential_source=source, http_client=client, extra_headers=connection.extra_headers
        )
    except (OpenAIError, UserError, ModelAuthenticationError):
        raise ProviderOperationError("ChatGPT model discovery failed; check authorization and retry") from None
    return tuple(DiscoveredModel(model_name=model.slug, display_name=model.display_name) for model in models)


DEFINITION = ModelProviderDefinition(
    type="openai_chatgpt",
    display_name="ChatGPT",
    configuration_model=Config,
    credential_model=Credential,
    authentication=Authentication(
        mode=CredentialMode.forbidden,
        cases=(
            AuthenticationCase(
                field="token_endpoint_auth_method", equals="client_secret_basic", mode=CredentialMode.required
            ),
        ),
    ),
    supported_model_apis=("openai.responses",),
    oauth=ModelOAuth(scheme="openai-chatgpt", build_provider=_provider),
    build_model=_model,
    model_discovery=_discover,
    endpoint="https://api.openai.com/v1",
    setup_url="https://chatgpt.com/settings/usage",
    setup_label="ChatGPT plan usage",
)
