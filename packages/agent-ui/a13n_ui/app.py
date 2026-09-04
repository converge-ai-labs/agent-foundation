"""Process-local application boundary shared by Agent UI surfaces."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from a13n_environment_provider import EnvironmentProvider
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
    configuration_tree_fingerprint,
    delete_configuration_source,
    empty_agent_ui_configuration,
    load_agent_ui_configuration,
    mutate_configuration_source,
    preview_external_subagent_import,
)
from a13n_ui.environment_profiles import BUILT_IN_ENVIRONMENT_PROFILES
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
from a13n_ui.live import (
    AgentUiLiveHub,
    AgentUiSummaryHub,
    LiveCursor,
    LiveSubscription,
    SummaryCursor,
    SummarySubscription,
)
from a13n_ui.model_accounts import (
    DEFAULT_GROK_OAUTH_SCOPE,
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
from a13n_ui.root_execution import RootRunExecutor
from a13n_ui.root_run import RootRunCoordinator
from a13n_ui.settings import AgentUiSettings
from a13n_ui.storage import LocalStore, ThreadConfigurationMutation, open_local_store
from a13n_ui.subagent_operator import AgentUiSubagentOperator
from a13n_ui.surfaces import (
    ChildControlResult,
    ChildExecutionPage,
    EnvironmentProfileSummary,
    ProjectSummary,
    RootControlResult,
    RootOperationView,
    RootRunReceipt,
    ThreadDeferredResponse,
    ThreadDetail,
    ThreadFocusSnapshot,
    ThreadMetadataMutation,
    ThreadPage,
    ThreadSummary,
    TranscriptPage,
)
from a13n_ui.thread_capability import AgentUiThreadCapability, ThreadToolController
from a13n_ui.thread_projection import ThreadProjectionService
from a13n_ui.thread_service import RootThreadDefaults, ThreadService


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


@dataclass(frozen=True, slots=True)
class ThreadWatch:
    snapshot: ThreadFocusSnapshot
    events: LiveSubscription


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
        projections: ThreadProjectionService,
        root_runs: RootRunCoordinator,
        subagent_operator: AgentUiSubagentOperator,
        live_hub: AgentUiLiveHub,
        summary_hub: AgentUiSummaryHub,
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
        self._projections = projections
        self._root_runs = root_runs
        self._subagent_operator = subagent_operator
        self._live_hub = live_hub
        self._summary_hub = summary_hub
        self._codex_account = codex_account
        self._grok_account = grok_account
        self._grok_account_error = grok_account_error
        self._codex_login = codex_login
        self._grok_login = grok_login
        self._candidate_error = candidate_error
        self._configuration_seen = configuration_path is not None and configuration_path.exists()
        self._configuration_fingerprint: tuple[tuple[str, tuple[int, int, int, int]], ...] | None = None
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
            if not self._configuration_seen:
                return
            diagnostic_changed = self._replace_candidate_error(
                ConfigurationError(
                    "The Agent UI configuration root is unavailable.",
                    code="settings_unavailable",
                    details={"path": str(path)},
                )
            )
            if diagnostic_changed:
                await self._summary_hub.publish(kind="configuration")
            return
        self._configuration_seen = True
        generation_changed = False
        try:
            candidate = await load_agent_ui_configuration(path)
            current = await self._store.configurations.current_digest()
            if candidate.source_digest != current:
                await self._configurations.accept(
                    candidate,
                    expected_current_digest=current,
                )
                generation_changed = True
            candidate_error: AgentUiError | None = None
        except AgentUiError as exc:
            candidate_error = exc
        diagnostic_changed = self._replace_candidate_error(candidate_error)
        if generation_changed or diagnostic_changed:
            await self._summary_hub.publish(kind="configuration")
        if generation_changed:
            await self._summary_hub.publish(kind="project")

    async def _observe_configuration(self) -> None:
        while self._state is AppState.ready:
            await sleep(0.5)
            if self._state is not AppState.ready:
                return
            path = self._require_configuration_path()
            try:
                fingerprint = await configuration_tree_fingerprint(path)
            except AgentUiError as exc:
                diagnostic_changed = self._replace_candidate_error(exc)
                self._configuration_fingerprint = None
                if diagnostic_changed:
                    await self._summary_hub.publish(kind="configuration")
                continue
            if fingerprint == self._configuration_fingerprint:
                continue
            self._configuration_fingerprint = fingerprint
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
            references = self._catalog.refresh()
            await self._summary_hub.publish(kind="catalog")
            return references

    async def projects(self) -> tuple[ProjectSummary, ...]:
        async with self._operation():
            return await self._projections.projects()

    async def environment_profiles(self) -> tuple[EnvironmentProfileSummary, ...]:
        """Return release-owned modes plus accepted custom Environment profiles."""

        async with self._operation():
            source = await self._configurations.current()
            if source is None:
                raise ConfigurationError(
                    "No accepted Agent UI configuration is selected.",
                    code="configuration_not_accepted",
                )
            result = [
                EnvironmentProfileSummary(
                    profile_id=profile.profile_id,
                    name=profile.name,
                    mode=profile.mode.value,
                    description=profile.description,
                    provider_key=profile.provider_key,
                    release_owned=True,
                    canonical_host_paths=self._catalog.environment_adapter(
                        profile.adapter_key,
                        profile.provider_key,
                    ).preserves_host_paths,
                )
                for profile in BUILT_IN_ENVIRONMENT_PROFILES
            ]
            for profile in sorted(
                source.environment_profiles.values(), key=lambda item: (item.name.casefold(), item.id)
            ):
                adapter = self._catalog.environment_adapter(profile.adapter_key, profile.provider_key)
                result.append(
                    EnvironmentProfileSummary(
                        profile_id=profile.id,
                        name=profile.name,
                        mode="custom",
                        description=f"Custom Environment profile using {profile.provider_key}.",
                        provider_key=profile.provider_key,
                        release_owned=False,
                        canonical_host_paths=adapter.preserves_host_paths,
                    )
                )
            return tuple(result)

    async def create_thread(
        self,
        *,
        defaults: RootThreadDefaults | None = None,
        title: str | None = None,
    ) -> ThreadSummary:
        async with self._operation():
            thread = await self._threads.create(defaults=defaults, title=title)
            await self._summary_hub.publish(kind="thread", thread_id=thread.thread_id)
            return await self._projections.get_thread(thread.thread_id)

    async def get_thread(self, thread_id: str) -> ThreadDetail:
        async with self._operation():
            return await self._projections.detail(thread_id)

    async def list_threads(
        self,
        *,
        query: str | None = None,
        include_archived: bool = False,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ThreadPage:
        async with self._operation():
            return await self._projections.list_threads(
                query=query,
                include_archived=include_archived,
                cursor=cursor,
                limit=limit,
            )

    async def get_thread_transcript(
        self,
        *,
        thread_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> TranscriptPage:
        async with self._operation():
            return await self._projections.transcript(
                thread_id=thread_id,
                cursor=cursor,
                limit=limit,
            )

    async def update_thread_configuration(
        self,
        *,
        thread_id: str,
        mutation: ThreadConfigurationMutation,
    ) -> ThreadSummary:
        async with self._operation():
            await self._threads.update_configuration(
                thread_id=thread_id,
                mutation=mutation,
            )
            await self._summary_hub.publish(kind="thread", thread_id=thread_id)
            return await self._projections.get_thread(thread_id)

    async def update_thread_metadata(
        self,
        *,
        thread_id: str,
        mutation: ThreadMetadataMutation,
    ) -> ThreadSummary:
        async with self._operation():
            if mutation.patch.archived is True:
                async with self._root_runs.require_inactive(thread_id):
                    await self._threads.update_metadata(thread_id=thread_id, mutation=mutation)
            else:
                await self._threads.update_metadata(thread_id=thread_id, mutation=mutation)
            await self._summary_hub.publish(kind="thread", thread_id=thread_id)
            return await self._projections.get_thread(thread_id)

    async def submit_thread(
        self,
        *,
        thread_id: str,
        prompt: str,
        mutation: ThreadConfigurationMutation | None = None,
    ) -> RootRunReceipt:
        async with self._operation():
            return await self._root_runs.submit_prompt(
                thread_id=thread_id,
                prompt=prompt,
                mutation=mutation,
            )

    async def respond_thread(
        self,
        *,
        thread_id: str,
        response: ThreadDeferredResponse,
        mutation: ThreadConfigurationMutation | None = None,
    ) -> RootRunReceipt:
        async with self._operation():
            return await self._root_runs.submit_response(
                thread_id=thread_id,
                response=response,
                mutation=mutation,
            )

    async def get_root_operation(self, receipt_id: str) -> RootOperationView:
        async with self._operation():
            return await self._root_runs.get(receipt_id)

    async def active_root_operation(self, thread_id: str) -> RootOperationView | None:
        async with self._operation():
            return await self._root_runs.active(thread_id)

    async def wait_root_operation(
        self,
        receipt_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> RootOperationView:
        async with self._operation():
            return await self._root_runs.wait(receipt_id, timeout_seconds=timeout_seconds)

    async def steer_root_operation(self, *, receipt_id: str, message: str) -> RootControlResult:
        async with self._operation():
            return await self._root_runs.steer(receipt_id=receipt_id, message=message)

    async def cancel_root_operation(self, receipt_id: str) -> RootControlResult:
        async with self._operation():
            return await self._root_runs.cancel(receipt_id)

    async def query_child_executions(
        self,
        *,
        parent_thread_id: str,
        execution_id: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ChildExecutionPage:
        async with self._operation():
            return await self._subagent_operator.query_child_executions(
                parent_thread_id=parent_thread_id,
                execution_id=execution_id,
                cursor=cursor,
                limit=limit,
            )

    async def wait_child_executions(
        self,
        *,
        parent_thread_id: str,
        execution_id: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
        timeout_seconds: float | None = None,
    ) -> ChildExecutionPage:
        async with self._operation():
            return await self._subagent_operator.wait_child_executions(
                parent_thread_id=parent_thread_id,
                execution_id=execution_id,
                cursor=cursor,
                limit=limit,
                timeout_seconds=timeout_seconds,
            )

    async def steer_child_execution(
        self,
        *,
        parent_thread_id: str,
        execution_id: str,
        message: str,
    ) -> ChildControlResult:
        async with self._operation():
            result = await self._subagent_operator.steer_execution(
                parent_thread_id=parent_thread_id,
                execution_id=execution_id,
                message=message,
            )
            return ChildControlResult(
                execution_id=result.execution_id,
                accepted=result.accepted,
                enqueue_id=result.enqueue_id,
            )

    async def cancel_child_execution(
        self,
        *,
        parent_thread_id: str,
        execution_id: str,
    ) -> ChildControlResult:
        async with self._operation():
            result = await self._subagent_operator.cancel_execution(
                parent_thread_id=parent_thread_id,
                execution_id=execution_id,
            )
            return ChildControlResult(
                execution_id=result.execution_id,
                accepted=result.accepted,
                persisted_status=result.status,
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
        root_thread_id: str | None = None,
        after: LiveCursor | None = None,
    ) -> AsyncGenerator[LiveSubscription]:
        self._require_ready()
        async with self._live_hub.subscribe(root_thread_id=root_thread_id, after=after) as subscription:
            yield subscription

    @asynccontextmanager
    async def watch_thread(
        self,
        *,
        root_thread_id: str,
        child_limit: int = 20,
    ) -> AsyncGenerator[ThreadWatch]:
        self._require_ready()
        async with self._live_hub.subscribe(root_thread_id=root_thread_id) as subscription:
            async with self._operation():
                thread = await self._projections.detail(root_thread_id)
                if thread.thread.parent_thread_id is not None:
                    raise AppStateError("A focused watch requires a root Thread.", code="child_thread_scoped")
                children = await self._subagent_operator.query_child_executions(
                    parent_thread_id=root_thread_id,
                    limit=child_limit,
                )
            cursor = subscription.cursor
            yield ThreadWatch(
                snapshot=ThreadFocusSnapshot(
                    epoch=cursor.epoch,
                    cutover_sequence=cursor.sequence,
                    thread=thread,
                    children=children,
                ),
                events=subscription,
            )

    @asynccontextmanager
    async def summary_events(
        self,
        *,
        after: SummaryCursor | None = None,
    ) -> AsyncGenerator[SummarySubscription]:
        self._require_ready()
        async with self._summary_hub.subscribe(after=after) as subscription:
            yield subscription

    async def _accept_mutation(self, result: ConfigurationMutationResult) -> None:
        expected = await self._store.configurations.current_digest()
        try:
            await self._configurations.accept(
                result.configuration,
                expected_current_digest=expected,
            )
        except AgentUiError as exc:
            diagnostic_changed = self._replace_candidate_error(exc)
            if diagnostic_changed:
                await self._summary_hub.publish(kind="configuration")
            raise
        self._replace_candidate_error(None)
        self._configuration_fingerprint = await configuration_tree_fingerprint(self._require_configuration_path())
        await self._summary_hub.publish(kind="configuration")
        await self._summary_hub.publish(kind="project")

    def _replace_candidate_error(self, replacement: AgentUiError | None) -> bool:
        previous = None if self._candidate_error is None else (self._candidate_error.code, str(self._candidate_error))
        current = None if replacement is None else (replacement.code, str(replacement))
        self._candidate_error = replacement
        return current != previous

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
        await self._root_runs.stop_admission()
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
            await self._root_runs.close(timeout_seconds=self._settings.shutdown_timeout_seconds)
        finally:
            try:
                await self._subagent_operator.close(timeout_seconds=self._settings.shutdown_timeout_seconds)
            finally:
                with CancelScope(shield=True):
                    try:
                        await self._live_hub.close()
                    finally:
                        await self._summary_hub.close()

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
            summary_hub = AgentUiSummaryHub(epoch=live_hub.epoch)
            cleanup_timeout = min(
                settings.shutdown_timeout_seconds,
                settings.storage.cleanup_timeout_seconds,
            )
            codex_account = CodexAccountStore(await resolve_codex_policy())
            grok_account_error: AccountStoreError | None = None
            try:
                grok_policy = resolve_grok_policy()
                selected_grok_scope = grok_scope or await resolve_grok_scope(grok_policy) or DEFAULT_GROK_OAUTH_SCOPE
                grok_account = GrokAccountStore(grok_policy, scope=selected_grok_scope)
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
                summary_hub=summary_hub,
                cleanup_timeout_seconds=cleanup_timeout,
            )
            threads = ThreadService(
                store=store,
                configurations=configurations,
            )
            root_executor = RootRunExecutor(
                store=store,
                threads=threads,
                configurations=configurations,
                compositions=compositions,
                agent_reconstructor=agent_reconstructor,
                environment_service=environment_service,
                subagent_operator=operator,
                subscription_sources=subscription_sources,
                live_hub=live_hub,
                cleanup_timeout_seconds=cleanup_timeout,
            )
            root_runs = RootRunCoordinator(root_executor, summary_hub=summary_hub)
            projections = ThreadProjectionService(
                store=store,
                configurations=configurations,
                root_activity=root_runs.activity,
            )
            thread_tools = ThreadToolController(projections=projections, root_runs=root_runs)
            root_executor.set_root_capability_factory(
                lambda thread_id: AgentUiThreadCapability(
                    controller=thread_tools,
                    source_thread_id=thread_id,
                )
            )
            app = AgentUiApp(
                settings,
                store,
                configuration_path=configuration_path,
                catalog=catalog,
                configurations=configurations,
                threads=threads,
                projections=projections,
                root_runs=root_runs,
                subagent_operator=operator,
                live_hub=live_hub,
                summary_hub=summary_hub,
                codex_account=codex_account,
                grok_account=grok_account,
                grok_account_error=grok_account_error,
                codex_login=codex_login,
                grok_login=grok_login,
                candidate_error=candidate_error,
            )
            try:
                await operator.start()
                await root_runs.start()
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
    "ThreadWatch",
    "open_agent_ui_app",
]
