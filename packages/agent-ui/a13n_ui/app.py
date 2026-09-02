"""Process-local application boundary shared by Agent UI surfaces."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from a13n_environment_provider import EnvironmentProvider
from a13n_harness.capabilities import SubagentCancelResult, SubagentSteerResult
from a13n_harness.environment import EnvironmentRunExtensionFactory
from a13n_harness.model_auth import CodexCredentials, GrokCredentials
from a13n_harness.plugin_factories import HarnessPluginFactory
from anyio import CancelScope, Event, Lock, create_task_group, move_on_after, sleep
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.capabilities import AbstractCapability

from a13n_ui.composition import (
    AgentCompositionResolver,
    AgentReconstructor,
    CompositionAcceptanceService,
    RunCompositionService,
)
from a13n_ui.configuration import (
    ConfigurationMutationResult,
    ExternalSubagentImportCandidate,
    ExternalSubagentImportPreview,
    ExternalSubagentProduct,
    ExternalSubagentScope,
    LoadedAgentUiConfiguration,
    ResourceMutationRequest,
    apply_external_subagent_import,
    delete_configuration_source,
    empty_agent_ui_configuration,
    load_agent_ui_configuration,
    mutate_configuration_source,
    preview_external_subagent_import,
)
from a13n_ui.environment_runtime import (
    EnvironmentRunService,
    EnvironmentSnapshotReconstructor,
    ProviderRuntimeFactory,
)
from a13n_ui.errors import AgentUiError, AppStateError, ConfigurationError
from a13n_ui.extensions import (
    AgentUiExtensionCatalog,
    CatalogReference,
    EnvironmentProjectAdapter,
)
from a13n_ui.live import AgentUiLiveHub, LiveSubscription
from a13n_ui.model_accounts import (
    AccountProjection,
    AccountStoreError,
    CodexAccountStore,
    CodexLoginCallback,
    GrokAccountStore,
    GrokLoginCallback,
    Provider,
    resolve_codex_policy,
    resolve_grok_policy,
    resolve_grok_scope,
)
from a13n_ui.model_runtime import CodexSubscriptionSource, GrokSubscriptionSource, SubscriptionSource
from a13n_ui.settings import AgentUiSettings
from a13n_ui.storage import (
    LocalStore,
    Thread,
    ThreadConfigurationMutation,
    open_local_store,
)
from a13n_ui.subagent_operator import AgentUiSubagentOperator, ChildExecutionPage
from a13n_ui.thread_service import (
    ProjectProjection,
    RootCancelResult,
    RootRunOutcome,
    RootSteerResult,
    RootThreadDefaults,
    ThreadService,
)


@dataclass(frozen=True, slots=True)
class AgentUiIntegrations:
    """Explicit trusted Host registrations fixed for one App lifetime."""

    capabilities: Mapping[str, type[AbstractCapability[Any]]] = field(default_factory=dict)
    environment_providers: tuple[EnvironmentProvider, ...] = ()
    environment_adapters: tuple[EnvironmentProjectAdapter, ...] = ()
    harness_plugin_factories: tuple[HarnessPluginFactory, ...] = ()
    environment_run_extension_factories: tuple[EnvironmentRunExtensionFactory, ...] = ()
    provider_runtime_factories: Mapping[str, ProviderRuntimeFactory] = field(default_factory=dict)


class AppState(StrEnum):
    """Observable lifecycle state for one Agent UI App."""

    starting = "starting"
    ready = "ready"
    stopping = "stopping"
    closed = "closed"


class AppStatus(BaseModel):
    """Detached application health and accepted-source projection."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    state: AppState
    object_count: int = Field(ge=0)
    accepted_generation_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidate_error_code: str | None = None
    candidate_error_message: str | None = None


class AgentUiApp:
    """The only application boundary shared by Agent UI surfaces."""

    def __init__(
        self,
        settings: AgentUiSettings,
        store: LocalStore,
        *,
        configuration_path: Path | None,
        catalog: AgentUiExtensionCatalog,
        configurations: CompositionAcceptanceService,
        threads: ThreadService,
        subagent_operator: AgentUiSubagentOperator,
        live_hub: AgentUiLiveHub,
        codex_account: CodexAccountStore,
        grok_account: GrokAccountStore | None,
        grok_account_error: AccountStoreError | None,
        codex_login: CodexLoginCallback | None,
        grok_login: GrokLoginCallback | None,
        candidate_error: AgentUiError | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._configuration_path = configuration_path
        self._catalog = catalog
        self._configurations = configurations
        self._threads = threads
        self._subagent_operator = subagent_operator
        self._live_hub = live_hub
        self._codex_account = codex_account
        self._grok_account = grok_account
        self._grok_account_error = grok_account_error
        self._codex_login = codex_login
        self._grok_login = grok_login
        self._candidate_error = candidate_error
        self._configuration_seen = configuration_path is not None and configuration_path.exists()
        self._state = AppState.starting
        self._operation_lock = Lock()
        self._operation_scopes: set[CancelScope] = set()
        self._operations_idle = Event()
        self._operations_idle.set()

    @property
    def state(self) -> AppState:
        return self._state

    async def status(self) -> AppStatus:
        async with self._operation():
            current_digest = await self._store.configurations.current_digest()
            return AppStatus(
                state=self._state,
                object_count=await self._store.object_count(),
                accepted_generation_digest=current_digest,
                candidate_error_code=(None if self._candidate_error is None else self._candidate_error.code),
                candidate_error_message=(None if self._candidate_error is None else str(self._candidate_error)),
            )

    async def current_configuration(self) -> LoadedAgentUiConfiguration | None:
        async with self._operation():
            return await self._configurations.current()

    async def reload_configuration(self) -> LoadedAgentUiConfiguration | None:
        """Load and accept one stable complete source tree, retaining the previous generation on failure."""

        async with self._operation():
            await self._reload_configuration_from_path()
            return await self._configurations.current()

    async def _reload_configuration_from_path(self) -> None:
        path = self._require_configuration_path()
        if not path.exists():
            if self._configuration_seen:
                self._candidate_error = ConfigurationError(
                    "The Agent UI configuration root is unavailable.",
                    code="settings_unavailable",
                    details={"path": str(path)},
                )
            return
        self._configuration_seen = True
        try:
            candidate = await load_agent_ui_configuration(path)
            current = await self._store.configurations.current_digest()
            if candidate.source_digest != current:
                await self._configurations.accept(
                    candidate,
                    expected_current_digest=current,
                )
            self._candidate_error = None
        except AgentUiError as exc:
            self._candidate_error = exc

    async def _observe_configuration(self) -> None:
        while self._state is AppState.ready:
            await sleep(0.5)
            if self._state is not AppState.ready:
                return
            await self._reload_configuration_from_path()

    async def mutate_configuration(
        self,
        *,
        relative_path: str,
        request: ResourceMutationRequest,
    ) -> ConfigurationMutationResult:
        async with self._operation():
            path = self._require_configuration_path()
            result = await mutate_configuration_source(
                path,
                relative_path,
                request,
                validate_candidate=self._configurations.validate,
            )
            await self._accept_mutation(result)
            return result

    async def delete_configuration(
        self,
        *,
        relative_path: str,
        expected_source_digest: str,
    ) -> ConfigurationMutationResult:
        async with self._operation():
            path = self._require_configuration_path()
            result = await delete_configuration_source(
                path,
                relative_path,
                expected_source_digest=expected_source_digest,
                validate_candidate=self._configurations.validate,
            )
            await self._accept_mutation(result)
            return result

    async def preview_subagent_import(
        self,
        *,
        product: ExternalSubagentProduct | str,
        scope: ExternalSubagentScope | str,
        project_root: Path | None = None,
        user_home: Path | None = None,
    ) -> ExternalSubagentImportPreview:
        async with self._operation():
            return await preview_external_subagent_import(
                self._require_configuration_path(),
                product=product,
                scope=scope,
                project_root=project_root,
                user_home=user_home,
            )

    async def apply_subagent_import(
        self,
        candidate: ExternalSubagentImportCandidate,
    ) -> ConfigurationMutationResult:
        async with self._operation():
            result = await apply_external_subagent_import(
                self._require_configuration_path(),
                candidate,
                validate_candidate=self._configurations.validate,
            )
            await self._accept_mutation(result)
            return result

    async def list_catalog(self) -> tuple[CatalogReference, ...]:
        async with self._operation():
            return self._catalog.references

    async def refresh_catalog(self) -> tuple[CatalogReference, ...]:
        async with self._operation():
            return self._catalog.refresh()

    async def projects(self) -> tuple[ProjectProjection, ...]:
        async with self._operation():
            return await self._threads.projects()

    async def create_thread(
        self,
        *,
        defaults: RootThreadDefaults | None = None,
        title: str | None = None,
    ) -> Thread:
        async with self._operation():
            return await self._threads.create(defaults=defaults, title=title)

    async def get_thread(self, thread_id: str) -> Thread:
        async with self._operation():
            return await self._threads.get(thread_id)

    async def list_threads(
        self,
        *,
        query: str | None = None,
        include_archived: bool = False,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[tuple[Thread, ...], int]:
        async with self._operation():
            return await self._threads.list(
                query=query,
                include_archived=include_archived,
                offset=offset,
                limit=limit,
            )

    async def inspect_thread(
        self,
        *,
        thread_id: str,
        history_offset: int = 0,
        history_limit: int = 50,
    ) -> tuple[Thread, list[object], int]:
        async with self._operation():
            thread, history, total = await self._threads.inspect(
                thread_id=thread_id,
                history_offset=history_offset,
                history_limit=history_limit,
            )
            return thread, list(history), total

    async def update_thread_configuration(
        self,
        *,
        thread_id: str,
        mutation: ThreadConfigurationMutation,
    ) -> Thread:
        async with self._operation():
            return await self._threads.update_configuration(
                thread_id=thread_id,
                mutation=mutation,
            )

    async def archive_thread(self, *, thread_id: str, archived: bool = True) -> Thread:
        async with self._operation():
            return await self._threads.archive(thread_id=thread_id, archived=archived)

    async def run_thread(
        self,
        *,
        thread_id: str,
        prompt: str,
        mutation: ThreadConfigurationMutation | None = None,
    ) -> RootRunOutcome:
        async with self._operation():
            return await self._threads.run(
                thread_id=thread_id,
                prompt=prompt,
                mutation=mutation,
            )

    async def steer_thread(self, *, thread_id: str, message: str) -> RootSteerResult:
        async with self._operation():
            return await self._threads.steer(thread_id=thread_id, message=message)

    async def cancel_thread(self, *, thread_id: str) -> RootCancelResult:
        async with self._operation():
            return await self._threads.cancel(thread_id=thread_id)

    async def query_child_executions(
        self,
        *,
        parent_thread_id: str,
        execution_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> ChildExecutionPage:
        async with self._operation():
            return await self._subagent_operator.query_child_executions(
                parent_thread_id=parent_thread_id,
                execution_id=execution_id,
                execution_offset=offset,
                execution_limit=limit,
            )

    async def wait_child_executions(
        self,
        *,
        parent_thread_id: str,
        execution_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
        timeout_seconds: float | None = None,
    ) -> ChildExecutionPage:
        async with self._operation():
            return await self._subagent_operator.wait_child_executions(
                parent_thread_id=parent_thread_id,
                execution_id=execution_id,
                execution_offset=offset,
                execution_limit=limit,
                timeout_seconds=timeout_seconds,
            )

    async def steer_child_execution(
        self,
        *,
        parent_thread_id: str,
        execution_id: str,
        message: str,
    ) -> SubagentSteerResult:
        async with self._operation():
            return await self._subagent_operator.steer_execution(
                parent_thread_id=parent_thread_id,
                execution_id=execution_id,
                message=message,
            )

    async def cancel_child_execution(
        self,
        *,
        parent_thread_id: str,
        execution_id: str,
    ) -> SubagentCancelResult:
        async with self._operation():
            return await self._subagent_operator.cancel_execution(
                parent_thread_id=parent_thread_id,
                execution_id=execution_id,
            )

    async def inspect_model_account(self, provider: Provider | str) -> AccountProjection:
        async with self._operation():
            selected = Provider(provider)
            return await self._account(selected).inspect()

    async def login_model_account(
        self,
        provider: Provider | str,
        *,
        allow_account_switch: bool = False,
    ) -> AccountProjection:
        async with self._operation():
            selected = Provider(provider)
            if selected is Provider.CODEX:
                if self._codex_login is None:
                    raise AppStateError(
                        "No Codex login flow is registered.",
                        code="model_account_login_unavailable",
                    )
                return await self._codex_account.login(
                    self._codex_login,
                    allow_account_switch=allow_account_switch,
                )
            account = self._account(Provider.GROK)
            assert isinstance(account, GrokAccountStore)
            if self._grok_login is None:
                raise AppStateError(
                    "No Grok login flow is registered.",
                    code="model_account_login_unavailable",
                )
            return await account.login(
                self._grok_login,
                allow_account_switch=allow_account_switch,
            )

    async def logout_model_account(self, provider: Provider | str) -> bool:
        async with self._operation():
            return await self._account(Provider(provider)).logout()

    @asynccontextmanager
    async def live_events(
        self,
        *,
        thread_id: str | None = None,
    ) -> AsyncGenerator[LiveSubscription]:
        self._require_ready()
        async with self._live_hub.subscribe(thread_id=thread_id) as subscription:
            yield subscription

    async def _accept_mutation(self, result: ConfigurationMutationResult) -> None:
        expected = await self._store.configurations.current_digest()
        try:
            await self._configurations.accept(
                result.configuration,
                expected_current_digest=expected,
            )
        except AgentUiError as exc:
            self._candidate_error = exc
            raise
        self._candidate_error = None

    def _account(self, provider: Provider) -> CodexAccountStore | GrokAccountStore:
        if provider is Provider.CODEX:
            return self._codex_account
        if self._grok_account is None:
            if self._grok_account_error is not None:
                raise self._grok_account_error
            raise AppStateError(
                "The Grok compatible account scope is not configured.",
                code="model_account_integration_unavailable",
            )
        return self._grok_account

    def _require_configuration_path(self) -> Path:
        if self._configuration_path is None:
            raise AppStateError(
                "This App lifetime has no editable configuration path.",
                code="configuration_path_unavailable",
            )
        return self._configuration_path

    @asynccontextmanager
    async def _operation(self) -> AsyncGenerator[None]:
        scope = CancelScope()
        async with self._operation_lock:
            self._require_ready()
            if not self._operation_scopes:
                self._operations_idle = Event()
            self._operation_scopes.add(scope)
        cancelled_by_shutdown = False
        try:
            with scope:
                yield
            cancelled_by_shutdown = scope.cancel_called
        finally:
            with CancelScope(shield=True):
                async with self._operation_lock:
                    self._operation_scopes.discard(scope)
                    if not self._operation_scopes:
                        self._operations_idle.set()
        if cancelled_by_shutdown:
            raise AppStateError(
                "Agent UI operation was cancelled during App shutdown.",
                code="app_stopping",
            )

    async def _stop(self) -> None:
        async with self._operation_lock:
            if self._state is not AppState.ready:
                return
            self._state = AppState.stopping
            idle = self._operations_idle
        await self._subagent_operator.stop_admission()

        with move_on_after(self._settings.shutdown_timeout_seconds) as drain_scope:
            await idle.wait()
        if not drain_scope.cancel_called:
            return

        async with self._operation_lock:
            scopes = tuple(self._operation_scopes)
            idle = self._operations_idle
        for scope in scopes:
            scope.cancel()
        with move_on_after(self._settings.shutdown_timeout_seconds):
            await idle.wait()

    async def _close_collaborators(self) -> None:
        try:
            await self._subagent_operator.close(timeout_seconds=self._settings.shutdown_timeout_seconds)
        finally:
            await self._live_hub.close()

    def _require_ready(self) -> None:
        if self._state is not AppState.ready:
            raise AppStateError(
                "Agent UI App is not accepting commands.",
                code="app_not_ready",
                details={"state": self._state.value},
            )


@asynccontextmanager
async def open_agent_ui_app(
    settings: AgentUiSettings,
    *,
    configuration_path: Path | None = None,
    configuration_error: ConfigurationError | None = None,
    codex_refresh: Callable[[CodexCredentials], Awaitable[CodexCredentials]] | None = None,
    codex_login: CodexLoginCallback | None = None,
    grok_scope: str | None = None,
    grok_refresh: Callable[[GrokCredentials], Awaitable[GrokCredentials]] | None = None,
    grok_login: GrokLoginCallback | None = None,
    integrations: AgentUiIntegrations | None = None,
) -> AsyncGenerator[AgentUiApp]:
    """Start, expose, and close one complete process-local App lifetime."""

    app: AgentUiApp | None = None
    operator: AgentUiSubagentOperator | None = None
    try:
        async with open_local_store(settings.storage) as store:
            selected_integrations = integrations or AgentUiIntegrations()
            catalog = AgentUiExtensionCatalog(
                host_capabilities=dict(selected_integrations.capabilities),
                host_providers=selected_integrations.environment_providers,
                host_adapters=selected_integrations.environment_adapters,
                host_plugin_factories=selected_integrations.harness_plugin_factories,
                host_run_extension_factories=(selected_integrations.environment_run_extension_factories),
            )
            resolver = AgentCompositionResolver(catalog)
            configurations = CompositionAcceptanceService(store, resolver)
            compositions = RunCompositionService(store, resolver)
            candidate_error: AgentUiError | None = configuration_error
            candidate: LoadedAgentUiConfiguration | None = None
            if configuration_path is not None and configuration_path.exists():
                try:
                    candidate = await load_agent_ui_configuration(configuration_path)
                    candidate_error = None
                except AgentUiError as exc:
                    candidate_error = exc
            current_digest = await store.configurations.current_digest()
            if candidate is not None:
                try:
                    await configurations.accept(
                        candidate,
                        expected_current_digest=current_digest,
                    )
                except AgentUiError as exc:
                    if current_digest is None:
                        raise
                    candidate_error = exc
            elif current_digest is None and candidate_error is None:
                empty = empty_agent_ui_configuration()
                await configurations.accept(empty, expected_current_digest=None)

            environment_reconstructor = EnvironmentSnapshotReconstructor(
                catalog=catalog,
                envd_settings=settings.envd_runtime,
                local_runtime_parent=store.layout.runtimes,
                runtime_factories=selected_integrations.provider_runtime_factories,
            )
            environment_service = EnvironmentRunService(store, environment_reconstructor)
            agent_reconstructor = AgentReconstructor(catalog)
            live_hub = AgentUiLiveHub()
            cleanup_timeout = min(
                settings.shutdown_timeout_seconds,
                settings.storage.cleanup_timeout_seconds,
            )
            codex_account = CodexAccountStore(await resolve_codex_policy())
            grok_account_error: AccountStoreError | None = None
            try:
                grok_policy = resolve_grok_policy()
                selected_grok_scope = grok_scope or await resolve_grok_scope(grok_policy)
                grok_account = (
                    None if selected_grok_scope is None else GrokAccountStore(grok_policy, scope=selected_grok_scope)
                )
            except AccountStoreError as exc:
                grok_account = None
                grok_account_error = exc
            subscription_sources: dict[str, SubscriptionSource] = {
                "codex_subscription": CodexSubscriptionSource(
                    source=codex_account,
                    refresh=codex_refresh,
                ),
            }
            if grok_account is not None:
                subscription_sources["grok_subscription"] = GrokSubscriptionSource(
                    source=grok_account,
                    refresh=grok_refresh,
                )

            operator = AgentUiSubagentOperator(
                store=store,
                configurations=configurations,
                compositions=compositions,
                agent_reconstructor=agent_reconstructor,
                environment_service=environment_service,
                subscription_sources=subscription_sources,
                live_hub=live_hub,
                cleanup_timeout_seconds=cleanup_timeout,
            )
            threads = ThreadService(
                store=store,
                configurations=configurations,
                compositions=compositions,
                agent_reconstructor=agent_reconstructor,
                environment_service=environment_service,
                subagent_operator=operator,
                subscription_sources=subscription_sources,
                live_hub=live_hub,
                cleanup_timeout_seconds=cleanup_timeout,
            )
            app = AgentUiApp(
                settings,
                store,
                configuration_path=configuration_path,
                catalog=catalog,
                configurations=configurations,
                threads=threads,
                subagent_operator=operator,
                live_hub=live_hub,
                codex_account=codex_account,
                grok_account=grok_account,
                grok_account_error=grok_account_error,
                codex_login=codex_login,
                grok_login=grok_login,
                candidate_error=candidate_error,
            )
            try:
                await operator.start()
                app._state = AppState.ready
                async with create_task_group() as background:
                    if configuration_path is not None:
                        background.start_soon(app._observe_configuration)
                    try:
                        yield app
                    finally:
                        with CancelScope(shield=True):
                            await app._stop()
                        background.cancel_scope.cancel()
            finally:
                await app._close_collaborators()
                app._state = AppState.closed
    finally:
        if app is not None:
            app._state = AppState.closed
        elif operator is not None:
            await operator.close(timeout_seconds=settings.shutdown_timeout_seconds)


__all__ = [
    "AgentUiApp",
    "AgentUiIntegrations",
    "AppState",
    "AppStatus",
    "open_agent_ui_app",
]
