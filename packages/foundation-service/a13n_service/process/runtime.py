"""Typed process composition and transport accessors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from fastapi import Request

    from a13n_service.agents.environment_resolution import AgentEnvironmentSelectionResolver
    from a13n_service.agents.invocation_resolution import AgentInvocationResolver
    from a13n_service.agents.plugin_resolution import AgentPluginSelectionResolver
    from a13n_service.agents.resolution import AgentResolver
    from a13n_service.agents.service import AgentService
    from a13n_service.assets.service import AssetService
    from a13n_service.connectivity.runtime import (
        ConnectivityControlRuntime,
        ConnectivityDataRuntime,
        ConnectivityRuntime,
    )
    from a13n_service.environments.catalog import FoundationEnvironmentProviderCatalog
    from a13n_service.environments.service import EnvironmentManagementService
    from a13n_service.iam import RequestAuthenticator
    from a13n_service.models.model_factory import NativeModelFactory
    from a13n_service.models.provider_service import ModelProviderService
    from a13n_service.models.runtime import AcceptedModelSelector
    from a13n_service.models.service import ModelService
    from a13n_service.observability import ObservabilityRuntime
    from a13n_service.plugins.commands import PluginRuntimeCandidateResolver
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
    """Control-plane services and internal coordination capabilities."""

    trace_queries: TraceQueryService
    environments: EnvironmentManagementService
    environment_provider_catalog: FoundationEnvironmentProviderCatalog
    agent_plugin_selection: AgentPluginSelectionResolver
    plugin_runtime_candidate_resolver: PluginRuntimeCandidateResolver | None
    plugins: PluginService
    skill_uploads: SkillUploadService
    skill_publication: SkillPublicationService
    skill_catalog: SkillCatalogService
    accepted_models: AcceptedModelSelector
    agent_environment_selection: AgentEnvironmentSelectionResolver
    agent_resolver: AgentResolver
    agent_invocation_resolver: AgentInvocationResolver
    agents: AgentService
    models: ModelService
    model_providers: ModelProviderService
    assets: AssetService


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    """Worker-owned execution components."""

    plugin_materializer: PluginRuntimeMaterializer
    plugin_runner: PluginRunnerSupervisor | None
    on_demand_plugins: OnDemandPluginRuntime | None
    native_model_factory: NativeModelFactory
    skill_runtime: SkillRuntimePreparer


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


def get_service_runtime(request: Request) -> ServiceRuntime | None:
    """Return the initialized process runtime at the transport boundary."""

    return cast("ServiceRuntime | None", getattr(request.app.state, "runtime", None))


def get_control_runtime(request: Request) -> ControlRuntime | None:
    """Return Control-plane capabilities when the selected role owns them."""

    runtime = get_service_runtime(request)
    return None if runtime is None else runtime.control


def get_connectivity_control_runtime(request: Request) -> ConnectivityControlRuntime | None:
    """Return Connectivity management capabilities when owned by this role."""

    runtime = get_service_runtime(request)
    if runtime is None or runtime.connectivity is None:
        return None
    return runtime.connectivity.control


def get_connectivity_data_runtime(request: Request) -> ConnectivityDataRuntime | None:
    """Return Connectivity ingress capabilities when owned by this role."""

    runtime = get_service_runtime(request)
    if runtime is None or runtime.connectivity is None:
        return None
    return runtime.connectivity.data


__all__ = [
    "ControlRuntime",
    "ProcessStatus",
    "ServiceRuntime",
    "SharedRuntime",
    "WorkerRuntime",
    "get_connectivity_control_runtime",
    "get_connectivity_data_runtime",
    "get_control_runtime",
    "get_service_runtime",
]
