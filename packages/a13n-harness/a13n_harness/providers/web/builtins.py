"""Built-ins use the same definitions as installed Web extensions."""

from ..authentication import Authentication, CredentialMode
from .configuration import ApiKeyCredential, EmptyConfiguration
from .definition import WebProviderDefinition
from .vendors import brave, duckduckgo, exa, firecrawl, jina, parallel, perplexity, serpapi, tavily


def built_in_web_providers() -> tuple[WebProviderDefinition, ...]:
    definitions: list[WebProviderDefinition] = [
        WebProviderDefinition(
            type="duckduckgo",
            display_name="DuckDuckGo",
            setup_url="https://duckduckgo.com/",
            configuration_model=EmptyConfiguration,
            credential_model=EmptyConfiguration,
            authentication=Authentication(mode=CredentialMode.forbidden),
            search=duckduckgo.search,
        )
    ]
    for provider_type, display_name, setup_url, search, scrape in (
        ("brave", "Brave Search", "https://api-dashboard.search.brave.com/", brave.search, None),
        ("exa", "Exa", "https://dashboard.exa.ai/api-keys", exa.search, exa.scrape),
        ("parallel", "Parallel", "https://platform.parallel.ai/", parallel.search, parallel.scrape),
        ("tavily", "Tavily", "https://app.tavily.com/", tavily.search, tavily.scrape),
        ("firecrawl", "Firecrawl", "https://www.firecrawl.dev/app", firecrawl.search, firecrawl.scrape),
        ("jina", "Jina", "https://jina.ai/reader/", jina.search, jina.scrape),
        ("perplexity", "Perplexity", "https://www.perplexity.ai/settings/api", perplexity.search, None),
        ("serpapi", "SerpApi", "https://serpapi.com/manage-api-key", serpapi.search, None),
    ):
        definitions.append(
            WebProviderDefinition(
                type=provider_type,
                display_name=display_name,
                setup_url=setup_url,
                configuration_model=EmptyConfiguration,
                credential_model=ApiKeyCredential,
                search=search,
                scrape=scrape,
            )
        )
    return tuple(definitions)
