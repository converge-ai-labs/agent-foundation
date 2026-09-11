"""Attempt-fenced root and inline-child search bindings."""

from collections.abc import Callable

from a13n_harness.capabilities.web import (
    WebCapability,
    WebConfiguration,
    WebRunCapability,
    WebScrapeConfiguration,
    WebSearchBackendBinding,
    WebSearchConfiguration,
)
from a13n_harness.errors import RunError
from a13n_harness.tools.invocation import current_invocation_scope
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.agents.models import AgentRecord
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptContext, read_attempt_authority
from a13n_service.interactions.domain import Run
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, utc_now

from .adapters import SearchTransport
from .domain import SearchSelection
from .execution import AuthorizedSearch, SearchSnapshot
from .resources import SearchProviderError, require_provider
from .web import WebTransport, WebTransportPolicy


def graph_uses_search(config: EffectiveAgentConfig) -> bool:
    return config.search is not None or any(
        graph_uses_search(child.effective_config) for child in config.child_configs.values()
    )


def search_capability(selection: SearchSelection) -> WebCapability:
    return WebCapability(
        WebConfiguration(
            search=WebSearchConfiguration(mode="host", backend=selection.provider_id),
            scrape=WebScrapeConfiguration(mode="off"),
            max_search_results=selection.max_results,
            deadline_seconds=30,
        )
    )


class SearchRuntime:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        protector: SecretProtector,
        *,
        transport: SearchTransport | None = None,
        web_transport: WebTransport | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._protector = protector
        self._transport = transport or SearchTransport()
        self._web_transport = web_transport or WebTransport()

    async def validate(
        self,
        *,
        run: Run,
        workspace_id: str,
        config: EffectiveAgentConfig,
        current_context: Callable[[], AttemptContext],
    ) -> None:
        if config.search is not None:
            _require_environment(run)
        pending = [(run.agent_id, config)]
        while pending:
            agent_id, node = pending.pop()
            if node.search is not None:
                await self._snapshot(run, workspace_id, agent_id, node.search, current_context)
            pending.extend((child.agent_id, child.effective_config) for child in node.child_configs.values())

    def binding(
        self,
        *,
        run: Run,
        workspace_id: str,
        agent_id: str,
        selection: SearchSelection,
        current_context: Callable[[], AttemptContext],
    ) -> WebRunCapability:
        async def acquire() -> SearchSnapshot:
            _require_environment(run)
            try:
                invocation = current_invocation_scope().invocation
            except RuntimeError as error:
                raise RunError(
                    "Search requires an authorized tool invocation.", code="search_provider_unavailable"
                ) from error
            if invocation.tool_id != "web.search":
                raise RunError("Search requires an authorized tool invocation.", code="search_provider_unavailable")
            snapshot = await self._snapshot(run, workspace_id, agent_id, selection, current_context)
            span = trace.get_current_span()
            attributes = getattr(span, "attributes", None) or {}
            if (
                attributes.get("gen_ai.operation.name") == "execute_tool"
                and attributes.get("gen_ai.tool.call.id") == invocation.tool_call_id
            ):
                span.set_attribute("a13n.search.provider.id", snapshot.provider_id)
                span.set_attribute("a13n.search.provider.type", snapshot.provider_type)
            return snapshot

        async def reauthorize() -> None:
            await acquire()

        async def authorize_web() -> None:
            _require_environment(run)
            try:
                invocation = current_invocation_scope().invocation
            except RuntimeError as error:
                raise RunError(
                    "Web requires an authorized tool invocation.", code="web_operation_unavailable"
                ) from error
            if invocation.tool_id not in {"web.fetch", "web.download"}:
                raise RunError("Web requires an authorized tool invocation.", code="web_operation_unavailable")
            await self._snapshot(run, workspace_id, agent_id, selection, current_context)

        return WebRunCapability(
            client=self._web_transport,
            policy=WebTransportPolicy(authorize_web),
            search_backends=(
                WebSearchBackendBinding(
                    selection.provider_id,
                    AuthorizedSearch(
                        selection=selection,
                        acquire=acquire,
                        reauthorize=reauthorize,
                        protector=self._protector,
                        transport=self._transport,
                    ),
                ),
            ),
        )

    async def _snapshot(
        self,
        expected: Run,
        workspace_id: str,
        agent_id: str,
        selection: SearchSelection,
        current_context: Callable[[], AttemptContext],
    ) -> SearchSnapshot:
        try:
            async with short_session(self._sessions) as session:
                run, _, _ = await read_attempt_authority(session, current_context(), self._clock())
                if (
                    run.id != expected.id
                    or run.authority_principal_id != expected.authority_principal.principal_id
                    or run.authority_principal_type != expected.authority_principal.principal_type.value
                ):
                    raise AttemptAuthorityError("Search execution principal changed")
                actor = AuthenticatedActor(
                    principal=expected.authority_principal,
                    auth_method="internal",
                    credential_id="attempt-search",
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
                        or agent.organization_id != run.organization_id
                        or agent.workspace_id != workspace_id
                        or not agent.enabled
                        or agent.archived_at is not None
                    ):
                        raise AttemptAuthorityError("Search Agent is unavailable")
                provider = await require_provider(
                    session,
                    organization_id=run.organization_id,
                    workspace_id=workspace_id,
                    provider_id=selection.provider_id,
                    eligible=True,
                )
                return SearchSnapshot(provider.id, provider.type, provider.credential_snapshot())
        except (AttemptAuthorityError, AuthorizationError, SearchProviderError) as error:
            raise RunError(
                "The selected search resource or execution authority is unavailable.",
                code="search_provider_unavailable",
            ) from error


def _require_environment(run: Run) -> None:
    if run.environment_id is None:
        raise RunError("Web tools require a Run Environment.", code="environment_required")
