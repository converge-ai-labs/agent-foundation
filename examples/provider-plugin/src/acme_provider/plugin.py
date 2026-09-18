"""A Web provider needs typed inputs, an operation, and an inert definition."""

from a13n_harness.providers.model import ModelConnection, ModelProviderDefinition, ProviderConfiguration
from a13n_harness.providers.plugins import ProviderManifest
from a13n_harness.providers.web import (
    SearchOptions,
    WebProviderDefinition,
    WebProviderTransport,
    WebSearchRequest,
    WebSearchResponse,
)
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class AcmeWebConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    index: str = Field(default="docs", pattern=r"^[a-z][a-z0-9_-]{0,31}$")


class AcmeCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token: SecretStr = Field(min_length=1, max_length=4096)


async def search(
    configuration: AcmeWebConfiguration,
    credential: AcmeCredential | None,
    request: WebSearchRequest,
    options: SearchOptions,
    transport: WebProviderTransport,
) -> WebSearchResponse:
    """Call the fictional Acme vendor; tests supply a mocked HTTP transport."""
    assert credential is not None
    payload = await transport.exchange_json(
        lambda client: client.build_request(
            "POST",
            "https://search.acme.example/v1/search",
            headers={"Authorization": f"Bearer {credential.token.get_secret_value()}"},
            json={
                "index": configuration.index,
                "query": request.query,
                "limit": min(request.limit, options.max_results),
            },
        ),
        operation="search",
        max_response_bytes=1024 * 1024,
    )
    return WebSearchResponse.model_validate(payload)


acme_web = WebProviderDefinition(
    type="acme_web",
    display_name="Acme Web",
    configuration_model=AcmeWebConfiguration,
    credential_model=AcmeCredential,
    setup_url="https://docs.example.com/provider-setup",
    search=search,
)


class AcmeModelConfiguration(ProviderConfiguration):
    index: str = Field(default="docs", min_length=1, max_length=64)


class AcmeModelCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    authorization: AcmeCredential
    revision: int = Field(ge=1)


def build_model_provider(
    connection: ModelConnection[AcmeModelConfiguration, AcmeModelCredential], http_client, model_api
):
    from openai import AsyncOpenAI
    from pydantic_ai.providers.openai import OpenAIProvider

    assert connection.credential is not None
    return OpenAIProvider(
        openai_client=AsyncOpenAI(
            api_key=connection.credential.authorization.token.get_secret_value(),
            base_url=connection.endpoint,
            http_client=http_client,
            max_retries=0,
            default_headers={
                **connection.extra_headers,
                "x-acme-index": connection.configuration.index,
                "x-acme-revision": str(connection.credential.revision),
            },
        )
    )


acme_model = ModelProviderDefinition(
    type="acme_model",
    setup_url="https://docs.example.com/model-setup",
    setup_label="Configure Acme access",
    display_name="Acme Model",
    configuration_model=AcmeModelConfiguration,
    credential_model=AcmeModelCredential,
    supported_model_apis=("openai.chat_completions",),
    build_provider=build_model_provider,
    endpoint="https://models.acme.example/v1",
)

manifest = ProviderManifest(api_version=1, web=(acme_web,), model=(acme_model,))
