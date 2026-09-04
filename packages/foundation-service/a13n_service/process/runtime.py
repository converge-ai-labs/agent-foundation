"""Typed process composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a13n_service.agents.service import AgentService
    from a13n_service.assets.service import AssetService
    from a13n_service.connectivity.runtime import ConnectivityRuntime
    from a13n_service.environments.keepalive import EnvironmentKeepaliveLoop
    from a13n_service.environments.service import EnvironmentManagementService
    from a13n_service.iam import RequestAuthenticator
    from a13n_service.models.model_factory import NativeModelFactory
    from a13n_service.models.provider_service import ModelProviderService
    from a13n_service.models.service import ModelService
    from a13n_service.observability import ObservabilityRuntime
    from a13n_service.plugins.materialization import PluginRuntimeMaterializer
    from a13n_service.plugins.on_demand import OnDemandPluginRuntime
    from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
    from a13n_service.plugins.service import PluginService
    from a13n_service.secrets import SecretProtector
    from a13n_service.settings import ServiceSettings
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
    environments: EnvironmentManagementService
    plugins: PluginService
    skill_uploads: SkillUploadService
    skill_publication: SkillPublicationService
    skill_catalog: SkillCatalogService
    agents: AgentService
    models: ModelService
    model_providers: ModelProviderService
    assets: AssetService


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    """Worker-owned execution components."""

    plugin_materializer: PluginRuntimeMaterializer
    plugin_runtime: OnDemandPluginRuntime | PluginRunnerSupervisor
    native_model_factory: NativeModelFactory
    skill_runtime: SkillRuntimePreparer
    environment_keepalive: EnvironmentKeepaliveLoop


@dataclass(slots=True)
class ProcessStatus:
    """Mutable readiness and drain state shared with the HTTP boundary."""

    startup_complete: bool = False
    draining: bool = False


@dataclass(frozen=True, slots=True)
class ServiceRuntime:
    """One explicit runtime for the selected process-role composition."""

    settings: ServiceSettings
    status: ProcessStatus
    request_authenticator: RequestAuthenticator | None
    observability: ObservabilityRuntime
    shared: SharedRuntime
    control: ControlRuntime | None
    worker: WorkerRuntime | None
    connectivity: ConnectivityRuntime | None


__all__ = [
    "ControlRuntime",
    "ProcessStatus",
    "ServiceRuntime",
    "SharedRuntime",
    "WorkerRuntime",
]
