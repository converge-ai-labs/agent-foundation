"""Typed process composition."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from a13n_service.agent_configuration.service import ConfigurationService
    from a13n_service.agents.application import AgentManagement
    from a13n_service.assets.catalog import AssetCatalog
    from a13n_service.assets.uploads import AssetUploadService
    from a13n_service.bots.connectivity.service import BotService
    from a13n_service.connectivity.execution import ExternalToolRuntime
    from a13n_service.connectivity.runtime import ConnectivityRuntime
    from a13n_service.environments.lifecycle import EnvironmentLifecycle
    from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop
    from a13n_service.environments.mounts import RunEnvironmentMountService
    from a13n_service.environments.service import EnvironmentService
    from a13n_service.environments.websocket.runtime import ClientConnectionRuntime
    from a13n_service.environments.websocket.worker_connections import WorkerClientConnections
    from a13n_service.gateway import GatewayRuntime
    from a13n_service.hooks.management import HookSubscriptionService
    from a13n_service.iam import RequestAuthenticator
    from a13n_service.iam.runtime import IdentityRuntime
    from a13n_service.interactions.lifecycle import LifecycleWriter
    from a13n_service.interactions.worker import WorkerExecutionLoop
    from a13n_service.lifecycle.service import LifecycleEventService
    from a13n_service.memory.behaviors import MemoryBehaviors
    from a13n_service.memory.providers import MemoryProviderService
    from a13n_service.memory.service import MemoryService
    from a13n_service.models.model_factory import NativeModelFactory
    from a13n_service.models.provider_service import ModelProviderService
    from a13n_service.models.service import ModelService
    from a13n_service.observability import ObservabilityRuntime
    from a13n_service.run_stream import RedisRunStream, RunDisplayStore
    from a13n_service.secrets import SecretProtector
    from a13n_service.settings import Settings
    from a13n_service.skills.catalog import SkillCatalogService
    from a13n_service.skills.publication import SkillPublicationService
    from a13n_service.skills.runtime import SkillRuntimePreparer
    from a13n_service.skills.uploads import SkillUploadService
    from a13n_service.storage import StorageResources
    from a13n_service.subagents.maintenance import SubagentMaintenance
    from a13n_service.trace_query.service import TraceQueryService
    from a13n_service.web.service import WebProviderService

logger = logging.getLogger("a13n_service.process.runtime")


@dataclass(frozen=True, slots=True)
class SharedRuntime:
    """Resources constructed once for every process role."""

    storage: StorageResources
    lifecycle: LifecycleWriter
    secret_protector: SecretProtector
    memories: MemoryService | None = None
    memory_behaviors: MemoryBehaviors | None = None


@dataclass(frozen=True, slots=True)
class ControlRuntime:
    """Control-plane services exposed to request handlers."""

    trace_queries: TraceQueryService
    environments: EnvironmentService
    environment_mounts: RunEnvironmentMountService
    skill_uploads: SkillUploadService
    skill_publication: SkillPublicationService
    skill_catalog: SkillCatalogService
    agents: AgentManagement
    models: ModelService
    model_providers: ModelProviderService
    assets: AssetCatalog
    asset_uploads: AssetUploadService
    hook_subscriptions: HookSubscriptionService
    lifecycle_events: LifecycleEventService
    gateway: GatewayRuntime
    subagent_maintenance: SubagentMaintenance
    web_providers: WebProviderService | None = None
    memory_providers: MemoryProviderService | None = None
    identity: IdentityRuntime | None = None
    configuration: ConfigurationService | None = None
    client_connections: ClientConnectionRuntime | None = None


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    """Worker-owned execution components."""

    external_tools: ExternalToolRuntime
    native_model_factory: NativeModelFactory
    skill_runtime: SkillRuntimePreparer
    environment_maintenance: EnvironmentMaintenanceLoop
    environments: EnvironmentLifecycle
    run_stream: RedisRunStream
    run_display: RunDisplayStore
    execution_loop: WorkerExecutionLoop | None = None
    client_connections: WorkerClientConnections | None = None


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
    bots: BotService | None = None

    def begin_drain(self) -> None:
        """Reject new work before the HTTP server waits for connections to close."""
        first_request = not self.status.draining
        self.status.draining = True
        if self.connectivity is not None and self.connectivity.data is not None:
            if self.connectivity.data.event_connections is not None:
                self.connectivity.data.event_connections.drain()
        if self.worker is not None:
            if self.worker.execution_loop is not None:
                self.worker.execution_loop.begin_drain()
            self.worker.environment_maintenance.drain()
            if self.worker.client_connections is not None:
                self.worker.client_connections.stop_admission()
        if self.control is not None:
            self.control.subagent_maintenance.drain()
            if self.control.client_connections is not None:
                self.control.client_connections.begin_drain()
        if first_request:
            logger.info(
                "service_drain_started",
                extra={"event": "service_drain_started", "role": self.settings.service.role.value},
            )


__all__ = [
    "ControlRuntime",
    "ProcessRuntime",
    "ProcessStatus",
    "SharedRuntime",
    "WorkerRuntime",
]
