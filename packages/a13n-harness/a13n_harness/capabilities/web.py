"""Policy-bound Web tools over explicit asynchronous provider ports."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from a13n_harness.configuration import HostNotAllowedError, RunConfiguration
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.providers.web.contracts import WebPurpose
from a13n_harness.toolsets.web import (
    WEB_SCRAPE_BACKEND_ENV,
    WEB_SCRAPE_BACKEND_PRIORITY_ENV,
    WEB_SCRAPE_MODE_ENV,
    WEB_SEARCH_BACKEND_ENV,
    WEB_SEARCH_BACKEND_PRIORITY_ENV,
    WEB_SEARCH_CONTEXT_SIZE_ENV,
    WEB_SEARCH_MODE_ENV,
    WebClient,
    WebConfiguration,
    WebDomainPolicy,
    WebDownloadConfiguration,
    WebFetchConfiguration,
    WebPolicy,
    WebProviderError,
    WebRequest,
    WebResponse,
    WebScrapeBackendBinding,
    WebScrapeConfiguration,
    WebScrapeProvider,
    WebScrapeRequest,
    WebScrapeResult,
    WebSearchBackendBinding,
    WebSearchConfiguration,
    WebSearchProvider,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
    WebToolset,
)

WEB_CAPABILITY_ID = "a13n.web"
_WEB_TOOLSET_ID = "a13n-web-tools"


@dataclass(frozen=True, slots=True, kw_only=True)
class WebBinding:
    """Host-owned Web transport, policy, and providers for one logical run."""

    client: WebClient = field()
    policy: WebPolicy = field()
    search_backends: tuple[WebSearchBackendBinding, ...] = ()
    scrape_backends: tuple[WebScrapeBackendBinding, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.client, WebClient):
            raise TypeError("client must implement WebClient")
        if not isinstance(self.policy, WebPolicy):
            raise TypeError("policy must implement WebPolicy")
        if not all(isinstance(item, WebSearchBackendBinding) for item in self.search_backends):
            raise TypeError("search_backends must contain WebSearchBackendBinding values")
        if not all(isinstance(item, WebScrapeBackendBinding) for item in self.scrape_backends):
            raise TypeError("scrape_backends must contain WebScrapeBackendBinding values")
        if len({item.backend_id for item in self.search_backends}) != len(self.search_backends):
            raise ValueError("search backend IDs must be unique")
        if len({item.backend_id for item in self.scrape_backends}) != len(self.scrape_backends):
            raise ValueError("scrape backend IDs must be unique")


@dataclass(frozen=True, slots=True)
class _RunWebPolicy(WebPolicy):
    base: WebPolicy
    configuration: RunConfiguration

    async def authorize(self, url: str, *, purpose: WebPurpose) -> None:
        try:
            self.configuration.authorize_url(url)
        except HostNotAllowedError as error:
            raise WebProviderError("web_destination_denied") from error
        await self.base.authorize(url, purpose=purpose)


@dataclass(init=False)
class WebCapability(AbstractCapability[AgentContext]):
    """Expose provider-neutral search, scrape, fetch, and download tools."""

    id = WEB_CAPABILITY_ID

    def __init__(self, configuration: WebConfiguration | None = None) -> None:
        resolved = WebConfiguration.from_environment() if configuration is None else configuration
        self.configuration = resolved.model_copy(deep=True)
        self._run_configuration = RunConfiguration()

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        owner = WebCapability(self.configuration)
        owner._run_configuration = ctx.deps.configuration
        if ctx.deps.configuration.allowed_hosts is not None:
            # Provider-native navigation cannot apply per-hop exact hostname authorization.
            if owner.configuration.search.mode in {"auto", "native"}:
                search = owner.configuration.search.model_copy(update={"mode": "host"})
                owner.configuration = owner.configuration.model_copy(update={"search": search})
        return owner

    def get_native_tools(self) -> list[WebSearchTool]:
        search = self.configuration.search
        if (
            search.mode not in {"native", "auto"}
            or search.restricted
            or self._run_configuration.allowed_hosts is not None
        ):
            return []
        return [WebSearchTool(search_context_size=search.search_context_size)]

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return DynamicToolset(self._toolset_for_run, per_run_step=False, id=_WEB_TOOLSET_ID)

    async def _toolset_for_run(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
        attachment = self._bind(ctx)
        return WebToolset(
            client=attachment.client,
            policy=_RunWebPolicy(attachment.policy, ctx.deps.configuration),
            configuration=self.configuration,
            search_backends=attachment.search_backends,
            scrape_backends=attachment.scrape_backends,
            files=ctx.deps.environment.files,
            file_scopes=ctx.deps.environment,
        ).get_toolset()

    def _bind(self, ctx: RunContext[AgentContext]) -> WebBinding:
        if WEB_CAPABILITY_ID not in ctx.deps._capability_provenance.definition_ids:
            raise DefinitionError(
                "WebCapability must originate from the Agent definition.", code="capability_scope_invalid"
            )
        owner = ctx.capabilities.get(WEB_CAPABILITY_ID)
        if type(owner) is not WebCapability or owner is not self:
            raise DefinitionError(
                "The finalized Web owner has an incompatible identity.", code="capability_scope_invalid"
            )
        binding = ctx.deps.web
        if binding is None:
            raise DefinitionError("WebCapability requires RunBindings.web.", code="web_binding_missing")
        return binding


__all__ = [
    "WEB_SCRAPE_BACKEND_ENV",
    "WEB_SCRAPE_BACKEND_PRIORITY_ENV",
    "WEB_SCRAPE_MODE_ENV",
    "WEB_SEARCH_BACKEND_ENV",
    "WEB_SEARCH_BACKEND_PRIORITY_ENV",
    "WEB_SEARCH_CONTEXT_SIZE_ENV",
    "WEB_SEARCH_MODE_ENV",
    "WebBinding",
    "WebCapability",
    "WebClient",
    "WebConfiguration",
    "WebDomainPolicy",
    "WebDownloadConfiguration",
    "WebFetchConfiguration",
    "WebPolicy",
    "WebProviderError",
    "WebRequest",
    "WebResponse",
    "WebScrapeBackendBinding",
    "WebScrapeConfiguration",
    "WebScrapeProvider",
    "WebScrapeRequest",
    "WebScrapeResult",
    "WebSearchBackendBinding",
    "WebSearchConfiguration",
    "WebSearchProvider",
    "WebSearchRequest",
    "WebSearchResponse",
    "WebSearchResult",
]
