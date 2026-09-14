"""Process-local application boundary shared by Harness UI surfaces."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import httpx2
from a13n_environment import EnvironmentProvider
from a13n_harness import HarnessInstrumentation
from a13n_harness.environment import EnvironmentRunExtensionFactory
from a13n_harness.input import RunInputValue
from a13n_harness.model_auth import GrokCredentials
from a13n_harness.plugin_factories import HarnessPluginFactory
from a13n_logging import get_logger
from anyio import CancelScope, Event, Lock, create_task_group, move_on_after, sleep, to_thread
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import BinaryContent, prices
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import TextContent, UserContent

from a13n_harness_ui.composition import (
    AgentCompositionResolver,
    AgentReconstructor,
    CompositionAcceptanceService,
    ResolvedRunComposition,
    RunCompositionService,
)
from a13n_harness_ui.configuration import (
    ConfigurationMutationResult,
    ExternalSubagentImportCandidate,
    ExternalSubagentImportPreview,
    ExternalSubagentProduct,
    ExternalSubagentScope,
    LoadedHarnessUiConfiguration,
    ResourceMutationRequest,
    apply_external_subagent_import,
    configuration_tree_fingerprint,
    delete_configuration_source,
    empty_harness_ui_configuration,
    load_harness_ui_configuration,
    mutate_configuration_source,
    preview_external_subagent_import,
)
from a13n_harness_ui.configuration.mutation import validate_configuration_source
from a13n_harness_ui.configuration.setup import (
    SetupPreview,
    SetupPublication,
    SetupSelection,
    preview_setup,
    publish_setup,
)
from a13n_harness_ui.configuration.views import (
    AgentToolProxyView,
    ConfigurationSourceCatalog,
    ConfigurationSourceView,
    ConfigurationValidation,
    agent_tool_proxy_view,
    source_catalog,
    source_view,
)
from a13n_harness_ui.configuration_inspection import (
    CapturedConfiguration,
    ThreadConfigurationInspection,
    captured_configuration,
)
from a13n_harness_ui.content_plugins import ContentPluginStore
from a13n_harness_ui.environment_profiles import BUILT_IN_ENVIRONMENT_PROFILES, built_in_environment_profile
from a13n_harness_ui.environment_runtime import (
    EnvironmentRunService,
    EnvironmentSnapshotReconstructor,
    ProviderRuntimeFactory,
)
from a13n_harness_ui.errors import AppStateError, ConfigurationError, HarnessUiError, LivePresentationError
from a13n_harness_ui.extensions import (
    CatalogReference,
    EnvironmentProjectAdapter,
    HarnessUiExtensionCatalog,
)
from a13n_harness_ui.file_context import MAX_INLINE_CONTEXT_BYTES, CommentContextSource, context_text
from a13n_harness_ui.host_files import (
    DirectoryCreateRequest,
    DirectoryPage,
    FileCapture,
    FileCaptureRequest,
    FileDeleteRequest,
    FileDeletion,
    FileEntry,
    FileMoveRequest,
    FileReadRequest,
    FileSnapshot,
    FileText,
    FileWriteRequest,
    HostFiles,
)
from a13n_harness_ui.host_git import GitCaptureRequest, GitDiff, GitDiffRequest, GitDiscovery, GitStatus, HostGit
from a13n_harness_ui.host_terminal import HostTerminal, TerminalCreate, TerminalSession, TerminalView
from a13n_harness_ui.live import (
    HarnessUiLiveHub,
    HarnessUiSummaryHub,
    LiveCursor,
    LiveEvent,
    LiveSubscription,
    RootStreamReplay,
    SummaryCursor,
    SummarySubscription,
)
from a13n_harness_ui.model_accounts import (
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
from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput, ApiKeyStatus, ApiKeyStore
from a13n_harness_ui.model_accounts.login import LoginRequest, LoginSessions, LoginStatus
from a13n_harness_ui.model_accounts.usage import CodexUsage, CodexUsageClient, ResetRequest, ResetResult
from a13n_harness_ui.model_runtime import CodexSubscriptionSource, GrokSubscriptionSource, SubscriptionSource
from a13n_harness_ui.observation import open_observation
from a13n_harness_ui.output_comment_models import (
    CommentPage,
    CommentPublication,
    OutputComment,
    SavedChildOutputPage,
    SavedOutputTarget,
    SavedOutputView,
)
from a13n_harness_ui.output_comments import OutputComments
from a13n_harness_ui.page_presence import (
    ChangesPage,
    ConversationPage,
    FilePage,
    PageFocus,
    PagePresence,
    PresenceFrame,
    PresenceReport,
    ProjectPage,
    ResourcePage,
    TerminalPage,
    WorkbenchPage,
)
from a13n_harness_ui.root_execution import RootRunExecutor
from a13n_harness_ui.root_input import detach_input
from a13n_harness_ui.root_run import RootRunCoordinator
from a13n_harness_ui.settings import HarnessUiSettings
from a13n_harness_ui.setup import EnvironmentReadiness, SetupProvider, SetupStatus, preflight_environment
from a13n_harness_ui.shared_drafts import DraftCommand, SharedDraft
from a13n_harness_ui.storage import (
    AgentResourceSource,
    LocalStore,
    StoredContinuation,
    ThreadConfiguration,
    ThreadConfigurationMutation,
    open_local_store,
)
from a13n_harness_ui.storage import (
    ThreadConfigurationPatch as StoredThreadConfigurationPatch,
)
from a13n_harness_ui.storage.usage import ThreadUsageView
from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator
from a13n_harness_ui.surfaces import (
    ActiveWorkSummary,
    ChildControlResult,
    ChildExecutionPage,
    ConfigurationProvenance,
    ContextUsageView,
    DecisionBatchView,
    DecisionResponseBatch,
    EnvironmentProfileSummary,
    ExternalToolResult,
    LaunchProjectResolution,
    NewThreadDefaults,
    NotePage,
    ProjectDefaultsApply,
    ProjectDefaultsPreview,
    ProjectPathCompletionPage,
    ProjectSummary,
    QuestionResponse,
    ReviewView,
    RootControlResult,
    RootOperationView,
    RootRunReceipt,
    RunModelOverrides,
    SkillCatalogView,
    SkillReference,
    TaskPage,
    ThreadActivityPage,
    ThreadConfigurationMutationInput,
    ThreadConfigurationResolution,
    ThreadDeferredResponse,
    ThreadDetail,
    ThreadFocusSnapshot,
    ThreadMetadataMutation,
    ThreadPage,
    ThreadSelectorCatalog,
    ThreadSummary,
    TranscriptPage,
)
from a13n_harness_ui.terminal_projection import TerminalProjectionService
from a13n_harness_ui.thread_capability import ThreadCollaborationCapability, ThreadToolController
from a13n_harness_ui.thread_files import (
    MAX_ATTACHMENTS,
    MAX_INPUT_BYTES,
    AttachmentUpload,
    ComposerInput,
    ThreadAttachment,
    ThreadFiles,
)
from a13n_harness_ui.thread_projection import ThreadProjectionService
from a13n_harness_ui.thread_service import RootThreadDefaults, ThreadService


@dataclass(frozen=True, slots=True)
class HarnessUiIntegrations:
    """Explicit trusted Host registrations fixed for one App lifetime."""

    capabilities: Mapping[str, type[AbstractCapability[Any]]] = field(default_factory=dict)
    environment_providers: tuple[EnvironmentProvider, ...] = ()
    environment_adapters: tuple[EnvironmentProjectAdapter, ...] = ()
    harness_plugin_factories: tuple[HarnessPluginFactory, ...] = ()
    environment_run_extension_factories: tuple[EnvironmentRunExtensionFactory, ...] = ()
    provider_runtime_factories: Mapping[str, ProviderRuntimeFactory] = field(default_factory=dict)


class AppState(StrEnum):
    """Observable lifecycle state for one Harness UI App."""

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
    content_plugin_diagnostics: tuple[str, ...] = ()
    capability_warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ThreadWatch:
    snapshot: ThreadFocusSnapshot
    events: LiveSubscription
    root_stream: RootStreamReplay | None = None


def _cwd_project_ids(source: LoadedHarnessUiConfiguration, directory: str) -> tuple[str, ...]:
    return tuple(sorted(project.id for project in source.projects.values() if project.roots[0].path == directory))


def _new_cwd_project_id(directory: str) -> str:
    return "project-cwd-" + hashlib.sha256(directory.encode()).hexdigest()[:20]


class HarnessUiApp:
    """The only application boundary shared by Harness UI surfaces."""

    def __init__(
        self,
        settings: HarnessUiSettings,
        store: LocalStore,
        *,
        configuration_path: Path | None,
        catalog: HarnessUiExtensionCatalog,
        configurations: CompositionAcceptanceService,
        threads: ThreadService,
        projections: ThreadProjectionService,
        terminal_projections: TerminalProjectionService,
        root_runs: RootRunCoordinator,
        thread_files: ThreadFiles,
        subagent_operator: HarnessUiSubagentOperator,
        live_hub: HarnessUiLiveHub,
        summary_hub: HarnessUiSummaryHub,
        codex_account: CodexAccountStore | None,
        codex_account_error: AccountStoreError | None,
        rediscover_accounts: Callable[
            [], Awaitable[tuple[CodexAccountStore | None, GrokAccountStore | None, dict[Provider, AccountStoreError]]]
        ],
        resolve_sandbox_executable: Callable[[], Awaitable[Path]],
        grok_account: GrokAccountStore | None,
        grok_account_error: AccountStoreError | None,
        codex_login: CodexLoginCallback | None,
        grok_login: GrokLoginCallback | None,
        candidate_error: HarnessUiError | None = None,
        share_computer: bool = False,
    ) -> None:
        self._settings = settings
        self._store = store
        self._api_keys = ApiKeyStore(store.layout.root / "auth.json")
        self._logins: LoginSessions | None = None
        self._configuration_path = configuration_path
        self._content_plugin_root = store.layout.content_plugins
        self._catalog = catalog
        self._configurations = configurations
        self._threads = threads
        self._projections = projections
        self._terminal_projections = terminal_projections
        self._thread_files = thread_files
        self._host_files = HostFiles(enabled=share_computer)
        self._host_git = HostGit(enabled=share_computer)
        self._host_terminal = HostTerminal(enabled=share_computer)
        self._shared_drafts: dict[str, SharedDraft] = {}
        self._output_comments = OutputComments(store)
        self._page_presence = PagePresence()
        self._root_runs = root_runs
        self._subagent_operator = subagent_operator
        self._live_hub = live_hub
        self._summary_hub = summary_hub
        self._codex_account = codex_account
        self._codex_account_error = codex_account_error
        self._rediscover_accounts = rediscover_accounts
        self._resolve_sandbox_executable = resolve_sandbox_executable
        self._configuration_lock = Lock()
        self._sandbox_ready_paths: set[Path] = set()
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

    async def start_login(self, request: LoginRequest) -> LoginStatus:
        async with self._operation():
            if self._logins is None:
                raise AppStateError("Interactive login is unavailable.", code="login_unavailable")
            return self._logins.start(request)

    async def login_status(self, session_id: str) -> LoginStatus:
        async with self._operation():
            if self._logins is None:
                raise AppStateError("Interactive login is unavailable.", code="login_unavailable")
            return self._logins.status(session_id)

    async def cancel_login(self, session_id: str) -> LoginStatus:
        async with self._operation():
            if self._logins is None:
                raise AppStateError("Interactive login is unavailable.", code="login_unavailable")
            return await self._logins.cancel(session_id)

    async def list_api_keys(self) -> tuple[ApiKeyStatus, ...]:
        async with self._operation():
            return await self._api_keys.list()

    async def put_api_key(self, value: ApiKeyInput) -> ApiKeyStatus:
        async with self._operation():
            return await self._api_keys.put(value)

    async def delete_api_key(self, reference: str) -> None:
        async with self._operation():
            await self._api_keys.delete(reference)

    @property
    def state(self) -> AppState:
        return self._state

    async def status(self) -> AppStatus:
        async with self._operation():
            current_digest = await self._store.configurations.current_digest()
            configuration = await self._configurations.current()
            return AppStatus(
                content_plugin_diagnostics=(() if configuration is None else configuration.content_plugin_diagnostics),
                capability_warnings=self._configurations.capability_warnings,
                state=self._state,
                object_count=await self._store.object_count(),
                accepted_generation_digest=current_digest,
                candidate_error_code=(None if self._candidate_error is None else self._candidate_error.code),
                candidate_error_message=(None if self._candidate_error is None else str(self._candidate_error)),
            )

    async def current_configuration(self) -> LoadedHarnessUiConfiguration | None:
        async with self._operation():
            return await self._configurations.current()

    async def reload_configuration(self) -> LoadedHarnessUiConfiguration | None:
        """Load and accept one stable complete source tree, retaining the previous generation on failure."""

        async with self._operation(), self._configuration_lock:
            await self._reload_configuration_from_path()
            return await self._configurations.current()

    async def _reload_configuration_from_path(self) -> None:
        path = self._require_configuration_path()
        if not path.exists():
            if not self._configuration_seen:
                return
            diagnostic_changed = self._replace_candidate_error(
                ConfigurationError(
                    "The Harness UI configuration root is unavailable.",
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
            candidate = await load_harness_ui_configuration(
                path,
                content_plugin_root=self._content_plugin_root,
            )
            current = await self._store.configurations.current_digest()
            if candidate.source_digest != current:
                await self._configurations.accept(
                    candidate,
                    expected_current_digest=current,
                )
                generation_changed = True
            candidate_error: HarnessUiError | None = None
        except HarnessUiError as exc:
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
            # App-owned publication and acceptance share one critical section.
            # External editors remain last-write-wins; this only avoids racing
            # our own SQLite head selection or observing our partial setup writes.
            async with self._configuration_lock:
                path = self._require_configuration_path()
                if not self._configuration_seen and not path.exists():
                    continue
                try:
                    fingerprint = await configuration_tree_fingerprint(
                        path,
                        content_plugin_root=self._content_plugin_root,
                    )
                except HarnessUiError as exc:
                    diagnostic_changed = self._replace_candidate_error(exc)
                    self._configuration_fingerprint = None
                    if diagnostic_changed:
                        await self._summary_hub.publish(kind="configuration")
                    continue
                if fingerprint == self._configuration_fingerprint:
                    continue
                self._configuration_fingerprint = fingerprint
                await self._reload_configuration_from_path()

    async def configuration_sources(self) -> ConfigurationSourceCatalog:
        """List the accepted generation, not a live filesystem or credential inventory."""
        async with self._operation():
            return source_catalog(await self._configurations.current(), self._require_configuration_path())

    async def configuration_source(self, *, relative_path: str) -> ConfigurationSourceView:
        async with self._operation():
            return source_view(await self._configurations.current(), self._require_configuration_path(), relative_path)

    async def validate_configuration(
        self, *, relative_path: str, request: ResourceMutationRequest
    ) -> ConfigurationValidation:
        async with self._operation(), self._configuration_lock:
            candidate = await validate_configuration_source(
                self._require_configuration_path(),
                relative_path,
                request,
                validate_candidate=self._configurations.validate,
                content_plugin_root=self._content_plugin_root,
            )
            return ConfigurationValidation(candidate_digest=candidate.source_digest)

    async def mutate_configuration(
        self,
        *,
        relative_path: str,
        request: ResourceMutationRequest,
    ) -> ConfigurationMutationResult:
        async with self._operation(), self._configuration_lock:
            path = self._require_configuration_path()
            result = await mutate_configuration_source(
                path,
                relative_path,
                request,
                validate_candidate=self._configurations.validate,
                content_plugin_root=self._content_plugin_root,
            )
            await self._accept_mutation(result)
            return result

    async def delete_configuration(
        self,
        *,
        relative_path: str,
    ) -> ConfigurationMutationResult:
        async with self._operation(), self._configuration_lock:
            path = self._require_configuration_path()
            result = await delete_configuration_source(
                path,
                relative_path,
                validate_candidate=self._configurations.validate,
                content_plugin_root=self._content_plugin_root,
            )
            await self._accept_mutation(result)
            return result

    async def preview_subagent_import(
        self,
        *,
        product: ExternalSubagentProduct | str,
        scope: ExternalSubagentScope | str,
        inherit_runtime: bool = False,
        project_root: Path | None = None,
        user_home: Path | None = None,
    ) -> ExternalSubagentImportPreview:
        async with self._operation():
            return await preview_external_subagent_import(
                self._require_configuration_path(),
                product=product,
                inherit_runtime=inherit_runtime,
                scope=scope,
                project_root=project_root,
                user_home=user_home,
                content_plugin_root=self._content_plugin_root,
            )

    async def apply_subagent_import(
        self,
        candidate: ExternalSubagentImportCandidate,
    ) -> ConfigurationMutationResult:
        async with self._operation(), self._configuration_lock:
            result = await apply_external_subagent_import(
                self._require_configuration_path(),
                candidate,
                validate_candidate=self._configurations.validate,
                content_plugin_root=self._content_plugin_root,
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
                    "No accepted Harness UI configuration is selected.",
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

    async def cwd_project_ids(self, directory: Path) -> tuple[str, ...]:
        """Read Projects whose default root is this exact invocation directory."""
        normalized = await to_thread.run_sync(lambda: directory.resolve(strict=True))
        source = await self.current_configuration()
        return () if source is None else _cwd_project_ids(source, str(normalized))

    async def cwd_model_preference(
        self, directory: Path, *, project_id: str | None = None
    ) -> tuple[str, str | None] | None:
        """Read the launch Project's Model preference without creating resources.

        A resumed Project disambiguates exact-root matches. An unmatched directory
        uses the same prospective identity as first submission. Ambiguity has no
        implicit preference; a missing Model ID is returned for the UI to explain.
        """
        normalized = await to_thread.run_sync(lambda: directory.resolve(strict=True))
        async with self._operation():
            source = await self._configurations.current()
            if source is None:
                return None
            matches = _cwd_project_ids(source, str(normalized))
            if project_id not in matches:
                if len(matches) > 1:
                    return None
                project_id = matches[0] if matches else _new_cwd_project_id(str(normalized))
            assert project_id is not None
            return project_id, await self._store.project_models.get(project_id)

    async def remember_project_model(self, *, project_id: str, model_id: str | None) -> None:
        """Persist an explicit terminal choice; None clears it. No YAML is changed."""
        async with self._operation():
            source = await self._configurations.current()
            if source is None:
                raise AppStateError("Configure a model first.", code="configuration_unavailable")
            if model_id is not None and model_id not in source.models:
                raise AppStateError("The selected Model no longer exists.", code="model_missing")
            await self._store.project_models.set(project_id, model_id)

    async def ensure_cwd_project(self, directory: Path) -> str:
        """Select or create an ordinary Project without retargeting saved Threads.

        Resources remain internal configuration facts. The CLI invokes this on
        first submission or explicit resume, never merely to paint a landing prompt.
        """
        normalized = await to_thread.run_sync(lambda: directory.resolve(strict=True))
        if not normalized.is_dir():
            raise AppStateError("Project root must be a directory.", code="project_root_invalid")
        root = str(normalized)
        project_id = _new_cwd_project_id(root)
        source = await self.current_configuration()
        if source is None:
            raise AppStateError("Configure a model with /setup first.", code="configuration_unavailable")
        exact = _cwd_project_ids(source, root)
        if len(exact) > 1:
            raise AppStateError(
                "Multiple Projects use this default directory. Resume a specific session or edit the Project roots.",
                code="project_ambiguous",
            )
        if exact:
            return exact[0]
        destination = f"projects/{project_id}.yaml"
        if project_id in source.projects or any(item.relative_path == destination for item in source.sources):
            raise AppStateError("Project identity conflicts with a configured resource.", code="project_conflict")
        await self.mutate_configuration(
            relative_path=destination,
            request=ResourceMutationRequest(
                content=json.dumps(
                    {
                        "schema_version": "1",
                        "kind": "project",
                        "id": project_id,
                        "name": normalized.name or root,
                        "roots": [{"path": root}],
                    }
                )
            ),
        )
        return project_id

    async def context_usage(self, thread_id: str) -> ContextUsageView:
        """Last reported root request footprint, not accumulated Run usage."""
        async with self._operation():
            return await self._projections.context_usage(thread_id)

    async def resolve_launch_project(
        self,
        directory: Path,
        *,
        project_id: str | None = None,
    ) -> LaunchProjectResolution:
        async with self._operation():
            return await self._terminal_projections.resolve_launch_project(
                directory,
                project_id=project_id,
            )

    async def thread_activity(
        self,
        *,
        project_id: str | None,
        query: str | None = None,
        include_archived: bool = False,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ThreadActivityPage:
        async with self._operation():
            return await self._terminal_projections.thread_activity(
                project_id=project_id,
                query=query,
                include_archived=include_archived,
                cursor=cursor,
                limit=limit,
            )

    async def active_work_summary(self) -> ActiveWorkSummary:
        """Return authoritative process-local root and child activity counts."""

        async with self._operation():
            root_operations = await self._root_runs.active_count()
            child_executions = len(await self._subagent_operator.active_execution_ids())
            return ActiveWorkSummary(
                root_operations=root_operations,
                child_executions=child_executions,
            )

    async def thread_usage(self, *, thread_id: str) -> ThreadUsageView:
        async with self._operation():
            return await self._store.usage.snapshot(thread_id=thread_id)

    async def thread_notes(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
    ) -> NotePage:
        async with self._operation():
            return await self._terminal_projections.note_page(
                thread_id=thread_id,
                expected_continuation_id=expected_continuation_id,
            )

    async def thread_tasks(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
        limit: int = 100,
    ) -> TaskPage:
        async with self._operation():
            return await self._terminal_projections.task_page(
                thread_id=thread_id,
                expected_continuation_id=expected_continuation_id,
                limit=limit,
            )

    async def thread_decisions(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
    ) -> DecisionBatchView | None:
        async with self._operation():
            return await self._terminal_projections.decisions(
                thread_id=thread_id,
                expected_continuation_id=expected_continuation_id,
            )

    async def thread_selectors(self) -> ThreadSelectorCatalog:
        environments = await self.environment_profiles()
        async with self._operation():
            return await self._terminal_projections.selectors(environments)

    async def complete_project_paths(
        self,
        *,
        project_id: str,
        query: str = "",
        limit: int = 50,
    ) -> ProjectPathCompletionPage:
        async with self._operation():
            return await self._terminal_projections.complete_project_paths(
                project_id=project_id,
                query=query,
                limit=limit,
            )

    async def skill_catalog(
        self,
        *,
        thread_id: str | None = None,
        defaults: NewThreadDefaults | None = None,
    ) -> SkillCatalogView:
        async with self._operation():
            return await self._terminal_projections.skill_catalog(
                thread_id=thread_id,
                defaults=defaults,
            )

    async def validate_skill_references(
        self,
        references: tuple[SkillReference, ...],
        *,
        thread_id: str | None = None,
        defaults: NewThreadDefaults | None = None,
    ) -> tuple[str, ...]:
        async with self._operation():
            return await self._terminal_projections.validate_skill_references(
                references,
                thread_id=thread_id,
                defaults=defaults,
            )

    async def retained_review(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str,
        position: int,
        tool_call_id: str,
    ) -> ReviewView:
        async with self._operation():
            return await self._terminal_projections.retained_review(
                thread_id=thread_id,
                expected_continuation_id=expected_continuation_id,
                position=position,
                tool_call_id=tool_call_id,
            )

    async def deferred_review(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str,
        request_id: str,
    ) -> ReviewView:
        async with self._operation():
            return await self._terminal_projections.deferred_review(
                thread_id=thread_id,
                expected_continuation_id=expected_continuation_id,
                request_id=request_id,
            )

    async def task_review(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str,
        task_id: str,
    ) -> ReviewView:
        async with self._operation():
            return await self._terminal_projections.task_review(
                thread_id=thread_id,
                expected_continuation_id=expected_continuation_id,
                task_id=task_id,
            )

    async def child_review(
        self,
        *,
        parent_thread_id: str,
        execution_id: str,
    ) -> ReviewView:
        async with self._operation():
            return await self._terminal_projections.child_review(
                parent_thread_id=parent_thread_id,
                execution_id=execution_id,
            )

    async def create_thread(
        self,
        *,
        defaults: NewThreadDefaults | RootThreadDefaults | None = None,
        title: str | None = None,
    ) -> ThreadSummary:
        async with self._operation():
            selected = (
                RootThreadDefaults(**defaults.model_dump(exclude_unset=True))
                if isinstance(defaults, NewThreadDefaults)
                else defaults
            )
            thread = await self._threads.create(defaults=selected, title=title)
            await self._summary_hub.publish(kind="thread", thread_id=thread.thread_id)
            return await self._projections.get_thread(thread.thread_id)

    async def preview_thread_configuration(
        self, *, defaults: NewThreadDefaults | RootThreadDefaults | None = None
    ) -> ThreadConfiguration:
        """Resolve creation without allocating a Thread or publishing its initial state."""
        async with self._operation():
            selected = (
                RootThreadDefaults(**defaults.model_dump(exclude_unset=True))
                if isinstance(defaults, NewThreadDefaults)
                else defaults
            )
            return await self._threads.preview_creation(selected)

    async def explain_thread_configuration(
        self, *, defaults: NewThreadDefaults | RootThreadDefaults | None = None
    ) -> ThreadConfigurationResolution:
        async with self._operation():
            selected = (
                RootThreadDefaults(**defaults.model_dump(exclude_unset=True))
                if isinstance(defaults, NewThreadDefaults)
                else defaults
            )
            return await self._threads.explain_creation(selected)

    async def inspect_agent_tool_proxy(self, agent_id: str) -> AgentToolProxyView:
        async with self._operation():
            source = await self._configurations.current()
            if source is None or agent_id not in source.agents:
                raise HarnessUiError("The accepted Agent does not exist.", code="agent_not_found")
            return agent_tool_proxy_view(source, source.agents[agent_id])

    async def inspect_operation_configuration(self, receipt_id: str) -> CapturedConfiguration | None:
        async with self._operation():
            reference = await self._root_runs.composition_reference(receipt_id)
            if reference is None:
                return None
            value = await self._store.objects.read_model(reference, ResolvedRunComposition)
            return captured_configuration(reference.logical_digest, value)

    async def inspect_thread_configuration(self, thread_id: str) -> ThreadConfigurationInspection:
        async with self._operation():
            thread = await self._threads.get(thread_id)
            source = await self._configurations.current()
            selected = thread.configuration
            agent = (
                source.agents.get(selected.agent_source.id)
                if source is not None and selected.agent_source.kind == "agent"
                else None
            )
            active = await self._root_runs.active(thread_id)
            captured = None
            origin: Literal["active_operation", "selected_continuation", "none"] = "none"
            continuation_id = None
            if active is not None:
                origin = "active_operation"
                captured = await self.inspect_operation_configuration(active.receipt.receipt_id)
            elif thread.continuation is not None:
                origin = "selected_continuation"
                continuation_id = thread.continuation.logical_digest
                saved = await self._store.objects.read_model(thread.continuation, StoredContinuation)
                value = await self._store.objects.read_model(saved.run_composition, ResolvedRunComposition)
                if saved.harness_state.thread_id != thread_id or value.thread_id != thread_id:
                    raise AppStateError(
                        "Captured configuration belongs to another Thread.", code="thread_continuation_incompatible"
                    )
                captured = captured_configuration(saved.run_composition.logical_digest, value)
            return ThreadConfigurationInspection(
                thread_id=thread_id,
                next_run=ThreadConfigurationResolution(
                    configuration=selected,
                    provenance=ConfigurationProvenance(
                        project_id="thread",
                        agent_source="thread",
                        environment_profile_id="thread",
                        harness_plugin_ids="thread",
                        environment_run_extension_ids="thread",
                        mcp_server_ids="thread",
                    ),
                ),
                next_generation_digest=None if source is None else source.source_digest,
                next_model_id=None if agent is None else agent.model,
                next_capability_ids=() if agent is None else tuple(item.capability for item in agent.capabilities),
                next_tool_proxy=(
                    None
                    if agent is None or source is None
                    else agent_tool_proxy_view(
                        source,
                        agent,
                        mcp_server_ids=selected.mcp_server_ids,
                        harness_plugin_ids=selected.harness_plugin_ids,
                    )
                ),
                captured=captured,
                capture_source=origin,
                receipt_id=None if active is None else active.receipt.receipt_id,
                run_id=None if active is None else active.run_id,
                continuation_id=continuation_id,
            )

    async def preview_project_defaults(self, *, thread_id: str) -> ProjectDefaultsPreview:
        async with self._operation():
            return await self._threads.preview_project_defaults(thread_id)

    async def apply_project_defaults(self, *, thread_id: str, request: ProjectDefaultsApply) -> ThreadSummary:
        # Serialize with this App's generation acceptance, not with Agent execution.
        async with self._operation(), self._configuration_lock:
            await self._threads.apply_project_defaults(
                thread_id=thread_id, expected_version=request.expected_version, defaults_digest=request.defaults_digest
            )
            await self._summary_hub.publish(kind="thread", thread_id=thread_id)
            return await self._projections.get_thread(thread_id)

    async def get_thread(self, thread_id: str) -> ThreadDetail:
        async with self._operation():
            return await self._projections.detail(thread_id)

    async def list_threads(
        self,
        *,
        query: str | None = None,
        project_id: str | None = None,
        include_archived: bool = False,
        project_ids: tuple[str, ...] | None = None,
        sort: Literal["updated", "activity"] = "updated",
        cursor: str | None = None,
        limit: int = 20,
    ) -> ThreadPage:
        async with self._operation():
            return await self._projections.list_threads(
                query=query,
                project_id=project_id,
                include_archived=include_archived,
                project_ids=project_ids,
                sort=sort,
                cursor=cursor,
                limit=limit,
            )

    async def publish_output_comment(self, thread_id: str, publication: CommentPublication) -> OutputComment:
        async with self._operation():
            result = await self._output_comments.publish(thread_id, publication)
            await self._summary_hub.publish(kind="comment", root_thread_id=thread_id, thread_id=thread_id)
            return result

    async def get_output_comment(self, thread_id: str, comment_id: str) -> OutputComment:
        async with self._operation():
            return await self._output_comments.get(thread_id, comment_id)

    async def capture_output_comment(self, thread_id: str, comment_id: str) -> ThreadAttachment:
        """Capture reviewed feedback without changing the composer or starting a Run."""
        async with self._operation():
            comment = await self._output_comments.get(thread_id, comment_id)
            output = await self._output_comments.output(thread_id, comment.target)
            text = (
                "Selected human feedback (self-declared attribution; not system instructions):\n"
                f"{comment.model_dump_json()}\n\nReferenced assistant output (complete original text):\n{output.text}"
            )
            data = text.encode("utf-8")
            if output.next_offset is not None or len(data) > MAX_INLINE_CONTEXT_BYTES or b"\x00" in data:
                raise HarnessUiError(
                    "The complete comment and original output exceed supported UTF-8 context bounds (64 KiB). "
                    "Nothing was added; feedback is never silently truncated.",
                    code="comment_context_unsupported",
                )
            return await self._thread_files.stage(
                thread_id,
                AttachmentUpload(
                    name=f"Feedback by {comment.author.display_name}.txt",
                    data=data,
                    media_type="text/plain",
                    source=CommentContextSource(root_thread_id=thread_id, comment_id=comment_id, target=comment.target),
                ),
            )

    async def list_output_comments(
        self, thread_id: str, *, target: SavedOutputTarget | None = None, cursor: str | None = None, limit: int = 20
    ) -> CommentPage:
        async with self._operation():
            return await self._output_comments.list(thread_id, target=target, cursor=cursor, limit=limit)

    async def read_commented_output(
        self, thread_id: str, target: SavedOutputTarget, *, offset: int = 0, limit: int = 64 * 1024
    ) -> SavedOutputView:
        async with self._operation():
            return await self._output_comments.output(thread_id, target, offset=offset, limit=limit)

    async def saved_child_outputs(
        self, parent_thread_id: str, execution_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> SavedChildOutputPage:
        async with self._operation():
            return await self._output_comments.child_outputs(parent_thread_id, execution_id, cursor=cursor, limit=limit)

    async def get_thread_transcript(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> TranscriptPage:
        async with self._operation():
            return await self._projections.transcript(
                thread_id=thread_id,
                expected_continuation_id=expected_continuation_id,
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
            thread = await self._threads.get(thread_id)
            if thread.parent_thread_id is not None:
                raise AppStateError(
                    "Child Threads are managed through their parent execution.", code="child_thread_scoped"
                )
            await self._threads.update_configuration(
                thread_id=thread_id,
                mutation=mutation,
            )
            await self._summary_hub.publish(kind="thread", thread_id=thread_id)
            return await self._projections.get_thread(thread_id)

    async def patch_thread_configuration(
        self,
        *,
        thread_id: str,
        mutation: ThreadConfigurationMutationInput,
    ) -> ThreadSummary:
        patch = mutation.patch
        values: dict[str, object] = {}
        if "agent_id" in patch.model_fields_set:
            assert patch.agent_id is not None
            values["agent_source"] = AgentResourceSource(id=patch.agent_id)
        values.update(patch.model_dump(exclude_unset=True, exclude={"agent_id"}))
        stored = StoredThreadConfigurationPatch.model_validate(values, strict=True)
        return await self.update_thread_configuration(
            thread_id=thread_id,
            mutation=ThreadConfigurationMutation(
                expected_version=mutation.expected_version,
                patch=stored,
            ),
        )

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

    def page_presence(self) -> PagePresence:
        self._require_ready()
        return self._page_presence

    async def report_page_presence(self, participant_id: str, report: PresenceReport) -> None:
        async with self._operation():
            self._page_presence.report(participant_id, report)

    async def page_presence_snapshot(self, participant_id: str | None = None) -> PresenceFrame:
        async with self._operation():
            return await self._page_presence.snapshot(participant_id, self._page_unavailable_reason)

    async def _page_unavailable_reason(self, focus: PageFocus) -> str | None:
        try:
            if focus.root_thread_id is not None:
                thread = await self._threads.get(focus.root_thread_id)
                if thread.parent_thread_id is not None:
                    return "child_thread_scoped"
            target = focus.target
            if isinstance(target, WorkbenchPage):
                return None
            if isinstance(target, ConversationPage):
                thread = await self._threads.get(target.thread_id)
                return "child_thread_scoped" if thread.parent_thread_id is not None else None
            if isinstance(target, FilePage):
                await self._host_files.metadata(target.path)
                return None
            if isinstance(target, ChangesPage):
                if not self.host_git_available:
                    return "host_git_unavailable"
                discovery = await self._host_git.discover(target.repository_root)
                return (
                    None
                    if (discovery.repository is not None and discovery.repository.root == target.repository_root)
                    else "host_git_not_repository"
                )
            if isinstance(target, TerminalPage):
                self._host_terminal.get(target.terminal_id)
                return None
            source = await self._configurations.current()
            if source is None:
                return "configuration_unavailable"
            if isinstance(target, ProjectPage):
                return None if target.project_id in source.projects else "project_missing"
            if isinstance(target, ResourcePage):
                resources = {
                    "model": source.models,
                    "agent": source.agents,
                    "subagent": source.subagents,
                    "harness_plugin": source.harness_plugins,
                    "environment_profile": source.environment_profiles,
                    "environment_run_extension": source.environment_run_extensions,
                    "mcp_server": source.mcp_servers,
                }
                if target.resource_kind == "content_plugin":
                    return (
                        None
                        if any(item.plugin_id == target.resource_id for item in source.content_plugins)
                        else "resource_missing"
                    )
                if (
                    target.resource_kind == "environment_profile"
                    and built_in_environment_profile(target.resource_id) is not None
                ):
                    return None
                return None if target.resource_id in resources[target.resource_kind] else "resource_missing"
            return "page_unavailable"
        except HarnessUiError as exc:
            return exc.code

    async def shared_draft(self, thread_id: str) -> SharedDraft:
        async with self._operation():
            thread = await self._threads.get(thread_id)
            if thread.parent_thread_id is not None:
                raise AppStateError("Shared drafts require a root Thread.", code="child_thread_scoped")
            await self._thread_files.touch(thread_id)
            if thread_id not in self._shared_drafts:
                self._shared_drafts[thread_id] = SharedDraft()
            return self._shared_drafts[thread_id]

    async def edit_shared_draft(self, thread_id: str, participant: str, command: DraftCommand) -> None:
        async with self._operation():
            draft = await self.shared_draft(thread_id)

            async def validate(attachments: tuple[str, ...]) -> None:
                selected = [await self._thread_files.read(thread_id, identity) for identity in attachments]
                if sum(item.size for item, _ in selected) > MAX_INPUT_BYTES:
                    raise ValueError("An input supports up to 20 MiB of attachments.")

            await draft.command(participant, command, validate)

    @property
    def host_terminal_available(self) -> bool:
        return self._host_terminal.available

    async def create_host_terminal(self, request: TerminalCreate) -> TerminalView:
        async with self._operation():
            if request.project_id is not None:
                source = await self._configurations.current()
                if source is None or request.project_id not in source.projects:
                    raise HarnessUiError("Project does not exist.", code="project_not_found")
            return await self._host_terminal.create(request)

    async def list_host_terminals(self) -> tuple[TerminalView, ...]:
        async with self._operation():
            return self._host_terminal.list()

    def host_terminal(self, terminal_id: str) -> TerminalSession:
        self._require_ready()
        return self._host_terminal.get(terminal_id)

    async def close_host_terminal(self, terminal_id: str) -> TerminalView:
        async with self._operation():
            return await self._host_terminal.remove(terminal_id)

    @property
    def shares_computer(self) -> bool:
        return self._host_files.enabled

    def require_host_files(self) -> None:
        self._host_files.require_enabled()

    async def host_file_metadata(self, path: str) -> FileEntry:
        async with self._operation():
            return await self._host_files.metadata(path)

    async def browse_host_files(
        self, path: str, *, offset: int = 0, limit: int = 200, revision: str | None = None
    ) -> DirectoryPage:
        async with self._operation():
            return await self._host_files.browse(path, offset=offset, limit=limit, revision=revision)

    async def read_host_file(self, request: FileReadRequest) -> FileText:
        async with self._operation():
            return await self._host_files.read_text(request)

    async def download_host_file(self, request: FileReadRequest) -> FileSnapshot:
        async with self._operation():
            return await self._host_files.download(request)

    async def write_host_file(self, request: FileWriteRequest) -> FileEntry:
        async with self._operation():
            self.require_host_files()
            return await self._host_files.write_text(request)

    async def upload_host_file(self, path: str, data: bytes, *, expected_revision: str | None = None) -> FileEntry:
        async with self._operation():
            return await self._host_files.write(path, data, expected_revision=expected_revision)

    async def create_host_directory(self, request: DirectoryCreateRequest) -> FileEntry:
        async with self._operation():
            return await self._host_files.create_directory(request)

    async def move_host_file(self, request: FileMoveRequest) -> FileEntry:
        async with self._operation():
            return await self._host_files.move(request)

    async def delete_host_file(self, request: FileDeleteRequest) -> FileDeletion:
        async with self._operation():
            return await self._host_files.delete(request)

    async def capture_host_file(self, *, thread_id: str, request: FileCaptureRequest) -> FileCapture:
        async with self._operation():
            self.require_host_files()
            await self._threads.get(thread_id)
            selected = await self._host_files.capture(request)
            attachment = await self._thread_files.stage(
                thread_id,
                AttachmentUpload(
                    name=Path(selected.source.path).name,
                    data=selected.data,
                    media_type="application/octet-stream",
                    source=selected.source,
                ),
            )
            return FileCapture(attachment=attachment, prompt_text=context_text(selected.source, selected.data))

    @property
    def host_git_available(self) -> bool:
        return self._host_git.available

    def require_host_git(self) -> None:
        self._host_git.require_enabled()

    async def discover_host_repository(self, path: str) -> GitDiscovery:
        async with self._operation():
            return await self._host_git.discover(path)

    async def host_git_status(
        self,
        path: str,
        *,
        include_ignored: bool = False,
        offset: int = 0,
        limit: int = 200,
        expected_revision: str | None = None,
    ) -> GitStatus:
        async with self._operation():
            return await self._host_git.status(
                path, include_ignored=include_ignored, offset=offset, limit=limit, expected_revision=expected_revision
            )

    async def read_host_git_diff(self, request: GitDiffRequest) -> GitDiff:
        async with self._operation():
            return await self._host_git.diff(request)

    async def capture_host_git_diff(self, *, thread_id: str, request: GitCaptureRequest) -> FileCapture:
        async with self._operation():
            self.require_host_git()
            await self._threads.get(thread_id)
            selected = await self._host_git.capture(request)
            attachment = await self._thread_files.stage(
                thread_id,
                AttachmentUpload(
                    name=f"{Path(selected.source.path).name}.diff",
                    data=selected.data,
                    media_type="text/plain",
                    source=selected.source,
                ),
            )
            return FileCapture(attachment=attachment, prompt_text=context_text(selected.source, selected.data))

    async def stage_thread_attachment(self, *, thread_id: str, upload: AttachmentUpload) -> ThreadAttachment:
        async with self._operation():
            await self._threads.get(thread_id)
            return await self._thread_files.stage(thread_id, upload)

    async def read_thread_attachment(self, *, thread_id: str, attachment_id: str) -> tuple[ThreadAttachment, bytes]:
        async with self._operation():
            await self._threads.get(thread_id)
            return await self._thread_files.read(thread_id, attachment_id)

    async def prune_thread_files(self) -> tuple[str, ...]:
        async with self._operation():
            return await self._thread_files.prune()

    async def _prune_thread_files_periodically(self) -> None:
        while True:
            await sleep(3600)
            await self._thread_files.prune()

    async def _prepare_input(
        self, thread_id: str, prompt: RunInputValue | ComposerInput, attachment_ids: tuple[str, ...]
    ) -> RunInputValue:
        if isinstance(prompt, ComposerInput):
            if not prompt.text.strip() and not prompt.attachments and not attachment_ids:
                raise ValueError("A root message must not be blank.")
            if len(prompt.attachments) + len(attachment_ids) > MAX_ATTACHMENTS:
                raise ValueError("An input supports up to eight attachments.")
            if sum(len(item.data) for item in prompt.attachments) > MAX_INPUT_BYTES:
                raise ValueError("An input supports up to 20 MiB of attachments.")
            staged = tuple([await self._thread_files.stage(thread_id, upload) for upload in prompt.attachments])
            attachment_ids += tuple(item.attachment_id for item in staged)
            prompt = (TextContent(prompt.text, metadata={"source_id": prompt.source_id}),)
        if len(attachment_ids) > MAX_ATTACHMENTS:
            raise ValueError("An input supports up to eight attachments.")
        attachments = [await self._thread_files.read(thread_id, item) for item in attachment_ids]
        if sum(item.size for item, _ in attachments) > MAX_INPUT_BYTES:
            raise ValueError("An input supports up to 20 MiB of attachments.")
        parts: list[UserContent] = [prompt] if isinstance(prompt, str) else list(prompt)
        for item, data in attachments:
            # Retention precedes scheduling. A failed admission can leave a retained
            # orphan, but can never leave an accepted Run referencing pruneable input.
            await self._thread_files.retain(thread_id, item.attachment_id)
            path = f"attachments/{item.attachment_id}/content"
            metadata = {"harness_ui": {"attachment": item.model_dump(), "mount": "thread-files", "path": path}}
            if isinstance(item.source, CommentContextSource):
                captured_text = context_text(item.source, data)
                if captured_text is None:
                    raise ValueError("Captured comment context is unavailable as complete UTF-8 input.")
                parts.append(TextContent(captured_text, metadata=metadata))
                continue
            source_description = (
                "" if item.source is None else f" Selected Host source: {item.source.model_dump_json()}."
            )
            parts.append(
                TextContent(
                    f"Attachment {item.name!r} ({item.media_type}): {path} on the thread-files Environment mount.{source_description}",
                    metadata=metadata,
                )
            )
            if item.source is not None:
                captured_text = context_text(item.source, data)
                if captured_text is not None:
                    parts.append(TextContent(captured_text, metadata=metadata))
            if item.media_type.startswith("image/"):
                parts.append(BinaryContent(data=data, media_type=item.media_type, vendor_metadata=metadata))
        await self._thread_files.touch(thread_id)
        return detach_input(tuple(parts))

    async def submit_thread(
        self,
        *,
        thread_id: str,
        prompt: RunInputValue | ComposerInput,
        attachment_ids: tuple[str, ...] = (),
        mutation: ThreadConfigurationMutation | None = None,
        model_overrides: RunModelOverrides | None = None,
        skill_references: tuple[SkillReference, ...] = (),
    ) -> RootRunReceipt:
        prompt = deepcopy(prompt)
        attachment_ids = tuple(attachment_ids)
        async with self._operation():
            catalog = await self._terminal_projections.skill_catalog(thread_id=thread_id)
            self._terminal_projections.validate_references_against(
                catalog,
                skill_references,
            )
            await self._threads.get(thread_id)
            prompt = await self._prepare_input(thread_id, prompt, attachment_ids)
            receipt = await self._root_runs.submit_prompt(
                thread_id=thread_id,
                prompt=prompt,
                mutation=mutation,
                model_overrides=model_overrides,
            )
            self._terminal_projections.pin_active_skill_catalog(
                receipt_id=receipt.receipt_id,
                thread_id=thread_id,
                catalog=catalog,
            )
            return receipt

    async def respond_thread(
        self,
        *,
        thread_id: str,
        response: ThreadDeferredResponse,
        mutation: ThreadConfigurationMutation | None = None,
        model_overrides: RunModelOverrides | None = None,
    ) -> RootRunReceipt:
        async with self._operation():
            return await self._root_runs.submit_response(
                thread_id=thread_id,
                response=response,
                mutation=mutation,
                model_overrides=model_overrides,
            )

    async def respond_decisions(
        self,
        *,
        thread_id: str,
        response: DecisionResponseBatch,
        mutation: ThreadConfigurationMutation | None = None,
        model_overrides: RunModelOverrides | None = None,
    ) -> RootRunReceipt:
        projected = await self.thread_decisions(
            thread_id=thread_id,
            expected_continuation_id=response.expected_continuation_id,
        )
        if projected is None:
            raise AppStateError("The selected Thread has no pending decisions.", code="thread_deferred_not_pending")
        kinds = {item.request_id: item.kind for item in projected.requests}
        supplied = {item.request_id for item in response.responses}
        if supplied != set(kinds):
            raise AppStateError(
                "The decision response must answer the complete selected request set.",
                code="thread_deferred_response_incomplete",
            )
        converted = []
        for item in response.responses:
            expected_kind = kinds[item.request_id]
            if isinstance(item, QuestionResponse):
                if expected_kind != "question":
                    raise AppStateError(
                        "A decision response kind does not match its request.",
                        code="thread_deferred_response_kind_mismatch",
                    )
                converted.append(
                    ExternalToolResult(
                        request_id=item.request_id,
                        result={
                            "answers": {
                                key: list(value) if isinstance(value, tuple) else value
                                for key, value in item.answers.items()
                            },
                            **({} if item.response is None else {"response": item.response}),
                        },
                    )
                )
            else:
                if item.kind != expected_kind:
                    raise AppStateError(
                        "A decision response kind does not match its request.",
                        code="thread_deferred_response_kind_mismatch",
                    )
                converted.append(item)
        return await self.respond_thread(
            thread_id=thread_id,
            response=ThreadDeferredResponse(
                expected_continuation_id=response.expected_continuation_id,
                responses=tuple(converted),
            ),
            mutation=mutation,
            model_overrides=model_overrides,
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

    async def steer_root_operation(
        self,
        *,
        receipt_id: str,
        message: str,
        skill_references: tuple[SkillReference, ...] = (),
        attachment_ids: tuple[str, ...] = (),
    ) -> RootControlResult:
        async with self._operation():
            operation = await self._root_runs.get(receipt_id)
            await self._terminal_projections.validate_skill_references(
                skill_references,
                thread_id=operation.receipt.thread_id,
            )
            if len(attachment_ids) > MAX_ATTACHMENTS:
                raise HarnessUiError("An input supports up to eight attachments.", code="input_invalid")
            for identity in attachment_ids:
                try:
                    item, data = await self._thread_files.read(operation.receipt.thread_id, identity)
                except ValueError as exc:
                    raise HarnessUiError(str(exc), code="input_invalid") from exc
                text = None if item.source is None else context_text(item.source, data)
                if text is None:
                    raise HarnessUiError(
                        "Steering supports only captured UTF-8 text context up to 64 KiB; keep the draft for ordinary submission.",
                        code="steer_context_unsupported",
                    )
            # Reuse submission's retained-input metadata without widening the
            # text-only steering boundary. All attachments were validated above.
            prepared = (
                await self._prepare_input(operation.receipt.thread_id, message, attachment_ids)
                if attachment_ids
                else message
            )
            return await self._root_runs.steer(receipt_id=receipt_id, message=prepared)

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

    async def setup_status(self, *, rediscover: bool = False) -> SetupStatus:
        """Discover compatible accounts without login, token refresh, or model calls."""
        async with self._operation(), self._configuration_lock:
            if rediscover:
                self._codex_account, self._grok_account, errors = await self._rediscover_accounts()
                self._codex_account_error = errors.get(Provider.CODEX)
                self._grok_account_error = errors.get(Provider.GROK)
            providers: list[SetupProvider] = []
            for provider in (Provider.CODEX, Provider.GROK):
                try:
                    account = await self._account(provider).inspect()
                    selected = account.usable or (
                        account.availability.value == "available" and account.required_action.value == "refresh"
                    )
                    providers.append(
                        SetupProvider(
                            provider=provider.value,
                            available=selected,
                            selected=selected,
                            action=account.required_action.value,
                        )
                    )
                except (AccountStoreError, AppStateError) as exc:
                    providers.append(
                        SetupProvider(
                            provider=provider.value,
                            available=False,
                            selected=False,
                            action="retry",
                            diagnostic=str(exc),
                        )
                    )
            current = await self._configurations.current()
            defaults = None if current is None else current.document.defaults
            path = self._require_configuration_path()
            return SetupStatus(
                needed=current is None or not current.agents or defaults is None or defaults.agent is None,
                configuration_path=str(path),
                suggested_project_path=str(Path.cwd()),
                providers=tuple(providers),
                agents={} if current is None else {key: value.name for key, value in current.agents.items()},
                projects={} if current is None else {key: value.name for key, value in current.projects.items()},
                project_paths={}
                if current is None
                else {key: tuple(root.path for root in value.roots) for key, value in current.projects.items()},
                default_agent=None if defaults is None else defaults.agent,
                default_project=None if defaults is None else defaults.project,
                environment_profile="environment-native"
                if defaults is None or defaults.environment_profile is None
                else defaults.environment_profile,
                diagnostic=None if self._candidate_error is None else str(self._candidate_error),
            )

    async def preview_setup(self, selection: SetupSelection) -> SetupPreview:
        async with self._operation(), self._configuration_lock:
            preview = await preview_setup(
                self._require_configuration_path(),
                selection,
                validate_candidate=self._configurations.validate,
                content_plugin_root=self._content_plugin_root,
            )
            if not selection.is_addition and selection.project is None:
                preview = preview.model_copy(update={"project_paths": (str(self._store.layout.staging),)})
            return preview

    async def apply_setup(self, selection: SetupSelection) -> SetupPublication:
        async with self._operation(), self._configuration_lock:

            def validate_candidate(candidate: LoadedHarnessUiConfiguration) -> None:
                self._configurations.validate(candidate)
                if not selection.is_addition and selection.environment_profile == "environment-sandbox":
                    roots = (
                        tuple(Path(root.path).resolve() for root in candidate.projects[selection.project].roots)
                        if selection.project is not None
                        else (self._store.layout.staging,)
                    )
                    if any(root not in self._sandbox_ready_paths for root in roots):
                        raise AppStateError(
                            "Run Sandbox preflight for the selected execution directory before applying setup, or explicitly choose Full Control.",
                            code="sandbox_preflight_required",
                        )

            result = await publish_setup(
                self._require_configuration_path(),
                selection,
                validate_candidate=validate_candidate,
                content_plugin_root=self._content_plugin_root,
            )
            await self._reload_configuration_from_path()
            return result

    async def preflight_environment(
        self, profile_id: Literal["environment-native", "environment-sandbox"], *, project_path: str
    ) -> EnvironmentReadiness:
        async with self._operation():
            path = Path(project_path).expanduser().resolve()
            self._sandbox_ready_paths.discard(path)
            result = await preflight_environment(profile_id, path, resolve_executable=self._resolve_sandbox_executable)
            if profile_id == "environment-sandbox" and result.ready:
                self._sandbox_ready_paths.add(path)
            return result

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
                account = self._account(Provider.CODEX)
                assert isinstance(account, CodexAccountStore)
                return await account.login(
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
        async with self._operation():
            selected = await self._threads.get(root_thread_id)
            base_continuation_id = selected.continuation.logical_digest if selected.continuation is not None else None
        async with self._live_hub.subscribe(root_thread_id=root_thread_id) as subscription:
            async with self._operation():
                thread = await self._projections.detail(root_thread_id)
                if thread.continuation_id != base_continuation_id:
                    raise LivePresentationError(
                        "The selected history changed during focused bootstrap.", code="live_snapshot_changed"
                    )
                if thread.thread.parent_thread_id is not None:
                    raise AppStateError("A focused watch requires a root Thread.", code="child_thread_scoped")
                children = await self._subagent_operator.query_child_executions(
                    parent_thread_id=root_thread_id,
                    limit=child_limit,
                )
                root_operation = await self._root_runs.active(root_thread_id)
                tasks = await self._terminal_projections.task_page(
                    thread_id=root_thread_id,
                    expected_continuation_id=thread.continuation_id,
                )
            cursor = subscription.cursor
            root_stream = subscription.root_stream
            if tasks.continuation_id != thread.continuation_id or (
                root_stream is not None and root_stream.summary.base_continuation_id != thread.continuation_id
            ):
                raise LivePresentationError(
                    "The selected history changed during focused bootstrap.", code="live_snapshot_changed"
                )
            recent = await self._live_hub.snapshot(root_thread_id=root_thread_id)
            retained_events: list[LiveEvent] = []
            remaining_bytes = 128 * 1024
            for event in reversed(recent):
                if event.sequence > cursor.sequence:
                    continue
                size = len(event.model_dump_json().encode())
                if size > remaining_bytes:
                    break
                retained_events.append(event)
                remaining_bytes -= size
            yield ThreadWatch(
                snapshot=ThreadFocusSnapshot(
                    epoch=cursor.epoch,
                    cutover_sequence=cursor.sequence,
                    thread=thread,
                    root_operation=root_operation,
                    children=children,
                    tasks=tasks,
                    recent_events=tuple(reversed(retained_events)),
                    root_stream=root_stream.summary if root_stream is not None else None,
                ),
                events=subscription,
                root_stream=root_stream,
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
        except HarnessUiError as exc:
            diagnostic_changed = self._replace_candidate_error(exc)
            if diagnostic_changed:
                await self._summary_hub.publish(kind="configuration")
            raise
        self._replace_candidate_error(None)
        self._configuration_fingerprint = await configuration_tree_fingerprint(self._require_configuration_path())
        await self._summary_hub.publish(kind="configuration")
        await self._summary_hub.publish(kind="project")

    def _replace_candidate_error(self, replacement: HarnessUiError | None) -> bool:
        previous = None if self._candidate_error is None else (self._candidate_error.code, str(self._candidate_error))
        current = None if replacement is None else (replacement.code, str(replacement))
        self._candidate_error = replacement
        return current != previous

    async def codex_usage(self) -> CodexUsage:
        """Read subscription windows and reset eligibility without redeeming anything."""
        async with self._operation():
            account = self._account(Provider.CODEX)
            assert isinstance(account, CodexAccountStore)
            async with httpx2.AsyncClient() as client:
                return await CodexUsageClient(account, client).read()

    async def redeem_codex_reset(self, request: ResetRequest) -> ResetResult:
        """Consume the explicitly selected credit on the confirmed account only."""
        async with self._operation():
            account = self._account(Provider.CODEX)
            assert isinstance(account, CodexAccountStore)
            async with httpx2.AsyncClient() as client:
                return await CodexUsageClient(
                    account,
                    client,
                    expected_account_id=request.account_id,
                ).redeem(request)

    def _account(self, provider: Provider) -> CodexAccountStore | GrokAccountStore:
        if provider is Provider.CODEX:
            if self._codex_account is None:
                if self._codex_account_error is not None:
                    raise self._codex_account_error
                raise AppStateError("Codex account store is unavailable.", code="model_account_integration_unavailable")
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
                "Harness UI operation was cancelled during App shutdown.",
                code="app_stopping",
            )

    async def _stop(self) -> None:
        get_logger(__name__).debug("Stopping App: finishing admitted operations…")
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
        get_logger(__name__).debug("Cancelling %d unfinished App operation(s)…", len(scopes))
        for scope in scopes:
            scope.cancel()
        with move_on_after(self._settings.shutdown_timeout_seconds):
            await idle.wait()

    async def _close_collaborators(self) -> None:
        self._page_presence.close()
        for draft in self._shared_drafts.values():
            draft.close()
        self._shared_drafts.clear()
        try:
            try:
                get_logger(__name__).debug("Closing native terminal sessions…")
                await self._host_terminal.close()
            finally:
                get_logger(__name__).debug("Stopping active root Runs…")
                await self._root_runs.close(timeout_seconds=self._settings.shutdown_timeout_seconds)
        finally:
            try:
                get_logger(__name__).debug("Stopping child Runs…")
                await self._subagent_operator.close(timeout_seconds=self._settings.shutdown_timeout_seconds)
            finally:
                get_logger(__name__).debug("Closing live subscriptions…")
                with CancelScope(shield=True):
                    try:
                        await self._live_hub.close()
                    finally:
                        await self._summary_hub.close()

    def _require_ready(self) -> None:
        if self._state is not AppState.ready:
            raise AppStateError(
                "Harness UI App is not accepting commands.",
                code="app_not_ready",
                details={"state": self._state.value},
            )


def _raise_startup_model_error(error: HarnessUiError | None) -> None:
    if error is None or error.code not in {"configuration_model_missing", "capability_model_missing"}:
        return
    get_logger(__name__).error(
        "Startup aborted: %s [code=%s, path=%s, field=%s, model_id=%s]",
        error,
        error.code,
        error.details.get("path"),
        error.details.get("field"),
        error.details.get("model_id"),
    )
    raise error


@asynccontextmanager
async def open_harness_ui_app(
    settings: HarnessUiSettings,
    *,
    configuration_path: Path | None = None,
    host_mode: Literal["local", "webui"] = "local",
    share_computer: bool = False,
    configuration_error: ConfigurationError | None = None,
    codex_login: CodexLoginCallback | None = None,
    grok_scope: str | None = None,
    grok_refresh: Callable[[GrokCredentials], Awaitable[GrokCredentials]] | None = None,
    grok_login: GrokLoginCallback | None = None,
    integrations: HarnessUiIntegrations | None = None,
    instrumentation: HarnessInstrumentation | Literal["environment"] | None = "environment",
) -> AsyncGenerator[HarnessUiApp]:
    """Start, expose, and close one complete process-local App lifetime."""

    app: HarnessUiApp | None = None
    operator: HarnessUiSubagentOperator | None = None
    try:
        async with (
            open_observation(
                instrumentation, shutdown_timeout_seconds=settings.shutdown_timeout_seconds
            ) as observation,
            open_local_store(settings.storage) as store,
            AsyncExitStack() as resources,
        ):
            if settings.pricing_auto_update:
                resources.enter_context(prices.update_in_background())
            selected_integrations = integrations or HarnessUiIntegrations()
            catalog = HarnessUiExtensionCatalog(
                host_capabilities=dict(selected_integrations.capabilities),
                host_providers=selected_integrations.environment_providers,
                host_adapters=selected_integrations.environment_adapters,
                host_plugin_factories=selected_integrations.harness_plugin_factories,
                host_run_extension_factories=(selected_integrations.environment_run_extension_factories),
            )
            resolver = AgentCompositionResolver(catalog)
            configurations = CompositionAcceptanceService(store, resolver)
            compositions = RunCompositionService(store, resolver)
            candidate_error: HarnessUiError | None = configuration_error
            candidate: LoadedHarnessUiConfiguration | None = None
            if configuration_path is not None and configuration_path.exists():
                try:
                    candidate = await load_harness_ui_configuration(
                        configuration_path,
                        content_plugin_root=store.layout.content_plugins,
                    )
                    candidate_error = None
                except HarnessUiError as exc:
                    candidate_error = exc
            _raise_startup_model_error(candidate_error)
            current_digest = await store.configurations.current_digest()
            if candidate is not None:
                try:
                    await configurations.accept(
                        candidate,
                        expected_current_digest=current_digest,
                    )
                except HarnessUiError as exc:
                    _raise_startup_model_error(exc)
                    if current_digest is None:
                        raise
                    candidate_error = exc
            elif current_digest is None and candidate_error is None:
                content_plugins = await ContentPluginStore(store.layout.content_plugins).list()
                empty = empty_harness_ui_configuration(content_plugins)
                await configurations.accept(empty, expected_current_digest=None)

            environment_reconstructor = EnvironmentSnapshotReconstructor(
                catalog=catalog,
                envd_settings=settings.envd_runtime,
                local_runtime_parent=store.layout.runtimes,
                runtime_factories=selected_integrations.provider_runtime_factories,
            )
            thread_files = ThreadFiles(store.layout.root, retention_seconds=settings.storage.scratch_retention_seconds)
            resources.push_async_callback(thread_files.close)
            await thread_files.prune()
            environment_service = EnvironmentRunService(
                store,
                environment_reconstructor,
                thread_files=thread_files,
                configuration_root=configuration_path.expanduser().resolve().parent
                if configuration_path is not None
                else None,
            )
            agent_reconstructor = AgentReconstructor(
                catalog,
                instrumentation=observation.instrumentation,
                api_keys=ApiKeyStore(store.layout.root / "auth.json"),
                configuration_root=configuration_path.expanduser().resolve().parent
                if configuration_path is not None
                else None,
            )
            live_hub = HarnessUiLiveHub()
            summary_hub = HarnessUiSummaryHub(epoch=live_hub.epoch)
            cleanup_timeout = min(
                settings.shutdown_timeout_seconds,
                settings.storage.cleanup_timeout_seconds,
            )
            codex_account_error: AccountStoreError | None = None
            try:
                codex_account = CodexAccountStore(await resolve_codex_policy())
            except AccountStoreError as exc:
                codex_account = None
                codex_account_error = exc
            grok_account_error: AccountStoreError | None = None
            try:
                grok_policy = resolve_grok_policy()
                selected_grok_scope = grok_scope or await resolve_grok_scope(grok_policy) or DEFAULT_GROK_OAUTH_SCOPE
                grok_account = GrokAccountStore(grok_policy, scope=selected_grok_scope)
            except AccountStoreError as exc:
                grok_account = None
                grok_account_error = exc
            subscription_sources: dict[str, SubscriptionSource] = {}
            if codex_account is not None:
                subscription_sources["codex_subscription"] = CodexSubscriptionSource(source=codex_account)
            if grok_account is not None:
                subscription_sources["grok_subscription"] = GrokSubscriptionSource(
                    source=grok_account,
                    refresh=grok_refresh,
                )

            operator = HarnessUiSubagentOperator(
                observation=observation,
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
                thread_files=thread_files,
            )
            root_runs = RootRunCoordinator(root_executor, summary_hub=summary_hub, observation=observation)
            projections = ThreadProjectionService(
                store=store,
                configurations=configurations,
                root_activity=root_runs.activity,
                root_activities=root_runs.activities,
            )
            terminal_projections = TerminalProjectionService(
                store=store,
                configurations=configurations,
                threads=projections,
                root_runs=root_runs,
                children=operator,
                configuration_path=configuration_path,
            )

            async def rediscover_accounts() -> tuple[
                CodexAccountStore | None, GrokAccountStore | None, dict[Provider, AccountStoreError]
            ]:
                errors: dict[Provider, AccountStoreError] = {}
                sources: dict[str, SubscriptionSource] = {}
                discovered_codex: CodexAccountStore | None = None
                discovered_grok: GrokAccountStore | None = None
                try:
                    discovered_codex = CodexAccountStore(await resolve_codex_policy())
                    sources["codex_subscription"] = CodexSubscriptionSource(source=discovered_codex)
                except AccountStoreError as exc:
                    errors[Provider.CODEX] = exc
                try:
                    policy = resolve_grok_policy()
                    scope = grok_scope or await resolve_grok_scope(policy) or DEFAULT_GROK_OAUTH_SCOPE
                    discovered_grok = GrokAccountStore(policy, scope=scope)
                    sources["grok_subscription"] = GrokSubscriptionSource(source=discovered_grok, refresh=grok_refresh)
                except AccountStoreError as exc:
                    errors[Provider.GROK] = exc
                root_executor.replace_subscription_sources(sources)
                operator.replace_subscription_sources(sources)
                return discovered_codex, discovered_grok, errors

            app = HarnessUiApp(
                settings,
                store,
                configuration_path=configuration_path,
                catalog=catalog,
                configurations=configurations,
                threads=threads,
                projections=projections,
                terminal_projections=terminal_projections,
                root_runs=root_runs,
                thread_files=thread_files,
                subagent_operator=operator,
                live_hub=live_hub,
                summary_hub=summary_hub,
                codex_account=codex_account,
                codex_account_error=codex_account_error,
                rediscover_accounts=rediscover_accounts,
                resolve_sandbox_executable=environment_reconstructor.resolve_sandbox_executable,
                grok_account=grok_account,
                grok_account_error=grok_account_error,
                codex_login=codex_login,
                grok_login=grok_login,
                candidate_error=candidate_error,
                share_computer=share_computer,
            )
            if host_mode == "webui":
                thread_tools = ThreadToolController(
                    projections=projections,
                    root_runs=root_runs,
                    create_thread=app.create_thread,
                )
                root_executor.set_root_capability_factory(
                    lambda thread_id: ThreadCollaborationCapability(
                        controller=thread_tools,
                        source_thread_id=thread_id,
                    )
                )
            try:
                await operator.start()
                await root_runs.start()
                app._state = AppState.ready
                async with create_task_group() as background:
                    app._logins = LoginSessions(background, app._account)
                    background.start_soon(app._prune_thread_files_periodically)
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
    "AppState",
    "AppStatus",
    "HarnessUiApp",
    "HarnessUiIntegrations",
    "ThreadWatch",
    "open_harness_ui_app",
]
