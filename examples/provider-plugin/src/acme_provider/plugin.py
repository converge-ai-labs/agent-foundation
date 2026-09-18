"""A Web provider needs typed inputs, an operation, and an inert definition."""

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
    credential: AcmeCredential,
    request: WebSearchRequest,
    options: SearchOptions,
    transport: WebProviderTransport,
) -> WebSearchResponse:
    """Call the fictional Acme vendor; tests supply a mocked HTTP transport."""
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

manifest = ProviderManifest(api_version=1, web=(acme_web,))
