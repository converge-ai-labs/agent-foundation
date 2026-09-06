"""Typed process composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a13n_service.agents.application import AgentManagement
    from a13n_service.assets.service import AssetService
    from a13n_service.connectivity.execution import ExternalToolRuntime
    from a13n_service.connectivity.runtime import ConnectivityRuntime
    from a13n_service.environments.lifecycle import EnvironmentLifecycle
    from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop
    from a13n_service.environments.service import EnvironmentService
    from a13n_service.gateway import GatewayRuntime
    from a13n_service.hooks.management import HookSubscriptionService
    from a13n_service.iam import RequestAuthenticator
    from a13n_service.interactions.worker import WorkerExecutionLoop
    from a13n_service.lifecycle.service import LifecycleEventService
    from a13n_service.models.model_factory import NativeModelFactory
    from a13n_service.models.provider_service import ModelProviderService
    from a13n_service.models.service import ModelService
    from a13n_service.observability import ObservabilityRuntime
    from a13n_service.plugins.materialization import PluginRuntimeMaterializer
    from a13n_service.plugins.on_demand import OnDemandPluginRuntime
    from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
    from a13n_service.plugins.service import PluginService
    from a13n_service.run_stream import RedisRunStream, RunReplayStore
    from a13n_service.secrets import SecretProtector
    from a13n_service.settings import Settings
    from a13n_service.skills.catalog import SkillCatalogService
    from a13n_service.skills.publication import SkillPublicationService
    from a13n_service.skills.runtime import SkillRuntimePreparer
    from a13n_service.skills.uploads import SkillUploadService
    from a13n_service.storage import StorageResources
    from a13n_service.trace_query.service import TraceQueryService


@dataclass(frozen=True, slots=True)
class SharedRuntime:
    """Resources constructed once for every process role."""

    storage: StorageResources
    secret_protector: SecretProtector


@dataclass(frozen=True, slots=True)
class ControlRuntime:
    """Control-plane services exposed to request handlers."""

    trace_queries: TraceQueryService
    environments: EnvironmentService
    plugins: PluginService
    skill_uploads: SkillUploadService
    skill_publication: SkillPublicationService
    skill_catalog: SkillCatalogService
    agents: AgentManagement
    models: ModelService
    model_providers: ModelProviderService
    assets: AssetService
    hook_subscriptions: HookSubscriptionService
    lifecycle_events: LifecycleEventService
    gateway: GatewayRuntime


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    """Worker-owned execution components."""

    external_tools: ExternalToolRuntime
    plugin_materializer: PluginRuntimeMaterializer
    plugin_runtime: OnDemandPluginRuntime | PluginRunnerSupervisor
    native_model_factory: NativeModelFactory
    skill_runtime: SkillRuntimePreparer
    environment_maintenance: EnvironmentMaintenanceLoop
    environments: EnvironmentLifecycle
    run_stream: RedisRunStream
    run_replay: RunReplayStore
    execution_loop: WorkerExecutionLoop | None


@dataclass(slots=True)
class ProcessStatus:
    """Mutable readiness and drain state shared with the HTTP boundary."""

    startup_complete: bool = False
    draining: bool = False


@dataclass(frozen=True, slots=True)
class ProcessRuntime:
    """One explicit runtime for the selected process-role composition."""

    settings: Settings
    status: ProcessStatus
    request_authenticator: RequestAuthenticator | None
    observability: ObservabilityRuntime
    shared: SharedRuntime
    control: ControlRuntime | None
    worker: WorkerRuntime | None
    connectivity: ConnectivityRuntime | None


__all__ = [
    "ControlRuntime",
    "ProcessRuntime",
    "ProcessStatus",
    "SharedRuntime",
    "WorkerRuntime",
]
