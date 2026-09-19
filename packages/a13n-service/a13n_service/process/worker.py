"""Worker process composition."""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta
from functools import partial

import httpx2
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_harness.providers.connector import ConnectorProviderCatalog
from a13n_harness.providers.connector.http import ConnectorHttpClient
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.environment.catalog import EnvironmentProviderCatalog

from a13n_service.agent_configuration.drafts import ConfigurationDrafts
from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.resolution import AgentResolver
from a13n_service.assets.catalog import AssetCatalog
from a13n_service.assets.objects import AssetObjectStore
from a13n_service.assets.publication import AssetPublisher
from a13n_service.assets.runtime import AssetRuntime
from a13n_service.assets.staging import AssetStaging
from a13n_service.connectivity.connectors.composition import ConnectorProviders
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.connectivity.file_delivery import FileDelivery
from a13n_service.connectivity.http import cookie_free_jar
from a13n_service.connectivity.native_actions import NativeObservationFactory
from a13n_service.environments.capacity import CapacityLimits
from a13n_service.environments.image_jobs import DockerConnectivityProbe, DockerImageTestWorker
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop
from a13n_service.gateway.agui_replay import HostedAguiReplayStore
from a13n_service.gateway.hosted_agui import HostedAguiTerminalProjector
from a13n_service.hooks import InlineHookValidator
from a13n_service.ids import new_object_id
from a13n_service.interactions.queue_drain import QueueDrain
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.interactions.worker import WorkerExecutionLoop
from a13n_service.memory.organization import MemoryOrganizer
from a13n_service.observability import ObservabilityRuntime
from a13n_service.process.attempts import WorkerAttempts
from a13n_service.process.background import BackgroundTask
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime, WorkerRuntime
from a13n_service.process.submission import build_input_commands
from a13n_service.provider_plugins import ProviderCatalogs, load_provider_catalogs
from a13n_service.run_stream import LifecycleRunStreamProjector, RedisRunStream, RunDisplayStore
from a13n_service.run_stream.display_candidates import DisplayCandidates
from a13n_service.run_stream.display_consumer import DisplayConsumerPolicy, RunDisplayConsumer
from a13n_service.settings import Settings
from a13n_service.skills.runtime import SkillRuntimePreparer
from a13n_service.web.registry import WebProviderRegistry

from .client_environments import build_worker_client_connections
from .connectivity_clients import build_mcp_clients, connectivity_http_timeout


async def build_worker_runtime(
    settings: Settings,
    shared: SharedRuntime,
    execution: ExecutionResources,
    environment_catalog: EnvironmentProviderCatalog,
    stack: AsyncExitStack,
    connector_providers: ConnectorProviders | None = None,
    *,
    provider_catalogs: ProviderCatalogs | None = None,
    plugin_catalog: HarnessPluginFactoryCatalog,
    invocations: AgentInvocationResolver,
    observability: ObservabilityRuntime | None = None,
    configuration_resolver: AgentResolver | None = None,
    observations: NativeObservationFactory | None = None,
) -> tuple[WorkerRuntime, tuple[BackgroundTask, ...]]:
    """Construct the components owned by a Worker-capable role."""

    selected_provider_catalogs = provider_catalogs or load_provider_catalogs(())
    worker_id = new_object_id("wrk")
    client_connections = await build_worker_client_connections(settings, shared, environment_catalog, stack, worker_id)

    environments = EnvironmentLifecycle(
        shared.storage.sessions,
        environment_catalog,
        shared.secret_protector,
        timeout_seconds=settings.environments.operation_timeout_seconds,
        capacity=CapacityLimits(
            max_targets=settings.environments.max_targets_per_workspace,
            max_active=settings.environments.max_active_per_workspace,
        ),
    )
    image_test_worker = (
        DockerImageTestWorker(shared.storage.sessions, shared.storage.redis)
        if "docker" in environment_catalog
        else None
    )
    environment_maintenance = EnvironmentMaintenanceLoop(
        environments,
        interval_seconds=settings.environments.maintenance_interval_seconds,
        concurrency=settings.environments.maintenance_concurrency,
        batch_size=settings.environments.maintenance_batch_size,
    )

    run_stream = RedisRunStream(
        shared.storage.redis,
        max_events=settings.runs.stream_max_events,
        max_event_bytes=settings.runs.stream_max_event_bytes,
        closed_ttl_seconds=settings.runs.stream_closed_ttl_seconds,
        max_pending_events=settings.runs.stream_max_pending_events,
        max_pending_bytes=settings.runs.stream_max_pending_bytes,
        backpressure_timeout_seconds=settings.runs.stream_backpressure_timeout_seconds,
    )
    run_display = RunDisplayStore(
        shared.storage.objects,
        max_items=settings.runs.display_max_items,
        max_bytes=settings.runs.display_max_bytes,
    )
    display_consumer = RunDisplayConsumer(
        DisplayCandidates(shared.storage.sessions),
        run_stream,
        run_display,
        policy=DisplayConsumerPolicy(
            concurrency=settings.runs.display_concurrency,
            candidate_batch_size=settings.runs.display_candidate_batch_size,
            event_batch_size=settings.runs.display_event_batch_size,
            flush_events=settings.runs.display_flush_events,
            flush_bytes=settings.runs.display_flush_bytes,
            flush_interval_seconds=settings.runs.display_flush_interval_seconds,
            poll_interval_seconds=settings.runs.display_poll_interval_seconds,
            operation_timeout_seconds=settings.runs.display_operation_timeout_seconds,
            max_items=settings.runs.display_max_items,
            max_snapshot_bytes=settings.runs.display_max_bytes,
        ),
    )
    lifecycle_projector = LifecycleRunStreamProjector(
        shared.storage.sessions,
        run_stream,
        worker_id=new_object_id("lsp"),
        lease_duration=timedelta(seconds=settings.lifecycle.projection_lease_seconds),
        retry_after=timedelta(seconds=settings.lifecycle.projection_retry_seconds),
        max_attempts=settings.lifecycle.projection_max_attempts,
        poll_interval_seconds=settings.lifecycle.projection_poll_interval_seconds,
        claim_limit=settings.lifecycle.projection_claim_limit,
        terminal_projection=HostedAguiTerminalProjector(
            shared.storage.sessions,
            HostedAguiReplayStore(
                shared.storage.objects,
                max_events=settings.runs.hosted_archive_max_events + 2,
                max_bytes=settings.runs.hosted_archive_max_bytes,
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
    connector_http = ConnectorHttpClient(
        http,
        endpoint_policy,
        response_max_bytes=settings.connectivity.response_max_bytes,
        timeout_seconds=settings.connectivity.total_timeout_seconds,
    )
    staging = await AssetStaging.create(shared.storage.files_root, limiter=shared.storage.file_limiter)
    assets = AssetObjectStore(shared.storage.objects, staging)
    external_tools = ExternalToolRuntime(
        shared.storage.sessions,
        shared.secret_protector,
        connector_providers
        or ConnectorProviders(ConnectorProviderCatalog(selected_provider_catalogs.connector), connector_http),
        clients.transport,
        endpoint_policy,
        http,
        clients.credentials,
        observations=observations,
        files=FileDelivery(AssetCatalog(shared.storage.sessions, assets)),
    )

    skills = SkillRuntimePreparer(shared.storage.sessions, execution.skill_package_store)
    asset_publication = AssetRuntime(
        shared.storage.sessions, AssetPublisher(assets, staging, max_size_bytes=settings.assets.max_size_bytes), assets
    )
    inline_hooks = InlineHookValidator(
        EndpointPolicy.from_operator_allowlist(
            private_domains=settings.webhooks.private_endpoint_domains,
            private_cidrs=settings.webhooks.private_endpoint_cidrs,
        )
    )
    queue_drain = QueueDrain(
        shared.storage.sessions,
        build_input_commands(
            settings, shared, invocations, AssetCatalog(shared.storage.sessions, assets), inline_hooks
        ).queued,
        item_timeout_seconds=settings.control.recovery_item_timeout_seconds,
    )
    execution_loop = WorkerExecutionLoop(
        shared.storage.sessions,
        AttemptScheduler(shared.storage.sessions, lifecycle=shared.lifecycle),
        plugin_catalog,
        WorkerAttempts(
            shared,
            execution,
            environments=environments,
            client_connections=client_connections,
            external_tools=external_tools,
            skills=skills,
            stream=run_stream,
            display=run_display,
            assets=assets,
            asset_publication=asset_publication,
            observability=observability,
            queue_drain=queue_drain,
            web_registry=WebProviderRegistry(selected_provider_catalogs.web),
            configuration_drafts=None
            if configuration_resolver is None
            else ConfigurationDrafts(shared.storage.sessions, configuration_resolver),
        ),
        build_id=settings.service.build_version,
        worker_id=worker_id,
        queue_name=settings.gateway.run_queue_name,
        concurrency=settings.worker.concurrency,
        poll_seconds=settings.worker.poll_interval_seconds,
        lease_seconds=settings.worker.lease_seconds,
        cleanup_seconds=settings.worker.cleanup_seconds,
        drain_seconds=settings.worker.drain_seconds,
    )
    runtime = WorkerRuntime(
        external_tools=external_tools,
        native_model_factory=execution.native_model_factory,
        skill_runtime=skills,
        environment_maintenance=environment_maintenance,
        environments=environments,
        run_stream=run_stream,
        run_display=run_display,
        execution_loop=execution_loop,
        client_connections=client_connections,
    )

    async def shutdown_execution() -> None:
        await execution_loop.drain()
        await execution_loop.wait_stopped()

    execution_task = BackgroundTask(
        "RunAttempt execution", execution_loop.run, execution_loop.is_draining, shutdown_execution
    )
    image_test_tasks: tuple[BackgroundTask, ...] = ()
    if image_test_worker is not None:
        docker_connectivity = DockerConnectivityProbe(shared.storage.sessions, shared.storage.redis)
        image_test_tasks = (
            BackgroundTask(
                name="docker_connectivity_probe",
                run=docker_connectivity.run,
                return_is_expected=lambda: docker_connectivity.draining,
                shutdown=docker_connectivity.shutdown,
            ),
            BackgroundTask(
                name="docker_image_tests",
                run=image_test_worker.run,
                return_is_expected=lambda: image_test_worker.draining,
                shutdown=image_test_worker.shutdown,
            ),
        )
    organization_tasks: tuple[BackgroundTask, ...] = ()
    if shared.memories is not None:
        organizer = MemoryOrganizer(
            shared.memories, shared.storage.objects, execution.native_model_factory, execution.live_model_providers
        )
        organization_tasks = (
            BackgroundTask(
                "memory_organization",
                organizer.run,
                organizer.is_draining,
                partial(organizer.shutdown, timeout_seconds=125),
            ),
        )
    background_tasks = [
        *organization_tasks,
        execution_task,
        *image_test_tasks,
        BackgroundTask(
            name="environment_maintenance",
            run=environment_maintenance.run,
            return_is_expected=environment_maintenance.is_draining,
            shutdown=partial(
                environment_maintenance.shutdown, timeout_seconds=settings.environments.operation_timeout_seconds
            ),
        ),
        BackgroundTask("lifecycle Run Stream projector", lifecycle_projector.run),
        BackgroundTask(
            "Run display persistence", display_consumer.run, display_consumer.is_draining, display_consumer.shutdown
        ),
    ]
    if client_connections is not None:
        background_tasks.append(
            BackgroundTask(
                "Client Environment responses and use leases",
                client_connections.run,
                client_connections.is_closed,
                client_connections.close,
            )
        )
    return runtime, tuple(background_tasks)


__all__ = ["build_worker_runtime"]
