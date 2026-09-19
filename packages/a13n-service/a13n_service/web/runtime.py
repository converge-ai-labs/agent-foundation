"""Attempt-fenced Web search, scrape, fetch, and download bindings."""

from collections.abc import Awaitable, Callable

from a13n_harness.capabilities.web import (
    WebBinding,
    WebCapability,
    WebConfiguration,
    WebDownloadConfiguration,
    WebFetchConfiguration,
    WebScrapeBackendBinding,
    WebScrapeConfiguration,
    WebSearchBackendBinding,
    WebSearchConfiguration,
)
from a13n_harness.errors import RunError
from a13n_harness.observation import set_tool_span_attributes
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.web.definition import WebProviderDefinition
from a13n_harness.providers.web.transport import WebProviderTransport
from a13n_harness.tools.invocation import current_invocation_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.agents.models import AgentRecord
from a13n_service.agents.toolsets import web_selection
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptContext, read_attempt_authority
from a13n_service.interactions.domain import Run
from a13n_service.interactions.models import RunRecord
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, utc_now

from .domain import ScrapeSelection, SearchSelection, WebSelection, provider_selections
from .execution import AuthorizedScrape, AuthorizedSearch, WebProviderSnapshot
from .resources import WebProviderError, require_operation, require_provider
from .web import WebTransport, WebTransportPolicy


def graph_uses_web(config: EffectiveAgentConfig) -> bool:
    return web_selection(config.toolsets) is not None or any(
        graph_uses_web(child.effective_config) for child in config.child_configs.values()
    )


def web_capability(selection: WebSelection) -> WebCapability:
    search = selection.search
    scrape = selection.scrape
    fetch = selection.fetch
    download = selection.download
    return WebCapability(
        WebConfiguration(
            search=WebSearchConfiguration(
                mode="off" if search is None else "host",
                backend=None if search is None else search.provider_id,
                allow_domains=() if search is None else search.allow_domains,
                deny_domains=() if search is None else search.deny_domains,
            ),
            scrape=WebScrapeConfiguration(
                mode="off" if scrape is None else "host",
                backend=None if scrape is None else scrape.provider_id,
                allow_domains=() if scrape is None else scrape.allow_domains,
                deny_domains=() if scrape is None else scrape.deny_domains,
            ),
            fetch=WebFetchConfiguration(
                enabled=fetch is not None,
                allow_domains=() if fetch is None else fetch.allow_domains,
                deny_domains=() if fetch is None else fetch.deny_domains,
            ),
            download=WebDownloadConfiguration(
                enabled=download is not None,
                allow_domains=() if download is None else download.allow_domains,
                deny_domains=() if download is None else download.deny_domains,
            ),
            max_search_results=10 if search is None else search.max_results,
            max_scrape_bytes=512 * 1024 if scrape is None else scrape.max_content_bytes,
            max_text_bytes=256 * 1024 if fetch is None else fetch.max_content_bytes,
            deadline_seconds=30,
        )
    )


class WebRuntime:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        protector: SecretProtector,
        catalog: ProviderCatalog[WebProviderDefinition],
        *,
        provider_transport: WebProviderTransport | None = None,
        web_transport: WebTransport | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._protector = protector
        self._catalog = catalog
        self._provider_transport = provider_transport
        self._web_transport = web_transport or WebTransport()

    async def validate(
        self,
        *,
        run: Run,
        workspace_id: str,
        config: EffectiveAgentConfig,
        current_context: Callable[[], AttemptContext],
    ) -> None:
        pending = [(run.agent_id, config)]
        while pending:
            agent_id, node = pending.pop()
            selection = web_selection(node.toolsets)
            if selection is not None:
                for operation, selected in provider_selections(selection):
                    await self._snapshot(
                        run,
                        workspace_id,
                        agent_id,
                        selected,
                        operation=operation,
                        current_context=current_context,
                    )
            pending.extend((child.agent_id, child.effective_config) for child in node.child_configs.values())

    def binding(
        self,
        *,
        run: Run,
        workspace_id: str,
        agent_id: str,
        selection: WebSelection,
        current_context: Callable[[], AttemptContext],
    ) -> WebBinding:
        async def authorize_builtin(purpose: str) -> None:
            tool_id = f"web.{purpose}"
            await self._authorize_agent(run, workspace_id, agent_id, tool_id, current_context)

        search_backends: tuple[WebSearchBackendBinding, ...] = ()
        if selection.search is not None:
            selected_search = selection.search
            acquire_search = self._acquire(run, workspace_id, agent_id, selected_search, "search", current_context)

            async def reauthorize_search() -> None:
                await acquire_search()

            search_backends = (
                WebSearchBackendBinding(
                    selected_search.provider_id,
                    AuthorizedSearch(
                        selection=selected_search,
                        acquire=acquire_search,
                        reauthorize=reauthorize_search,
                        protector=self._protector,
                        catalog=self._catalog,
                        transport=self._provider_transport,
                    ),
                ),
            )

        scrape_backends: tuple[WebScrapeBackendBinding, ...] = ()
        if selection.scrape is not None:
            selected_scrape = selection.scrape
            acquire_scrape = self._acquire(run, workspace_id, agent_id, selected_scrape, "scrape", current_context)

            async def reauthorize_scrape() -> None:
                await acquire_scrape()

            scrape_backends = (
                WebScrapeBackendBinding(
                    selected_scrape.provider_id,
                    AuthorizedScrape(
                        selection=selected_scrape,
                        acquire=acquire_scrape,
                        reauthorize=reauthorize_scrape,
                        protector=self._protector,
                        catalog=self._catalog,
                        transport=self._provider_transport,
                    ),
                ),
            )

        return WebBinding(
            client=self._web_transport,
            policy=WebTransportPolicy(authorize_builtin),
            search_backends=search_backends,
            scrape_backends=scrape_backends,
        )

    def _acquire(
        self,
        run: Run,
        workspace_id: str,
        agent_id: str,
        selection: SearchSelection | ScrapeSelection,
        operation: str,
        current_context: Callable[[], AttemptContext],
    ) -> Callable[[], Awaitable[WebProviderSnapshot]]:
        async def acquire() -> WebProviderSnapshot:
            self._require_invocation(f"web.{operation}")
            snapshot = await self._snapshot(
                run,
                workspace_id,
                agent_id,
                selection,
                operation=operation,
                current_context=current_context,
            )
            set_tool_span_attributes(
                {
                    "a13n.web.provider.id": snapshot.provider_id,
                    "a13n.web.provider.type": snapshot.provider_type,
                    "a13n.web.operation": operation,
                }
            )
            return snapshot

        return acquire

    async def _authorize_agent(
        self,
        expected: Run,
        workspace_id: str,
        agent_id: str,
        tool_id: str,
        current_context: Callable[[], AttemptContext],
    ) -> None:
        self._require_invocation(tool_id)
        try:
            async with short_session(self._sessions) as session:
                run, _, _ = await read_attempt_authority(session, current_context(), self._clock())
                self._verify_run(expected, run)
                await self._authorize_selected_agents(session, expected, workspace_id, agent_id, current_context)
        except (AttemptAuthorityError, AuthorizationError) as error:
            raise RunError("Web execution authority is unavailable.", code="web_operation_unavailable") from error

    @staticmethod
    def _require_invocation(tool_id: str) -> None:
        try:
            invocation = current_invocation_scope().invocation
        except RuntimeError as error:
            raise RunError("Web requires an authorized tool invocation.", code="web_operation_unavailable") from error
        if invocation.tool_id != tool_id:
            raise RunError("Web requires an authorized tool invocation.", code="web_operation_unavailable")

    async def _snapshot(
        self,
        expected: Run,
        workspace_id: str,
        agent_id: str,
        selection: SearchSelection | ScrapeSelection,
        *,
        operation: str,
        current_context: Callable[[], AttemptContext],
    ) -> WebProviderSnapshot:
        try:
            async with short_session(self._sessions) as session:
                run, _, _ = await read_attempt_authority(session, current_context(), self._clock())
                self._verify_run(expected, run)
                await self._authorize_selected_agents(session, expected, workspace_id, agent_id, current_context)
                provider = await require_provider(
                    session,
                    organization_id=run.organization_id,
                    workspace_id=workspace_id,
                    provider_id=selection.provider_id,
                    eligible=True,
                    catalog=self._catalog,
                )
                require_operation(
                    provider,
                    operation,
                    self._catalog,
                    selection=selection if isinstance(selection, ScrapeSelection) else None,
                )
                return WebProviderSnapshot(
                    provider.id,
                    provider.type,
                    provider.configuration,
                    provider.credential_snapshot(),
                )
        except (AttemptAuthorityError, AuthorizationError, WebProviderError) as error:
            raise RunError(
                "The selected Web Provider or execution authority is unavailable.",
                code="web_provider_unavailable",
            ) from error

    @staticmethod
    def _verify_run(expected: Run, current: RunRecord) -> None:
        if (
            current.id != expected.id
            or current.authority_principal_id != expected.authority_principal.principal_id
            or current.authority_principal_type != expected.authority_principal.principal_type.value
        ):
            raise AttemptAuthorityError("Web execution principal changed")

    @staticmethod
    async def _authorize_selected_agents(
        session: AsyncSession,
        expected: Run,
        workspace_id: str,
        agent_id: str,
        current_context: Callable[[], AttemptContext],
    ) -> None:
        actor = AuthenticatedActor(
            principal=expected.authority_principal,
            auth_method="internal",
            credential_id="attempt-web",
            boundary_workspace_id=workspace_id,
        )
        for selected_agent in {expected.agent_id, agent_id}:
            await authorize_agent(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=selected_agent,
                action=WorkspaceAction.agent_invoke,
                snapshot=current_context().authorization.snapshot,
            )
            agent = await session.get(AgentRecord, selected_agent)
            if (
                agent is None
                or agent.organization_id != expected.organization_id
                or agent.workspace_id != workspace_id
                or not agent.enabled
                or agent.archived_at is not None
            ):
                raise AttemptAuthorityError("Web Agent is unavailable")
