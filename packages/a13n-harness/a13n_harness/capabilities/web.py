"""Policy-bound Web tools over explicit asynchronous provider ports."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.native_tools import WebSearchTool
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
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
WEB_RUN_CAPABILITY_ID = "a13n.web.run"
_WEB_TOOLSET_ID = "a13n-web-tools"


@dataclass(kw_only=True)
class WebRunCapability(AbstractCapability[AgentContext]):
    """Fresh run attachment carrying Web transport, policy, and optional providers."""

    id: str | None = WEB_RUN_CAPABILITY_ID
    client: WebClient = field()
    policy: WebPolicy = field()
    search_provider: WebSearchProvider | None = None
    scrape_provider: WebScrapeProvider | None = None
    search_backends: tuple[WebSearchBackendBinding, ...] = ()
    scrape_backends: tuple[WebScrapeBackendBinding, ...] = ()

    def __post_init__(self) -> None:
        if self.id != WEB_RUN_CAPABILITY_ID:
            raise ValueError(f"WebRunCapability.id must be {WEB_RUN_CAPABILITY_ID!r}")
        if not isinstance(self.client, WebClient):
            raise TypeError("client must implement WebClient")
        if not isinstance(self.policy, WebPolicy):
            raise TypeError("policy must implement WebPolicy")
        if self.search_provider is not None and not isinstance(self.search_provider, WebSearchProvider):
            raise TypeError("search_provider must implement WebSearchProvider")
        if self.scrape_provider is not None and not isinstance(self.scrape_provider, WebScrapeProvider):
            raise TypeError("scrape_provider must implement WebScrapeProvider")
        if self.search_provider is not None and self.search_backends:
            raise ValueError("search_provider and search_backends are mutually exclusive")
        if self.scrape_provider is not None and self.scrape_backends:
            raise ValueError("scrape_provider and scrape_backends are mutually exclusive")
        if not all(isinstance(item, WebSearchBackendBinding) for item in self.search_backends):
            raise TypeError("search_backends must contain WebSearchBackendBinding values")
        if not all(isinstance(item, WebScrapeBackendBinding) for item in self.scrape_backends):
            raise TypeError("scrape_backends must contain WebScrapeBackendBinding values")
        self.search_backends = tuple(self.search_backends) or (
            (WebSearchBackendBinding("default", self.search_provider),) if self.search_provider is not None else ()
        )
        self.scrape_backends = tuple(self.scrape_backends) or (
            (WebScrapeBackendBinding("default", self.scrape_provider),) if self.scrape_provider is not None else ()
        )
        if len({item.backend_id for item in self.search_backends}) != len(self.search_backends):
            raise ValueError("search backend IDs must be unique")
        if len({item.backend_id for item in self.scrape_backends}) != len(self.scrape_backends):
            raise ValueError("scrape backend IDs must be unique")


@dataclass(init=False)
class WebCapability(AbstractCapability[AgentContext]):
    """Expose provider-neutral search, scrape, fetch, and download tools."""

    id = WEB_CAPABILITY_ID

    def __init__(self, configuration: WebConfiguration | None = None) -> None:
        resolved = WebConfiguration.from_environment() if configuration is None else configuration
        self.configuration = resolved.model_copy(deep=True)

    def get_native_tools(self) -> list[WebSearchTool]:
        search = self.configuration.search
        if search.mode not in {"native", "auto"} or search.restricted:
            return []
        return [WebSearchTool(search_context_size=search.search_context_size)]

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(WEB_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, _WebActiveCapability):
                raise DefinitionError("Web has an incompatible run replacement.", code="capability_type_mismatch")
            return existing
        if WEB_CAPABILITY_ID not in ctx.deps._capability_provenance.definition_ids:
            raise DefinitionError(
                "WebCapability must originate from the Agent definition.", code="capability_scope_invalid"
            )
        replacement = _WebActiveCapability(self.configuration, context=ctx.deps)
        ctx.deps._record_run_capability(WEB_CAPABILITY_ID, replacement)
        return replacement


@dataclass(init=False)
class _WebActiveCapability(WebCapability):
    def __init__(self, configuration: WebConfiguration, *, context: AgentContext) -> None:
        super().__init__(configuration)
        self._context = context
        self._attachment: WebRunCapability | None = None
        self._collaborators: tuple[object, ...] | None = None

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps is not self._context:
            raise DefinitionError("Web run replacement cannot cross logical runs.", code="capability_scope_invalid")
        return self

    def get_toolset(self) -> AbstractToolset[AgentContext]:
        return DynamicToolset(self._toolset_for_run, per_run_step=False, id=_WEB_TOOLSET_ID)

    async def _toolset_for_run(self, ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext]:
        attachment = self._bind(ctx)
        return WebToolset(
            client=attachment.client,
            policy=attachment.policy,
            configuration=self.configuration,
            search_backends=attachment.search_backends,
            scrape_backends=attachment.scrape_backends,
            files=ctx.deps.environment.files,
            file_scopes=ctx.deps.environment,
        ).get_toolset()

    def _bind(self, ctx: RunContext[AgentContext]) -> WebRunCapability:
        if ctx.deps is not self._context:
            raise DefinitionError("Web run replacement cannot cross logical runs.", code="capability_scope_invalid")
        owner = ctx.capabilities.get(WEB_CAPABILITY_ID)
        if type(owner) is not _WebActiveCapability or owner is not self:
            raise DefinitionError(
                "The finalized Web owner has an incompatible identity.", code="capability_scope_invalid"
            )
        attachment = ctx.capabilities.get(WEB_RUN_CAPABILITY_ID)
        if type(attachment) is not WebRunCapability:
            raise DefinitionError("WebCapability requires one fresh WebRunCapability.", code="web_binding_missing")
        if WEB_RUN_CAPABILITY_ID not in ctx.deps._capability_provenance.run_ids:
            raise DefinitionError("WebRunCapability must originate from RunBindings.", code="capability_scope_invalid")
        collaborators = (
            attachment.client,
            attachment.policy,
            attachment.search_provider,
            attachment.scrape_provider,
            attachment.search_backends,
            attachment.scrape_backends,
        )
        if self._attachment is None:
            self._attachment = attachment
            self._collaborators = collaborators
        elif (
            self._attachment is not attachment
            or self._collaborators is None
            or any(
                current is not captured for current, captured in zip(collaborators, self._collaborators, strict=True)
            )
        ):
            raise DefinitionError("Web binding identity changed within one run.", code="capability_scope_invalid")
        return attachment


__all__ = [
    "WEB_SCRAPE_BACKEND_ENV",
    "WEB_SCRAPE_BACKEND_PRIORITY_ENV",
    "WEB_SCRAPE_MODE_ENV",
    "WEB_SEARCH_BACKEND_ENV",
    "WEB_SEARCH_BACKEND_PRIORITY_ENV",
    "WEB_SEARCH_CONTEXT_SIZE_ENV",
    "WEB_SEARCH_MODE_ENV",
    "WebCapability",
    "WebClient",
    "WebConfiguration",
    "WebDomainPolicy",
    "WebPolicy",
    "WebProviderError",
    "WebRequest",
    "WebResponse",
    "WebRunCapability",
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
