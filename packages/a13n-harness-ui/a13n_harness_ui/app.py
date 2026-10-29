"""Process-local application boundary shared by Harness UI surfaces."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import StrEnum
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import httpx2
from a13n_envd_client.eip.v1 import DirectoryListResult
from a13n_envd_client.websocket import WebSocketConnection
from a13n_harness import HarnessInstrumentation
from a13n_harness.content import ContentItem, ContentMetadata
from a13n_harness.environment import EnvironmentRunExtensionFactory
from a13n_harness.http import outbound_tls_verify
from a13n_harness.input import RunInputValue
from a13n_harness.model_catalog_updates import run_official_model_updates
from a13n_harness.models.codex_live import CodexLiveModel
from a13n_harness.plugin_factories import HarnessPluginFactory
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.environment.remote_envd.pairing import PairingChallenge, PairingRequest, PairingResponse
from a13n_harness.providers.memory import DirectoryFileStore, MemoryStoreError
from a13n_harness.providers.model.oauth import GrokCredentials
from a13n_harness.usage import RunUsageSummary
from a13n_logging import get_logger
from anyio import CancelScope, Event, Lock, create_task_group, move_on_after, sleep, to_thread
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from pydantic_ai import BinaryContent, prices
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import TextContent, UserContent

from a13n_harness_ui.browser import (
    BrowserAccess,
    BrowserAccessRequest,
    BrowserCommand,
    BrowserControl,
    BrowserState,
    BrowserViewportRequest,
    HostBrowsers,
)
from a13n_harness_ui.browser_setup import BrowserScan, BrowserSetup, scan_browsers
from a13n_harness_ui.compaction import BackgroundCompaction
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
from a13n_harness_ui.configuration.mcp_tools import (
    McpToolDescriptor,
    McpToolSelection,
    McpToolSettings,
    discover_tools,
    selection_mutation,
    tool_settings,
)
from a13n_harness_ui.configuration.models import BrowserConfiguration, DeviceResource, PairedDeviceAuthentication
from a13n_harness_ui.configuration.mutation import validate_configuration_source
from a13n_harness_ui.configuration.setup import (
    SetupPreview,
    SetupPublication,
    SetupSelection,
    preview_setup,
    publish_setup,
)
from a13n_harness_ui.configuration.views import (
    AgentToolSearchView,
    ConfigurationSourceCatalog,
    ConfigurationSourceView,
    ConfigurationValidation,
    agent_tool_search_view,
    source_catalog,
    source_view,
)
from a13n_harness_ui.configuration_inspection import (
    CapturedConfiguration,
    ThreadConfigurationInspection,
    captured_configuration,
)
from a13n_harness_ui.content_plugins import ContentPluginStore
from a13n_harness_ui.device_pairing import DevicePairings
from a13n_harness_ui.devices import DeviceAttachment, DeviceConnections, DeviceInfo, DeviceSummary
from a13n_harness_ui.envd_runtime import EnvdRuntimeManager, EnvdStatus
from a13n_harness_ui.environment_bindings import EnvironmentSelectionPatch
from a13n_harness_ui.environment_monitoring import (
    EnvironmentMonitoring,
    MonitoringCatalog,
    MonitoringDetail,
    MonitoringSource,
)
from a13n_harness_ui.environment_profiles import (
    BUILT_IN_ENVIRONMENT_PROFILES,
    LocalProfileId,
    built_in_environment_profile,
)
from a13n_harness_ui.environment_runtime import (
    EnvironmentRunService,
    EnvironmentSnapshotReconstructor,
    ProviderRuntimeFactory,
)
from a13n_harness_ui.errors import (
    AppStateError,
    ConfigurationError,
    EnvironmentLifecycleError,
    HarnessUiError,
    LivePresentationError,
    RunCoordinationError,
    ThreadError,
)
from a13n_harness_ui.extensions import (
    CatalogReference,
    EnvironmentProjectAdapter,
    HarnessUiExtensionCatalog,
)
from a13n_harness_ui.file_context import (
    context_text,
)
from a13n_harness_ui.goal import GoalMode, GoalView
from a13n_harness_ui.host_files import (
    DirectoryCreateRequest,
    DirectoryPage,
    FileCapture,
    FileCaptureRequest,
    FileDeleteRequest,
    FileDeletion,
    FileEntry,
    FileInfo,
    FileMoveRequest,
    FileReadRequest,
    FileStream,
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
    LiveSubscription,
    RootStreamReplay,
    SummaryCursor,
    SummarySubscription,
)
from a13n_harness_ui.mcp_apps.connections import Connections
from a13n_harness_ui.mcp_apps.context import AppContext, AppContextReference, AppContextUpdate
from a13n_harness_ui.mcp_apps.messages import AppMessageReceipt, AppMessageRequest
from a13n_harness_ui.mcp_apps.models import AppPresentation, AppReference
from a13n_harness_ui.mcp_apps.operations import AppOperation, AppOperations, AppToolRequest, AppView
from a13n_harness_ui.mcp_apps.owners import CurrentOwners
from a13n_harness_ui.mcp_apps.resources import AppResourceRequest
from a13n_harness_ui.mcp_apps.snapshots import AppSnapshots
from a13n_harness_ui.mcp_runtime.connections import Connections as HostConnections
from a13n_harness_ui.mcp_runtime.inputs import Inputs, McpInputRequestView, McpInputResponse, McpIntegrationView
from a13n_harness_ui.memory import MemoryOrganizationRun, memory_scopes
from a13n_harness_ui.memory_organization import MemoryOrganizationStatus, MemoryOrganizer
from a13n_harness_ui.model_accounts import (
    AccountProjection,
    AccountStoreError,
    CodexAccountStore,
    CodexLoginCallback,
    GrokAccountStore,
    GrokLoginCallback,
    Provider,
)
from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput, ApiKeyStatus, ApiKeyStore

if TYPE_CHECKING:
    from a13n_harness_ui.model_accounts.chatgpt import ChatGPTAccountStore

from a13n_harness.providers.model.oauth.models import ModelAuthenticationError

from a13n_harness_ui.model_accounts.codex import BoundCodexCredentialSource
from a13n_harness_ui.model_accounts.copilot import CopilotAccountStore, CopilotLoginCallback
from a13n_harness_ui.model_accounts.login import LoginRequest, LoginSessions, LoginStatus
from a13n_harness_ui.model_accounts.managed import SubscriptionAccount, SubscriptionAccounts
from a13n_harness_ui.model_accounts.models import AccountCandidate, AccountSelection
from a13n_harness_ui.model_accounts.provider_usage import CopilotUsage, read_copilot_usage
from a13n_harness_ui.model_accounts.subscriptions import (
    SubscriptionAccountCreate,
    SubscriptionAccountUpdate,
    SubscriptionAccountView,
    SubscriptionCatalog,
)
from a13n_harness_ui.model_accounts.usage import CodexUsage, CodexUsageClient, ResetRequest, ResetResult
from a13n_harness_ui.model_accounts.usage_cache import SubscriptionUsage, UsageSnapshot
from a13n_harness_ui.model_authoring import (
    ModelChoice,
    ModelChoices,
    ModelOptions,
    ModelOptionsRequest,
    ModelRecipe,
    ModelRecipeRequest,
    model_options,
    prepare_model,
)
from a13n_harness_ui.model_catalog import ModelCatalog, ModelCatalogSnapshot
from a13n_harness_ui.model_runtime import (
    ChatGPTSubscriptionSource,
    CopilotSubscriptionSource,
    ManagedSubscriptionSource,
    SubscriptionSource,
)
from a13n_harness_ui.observation import open_observation
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
from a13n_harness_ui.push_models import PushConfiguration, PushSubscriptionInput, PushSubscriptionView, PushTestResult
from a13n_harness_ui.restart import GracefulRestart
from a13n_harness_ui.restart_recovery import recover_restart
from a13n_harness_ui.root_execution import RootRunExecutor
from a13n_harness_ui.root_input import append_surface_hint, detach_input
from a13n_harness_ui.root_run import RootRunCoordinator
from a13n_harness_ui.saved_output_models import SavedChildOutputPage, SavedOutputTarget, SavedOutputView
from a13n_harness_ui.saved_outputs import SavedOutputs
from a13n_harness_ui.settings import HarnessUiSettings
from a13n_harness_ui.setup import (
    EnvironmentReadiness,
    SetupProvider,
    SetupStatus,
    preflight_environment,
)
from a13n_harness_ui.shared_drafts import DraftCapture, DraftCommand, DraftSummary, SharedDraft
from a13n_harness_ui.sharing import Sharing
from a13n_harness_ui.skill_input import prepare_skill_input
from a13n_harness_ui.storage import (
    AgentResourceSource,
    LocalStore,
    ThreadConfiguration,
    ThreadConfigurationMutation,
    open_local_store,
)
from a13n_harness_ui.storage import (
    ThreadConfigurationPatch as StoredThreadConfigurationPatch,
)
from a13n_harness_ui.storage.shares import ShareRepository
from a13n_harness_ui.storage.usage import ThreadUsageView
from a13n_harness_ui.storage.usage_analytics import UsageAnalyticsView, UsageQuery
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
    MemoryFileEntry,
    MemoryFileText,
    NewThreadDefaults,
    NotePage,
    ProjectDefaultsApply,
    ProjectDefaultsPreview,
    ProjectPathCompletionPage,
    ProjectSummary,
    QuestionResponse,
    ReviewView,
    RootControlResult,
    RootOperationStatus,
    RootOperationView,
    RootRunReceipt,
    RunModelOverrides,
    SkillCatalogView,
    SkillReference,
    TaskPage,
    ThreadActivityPage,
    ThreadActivityView,
    ThreadConfigurationMutationInput,
    ThreadConfigurationResolution,
    ThreadDeferredResponse,
    ThreadDetail,
    ThreadFocusSnapshot,
    ThreadMetadataMutation,
    ThreadPage,
    ThreadSelectorCatalog,
    ThreadSummary,
    ThreadWork,
    ToolExecutionView,
    TranscriptInputPage,
    TranscriptPage,
)
from a13n_harness_ui.terminal_projection import TerminalProjectionService
from a13n_harness_ui.thread_capability import ThreadCollaborationCapability, ThreadToolController
from a13n_harness_ui.thread_files import (
    MAX_ATTACHMENTS,
    MAX_INPUT_BYTES,
    AttachmentUpload,
    ComposerAttachmentReference,
    ComposerInput,
    ThreadAttachment,
    ThreadFiles,
)
from a13n_harness_ui.thread_projection import ThreadProjectionService
from a13n_harness_ui.thread_service import RootThreadDefaults, ThreadService
from a13n_harness_ui.thread_work import ThreadWorkService
from a13n_harness_ui.tool_executions import ToolExecutions, execution_view
from a13n_harness_ui.voice import VoiceController
from a13n_harness_ui.web_push import WebPush


@dataclass(frozen=True, slots=True)
class HarnessUiIntegrations:
    """Explicit trusted Host registrations fixed for one App lifetime."""

    capabilities: Mapping[str, type[AbstractCapability[Any]]] = field(default_factory=dict)
    environment_providers: tuple[EnvironmentProviderDefinition, ...] = ()
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
    memory_organization: MemoryOrganizationStatus | None = None


@dataclass(frozen=True, slots=True)
class ThreadWatch:
    snapshot: ThreadFocusSnapshot
    events: LiveSubscription
    root_stream: RootStreamReplay | None = None


def _cwd_project_ids(source: LoadedHarnessUiConfiguration, directory: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            project.id for project in source.projects.values() if project.roots and project.roots[0].path == directory
        )
    )


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
        work: ThreadWorkService,
        root_runs: RootRunCoordinator,
        tool_executions: ToolExecutions,
        thread_files: ThreadFiles,
        subagent_operator: HarnessUiSubagentOperator,
        live_hub: HarnessUiLiveHub,
        summary_hub: HarnessUiSummaryHub,
        copilot_account: CopilotAccountStore,
        codex_account: CodexAccountStore | None,
        codex_account_error: AccountStoreError | None,
        rediscover_accounts: Callable[
            [], Awaitable[tuple[CodexAccountStore | None, GrokAccountStore | None, dict[Provider, AccountStoreError]]]
        ],
        resolve_sandbox_executable: Callable[[], Awaitable[Path]],
        envd: EnvdRuntimeManager,
        devices: DeviceConnections,
        environment_monitoring: EnvironmentMonitoring,
        grok_account: GrokAccountStore | None,
        grok_account_error: AccountStoreError | None,
        codex_login: CodexLoginCallback | None,
        grok_login: GrokLoginCallback | None,
        copilot_login: CopilotLoginCallback | None,
        candidate_error: HarnessUiError | None = None,
        browsers: HostBrowsers | None = None,
        share_computer: bool = False,
        web_push: WebPush | None = None,
        restart_coordinator: GracefulRestart,
        memory_organizer: MemoryOrganizer,
        mcp_apps: AppSnapshots | None = None,
        mcp_operations: AppOperations | None = None,
        mcp_connections: HostConnections | None = None,
        mcp_inputs: Inputs | None = None,
        compaction: BackgroundCompaction | None = None,
    ) -> None:
        self._browsers = browsers
        if browsers is not None:
            browsers.handback.deliver = self._deliver_browser_handback
        self._compaction = compaction
        self._settings = settings
        self._mcp_apps = mcp_apps
        self._mcp_connections = mcp_connections or (mcp_apps.connections if mcp_apps else None)
        self._mcp_inputs = mcp_inputs
        self._mcp_operations = mcp_operations
        self._mcp_app_owners = (
            mcp_operations.owners
            if mcp_operations is not None
            else CurrentOwners(
                store, configurations, AgentCompositionResolver(catalog, host_mode="webui" if mcp_apps else "local")
            )
        )
        self._restart = restart_coordinator
        self._memory_organizer = memory_organizer
        self._voice: VoiceController | None = None
        self._web_push = web_push
        self._store = store
        self.sharing = Sharing(self, ShareRepository(store.database.sessions))
        self._saved_outputs = SavedOutputs(store)
        self._api_keys = ApiKeyStore(store.layout.root / "auth.json")
        self._model_catalog = ModelCatalog()
        self._logins: LoginSessions | None = None
        self._configuration_path = configuration_path
        self._content_plugin_root = store.layout.content_plugins
        self._catalog = catalog
        self._configurations = configurations
        self._threads = threads
        self._projections = projections
        self._terminal_projections = terminal_projections
        self._work = work
        self._thread_files = thread_files
        self._host_files = HostFiles(enabled=share_computer)
        self._host_git = HostGit(enabled=share_computer)
        self._host_terminal = HostTerminal(enabled=share_computer)
        self._shared_drafts: dict[str, SharedDraft] = {}
        self._page_presence = PagePresence()
        self._root_runs = root_runs
        self._tool_executions = tool_executions
        tool_executions.deliver = self._deliver_tool_completion
        tool_executions.changed = self._tool_execution_changed
        self._subagent_operator = subagent_operator
        self._live_hub = live_hub
        self._summary_hub = summary_hub
        self._copilot_account = copilot_account
        self._copilot_login = copilot_login
        self.subscription_accounts = SubscriptionAccounts(store.layout.root)
        self.subscription_usage = SubscriptionUsage(self.subscription_accounts)
        self._codex_account = codex_account
        from a13n_harness_ui.model_accounts.chatgpt import ChatGPTAccountStore

        self._chatgpt_account = ChatGPTAccountStore(store.layout.root / "auth.json")
        self._codex_account_error = codex_account_error
        self._rediscover_accounts = rediscover_accounts
        self._resolve_sandbox_executable = resolve_sandbox_executable
        self._envd = envd
        self._devices = devices
        self._environment_monitoring = environment_monitoring
        self._device_pairings = DevicePairings()
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

    def voice(self) -> VoiceController:
        if self._voice is None:
            raise HarnessUiError("Live is available in WebUI with a configuration directory.", code="live_unavailable")
        return self._voice

    async def _voice_model(self, source: LoadedHarnessUiConfiguration) -> tuple[CodexLiveModel, str]:
        from a13n_harness_ui.model_accounts.routing import account_order, choose_account

        assert source.document.live is not None and source.document.live.model is not None
        voice = source.document.live.voice
        if voice == "marin" or voice == "cedar":
            raise ThreadError(
                "Choose cove or another Codex V3 voice in Live settings before starting voice.",
                code="live_voice_incompatible",
            )
        recipe = AgentCompositionResolver().model_recipe(source.models[source.document.live.model], source)
        group = recipe.subscription_group
        assert group is not None
        account = await choose_account(
            group,
            self.subscription_accounts,
            self.subscription_usage,
            recipe.route.partition(":")[2],
            account_order(group),
        )
        store = self.subscription_accounts.store(account)
        assert isinstance(store, CodexAccountStore)
        return (
            CodexLiveModel(
                BoundCodexCredentialSource(store, account_id=account.upstream_account_id),
                voice=voice,
                configuration=source.document.run_configuration,
            ),
            account.id,
        )

    def _push(self) -> WebPush:
        if self._web_push is None:
            raise HarnessUiError("Web Push is only available in WebUI mode.", code="push_unavailable")
        return self._web_push

    async def push_configuration(self) -> PushConfiguration:
        async with self._operation():
            return await self._push().configuration()

    async def subscribe_push(self, subscription: PushSubscriptionInput) -> PushSubscriptionView:
        async with self._operation():
            return PushSubscriptionView(subscription_id=await self._push().repository.save(subscription))

    async def record_push_activity(self, subscription_id: str) -> None:
        async with self._operation():
            if not await self._push().repository.mark_active(subscription_id):
                raise HarnessUiError("Push subscription not found.", code="not_found")

    async def unsubscribe_push(self, subscription_id: str) -> None:
        async with self._operation():
            await self._push().repository.remove(subscription_id)

    async def test_push(self, subscription_id: str) -> PushTestResult:
        async with self._operation():
            return await self._push().test(subscription_id)

    async def start_login(self, request: LoginRequest) -> LoginStatus:
        async with self._operation():
            if self._logins is None:
                raise AppStateError("Interactive login is unavailable.", code="login_unavailable")
            if request.provider in {"codex", "grok"}:
                account_id = request.account_id or f"acct-{request.provider}-default"
                account = await self.subscription_accounts.get(account_id)
                if account.provider != request.provider:
                    raise AppStateError(
                        "The subscription provider does not match this account.", code="account_provider_mismatch"
                    )
                return self._logins.start(
                    request.model_copy(update={"account_id": account_id}),
                    account=self.subscription_accounts.store(account),
                )
            if request.account_id is not None:
                raise AppStateError("This provider does not use subscription groups.", code="account_provider_mismatch")
            return self._logins.start(request)

    async def active_login(self) -> LoginStatus | None:
        async with self._operation():
            return self._logins.active() if self._logins is not None else None

    async def login_status(self, session_id: str) -> LoginStatus:
        async with self._operation():
            if self._logins is None:
                raise AppStateError("Interactive login is unavailable.", code="login_unavailable")
            return self._logins.status(session_id)

    async def submit_login_callback(self, session_id: str, callback_url: str) -> LoginStatus:
        async with self._operation():
            if self._logins is None:
                raise AppStateError("Interactive login is unavailable.", code="login_unavailable")
            return self._logins.submit_callback(session_id, callback_url)

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
                memory_organization=self._memory_organizer.status(configuration),
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
            published = await self.subscription_accounts.publish_default_groups(
                path.parent,
                frozenset(
                    model.authentication.kind.removesuffix("_subscription")
                    for model in candidate.models.values()
                    if model.authentication.kind in {"codex_subscription", "grok_subscription"}
                ),
                existing_groups=frozenset(candidate.subscription_groups),
            )
            if published:
                candidate = await load_harness_ui_configuration(path, content_plugin_root=self._content_plugin_root)
            current = await self._store.configurations.current_digest()
            if candidate.source_digest != current:
                await self._configurations.accept(
                    candidate,
                    expected_current_digest=current,
                )
                generation_changed = True
                self._memory_organizer.configuration_changed(candidate)
                await self._retire_mcp_bindings(candidate)
            self._devices.update_monitoring_sources(candidate.devices.values())
            await self._devices.synchronize_registrations(candidate.devices.values())
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

    async def mcp_tool_settings(self, server_id: str) -> McpToolSettings:
        async with self._operation():
            return await to_thread.run_sync(
                tool_settings, self._require_configuration_path(), await self._configurations.current(), server_id
            )

    async def discover_mcp_tools(self, server_id: str) -> tuple[McpToolDescriptor, ...]:
        async with self._operation():
            source = await self._configurations.current()
            return await discover_tools(source, server_id, self._require_configuration_path().parent)

    async def select_mcp_tools(self, server_id: str, request: McpToolSelection) -> ConfigurationMutationResult:
        async with self._operation(), self._configuration_lock:
            source = await self._configurations.current()
            path = self._require_configuration_path()
            relative_path, mutation = await to_thread.run_sync(selection_mutation, path, source, server_id, request)
            result = await mutate_configuration_source(
                path,
                relative_path,
                mutation,
                validate_candidate=self._configurations.validate,
                content_plugin_root=self._content_plugin_root,
            )
            return await self._accept_mutation(result)

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
            return await self._accept_mutation(result)

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
            return await self._accept_mutation(result)

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
            return await self._accept_mutation(result)

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
        project_scope: Literal["all", "projectless", "unavailable"] = "all",
        query: str | None = None,
        include_archived: bool = False,
        archived_only: bool = False,
        include_active: bool = False,
        include_starred: bool = False,
        coordinator_thread_id: str | None = None,
        independent_only: bool = False,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ThreadActivityPage:
        async with self._operation():
            return await self._terminal_projections.thread_activity(
                project_id=project_id,
                project_scope=project_scope,
                query=query,
                include_archived=include_archived,
                archived_only=archived_only,
                include_active=include_active,
                include_starred=include_starred,
                coordinator_thread_id=coordinator_thread_id,
                independent_only=independent_only,
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

    async def usage_analytics(self, query: UsageQuery) -> UsageAnalyticsView:
        async with self._operation():
            source = await self._configurations.current()
            names = {item.id: item.name for item in source.projects.values()} if source is not None else {}
            return await self._store.usage_analytics.snapshot(query, project_names=names)

    async def thread_usage(self, *, thread_id: str) -> ThreadUsageView:
        async with self._operation():
            return await self._store.usage.snapshot(thread_id=thread_id)

    async def thread_work(
        self,
        *,
        thread_id: str,
        include: tuple[Literal["tasks", "notes"], ...] = (),
    ) -> ThreadWork:
        async with self._operation():
            return await self._work.snapshot(thread_id, include)

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
        environment: EnvironmentSelectionPatch | None = None,
    ) -> SkillCatalogView:
        async with self._operation():
            return await self._terminal_projections.skill_catalog(
                thread_id=thread_id,
                defaults=defaults,
                local_roots_override=None if environment is None else environment.local_roots,
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
        thread_id: str | None = None,
        coordinator_thread_id: str | None = None,
        coordinator: bool = False,
    ) -> ThreadSummary:
        async with self._operation():
            selected = (
                RootThreadDefaults(**defaults.model_dump(exclude_unset=True))
                if isinstance(defaults, NewThreadDefaults)
                else defaults
            )
            thread = await self._threads.create(
                defaults=selected,
                title=title,
                thread_id=thread_id,
                coordinator_thread_id=coordinator_thread_id,
                coordinator=coordinator,
            )
            await self._summary_hub.publish(kind="thread", thread_id=thread.thread_id)
            return await self._projections.get_thread(thread.thread_id)

    async def fork_thread(self, thread_id: str) -> ThreadSummary:
        """Copy an independent ordinary Thread without admitting a Run."""
        async with self._operation():
            thread = await self._threads.fork(thread_id, files=self._thread_files)
            await self._summary_hub.publish(kind="thread", thread_id=thread.thread_id)
            return await self._projections.get_thread(thread.thread_id)

    async def promote_coordinator(self, thread_id: str) -> ThreadSummary:
        """Convert an idle independent root without replacing its conversation."""
        async with self._operation():
            async with self._root_runs.require_inactive(thread_id):
                thread = await self._threads.get(thread_id)
                if thread.read_model is not None and thread.read_model.deferred_requests is not None:
                    raise ThreadError(
                        "Resolve pending decisions before converting this Thread.", code="thread_deferred_pending"
                    )
                await self._threads.promote_coordinator(thread_id)
            await self._summary_hub.publish(kind="thread", thread_id=thread_id)
            return await self._projections.get_thread(thread_id)

    async def set_auto_followup(self, thread_id: str, auto_followup: bool) -> ThreadSummary:
        async with self._operation():
            await self._threads.set_auto_followup(thread_id, auto_followup)
            await self._summary_hub.publish(kind="thread", thread_id=thread_id)
            return await self._projections.get_thread(thread_id)

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

    async def inspect_agent_tool_search(self, agent_id: str) -> AgentToolSearchView:
        async with self._operation():
            source = await self._configurations.current()
            if source is None or agent_id not in source.agents:
                raise HarnessUiError("The accepted Agent does not exist.", code="agent_not_found")
            agent = source.agents[agent_id]
            return agent_tool_search_view(
                source,
                agent,
                mcp_server_ids=self._mcp_app_owners.resolver.effective_mcp_server_ids(
                    source, source.selected_mcp_servers(agent)
                ),
            )

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
                saved = await self._projections.inspection(thread)
                assert saved.run_composition is not None
                value = await self._store.objects.read_model(saved.run_composition, ResolvedRunComposition)
                if value.thread_id != thread_id:
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
                        default_model_id="thread" if selected.default_model_id is not None else "agent",
                        local_roots="thread",
                        environment_profile_id="thread",
                        environment_bindings="thread",
                        default_environment="thread",
                        harness_plugin_ids="thread",
                        environment_run_extension_ids="thread",
                        mcp_server_ids="thread",
                    ),
                ),
                next_generation_digest=None if source is None else source.source_digest,
                next_model_id=(
                    source.document.live.model
                    if selected.agent_source.kind == "live" and source is not None and source.document.live is not None
                    else source.memory_organization_model_id
                    if thread.memory_scope is not None and source is not None
                    else selected.default_model_id or (None if agent is None else agent.model)
                ),
                next_capability_ids=(
                    ("working_state", "runtime_context", "user_interaction", "dynamic_environment")
                    if selected.agent_source.kind == "live"
                    else ()
                    if agent is None
                    else tuple(item.capability for item in agent.capabilities)
                ),
                next_tool_search=(
                    None
                    if agent is None or source is None
                    else agent_tool_search_view(
                        source,
                        agent,
                        mcp_server_ids=self._mcp_app_owners.resolver.effective_mcp_server_ids(
                            source, selected.mcp_server_ids
                        ),
                        harness_plugin_ids=selected.harness_plugin_ids,
                    )
                ),
                captured=captured,
                capture_source=origin,
                receipt_id=None if active is None else active.receipt.receipt_id,
                run_id=None if active is None else active.run_id,
                continuation_id=continuation_id,
            )

    async def preview_project_defaults(
        self, *, thread_id: str, environments_only: bool = False
    ) -> ProjectDefaultsPreview:
        async with self._operation():
            return await self._threads.preview_project_defaults(thread_id, environments_only=environments_only)

    async def apply_project_defaults(
        self, *, thread_id: str, request: ProjectDefaultsApply, environments_only: bool = False
    ) -> ThreadSummary:
        # Serialize with this App's generation acceptance, not with Agent execution.
        async with self._operation(), self._configuration_lock:
            await self._threads.apply_project_defaults(
                thread_id=thread_id,
                expected_version=request.expected_version,
                defaults_digest=request.defaults_digest,
                environments_only=environments_only,
            )
            await self._retire_mcp_owners()
            await self._summary_hub.publish(kind="thread", thread_id=thread_id)
            return await self._projections.get_thread(thread_id)

    async def mcp_status(self, thread_id: str) -> tuple[McpIntegrationView, ...]:
        async with self._operation():
            await self._threads.get(thread_id)
            return (
                tuple(
                    McpIntegrationView(
                        thread_id=item.thread_id,
                        server_id=item.server_id,
                        generation=item.generation,
                        connected=item.connected,
                        retired=item.retired,
                    )
                    for item in self._mcp_connections.current()
                    if item.thread_id == thread_id
                )
                if self._mcp_connections is not None
                else ()
            )

    async def close_mcp_integration(self, thread_id: str, server_id: str) -> None:
        async with self._operation():
            await self._threads.get(thread_id)
            if self._mcp_connections is not None:
                await self._mcp_connections.close_integration(thread_id, server_id)
            await self._summary_hub.publish(kind="thread", thread_id=thread_id)

    async def _mcp_input_scope(self, thread_id: str) -> set[str]:
        await self._threads.get(thread_id)
        result = {thread_id}
        if self._mcp_inputs is not None:
            for candidate in self._mcp_inputs.thread_ids():
                current = await self._store.threads.get(candidate)
                while current is not None and current.parent_thread_id is not None:
                    if current.parent_thread_id == thread_id:
                        result.add(candidate)
                        break
                    current = await self._store.threads.get(current.parent_thread_id)
        return result

    async def mcp_input_requests(self, thread_id: str) -> tuple[McpInputRequestView, ...]:
        async with self._operation():
            scope = await self._mcp_input_scope(thread_id)
            return self._mcp_inputs.requests(scope) if self._mcp_inputs is not None else ()

    async def respond_mcp_input(
        self, thread_id: str, request_id: str, response: McpInputResponse
    ) -> McpInputRequestView:
        async with self._operation():
            if self._mcp_inputs is None:
                raise HarnessUiError("Interactive MCP input is unavailable.", code="mcp_input_unavailable")
            return await self._mcp_inputs.respond(await self._mcp_input_scope(thread_id), request_id, response)

    async def open_mcp_app(self, thread_id: str, reference: AppReference) -> AppPresentation:
        async with self._operation():
            thread = await self._threads.get(thread_id)
            source = await self._configurations.current()
            if source is None or not source.document.webui.mcp_apps.enabled or self._mcp_apps is None:
                raise HarnessUiError("MCP Apps are disabled.", code="mcp_apps_disabled")
            if reference.thread_id != thread_id:
                raise HarnessUiError("App belongs to another Thread.", code="mcp_app_reference_invalid")
            if thread.parent_thread_id is not None:
                retained = await self._subagent_operator.retains_mcp_app(
                    reference, parent_thread_id=thread.parent_thread_id
                )
            else:
                retained = await self._live_hub.retains_mcp_app(reference)
                if not retained:
                    retained = await self._projections.retains_mcp_app(reference)
            if not retained:
                raise HarnessUiError("App is not in retained Thread history.", code="mcp_app_reference_invalid")
            return await self._mcp_apps.read(reference)

    def _apps(self) -> AppOperations:
        if self._mcp_operations is None:
            raise HarnessUiError("Interactive MCP Apps are unavailable.", code="mcp_apps_disabled")
        return self._mcp_operations

    async def activate_mcp_app(self, thread_id: str, reference: AppReference) -> AppView:
        # Opening checks retained presentation access independently of today's activation authority.
        await self.open_mcp_app(thread_id, reference)
        async with self._operation():
            return await self._apps().activate(reference)

    async def call_mcp_app_tool(self, thread_id: str, view_id: str, request: AppToolRequest) -> AppOperation:
        async with self._operation():
            return await self._apps().call_tool(thread_id, view_id, request)

    async def read_mcp_app_resource(
        self, thread_id: str, view_id: str, request: AppResourceRequest
    ) -> dict[str, JsonValue]:
        async with self._operation():
            return await self._apps().read_resource(thread_id, view_id, request)

    async def get_mcp_app_operation(self, thread_id: str, view_id: str, request_key: str) -> AppOperation:
        async with self._operation():
            return self._apps().get_operation(thread_id, view_id, request_key)

    async def decide_mcp_app_operation(
        self, thread_id: str, view_id: str, request_key: str, *, approve: bool
    ) -> AppOperation:
        async with self._operation():
            return self._apps().decide(thread_id, view_id, request_key, approve=approve)

    async def send_mcp_app_message(self, thread_id: str, view_id: str, request: AppMessageRequest) -> AppMessageReceipt:
        async with self._operation():
            return self._apps().send_message(thread_id, view_id, request, self._submit_app_message)

    async def get_mcp_app_message(self, thread_id: str, view_id: str, request_key: str) -> AppMessageReceipt:
        async with self._operation():
            return self._apps().get_message(thread_id, view_id, request_key)

    async def _submit_app_message(self, view: AppView, parts: tuple[str, ...]) -> RootRunReceipt:
        return await self.submit_thread(
            thread_id=view.root_thread_id, prompt=ComposerInput(parts=parts), input_surface="webui", mcp_app_view=view
        )

    async def update_mcp_app_context(self, thread_id: str, view_id: str, value: AppContextUpdate) -> AppContext:
        async with self._operation():
            return await self._apps().update_context(thread_id, view_id, value)

    async def discard_mcp_app_context(self, thread_id: str, view_id: str) -> None:
        async with self._operation():
            self._apps().discard_context(thread_id, view_id)

    async def close_mcp_app_view(self, thread_id: str, view_id: str) -> None:
        async with self._operation():
            self._apps().close_view(thread_id, view_id)

    async def get_thread(self, thread_id: str) -> ThreadDetail:
        async with self._operation():
            return await self._projections.detail(thread_id)

    async def list_threads(
        self,
        *,
        memory: bool = False,
        projectless: bool = False,
        query: str | None = None,
        project_id: str | None = None,
        include_archived: bool = False,
        project_ids: tuple[str, ...] | None = None,
        sort: Literal["updated", "activity", "touched"] = "updated",
        cursor: str | None = None,
        limit: int = 20,
    ) -> ThreadPage:
        async with self._operation():
            return await self._projections.list_threads(
                memory=memory,
                projectless=projectless,
                query=query,
                project_id=project_id,
                include_archived=include_archived,
                project_ids=project_ids,
                sort=sort,
                cursor=cursor,
                limit=limit,
            )

    async def read_saved_output(
        self, thread_id: str, target: SavedOutputTarget, *, offset: int = 0, limit: int = 64 * 1024
    ) -> SavedOutputView:
        async with self._operation():
            return await self._saved_outputs.output(thread_id, target, offset=offset, limit=limit)

    async def saved_child_outputs(
        self, parent_thread_id: str, execution_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> SavedChildOutputPage:
        async with self._operation():
            return await self._saved_outputs.child_outputs(parent_thread_id, execution_id, cursor=cursor, limit=limit)

    async def lookup_thread_activity(self, *, thread_ids: tuple[str, ...]) -> tuple[ThreadActivityView, ...]:
        async with self._operation():
            return await self._terminal_projections.lookup_thread_activity(thread_ids)

    async def lookup_threads(self, *, thread_ids: tuple[str, ...], memory: bool = False) -> ThreadPage:
        async with self._operation():
            return await self._projections.lookup_threads(thread_ids, memory=memory)

    async def memory_files(self, *, project_id: str | None = None) -> tuple[MemoryFileEntry, ...]:
        async with self._operation():
            store = await self._memory_store(project_id)
            return tuple(MemoryFileEntry.model_validate(entry, from_attributes=True) for entry in await store.list())

    async def memory_file(self, *, path: str, project_id: str | None = None) -> MemoryFileText:
        async with self._operation():
            store = await self._memory_store(project_id)
            try:
                return MemoryFileText.model_validate(await store.read(path), from_attributes=True)
            except MemoryStoreError as exc:
                raise ThreadError(str(exc), code=f"memory_{exc.code}") from exc

    async def _memory_store(self, project_id: str | None) -> DirectoryFileStore:
        source = await self._configurations.current()
        if source is None or not source.document.memory.enabled:
            raise ThreadError("Memory is disabled.", code="memory_disabled")
        if project_id is not None and project_id not in source.projects:
            raise ThreadError("Project is not configured.", code="project_not_found")
        scope = memory_scopes(self._require_configuration_path().parent, project_id)[-1]
        return DirectoryFileStore(scope.root)

    async def get_thread_transcript(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        turn_id: str | None = None,
    ) -> TranscriptPage:
        async with self._operation():
            return await self._projections.transcript(
                thread_id=thread_id,
                expected_continuation_id=expected_continuation_id,
                cursor=cursor,
                limit=limit,
                turn_id=turn_id,
            )

    async def get_thread_inputs(
        self,
        *,
        thread_id: str,
        expected_continuation_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> TranscriptInputPage:
        async with self._operation():
            return await self._projections.transcript_inputs(
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
            thread = await self._threads.require_interactive(thread_id)
            if thread.parent_thread_id is not None:
                raise AppStateError(
                    "Child Threads are managed through their parent execution.", code="child_thread_scoped"
                )
            await self._threads.update_configuration(
                thread_id=thread_id,
                mutation=mutation,
            )
            await self._retire_mcp_owners()
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

    async def touch_thread(self, thread_id: str) -> ThreadSummary:
        """Move a root Thread to recent navigation without changing its conversation."""

        async with self._operation():
            thread = await self._threads.require_interactive(thread_id)
            if thread.parent_thread_id is not None:
                raise ThreadError(
                    "Child Threads are managed through their parent execution.", code="child_thread_scoped"
                )
            await self._store.threads.touch(thread_id)
            await self._summary_hub.publish(kind="thread", thread_id=thread_id)
            return await self._projections.get_thread(thread_id)

    async def clear_thread_context(self, *, thread_id: str, expected_continuation_id: str) -> ThreadDetail:
        """Clear saved Agent context without running a model or deleting the transcript."""
        async with self._operation():
            async with self._root_runs.require_inactive(thread_id):
                await self._threads.clear_context(
                    thread_id=thread_id, expected_continuation_id=expected_continuation_id
                )
                self._tool_executions.clear_state(thread_id)
                if self._browsers is not None:
                    self._browsers.handback.suppress(thread_id)
            await self._summary_hub.publish(kind="thread", thread_id=thread_id)
            await self._summary_hub.publish(kind="thread_work", thread_id=thread_id, work_sections=("tasks", "notes"))
            return await self._projections.detail(thread_id)

    async def delete_thread(self, *, thread_id: str, expected_version: int) -> tuple[str, ...]:
        """Permanently remove an idle conversation and its subagent history."""
        deleted: tuple[str, ...] | None = None
        async with self._operation():
            async with self._root_runs.require_inactive(thread_id):
                with CancelScope(shield=True):
                    async with self._subagent_operator.guard_execution_admission() as active:
                        deleted = await self._threads.delete(
                            thread_id,
                            expected_version=expected_version,
                            active_execution_ids=active,
                            busy_thread_ids=self._tool_executions.busy_thread_ids(),
                        )
                    for removed in deleted:
                        self._tool_executions.suppress(removed)
                        self._tool_executions.clear_state(removed)
                        if (draft := self._shared_drafts.pop(removed, None)) is not None:
                            draft.close()
                        if self._browsers is not None:
                            self._browsers.handback.suppress(removed)
                        await self._thread_files.remove(removed)
                    await self._retire_mcp_owners()
                    await self._summary_hub.publish(kind="thread", thread_id=thread_id)
                assert deleted is not None
                return deleted

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
                    if self._browsers is not None:
                        self._browsers.handback.suppress(thread_id)
                await self._retire_mcp_owners()
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
                if target.thread_id == focus.root_thread_id:
                    return None
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

    async def list_unsent_drafts(self) -> tuple[DraftSummary, ...]:
        """Discover process-local shared input without joining editors or reading text."""
        async with self._operation():
            return tuple(
                DraftSummary(thread_id=thread_id, draft_id=draft.draft_id, unsent_since=draft.unsent_since)
                for thread_id, draft in self._shared_drafts.items()
                if draft.unsent_since is not None and not draft.closed
            )

    async def shared_draft(self, thread_id: str) -> SharedDraft:
        async with self._operation():
            thread = await self._threads.require_interactive(thread_id)
            if thread.parent_thread_id is not None:
                raise AppStateError("Shared drafts require a root Thread.", code="child_thread_scoped")
            await self._thread_files.touch(thread_id)
            if thread_id not in self._shared_drafts:
                self._shared_drafts[thread_id] = SharedDraft()
            return self._shared_drafts[thread_id]

    async def edit_shared_draft(self, thread_id: str, participant: str, command: DraftCommand) -> None:
        async with self._operation():
            # Joining establishes the immutable root/interactive scope and holds
            # Thread files until App close. Commands must belong to that room;
            # do not repeat database reads and scratch touches on every edit.
            draft = self._shared_drafts.get(thread_id)
            if draft is None:
                raise AppStateError("The shared draft instance changed.", code="draft_instance_conflict")

            async def validate(attachments: tuple[str, ...]) -> None:
                await self._thread_files.validate_attachments(thread_id, attachments)

            if await draft.command(participant, command, validate):
                await self._summary_hub.publish(kind="draft", thread_id=thread_id)

    def _prepare_draft_clear(self, thread_id: str, capture: DraftCapture | None) -> tuple[SharedDraft, bytes] | None:
        if capture is None:
            return None
        draft = self._shared_drafts.get(thread_id)
        if draft is None:
            raise AppStateError("The shared draft instance changed.", code="draft_instance_conflict")
        return draft, draft.prepare_clear(capture)

    async def _clear_submitted_draft(self, thread_id: str, prepared: tuple[SharedDraft, bytes] | None) -> None:
        if prepared is None:
            return
        draft, update = prepared
        # Admission already succeeded. Finish its draft cleanup even if the
        # initiating request disappears, without shielding admission itself.
        with CancelScope(shield=True):
            if await draft.clear(update):
                await self._summary_hub.publish(kind="draft", thread_id=thread_id)

    @property
    def host_terminal_available(self) -> bool:
        return self._host_terminal.available

    @property
    def browser_available(self) -> bool:
        return self._browsers is not None and self._browsers.available

    def browsers(self) -> HostBrowsers:
        if self._browsers is None or not self._browsers.available:
            raise HarnessUiError("Enable WebUI Host computer sharing to use the browser.", code="browser_disabled")
        return self._browsers

    async def browser_state(self, thread_id: str) -> BrowserState:
        async with self._operation():
            await self.browsers().authorize(thread_id)
            return self.browsers().state(thread_id)

    async def scan_browser_executables(self) -> BrowserScan:
        async with self._operation():
            self.browsers()
            return await scan_browsers()

    async def check_browser_configuration(self, configuration: BrowserConfiguration) -> BrowserSetup:
        async with self._operation():
            return await self.browsers().inspect(configuration)

    async def open_macos_browser_settings(self) -> None:
        async with self._operation():
            await self.browsers().open_macos_settings()

    async def browser_access(self, thread_id: str, request: BrowserAccessRequest) -> BrowserAccess:
        async with self._operation():
            return await self.browsers().access(thread_id, request)

    async def browser_setup(self, thread_id: str) -> BrowserSetup:
        async with self._operation():
            return await self.browsers().setup(thread_id)

    async def start_browser(self, thread_id: str) -> BrowserState:
        async with self._operation():
            return await self.browsers().open(thread_id)

    async def reset_browser(self, thread_id: str, command: BrowserCommand) -> BrowserState:
        async with self._operation():
            return await self.browsers().reset(thread_id, command.session_id)

    async def resize_browser(self, thread_id: str, request: BrowserViewportRequest) -> BrowserState:
        async with self._operation():
            return await self.browsers().resize(thread_id, request)

    async def close_browser_pages(self, thread_id: str, command: BrowserCommand) -> BrowserState:
        async with self._operation():
            return await self.browsers().close_pages(thread_id, command.session_id)

    async def control_browser(self, thread_id: str, command: BrowserCommand, *, human: bool) -> BrowserControl:
        async with self._operation():
            return await self.browsers().control(thread_id, command.session_id, human=human)

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

    async def host_file_info(self, request: FileReadRequest) -> FileInfo:
        async with self._operation():
            return await self._host_files.info(request)

    async def read_host_file(self, request: FileReadRequest) -> FileText:
        async with self._operation():
            return await self._host_files.read_text(request)

    async def open_host_file_stream(self, request: FileReadRequest) -> FileStream:
        opened: FileStream | None = None
        try:
            async with self._operation():
                opened = await self._host_files.open_stream(request)
            return opened
        except BaseException:
            if opened is not None:
                await opened.close()
            raise

    async def read_host_file_stream(self, opened: FileStream, offset: int, size: int) -> bytes:
        async with self._operation():
            return await self._host_files.read_stream(opened, offset, size)

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
            await self._threads.require_interactive(thread_id)
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
            await self._threads.require_interactive(thread_id)
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
            await self._threads.require_interactive(thread_id)
            return await self._thread_files.stage(thread_id, upload)

    async def read_thread_attachment(self, *, thread_id: str, attachment_id: str) -> tuple[ThreadAttachment, bytes]:
        async with self._operation():
            await self._threads.get(thread_id)
            return await self._thread_files.read(thread_id, attachment_id)

    async def prune_thread_files(self) -> tuple[str, ...]:
        async with self._operation():
            return await self._thread_files.prune()

    async def _maintain_read_models(self) -> None:
        while True:
            try:
                repaired = await self._store.repair_read_models()
                for thread_id in repaired:
                    await self._summary_hub.publish(kind="thread", thread_id=thread_id)
                    await self._summary_hub.publish(
                        kind="thread_work", thread_id=thread_id, work_sections=("tasks", "notes")
                    )
                await self._store.refresh_object_count()
            except Exception as exc:
                # Query maintenance is repairable, not an App-lifetime task.
                get_logger(__name__).warning("Read model maintenance failed", extra={"error_type": type(exc).__name__})
            await sleep(30)

    async def _prune_storage(self) -> None:
        """Independent maintenance failures never cancel the App's task group."""
        for kind, prune in (
            ("scratch", self._thread_files.prune),
            ("compaction", self._prune_compactions),
            ("checkpoint", self._store.checkpoints.prune),
        ):
            try:
                await prune()
            except Exception as exc:
                get_logger(__name__).warning(
                    "Storage pruning failed", extra={"kind": kind, "error_type": type(exc).__name__}
                )

    async def _prune_compactions(self) -> None:
        if self._compaction is not None:
            await self._compaction.prune(self._settings.storage.compaction_retention_seconds)

    async def _prune_storage_periodically(self) -> None:
        while True:
            await self._prune_storage()
            await sleep(3600)

    async def _prepare_input(
        self, thread_id: str, prompt: RunInputValue | ComposerInput, attachment_ids: tuple[str, ...]
    ) -> RunInputValue:
        uploads = prompt.attachments if isinstance(prompt, ComposerInput) else ()
        references = prompt.attachment_ids if isinstance(prompt, ComposerInput) else ()
        if len(uploads) + len(references) + len(attachment_ids) > MAX_ATTACHMENTS:
            raise ValueError("An input supports up to eight attachments.")
        resolved = [await self._thread_files.read(thread_id, item) for item in references]
        attachments = [await self._thread_files.read(thread_id, item) for item in attachment_ids]
        if (
            sum(len(item.data) for item in uploads) + sum(item.size for item, _ in (*resolved, *attachments))
            > MAX_INPUT_BYTES
        ):
            raise ValueError("An input supports up to 20 MiB of attachments.")
        parts: list[UserContent | ContentItem] = []
        if isinstance(prompt, ComposerInput):
            if not prompt.text.strip() and not uploads and not resolved and not attachments:
                raise ValueError("A root message must not be blank.")
            source_id = prompt.source_id or f"input_{uuid4().hex}"
            selected = iter(resolved)
            for index, part in enumerate(prompt.parts):
                if isinstance(part, str):
                    if part:
                        parts.append(
                            TextContent(
                                part, metadata={"source_id": source_id, "harness_ui": {"composer": {"index": index}}}
                            )
                        )
                elif isinstance(part, ComposerAttachmentReference):
                    item, data = next(selected)
                    parts.extend(
                        await self._attachment_input(
                            thread_id, item, data, source_id=source_id, index=index, label=part.label
                        )
                    )
                else:
                    item = await self._thread_files.stage(thread_id, part.upload)
                    parts.extend(
                        await self._attachment_input(
                            thread_id, item, part.upload.data, source_id=source_id, index=index, label=part.label
                        )
                    )
        else:
            parts.extend([prompt] if isinstance(prompt, str) else prompt)
        for item, data in attachments:
            parts.extend(await self._attachment_input(thread_id, item, data))
        await self._thread_files.touch(thread_id)
        return detach_input(tuple(parts))

    async def _attachment_input(
        self,
        thread_id: str,
        item: ThreadAttachment,
        data: bytes,
        *,
        source_id: str | None = None,
        index: int = 0,
        label: str | None = None,
    ) -> list[UserContent | ContentItem]:
        # Retain before admission, so accepted input never references draft scratch.
        await self._thread_files.retain(thread_id, item.attachment_id)
        path = f"attachments/{item.attachment_id}/content"
        namespace: dict[str, Any] = {"attachment": item.model_dump(), "mount": "thread-files", "path": path}
        metadata: dict[str, Any] = {"harness_ui": namespace}
        if source_id is not None:
            metadata["source_id"] = source_id
            namespace["composer"] = {"index": index, "label": label or item.name}
        source_description = "" if item.source is None else f" Selected Host source: {item.source.model_dump_json()}."
        is_image = item.media_type.startswith("image/")
        # The model sees the label beside the real image; display metadata alone
        # cannot teach the model what the user's 'image#2' refers to.
        name = f"{label} ({item.name!r})" if label else repr(item.name)
        description_metadata = {**metadata, "display": False} if source_id and is_image else metadata
        parts: list[UserContent | ContentItem] = [
            TextContent(
                f"Attachment {name} ({item.media_type}): {path} on the thread-files Environment mount.{source_description}",
                metadata=description_metadata,
            )
        ]
        if item.source is not None:
            captured_text = context_text(item.source, data)
            if captured_text is not None:
                parts.append(
                    TextContent(captured_text, metadata={**metadata, "display": False} if source_id else metadata)
                )
        if is_image:
            parts.append(
                ContentItem(
                    BinaryContent(data=data, media_type=item.media_type), ContentMetadata.model_validate(metadata)
                )
            )
        return parts

    async def submit_thread(
        self,
        *,
        thread_id: str,
        prompt: RunInputValue | ComposerInput,
        attachment_ids: tuple[str, ...] = (),
        mutation: ThreadConfigurationMutation | None = None,
        model_overrides: RunModelOverrides | None = None,
        skill_references: tuple[SkillReference, ...] = (),
        input_surface: Literal["tui", "webui"] | None = None,
        environment: EnvironmentSelectionPatch | None = None,
        mode: GoalMode = "normal",
        app_context: tuple[AppContextReference, ...] = (),
        mcp_app_view: AppView | None = None,
        draft: DraftCapture | None = None,
    ) -> RootRunReceipt:
        prompt = deepcopy(prompt)
        attachment_ids = tuple(attachment_ids)
        if mode not in {"normal", "goal"}:
            raise ValueError("Unknown submission mode")
        async with self._operation():
            await self._threads.require_interactive(thread_id)
            draft_clear = self._prepare_draft_clear(thread_id, draft)
            selected_context = await self._apps().capture_context(thread_id, app_context) if app_context else ()
            goal = None
            if mode == "goal":
                objective = (
                    prompt.text
                    if isinstance(prompt, ComposerInput)
                    else prompt
                    if isinstance(prompt, str)
                    else "".join(
                        part
                        if isinstance(part, str)
                        else part.content
                        if isinstance(part, TextContent) and (part.metadata or {}).get("display") is not False
                        else ""
                        for part in prompt
                    )
                ).strip()
                if not objective:
                    raise ValueError("A Goal requires a task description.")
                configuration = await self._configurations.current()
                if configuration is None:
                    raise AppStateError("No accepted configuration is selected.", code="configuration_not_accepted")
                goal = GoalView(objective=objective, max_iterations=configuration.document.max_goal_iterations)
            catalog = await self._terminal_projections.skill_catalog(
                thread_id=thread_id,
                local_roots_override=(
                    environment.local_roots
                    if environment is not None and environment.local_roots is not None
                    else mutation.patch.local_roots
                    if mutation is not None
                    else None
                ),
            )
            selected_skills = self._terminal_projections.validate_references_against(catalog, skill_references)
            await self._threads.get(thread_id)
            prompt = await self._prepare_input(thread_id, prompt, attachment_ids)
            prompt = prepare_skill_input(prompt, catalog, selected_skills)
            if selected_context:
                prompt = tuple([prompt] if isinstance(prompt, str) else prompt) + tuple(
                    TextContent(text, metadata={"harness_ui": {"mcp_app_context": True}}) for text in selected_context
                )
            if input_surface is not None:
                prompt = append_surface_hint(prompt, input_surface)

            async def authorize_app_message() -> None:
                if mcp_app_view is not None:
                    if mcp_app_view.root_thread_id != thread_id:
                        raise HarnessUiError(
                            "The App message belongs to another root.", code="mcp_app_owner_unavailable"
                        )
                    await self._apps().authorize_message(mcp_app_view)

            receipt = await self._root_runs.submit_prompt(
                thread_id=thread_id,
                prompt=prompt,
                mutation=mutation,
                model_overrides=model_overrides,
                environment=environment,
                goal=goal,
                touch=True,
                human_input=True,
                authorize=authorize_app_message if mcp_app_view is not None else None,
            )
            self._tool_executions.user_input(thread_id)
            await self._clear_submitted_draft(thread_id, draft_clear)
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
            await self._threads.require_interactive(thread_id)
            return await self._root_runs.submit_response(
                thread_id=thread_id,
                response=response,
                mutation=mutation,
                model_overrides=model_overrides,
                touch=True,
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
        if not supplied <= set(kinds):
            raise AppStateError(
                "The decision response contains an unknown request.",
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

    async def tool_executions(
        self, thread_id: str, *, offset: int = 0, limit: int = 20
    ) -> tuple[ToolExecutionView, ...]:
        async with self._operation():
            await self._threads.get(thread_id)
            records = await self._tool_executions.runtime.summarize(thread_id, offset=offset, limit=limit)
            return tuple(execution_view(record) for record in records)

    async def cancel_tool_execution(self, thread_id: str, execution_id: str) -> ToolExecutionView:
        async with self._operation():
            await self._threads.require_interactive(thread_id)
            record = await self._tool_executions.runtime.cancel(thread_id, execution_id)
            if record is None:
                raise RunCoordinationError(
                    "The tool execution is unavailable in this Thread.", code="tool_execution_unavailable"
                )
            await self._tool_execution_changed(thread_id)
            return execution_view(record)

    async def _tool_execution_changed(self, thread_id: str) -> None:
        thread = await self._store.threads.get(thread_id)
        if thread is not None:
            await self._summary_hub.publish(
                kind="thread", thread_id=thread_id, root_thread_id=await self._subagent_operator._root_thread_id(thread)
            )

    async def _deliver_tool_completion(self, thread_id: str, prompt: str) -> bool:
        if self._state is not AppState.ready:
            return False
        detail = await self._projections.detail(thread_id)
        if detail.thread.archived or detail.thread.memory_scope is not None or detail.deferred_requests:
            return False
        # Lifecycle hints are model context, not human-authored messages. Keep
        # the annotation through active steering and idle continuation alike.
        content = (ContentItem(prompt, ContentMetadata(display=False, source_id="tool_execution.completion")),)
        if detail.thread.parent_thread_id is not None:
            return await self._subagent_operator.deliver_tool_completion(thread_id, content)
        operation = await self._root_runs.active(thread_id)
        if operation is not None:
            result = await self._root_runs.steer(
                receipt_id=operation.receipt.receipt_id, message=content, immediate=False
            )
            return result.accepted
        await self._root_runs.submit_prompt(thread_id=thread_id, prompt=content)
        return True

    async def _deliver_browser_handback(self, thread_id: str, prompt: str, current: Callable[[], bool]) -> bool:
        if self._state is not AppState.ready or not current():
            return False
        detail = await self._projections.detail(thread_id)
        if (
            detail.thread.archived
            or detail.thread.memory_scope is not None
            or detail.thread.parent_thread_id is not None
            or detail.deferred_requests
        ):
            return False

        def before_accept() -> None:
            # Check at native enqueue or idle admission, after all preparation waits.
            if self._state is not AppState.ready or not current():
                raise RunCoordinationError("Browser handback notice is no longer current.", code="browser_notice_stale")

        async def authorize() -> None:
            before_accept()

        content = (ContentItem(prompt, ContentMetadata(display=False, source_id="browser.handback")),)
        operation = await self._root_runs.active(thread_id)
        if operation is not None:
            result = await self._root_runs.steer(
                receipt_id=operation.receipt.receipt_id, message=content, immediate=False, before_accept=before_accept
            )
            return result.accepted
        try:
            await self._root_runs.submit_prompt(
                thread_id=thread_id, prompt=content, authorize=authorize, reject_deferred=True
            )
        except RunCoordinationError:
            return False
        return True

    async def get_root_operation(self, receipt_id: str) -> RootOperationView:
        async with self._operation():
            return await self._root_runs.get(receipt_id)

    async def active_root_thread_ids(self) -> tuple[str, ...]:
        """Lightweight process-local observation capacity, without Thread projections."""
        async with self._operation():
            return await self._root_runs.active_thread_ids()

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
        message: str | ComposerInput,
        skill_references: tuple[SkillReference, ...] = (),
        attachment_ids: tuple[str, ...] = (),
        draft: DraftCapture | None = None,
    ) -> RootControlResult:
        async with self._operation():
            operation = await self._root_runs.get(receipt_id)
            await self._threads.require_interactive(operation.receipt.thread_id)
            draft_clear = self._prepare_draft_clear(operation.receipt.thread_id, draft)
            catalog = (
                await self._terminal_projections.skill_catalog(thread_id=operation.receipt.thread_id)
                if skill_references
                else None
            )
            selected_skills = (
                self._terminal_projections.validate_references_against(catalog, skill_references)
                if catalog is not None
                else ()
            )
            prepared = (
                await self._prepare_input(operation.receipt.thread_id, message, attachment_ids)
                if attachment_ids or isinstance(message, ComposerInput)
                else message
            )
            if catalog is not None:
                prepared = prepare_skill_input(prepared, catalog, selected_skills)
            source = await self._configurations.current()
            result = await self._root_runs.steer(
                receipt_id=receipt_id,
                message=prepared,
                immediate=source is None or source.document.input.steering_mode == "immediate",
                touch=True,
            )
            if result.accepted:
                self._tool_executions.user_input(operation.receipt.thread_id)
                if self._browsers is not None:
                    self._browsers.handback.user_input(operation.receipt.thread_id)
                await self._clear_submitted_draft(operation.receipt.thread_id, draft_clear)
            return result

    async def cancel_root_operation(self, receipt_id: str) -> RootControlResult:
        async with self._operation():
            operation = await self._root_runs.get(receipt_id)
            await self._threads.require_interactive(operation.receipt.thread_id)
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
            await self._threads.require_interactive(parent_thread_id)
            source = await self._configurations.current()
            result = await self._subagent_operator.steer_execution(
                parent_thread_id=parent_thread_id,
                execution_id=execution_id,
                message=message,
                immediate=source is None or source.document.input.steering_mode == "immediate",
            )
            if result.accepted:
                head = await self._store.child_executions.get(execution_id)
                if head is not None:
                    self._tool_executions.user_input(head.child_thread_id)
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
            await self._threads.require_interactive(parent_thread_id)
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
            for provider in Provider:
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

            def untouched() -> bool:
                return not path.exists() and not any(
                    any((path.parent / directory).glob("**/*.*"))
                    for directory in ("agents", "models", "projects", "extensions", "mcp", "subagents")
                )

            return SetupStatus(
                needed=current is None or not current.agents or defaults is None or defaults.agent is None,
                fresh=(current is None or not (current.agents or current.models or current.projects))
                and self._candidate_error is None
                and await to_thread.run_sync(untouched),
                draft_scope=hashlib.sha256(f"{path.resolve()}\0{self._store.layout.root}".encode()).hexdigest()[:24],
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

    async def model_choices(self) -> ModelChoices:
        async with self._operation():
            return ModelChoices()

    async def model_account_candidates(self, provider: Provider | str) -> tuple[AccountCandidate, ...]:
        async with self._operation():
            account = self._account(Provider(provider))
            from a13n_harness_ui.model_accounts.chatgpt import ChatGPTAccountStore

            return await account.candidates() if isinstance(account, CopilotAccountStore | ChatGPTAccountStore) else ()

    async def select_model_account(self, provider: Provider | str, selection: AccountSelection) -> AccountProjection:
        async with self._operation():
            account = self._account(Provider(provider))
            from a13n_harness_ui.model_accounts.chatgpt import ChatGPTAccountStore

            if not isinstance(account, CopilotAccountStore | ChatGPTAccountStore):
                raise AppStateError(
                    "This account has no source-selection action.", code="account_selection_unsupported"
                )
            return await account.select(selection)

    async def discover_account_models(self, provider: Provider | str) -> tuple[ModelChoice, ...]:
        """Explicit account-scoped discovery, separate from the public model directory."""
        from a13n_harness.providers.model.oauth import discover_copilot_models

        async with self._operation():
            if Provider(provider) is Provider.CHATGPT:
                from a13n_harness.providers.model.chatgpt import discover_chatgpt_models

                try:
                    models = await discover_chatgpt_models(credential_source=self._chatgpt_account.source())
                    return tuple(ModelChoice(value=item.slug, label=item.display_name) for item in models)
                except Exception:
                    raise AppStateError(
                        "ChatGPT model discovery failed. Check the selected registration and account access.",
                        code="model_discovery_failed",
                    ) from None
            if Provider(provider) is not Provider.COPILOT:
                raise AppStateError("This account has no model-discovery action.", code="model_discovery_unsupported")
            try:
                before = await self._copilot_account.load()
                ids = await discover_copilot_models(credential_source=self._copilot_account)
                after = await self._copilot_account.load()
                if (before.source_id, before.account_id) != (after.source_id, after.account_id):
                    raise ValueError("The selected account changed during discovery")
            except Exception:
                raise AppStateError(
                    "Copilot model discovery failed. Check the selected account, subscription and organization policy.",
                    code="model_discovery_failed",
                ) from None
            return tuple(ModelChoice(value=model_id, label=model_id) for model_id in ids)

    async def model_catalog(self) -> ModelCatalogSnapshot:
        async with self._operation():
            return await self._model_catalog.read()

    async def model_options(self, request: ModelOptionsRequest) -> ModelOptions:
        async with self._operation():
            return await to_thread.run_sync(model_options, request)

    async def prepare_model(self, request: ModelRecipeRequest) -> ModelRecipe:
        async with self._operation():
            return await to_thread.run_sync(prepare_model, request)

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

    async def pair_device(self, request: PairingRequest, credential: str) -> PairingResponse:
        async with self._operation(), self._configuration_lock:
            source = await self._configurations.current()
            return self._device_pairings.poll(request, credential, {} if source is None else source.devices)

    async def pending_device_pairings(self) -> tuple[PairingChallenge, ...]:
        async with self._operation(), self._configuration_lock:
            return self._device_pairings.list_pending()

    async def approve_device_pairing(self, pairing_id: str) -> DeviceSummary:
        async with self._operation(), self._configuration_lock:
            source = await self._configurations.current()
            resource = self._device_pairings.approve(pairing_id, {} if source is None else source.devices)
            if source is None or resource.id not in source.devices:
                result = await mutate_configuration_source(
                    self._require_configuration_path(),
                    f"devices/{resource.id}.yaml",
                    ResourceMutationRequest(content=resource.model_dump_json(indent=2)),
                    validate_candidate=self._configurations.validate,
                    content_plugin_root=self._content_plugin_root,
                )
                await self._accept_mutation(result)
            self._device_pairings.approved(pairing_id)
            return DeviceSummary.from_resource(resource)

    async def reject_device_pairing(self, pairing_id: str) -> None:
        async with self._operation(), self._configuration_lock:
            self._device_pairings.reject(pairing_id)

    async def revoke_device(self, device_id: str) -> DeviceSummary:
        async with self._operation(), self._configuration_lock:
            source = await self._configurations.current()
            resource = await self._device_resource(device_id)
            authentication = resource.authentication
            if not isinstance(authentication, PairedDeviceAuthentication):
                raise HarnessUiError("Only paired Device registrations can be revoked.", code="device_not_paired")
            assert source is not None
            relative_path = next(item.relative_path for item in source.sources if item.resource_id == device_id)
            revoked = resource.model_copy(
                update={"authentication": authentication.model_copy(update={"revoked": True})}
            )
            result = await mutate_configuration_source(
                self._require_configuration_path(),
                relative_path,
                ResourceMutationRequest(content=revoked.model_dump_json(indent=2)),
                validate_candidate=self._configurations.validate,
                content_plugin_root=self._content_plugin_root,
            )
            # Persist first. Even if acceptance fails, close captured runtime scopes;
            # future process startup reads the revoked source rather than restoring trust.
            with CancelScope(shield=True):
                try:
                    await self._devices.revoke(resource)
                finally:
                    await self._accept_mutation(result)
            return DeviceSummary.from_resource(revoked)

    async def list_devices(self) -> tuple[DeviceSummary, ...]:
        async with self._operation():
            source = await self._configurations.current()
            if source is None:
                return ()
            return tuple(DeviceSummary.from_resource(item) for item in source.devices.values())

    async def _device_resource(self, device_id: str) -> DeviceResource:
        source = await self._configurations.current()
        resource = None if source is None else source.devices.get(device_id)
        if resource is None:
            raise HarnessUiError("The selected Device is not configured.", code="device_missing")
        return resource

    async def environment_monitoring(self) -> MonitoringCatalog:
        async with self._operation():
            return await self._environment_monitoring.catalog()

    async def environment_metrics(self, target_id: str) -> MonitoringDetail:
        async with self._operation():
            return await self._environment_monitoring.detail(target_id)

    async def device_info(self, device_id: str) -> DeviceInfo:
        async with self._operation():
            return await self._devices.info(await self._device_resource(device_id))

    async def device_directories(
        self, device_id: str, *, path: str | None = None, offset: int = 0, limit: int = 100
    ) -> DirectoryListResult:
        async with self._operation():
            return await self._devices.list_directories(
                await self._device_resource(device_id), path=path, offset=offset, limit=limit
            )

    async def authenticate_device_attachment(self, device_id: str, token: str) -> DeviceAttachment:
        async with self._operation():
            return await self._devices.authenticate_attachment(await self._device_resource(device_id), token)

    async def attach_device(self, attachment: DeviceAttachment, connection: WebSocketConnection) -> None:
        async with self._operation():
            await attachment.attach(connection)

    async def configure_local_environment(self, profile_id: LocalProfileId) -> EnvironmentReadiness:
        """Explicitly prepare and publish the default for new Threads only."""
        import yaml

        readiness = await self.preflight_environment(profile_id)
        if not readiness.ready:
            return readiness
        async with self._operation(), self._configuration_lock:
            path = self._require_configuration_path()
            view = source_view(await self._configurations.current(), path, path.name)
            if not view.writable or not view.content_available:
                raise ConfigurationError("Root configuration is not editable.", code="configuration_unavailable")
            document = yaml.safe_load(view.content or "") or {}
            document.setdefault("defaults", {})["environment_profile"] = profile_id
            result = await mutate_configuration_source(
                path,
                path.name,
                ResourceMutationRequest(content=yaml.safe_dump(document, sort_keys=False, allow_unicode=True)),
                validate_candidate=self._configurations.validate,
                content_plugin_root=self._content_plugin_root,
            )
            await self._accept_mutation(result)
        return readiness

    async def envd_runtime_status(self) -> EnvdStatus:
        async with self._operation():
            status = await self._envd.status()
            configuration = await self._configurations.current()
            desired = None if configuration is None else configuration.document.envd
            return status.model_copy(update={"restart_required": desired != self._envd.configuration})

    async def start_envd(self, *, computer_use: bool = False) -> EnvironmentReadiness:
        async with self._operation():
            return await self._envd.start(
                "environment-desktop" if computer_use else "environment-envd", self._store.layout.staging
            )

    async def restart_envd(self, *, reinstall: bool = False) -> EnvironmentReadiness:
        async with self._operation():
            configuration = await self._configurations.current()
            desired = None if configuration is None else configuration.document.envd
            if desired != self._envd.configuration:
                raise EnvironmentLifecycleError(
                    "Restart Harness UI to apply saved Runtime configuration first.",
                    code="envd_configuration_pending",
                )
            self._sandbox_ready_paths.clear()
            return await self._envd.start(
                "environment-envd", self._store.layout.staging, restart=True, reinstall=reinstall
            )

    async def stop_envd(self) -> None:
        async with self._operation():
            await self._envd.stop()
            self._sandbox_ready_paths.clear()

    async def preflight_environment(
        self, profile_id: LocalProfileId, *, project_path: str | None = None
    ) -> EnvironmentReadiness:
        async with self._operation():
            path = self._store.layout.staging if project_path is None else Path(project_path).expanduser().resolve()
            if profile_id == "environment-desktop":
                self._envd.require_computer_use()
            self._sandbox_ready_paths.discard(path)
            result = await preflight_environment(
                profile_id,
                path,
                resolve_executable=self._resolve_sandbox_executable,
                protected_roots=(
                    self._store.layout.root,
                    *((self._configuration_path.expanduser().resolve().parent,) if self._configuration_path else ()),
                ),
                # Projectless setup probes only its exact Host-owned staging directory.
                owned_probe_root=self._store.layout.staging if path == self._store.layout.staging else None,
            )
            if profile_id != "environment-native" and project_path is None:
                self._envd.record_readiness(result)
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
            if selected is Provider.COPILOT:
                if self._copilot_login is None:
                    raise AppStateError("No Copilot login flow is registered.", code="model_account_login_unavailable")
                return await self._copilot_account.login(self._copilot_login, allow_account_switch=allow_account_switch)
            if selected is Provider.CODEX:
                if self._codex_login is None:
                    raise AppStateError(
                        "No Codex login flow is registered.",
                        code="model_account_login_unavailable",
                    )
                return await self.subscription_accounts.login_codex("acct-codex-default", self._codex_login)
            account = self._account(Provider.GROK)
            assert isinstance(account, GrokAccountStore)
            if self._grok_login is None:
                raise AppStateError(
                    "No Grok login flow is registered.",
                    code="model_account_login_unavailable",
                )
            return await self.subscription_accounts.login_grok("acct-grok-default", self._grok_login)

    async def logout_model_account(self, provider: Provider | str) -> bool:
        async with self._operation():
            if Provider(provider) in {Provider.CODEX, Provider.GROK}:
                return await self.subscription_accounts.logout(f"acct-{provider}-default")
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
    ) -> AsyncGenerator[ThreadWatch]:
        self._require_ready()
        async with self._operation():
            selected = await self._threads.get(root_thread_id)
            base_continuation_id = selected.continuation.logical_digest if selected.continuation is not None else None
        async with self._live_hub.subscribe(root_thread_id=root_thread_id) as subscription:
            # The bootstrap belongs to the consumer, not this long-lived generator.
            yield await self._capture_thread_watch(root_thread_id, base_continuation_id, subscription)

    async def _capture_thread_watch(
        self, root_thread_id: str, base_continuation_id: str | None, subscription: LiveSubscription
    ) -> ThreadWatch:
        async with self._operation():
            thread = await self._projections.detail(root_thread_id)
            if thread.continuation_id != base_continuation_id:
                raise LivePresentationError(
                    "The selected history changed during focused bootstrap.", code="live_snapshot_changed"
                )
            if thread.thread.parent_thread_id is not None:
                raise AppStateError("A focused watch requires a root Thread.", code="child_thread_scoped")
            root_operation = await self._root_runs.active(root_thread_id)
            selected = await self._threads.get(root_thread_id)
            selected_id = selected.continuation.logical_digest if selected.continuation is not None else None
        cursor = subscription.cursor
        root_stream = subscription.take_root_stream()
        if selected_id != thread.continuation_id or (
            root_stream is not None and not root_stream.includes_continuation(thread.continuation_id)
        ):
            raise LivePresentationError(
                "The selected history changed during focused bootstrap.", code="live_snapshot_changed"
            )
        recent = await self._live_hub.snapshot(
            root_thread_id=root_thread_id, through_sequence=cursor.sequence, max_bytes=128 * 1024
        )
        return ThreadWatch(
            snapshot=ThreadFocusSnapshot(
                epoch=cursor.epoch,
                cutover_sequence=cursor.sequence,
                thread=thread,
                root_operation=root_operation,
                mcp_inputs=await self.mcp_input_requests(root_thread_id),
                tool_executions=await self.tool_executions(root_thread_id),
                recent_events=recent,
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

    async def _accept_mutation(self, result: ConfigurationMutationResult) -> ConfigurationMutationResult:
        candidate = result.configuration
        path = self._require_configuration_path()
        if await self.subscription_accounts.publish_default_groups(
            path.parent,
            frozenset(
                model.authentication.kind.removesuffix("_subscription")
                for model in candidate.models.values()
                if model.authentication.kind in {"codex_subscription", "grok_subscription"}
            ),
            existing_groups=frozenset(candidate.subscription_groups),
        ):
            result = replace(
                result,
                configuration=await load_harness_ui_configuration(path, content_plugin_root=self._content_plugin_root),
            )
        await self._devices.synchronize_registrations(result.configuration.devices.values())
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
        self._devices.update_monitoring_sources(result.configuration.devices.values())
        self._memory_organizer.configuration_changed(result.configuration)
        await self._retire_mcp_bindings(result.configuration)
        self._configuration_fingerprint = await configuration_tree_fingerprint(self._require_configuration_path())
        await self._summary_hub.publish(kind="configuration")
        await self._summary_hub.publish(kind="project")
        return result

    async def _retire_mcp_bindings(self, source: LoadedHarnessUiConfiguration) -> None:
        if self._mcp_connections is not None:
            settings = source.document.webui.mcp_apps
            app_servers = set(settings.servers) if self._mcp_apps is not None and settings.enabled else set()
            retained = set(source.document.mcp.host_owned_servers) | app_servers
            self._mcp_connections.retain_bindings(
                {
                    identity: repr(
                        (
                            server.transport.model_dump_json(),
                            source.document.mcp.protocol_overrides.get(identity, "auto"),
                            identity in app_servers,
                        )
                    )
                    for identity, server in source.mcp_servers.items()
                    if identity in retained
                }
            )

        await self._retire_mcp_owners()

    async def _retire_mcp_owners(self) -> None:
        if self._mcp_connections is not None:
            await self._mcp_app_owners.retire_unselected(self._mcp_connections)

    def _replace_candidate_error(self, replacement: HarnessUiError | None) -> bool:
        previous = None if self._candidate_error is None else (self._candidate_error.code, str(self._candidate_error))
        current = None if replacement is None else (replacement.code, str(replacement))
        self._candidate_error = replacement
        return current != previous

    async def subscriptions(self) -> SubscriptionCatalog:
        """Read native accounts and cached usage without contacting providers."""
        async with self._operation():
            views = []
            for account in await self.subscription_accounts.list():
                views.append(
                    SubscriptionAccountView(
                        account=account,
                        authentication=await self.subscription_accounts.store(account).inspect(),
                        usage=await self.subscription_usage.read(account),
                    )
                )
            current = await self._configurations.current()
            return SubscriptionCatalog(
                accounts=tuple(views),
                groups=tuple(current.subscription_groups.values()) if current else (),
                adoption=await self.subscription_accounts.adoption(),
            )

    async def inspect_subscription_account(self, account_id: str) -> AccountProjection:
        async with self._operation():
            account = await self.subscription_accounts.get(account_id)
            return await self.subscription_accounts.store(account).inspect()

    async def create_subscription_account(self, request: SubscriptionAccountCreate) -> SubscriptionAccount:
        async with self._operation():
            return await self.subscription_accounts.create(request.provider, request.name)

    async def update_subscription_account(
        self, account_id: str, request: SubscriptionAccountUpdate
    ) -> SubscriptionAccount:
        async with self._operation():
            return await self.subscription_accounts.update(account_id, name=request.name, enabled=request.enabled)

    async def delete_subscription_account(self, account_id: str) -> None:
        async with self._operation():
            await self.subscription_accounts.delete(account_id)

    async def logout_subscription_account(self, account_id: str) -> bool:
        async with self._operation():
            return await self.subscription_accounts.logout(account_id)

    async def refresh_subscription_usage(self, account_id: str) -> UsageSnapshot:
        async with self._operation():
            return await self.subscription_usage.refresh(account_id, force=True)

    async def copilot_usage(self) -> CopilotUsage:
        """Read the currently selected Copilot account, independently of managed groups."""
        async with self._operation():
            try:
                async with httpx2.AsyncClient(verify=outbound_tls_verify()) as client:
                    return await read_copilot_usage(self._copilot_account, client)
            except (ValueError, ModelAuthenticationError, AccountStoreError):
                raise AppStateError(
                    "Copilot usage could not be read. Check the selected account or try again later.",
                    code="subscription_usage_failed",
                ) from None

    async def codex_usage(self, account_id: str = "acct-codex-default") -> CodexUsage:
        """Explicitly inspect a selected account's windows and reset vouchers."""
        async with self._operation():
            native = await self.subscription_accounts.get(account_id)
            account = self.subscription_accounts.store(native)
            if not isinstance(account, CodexAccountStore):
                raise AppStateError(
                    "Reset vouchers require a Codex subscription account.", code="subscription_provider_mismatch"
                )
            try:
                async with httpx2.AsyncClient(verify=outbound_tls_verify()) as client:
                    return await CodexUsageClient(
                        account, client, expected_account_id=native.upstream_account_id
                    ).read()
            except ValueError as exc:
                raise AppStateError(str(exc), code="subscription_usage_failed") from None

    async def redeem_codex_reset(self, request: ResetRequest, account_id: str = "acct-codex-default") -> ResetResult:
        """Consume the explicitly selected voucher on the confirmed native account."""
        async with self._operation():
            native = await self.subscription_accounts.get(account_id)
            account = self.subscription_accounts.store(native)
            if not isinstance(account, CodexAccountStore):
                raise AppStateError(
                    "Reset vouchers require a Codex subscription account.", code="subscription_provider_mismatch"
                )
            if native.upstream_account_id != request.account_id:
                raise AppStateError(
                    "The reset confirmation does not match the selected account.",
                    code="subscription_reset_identity_mismatch",
                )
            try:
                async with httpx2.AsyncClient(verify=outbound_tls_verify()) as client:
                    result = await CodexUsageClient(
                        account,
                        client,
                        expected_account_id=request.account_id,
                    ).redeem(request)
            except ValueError as exc:
                raise AppStateError(str(exc), code="subscription_reset_unconfirmed") from None
            # A confirmed redemption stays successful even if a concurrent logout
            # prevents updating its advisory usage snapshot.
            try:
                await self.subscription_usage.refresh(account_id, force=True)
            except AccountStoreError:
                pass
            return result

    def _account(
        self, provider: Provider
    ) -> ChatGPTAccountStore | CodexAccountStore | GrokAccountStore | CopilotAccountStore:
        if provider is Provider.CHATGPT:
            return self._chatgpt_account
        if provider is Provider.COPILOT:
            return self._copilot_account
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

    async def _stop(self, *, graceful: bool = False) -> None:
        get_logger(__name__).debug("Stopping App: finishing admitted operations…")
        async with self._operation_lock:
            if self._state is not AppState.ready:
                return
            self._state = AppState.stopping
            idle = self._operations_idle
        self._tool_executions.stop()
        if self._browsers is not None:
            self._browsers.handback.stop()
        self._memory_organizer.stop()
        if self._voice is not None:
            await self._voice.stop()
        await self._root_runs.stop_admission()
        if graceful:
            await self._restart.drain(self._settings.shutdown_timeout_seconds)
        await self._subagent_operator.stop_admission()
        if self._compaction is not None:
            await self._compaction.stop_admission()

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
        if self._mcp_inputs is not None:
            await self._mcp_inputs.close()
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
                    if self._compaction is not None:
                        await self._compaction.close()
                    try:
                        await self._tool_executions.close(timeout_seconds=self._settings.shutdown_timeout_seconds)
                    finally:
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
    mcp_input_enabled: bool = False,
    share_computer: bool = False,
    browser_no_sandbox: bool = False,
    configuration_error: ConfigurationError | None = None,
    codex_login: CodexLoginCallback | None = None,
    grok_scope: str | None = None,
    grok_refresh: Callable[[GrokCredentials], Awaitable[GrokCredentials]] | None = None,
    grok_login: GrokLoginCallback | None = None,
    copilot_login: CopilotLoginCallback | None = None,
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
            subscription_accounts = SubscriptionAccounts(store.layout.root)
            selected_integrations = integrations or HarnessUiIntegrations()
            catalog = HarnessUiExtensionCatalog(
                provider_plugins=settings.provider_plugins,
                host_capabilities=dict(selected_integrations.capabilities),
                host_providers=selected_integrations.environment_providers,
                host_adapters=selected_integrations.environment_adapters,
                host_plugin_factories=selected_integrations.harness_plugin_factories,
                host_run_extension_factories=(selected_integrations.environment_run_extension_factories),
            )
            resolver = AgentCompositionResolver(catalog, host_mode=host_mode)
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
            await subscription_accounts.adopt_once(
                configuration_path.expanduser().resolve().parent if configuration_path is not None else None,
                grok_scope=grok_scope,
                existing_groups=frozenset(candidate.subscription_groups) if candidate else frozenset(),
                group_providers=frozenset(
                    model.authentication.kind.removesuffix("_subscription")
                    for model in candidate.models.values()
                    if model.authentication.kind in {"codex_subscription", "grok_subscription"}
                )
                if candidate is not None
                else frozenset(),
            )
            if candidate is not None and configuration_path is not None:
                candidate = await load_harness_ui_configuration(
                    configuration_path, content_plugin_root=store.layout.content_plugins
                )
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

            runtime_configuration = await configurations.current()
            envd = EnvdRuntimeManager(
                settings=settings.envd_runtime,
                configuration=None if runtime_configuration is None else runtime_configuration.document.envd,
                runtime_parent=store.layout.runtimes,
                protected_roots=(
                    store.layout.root,
                    *((configuration_path.expanduser().resolve().parent,) if configuration_path else ()),
                ),
            )
            resources.push_async_callback(envd.close)
            environment_reconstructor = EnvironmentSnapshotReconstructor(
                catalog=catalog,
                envd=envd,
                runtime_factories=selected_integrations.provider_runtime_factories,
            )
            thread_files = ThreadFiles(store.layout.root, retention_seconds=settings.storage.scratch_retention_seconds)
            resources.push_async_callback(thread_files.close)
            devices = DeviceConnections(api_keys=ApiKeyStore(store.layout.root / "auth.json"))
            resources.push_async_callback(devices.close)
            device_configuration = await configurations.current()
            if device_configuration is not None:
                devices.update_monitoring_sources(device_configuration.devices.values())
                await devices.synchronize_registrations(device_configuration.devices.values())

            def monitoring_sources() -> Iterable[MonitoringSource]:
                return chain(
                    devices.monitoring_sources(),
                    environment_reconstructor.monitoring_sources(),
                )

            environment_monitoring = EnvironmentMonitoring(
                monitoring_sources, poll_seconds=envd.monitoring_interval_seconds
            )
            environment_service = EnvironmentRunService(
                store,
                environment_reconstructor,
                devices=devices,
                thread_files=thread_files,
                configuration_root=configuration_path.expanduser().resolve().parent
                if configuration_path is not None
                else None,
            )
            live_hub = HarnessUiLiveHub()
            summary_hub = HarnessUiSummaryHub(epoch=live_hub.epoch)

            async def input_changed(thread_id: str) -> None:
                thread = await store.threads.get(thread_id)
                while thread is not None and thread.parent_thread_id is not None:
                    thread = await store.threads.get(thread.parent_thread_id)
                await summary_hub.publish(kind="thread", thread_id=thread.thread_id if thread else thread_id)

            mcp_inputs = Inputs(input_changed) if host_mode == "webui" or mcp_input_enabled else None
            mcp_connections = (
                Connections(input_handler=mcp_inputs, cleanup_timeout_seconds=settings.shutdown_timeout_seconds)
                if host_mode == "webui"
                else HostConnections(
                    input_handler=mcp_inputs, cleanup_timeout_seconds=settings.shutdown_timeout_seconds
                )
            )
            resources.push_async_callback(mcp_connections.close)
            app_connections = mcp_connections if isinstance(mcp_connections, Connections) else None
            mcp_apps = AppSnapshots(store.objects, app_connections) if app_connections is not None else None
            compaction = BackgroundCompaction(store.compactions, store.layout.root)
            resources.push_async_callback(compaction.close)
            agent_reconstructor = AgentReconstructor(
                catalog,
                mcp_apps=app_connections,
                mcp_connections=mcp_connections,
                instrumentation=observation.instrumentation,
                api_keys=ApiKeyStore(store.layout.root / "auth.json"),
                configuration_root=configuration_path.expanduser().resolve().parent
                if configuration_path is not None
                else None,
            )
            cleanup_timeout = min(
                settings.shutdown_timeout_seconds,
                settings.storage.cleanup_timeout_seconds,
            )
            codex_account_error: AccountStoreError | None = None
            try:
                codex_account = subscription_accounts.store(await subscription_accounts.get("acct-codex-default"))
                assert isinstance(codex_account, CodexAccountStore)
            except AccountStoreError as exc:
                codex_account = None
                codex_account_error = exc
            grok_account_error: AccountStoreError | None = None
            try:
                grok_account = subscription_accounts.store(await subscription_accounts.get("acct-grok-default"))
                assert isinstance(grok_account, GrokAccountStore)
            except AccountStoreError as exc:
                grok_account = None
                grok_account_error = exc
            copilot_account = CopilotAccountStore(store.layout.root / "oauth" / "copilot.json")
            from a13n_harness_ui.model_accounts.chatgpt import ChatGPTAccountStore

            subscription_sources: dict[str, SubscriptionSource] = {
                "chatgpt_subscription": ChatGPTSubscriptionSource(
                    create_source=ChatGPTAccountStore(store.layout.root / "auth.json").source
                ),
                "copilot_subscription": CopilotSubscriptionSource(source=copilot_account),
            }
            managed_source = ManagedSubscriptionSource(
                subscription_accounts, SubscriptionUsage(subscription_accounts), grok_refresh=grok_refresh
            )
            subscription_sources["codex_subscription"] = managed_source
            subscription_sources["grok_subscription"] = managed_source

            mcp_operations = None
            if mcp_apps is not None and configuration_path is not None:
                mcp_operations = AppOperations(
                    mcp_apps,
                    CurrentOwners(store, configurations, resolver),
                    configuration_path.expanduser().resolve().parent,
                )
                # Drain admitted operations while their MCP transports and store still exist.
                resources.push_async_callback(mcp_operations.close)

            restart_coordinator = GracefulRestart(store.restarts, enabled=host_mode == "webui")
            tool_executions = ToolExecutions()
            operator = HarnessUiSubagentOperator(
                tool_executions=tool_executions,
                mcp_apps=mcp_apps,
                thread_files=thread_files,
                restart_coordinator=restart_coordinator,
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

            async def authorize_browser(thread_id: str) -> None:
                if host_mode != "webui" or not share_computer:
                    raise HarnessUiError("Host browser access is disabled.", code="browser_disabled")
                thread = await threads.require_interactive(thread_id)
                if thread.parent_thread_id is not None or thread.memory_scope is not None or thread.archived:
                    raise HarnessUiError(
                        "Browser requires an active root conversation.", code="browser_thread_unavailable"
                    )
                # Agent Environment authority is captured by RootRunAdmission.
                # Next-Run defaults do not revoke or expand the current Run.

            browsers = HostBrowsers(
                root=settings.storage.data_root / "browsers",
                available=host_mode == "webui" and share_computer,
                authorize=authorize_browser,
                no_sandbox=browser_no_sandbox,
                configuration=None if runtime_configuration is None else runtime_configuration.document.browser,
            )
            resources.push_async_callback(browsers.close)
            work = ThreadWorkService(store, summary_hub, operator.active_execution_ids)
            root_executor = RootRunExecutor(
                browsers=browsers,
                compaction=compaction,
                tool_executions=tool_executions,
                mcp_apps=mcp_apps,
                restart_coordinator=restart_coordinator,
                work=work,
                store=store,
                threads=threads,
                configurations=configurations,
                compositions=compositions,
                agent_reconstructor=agent_reconstructor,
                environment_service=environment_service,
                subagent_operator=operator,
                subscription_sources=subscription_sources,
                live_hub=live_hub,
                summary_hub=summary_hub,
                cleanup_timeout_seconds=cleanup_timeout,
                thread_files=thread_files,
            )
            web_push = None
            if host_mode == "webui":
                push_client = await resources.enter_async_context(
                    httpx2.AsyncClient(verify=outbound_tls_verify(), timeout=10, follow_redirects=False)
                )
                web_push = WebPush(store, push_client)

            async def run_memory(organization: MemoryOrganizationRun) -> RunUsageSummary:
                model_id = organization.source.memory_organization_model_id
                assert model_id is not None
                scope = organization.scope
                thread = await threads.memory_thread(
                    scope=scope.key,
                    project_id=scope.key.removeprefix("project:") if scope.name == "project" else None,
                    model_id=model_id,
                )
                receipt = await root_runs.submit_organization(thread.thread_id, organization)
                try:
                    operation = await root_runs.wait(receipt.receipt_id)
                except BaseException:
                    # Retain the scope lock until shared execution has joined, even
                    # when disable/deadline/shutdown cancels the opportunity owner.
                    with CancelScope(shield=True):
                        await root_runs.cancel(receipt_id=receipt.receipt_id)
                        await root_runs.wait(receipt.receipt_id)
                    raise
                if operation.status is not RootOperationStatus.completed or operation.outcome is None:
                    raise ThreadError("Memory organization did not complete.", code="memory_organization_failed")
                return TypeAdapter(RunUsageSummary).validate_python(operation.outcome.execution.usage or {})

            memory_organizer = MemoryOrganizer(
                configuration_root=configuration_path.expanduser().resolve().parent if configuration_path else None,
                current=configurations.current,
                run=run_memory,
                webui=host_mode == "webui",
            )

            async def notify_settled(project_id: str | None, operation: RootOperationView) -> None:
                await thread_tools.notify_coordinator(project_id, operation)
                if app is not None and app._voice is not None:
                    await app._voice.executor_settled(operation)

            root_runs = RootRunCoordinator(
                root_executor,
                tool_executions=tool_executions,
                browser_handback=browsers.handback,
                restart_coordinator=restart_coordinator,
                notify=web_push.enqueue if web_push is not None else None,
                on_input_admitted=memory_organizer.offer if host_mode == "webui" else None,
                on_human_admitted=(
                    lambda admission, operation: thread_tools.notify_worker_admitted(admission, operation)
                )
                if host_mode == "webui"
                else None,
                on_settled=notify_settled if host_mode == "webui" else None,
                summary_hub=summary_hub,
                observation=observation,
                touch_thread=store.threads.touch,
                save_execution=store.threads.save_execution,
                interaction_timeouts=host_mode == "webui",
            )
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
                sources: dict[str, SubscriptionSource] = {
                    "chatgpt_subscription": ChatGPTSubscriptionSource(
                        create_source=ChatGPTAccountStore(store.layout.root / "auth.json").source
                    ),
                    "copilot_subscription": CopilotSubscriptionSource(source=copilot_account),
                }
                discovered_codex: CodexAccountStore | None = None
                discovered_grok: GrokAccountStore | None = None
                try:
                    native_codex = subscription_accounts.store(await subscription_accounts.get("acct-codex-default"))
                    assert isinstance(native_codex, CodexAccountStore)
                    discovered_codex = native_codex
                except AccountStoreError as exc:
                    errors[Provider.CODEX] = exc
                try:
                    native_grok = subscription_accounts.store(await subscription_accounts.get("acct-grok-default"))
                    assert isinstance(native_grok, GrokAccountStore)
                    discovered_grok = native_grok
                except AccountStoreError as exc:
                    errors[Provider.GROK] = exc
                sources["codex_subscription"] = managed_source
                sources["grok_subscription"] = managed_source
                root_executor.replace_subscription_sources(sources)
                operator.replace_subscription_sources(sources)
                return discovered_codex, discovered_grok, errors

            app = HarnessUiApp(
                settings,
                store,
                mcp_apps=mcp_apps,
                mcp_operations=mcp_operations,
                mcp_connections=mcp_connections,
                mcp_inputs=mcp_inputs,
                configuration_path=configuration_path,
                catalog=catalog,
                configurations=configurations,
                threads=threads,
                projections=projections,
                terminal_projections=terminal_projections,
                work=work,
                root_runs=root_runs,
                tool_executions=tool_executions,
                thread_files=thread_files,
                subagent_operator=operator,
                live_hub=live_hub,
                summary_hub=summary_hub,
                copilot_account=copilot_account,
                copilot_login=copilot_login,
                codex_account=codex_account,
                codex_account_error=codex_account_error,
                rediscover_accounts=rediscover_accounts,
                resolve_sandbox_executable=environment_reconstructor.resolve_sandbox_executable,
                envd=envd,
                devices=devices,
                environment_monitoring=environment_monitoring,
                grok_account=grok_account,
                grok_account_error=grok_account_error,
                codex_login=codex_login,
                grok_login=grok_login,
                candidate_error=candidate_error,
                browsers=browsers,
                share_computer=share_computer,
                web_push=web_push,
                compaction=compaction,
                memory_organizer=memory_organizer,
                restart_coordinator=restart_coordinator,
            )
            if host_mode == "webui":
                if configuration_path is not None:
                    app._voice = VoiceController(
                        configuration_root=configuration_path.expanduser().resolve().parent,
                        configurations=configurations,
                        threads=threads,
                        store=store,
                        root_runs=root_runs,
                        projections=projections,
                        model=app._voice_model,
                    )
                thread_tools = ThreadToolController(
                    threads=store.threads,
                    projections=projections,
                    root_runs=root_runs,
                    create_thread=app.create_thread,
                    configurations=configurations,
                    inspect_configuration=app.inspect_thread_configuration,
                    voice=app._voice,
                )
                root_executor.set_root_capability_factory(
                    lambda composition: ThreadCollaborationCapability(
                        controller=thread_tools,
                        source_thread_id=composition.thread_id,
                        composition=composition,
                    )
                )
            try:
                await operator.start()
                await root_runs.start()
                app._state = AppState.ready
                async with create_task_group() as background:
                    background.start_soon(browsers.prepare)
                    background.start_soon(run_official_model_updates)
                    memory_organizer.start(background)
                    if app._voice is not None:
                        app._voice.start(background)
                    app._logins = LoginSessions(background, app._account, app.subscription_accounts)
                    background.start_soon(app.subscription_usage.poll)
                    background.start_soon(app._prune_storage_periodically)
                    background.start_soon(app._maintain_read_models)
                    if host_mode == "webui":
                        background.start_soon(environment_monitoring.run)
                    await recover_restart(restart_coordinator, root_runs, operator)
                    if web_push is not None:
                        background.start_soon(web_push.run)
                    if configuration_path is not None:
                        background.start_soon(app._observe_configuration)
                    graceful = False
                    try:
                        yield app
                        graceful = True
                    finally:
                        with CancelScope(shield=True):
                            await app._stop(graceful=graceful)
                        background.cancel_scope.cancel()
            finally:
                await app._close_collaborators()
                with CancelScope(shield=True):
                    await restart_coordinator.commit()
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
