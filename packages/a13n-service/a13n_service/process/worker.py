"""Worker process composition."""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta

import httpx2
from a13n_environment import EnvironmentProviderCatalog
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog

from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.assets.catalog import AssetCatalog
from a13n_service.assets.objects import AssetObjectStore
from a13n_service.assets.publication import AssetPublisher
from a13n_service.assets.runtime import AssetRuntime
from a13n_service.assets.staging import AssetStaging
from a13n_service.connectivity.connectors.providers import built_in_connector_provider_registry
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.connectivity.http import cookie_free_jar
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.environments.capacity import CapacityLimits
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop
from a13n_service.gateway.agui_replay import HostedAguiReplayStore
from a13n_service.gateway.hosted_agui import HostedAguiTerminalProjector
from a13n_service.hooks import InlineHookValidator
from a13n_service.ids import new_object_id
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.queue_completion import QueueCompletion
from a13n_service.interactions.queue_handoff import CompletionQueueHandoffService
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.interactions.worker import WorkerExecutionLoop
from a13n_service.observability import ObservabilityRuntime
from a13n_service.process.attempts import WorkerAttempts
from a13n_service.process.background import BackgroundTask
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime, WorkerRuntime
from a13n_service.process.submission import build_input_commands
from a13n_service.run_stream import LifecycleRunStreamProjector, RedisRunStream, RunReplayStore
from a13n_service.settings import Settings
from a13n_service.skills.runtime import SkillRuntimePreparer

from .connectivity_clients import build_mcp_clients, connectivity_http_timeout


async def build_worker_runtime(
    settings: Settings,
    shared: SharedRuntime,
    execution: ExecutionResources,
    environment_catalog: EnvironmentProviderCatalog,
    stack: AsyncExitStack,
    connector_providers: ConnectorProviderRegistry | None = None,
    *,
    plugin_catalog: HarnessPluginFactoryCatalog,
    invocations: AgentInvocationResolver,
    observability: ObservabilityRuntime | None = None,
) -> tuple[WorkerRuntime, tuple[BackgroundTask, ...]]:
    """Construct the components owned by a Worker-capable role."""

    environments = EnvironmentLifecycle(
        shared.storage.sessions,
        environment_catalog,
        shared.secret_protector,
        shared.storage.files_root,
        timeout_seconds=settings.environment_operation_timeout_seconds,
        capacity=CapacityLimits(
            max_targets=settings.environment_max_targets_per_workspace,
            max_active=settings.environment_max_active_per_workspace,
        ),
    )
    environment_maintenance = EnvironmentMaintenanceLoop(
        environments,
        interval_seconds=settings.environment_maintenance_interval_seconds,
        concurrency=settings.environment_maintenance_concurrency,
        batch_size=settings.environment_maintenance_batch_size,
    )

    run_stream = RedisRunStream(
        shared.storage.redis,
        max_events=settings.run_stream_max_events,
        max_event_bytes=settings.run_stream_max_event_bytes,
        closed_ttl_seconds=settings.run_stream_closed_ttl_seconds,
    )
    run_replay = RunReplayStore(
        shared.storage.objects,
        max_events=settings.run_replay_max_events,
        max_items=settings.run_replay_max_items,
        max_bytes=settings.run_replay_max_bytes,
    )
    lifecycle_projector = LifecycleRunStreamProjector(
        shared.storage.sessions,
        run_stream,
        run_replay,
        worker_id=new_object_id("lsp"),
        lease_duration=timedelta(seconds=settings.lifecycle_projection_lease_seconds),
        retry_after=timedelta(seconds=settings.lifecycle_projection_retry_seconds),
        max_attempts=settings.lifecycle_projection_max_attempts,
        poll_interval_seconds=settings.lifecycle_projection_poll_interval_seconds,
        claim_limit=settings.lifecycle_projection_claim_limit,
        terminal_projection=HostedAguiTerminalProjector(
            shared.storage.sessions,
            HostedAguiReplayStore(
                shared.storage.objects,
                max_events=settings.run_replay_max_events + 2,
                max_bytes=settings.run_replay_max_bytes,
            ),
        ).project,
    )
    endpoint_policy = settings.connectivity_endpoint_policy()
    http = await stack.enter_async_context(
        httpx2.AsyncClient(
            cookies=cookie_free_jar(),
            timeout=connectivity_http_timeout(settings),
            follow_redirects=False,
        )
    )
    clients = build_mcp_clients(settings, shared.storage.sessions, shared.secret_protector, http, endpoint_policy)
    external_tools = ExternalToolRuntime(
        shared.storage.sessions,
        shared.secret_protector,
        connector_providers
        or built_in_connector_provider_registry(
            http,
            endpoint_policy,
            response_max_bytes=settings.connectivity_response_max_bytes,
            timeout_seconds=settings.connectivity_total_timeout_seconds,
        ),
        clients.transport,
        endpoint_policy,
        http,
        clients.credentials,
    )

    skills = SkillRuntimePreparer(shared.storage.sessions, execution.skill_package_store)
    staging = await AssetStaging.create(shared.storage.files_root, limiter=shared.storage.file_limiter)
    assets = AssetObjectStore(shared.storage.objects, staging)
    asset_publication = AssetRuntime(
        shared.storage.sessions, AssetPublisher(assets, staging, max_size_bytes=settings.asset_max_size_bytes), assets
    )
    inline_hooks = InlineHookValidator(
        EndpointPolicy.from_operator_allowlist(
            private_domains=settings.webhook_private_endpoint_domains,
            private_cidrs=settings.webhook_private_endpoint_cidrs,
        )
    )
    queue_completion = QueueCompletion(
        shared.storage.sessions,
        build_input_commands(
            settings, shared, invocations, AssetCatalog(shared.storage.sessions, assets), inline_hooks
        ),
        CompletionQueueHandoffService(
            shared.storage.sessions,
            RunStateStore(shared.storage.objects),
            RunPayloadStore(shared.storage.objects),
            inline_hooks,
            lifecycle=shared.lifecycle,
        ),
    )
    execution_loop = WorkerExecutionLoop(
        shared.storage.sessions,
        AttemptScheduler(shared.storage.sessions, lifecycle=shared.lifecycle),
        plugin_catalog,
        WorkerAttempts(
            shared,
            execution,
            environments=environments,
            external_tools=external_tools,
            skills=skills,
            stream=run_stream,
            replay=run_replay,
            assets=assets,
            asset_publication=asset_publication,
            observability=observability,
            queue_completion=queue_completion,
        ),
        build_id=settings.build_version,
        queue_name=settings.gateway_run_queue_name,
        concurrency=settings.worker_concurrency,
        poll_seconds=settings.worker_poll_interval_seconds,
        lease_seconds=settings.worker_lease_seconds,
        cleanup_seconds=settings.worker_cleanup_seconds,
        drain_seconds=settings.worker_drain_seconds,
    )
    runtime = WorkerRuntime(
        external_tools=external_tools,
        native_model_factory=execution.native_model_factory,
        skill_runtime=skills,
        environment_maintenance=environment_maintenance,
        environments=environments,
        run_stream=run_stream,
        run_replay=run_replay,
        execution_loop=execution_loop,
    )
    execution_task = BackgroundTask("RunAttempt execution", execution_loop.run, execution_loop.is_draining)
    return runtime, (
        execution_task,
        BackgroundTask(
            name="environment_maintenance",
            run=environment_maintenance.run,
            return_is_expected=environment_maintenance.is_draining,
        ),
        BackgroundTask("lifecycle Run Stream projector", lifecycle_projector.run),
    )


__all__ = ["build_worker_runtime"]
