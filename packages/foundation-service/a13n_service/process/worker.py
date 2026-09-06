"""Worker process composition."""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta

from a13n_environment_provider import EnvironmentProviderCatalog
from anyio import to_thread

from a13n_service.agents.domain import PluginRuntimeMode
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop
from a13n_service.gateway.agui_replay import HostedAguiReplayStore
from a13n_service.gateway.hosted_agui import HostedAguiTerminalProjector
from a13n_service.ids import new_object_id
from a13n_service.interactions.preflight import OnDemandExecutionPreflight
from a13n_service.interactions.worker import WorkerIdentity
from a13n_service.observability import ObservabilityRuntime
from a13n_service.plugins.materialization import PluginRuntimeMaterializer
from a13n_service.plugins.on_demand import OnDemandPluginRuntime
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
from a13n_service.plugins.runtime import (
    PluginRuntimeLockStore,
    WorkerReleaseManifest,
    default_runtime_target,
    installed_distribution_versions,
    installed_harness_version,
)
from a13n_service.process.background import BackgroundTask
from a13n_service.process.build import worker_build_id
from a13n_service.process.execution import build_attempt_factory, build_execution_loop, build_external_tools
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runner_discovery import RunnerDiscoveryLoop
from a13n_service.process.runtime import SharedRuntime, WorkerRuntime
from a13n_service.run_stream import LifecycleRunStreamProjector, RedisRunStream, RunReplayStore
from a13n_service.settings import Settings
from a13n_service.skills.runtime import SkillRuntimePreparer


async def build_worker_runtime(
    settings: Settings,
    shared: SharedRuntime,
    execution: ExecutionResources,
    environment_catalog: EnvironmentProviderCatalog,
    stack: AsyncExitStack,
    connector_providers: ConnectorProviderRegistry | None = None,
    *,
    observability: ObservabilityRuntime,
) -> tuple[WorkerRuntime, tuple[BackgroundTask, ...]]:
    """Construct the components owned by a Worker-capable role."""

    manifest = WorkerReleaseManifest(
        worker_release=settings.build_version,
        harness_version=installed_harness_version(),
        runtime_target=default_runtime_target(),
        distributions=installed_distribution_versions(),
    )
    materializer = await PluginRuntimeMaterializer.create(
        shared.storage.files_root,
        execution.plugin_objects,
        manifest,
        executable=settings.plugin_runtime_resolver_executable,
        max_wheel_bytes=settings.plugin_max_wheel_bytes,
        max_expanded_bytes=settings.plugin_max_expanded_bytes,
        max_archive_members=settings.plugin_max_archive_members,
        max_runtime_bytes=settings.plugin_runtime_max_materialized_bytes,
        timeout_seconds=settings.plugin_runtime_resolver_timeout_seconds,
        limiter=shared.storage.file_limiter,
    )
    if settings.plugin_runtime_mode is PluginRuntimeMode.runner:
        if (
            settings.database_backend != "postgresql"
            or settings.redis_backend != "redis"
            or settings.object_backend != "s3"
        ):
            raise RuntimeError("Runner execution requires PostgreSQL, Redis, and shared S3 object storage")
        if connector_providers is not None:
            raise RuntimeError("Runner children cannot inherit process-local Connector registry overrides")
        plugin_runtime: OnDemandPluginRuntime | PluginRunnerSupervisor = await stack.enter_async_context(
            PluginRunnerSupervisor(
                materializer,
                settings=settings,
                ready_timeout_seconds=settings.plugin_runner_ready_timeout_seconds,
                command_timeout_seconds=settings.plugin_runner_command_timeout_seconds,
                shutdown_timeout_seconds=settings.plugin_runner_shutdown_timeout_seconds,
                max_processes=settings.plugin_runner_max_processes,
            )
        )
    else:
        plugin_runtime = OnDemandPluginRuntime(materializer)

    environments = EnvironmentLifecycle(
        shared.storage.sessions,
        environment_catalog,
        shared.secret_protector,
        shared.storage.files_root,
        timeout_seconds=settings.environment_operation_timeout_seconds,
    )
    environment_maintenance = EnvironmentMaintenanceLoop(
        environments,
        interval_seconds=settings.environment_maintenance_interval_seconds,
        concurrency=settings.environment_maintenance_concurrency,
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
    external_tools = await build_external_tools(settings, shared, stack, connector_providers)
    skills = SkillRuntimePreparer(shared.storage.sessions, execution.skill_package_store)
    execution_loop = None
    runner_discovery = None
    if isinstance(plugin_runtime, OnDemandPluginRuntime):
        factory = await build_attempt_factory(
            settings,
            shared,
            execution,
            environments,
            external_tools,
            skills,
            run_stream,
            stack,
            observability,
        )
        execution_loop = build_execution_loop(
            settings,
            shared.storage.sessions,
            OnDemandExecutionPreflight(
                shared.storage.sessions,
                PluginRuntimeLockStore(manifest),
                plugin_runtime,
                factory,
                timeout_seconds=settings.plugin_runtime_resolver_timeout_seconds,
            ),
            lifecycle=shared.lifecycle,
            identity=WorkerIdentity(
                new_object_id("worker"), new_object_id("wgen"), await to_thread.run_sync(worker_build_id)
            ),
        )
    else:
        runner_discovery = RunnerDiscoveryLoop(
            settings,
            shared.storage.sessions,
            PluginRuntimeLockStore(manifest),
            plugin_runtime,
            lifecycle=shared.lifecycle,
        )
    runtime = WorkerRuntime(
        external_tools=external_tools,
        plugin_materializer=materializer,
        plugin_runtime=plugin_runtime,
        native_model_factory=execution.native_model_factory,
        skill_runtime=skills,
        environment_maintenance=environment_maintenance,
        environments=environments,
        run_stream=run_stream,
        run_replay=run_replay,
        execution_loop=execution_loop,
        runner_discovery=runner_discovery,
    )
    return runtime, (
        BackgroundTask(
            name="environment_maintenance",
            run=environment_maintenance.run,
            return_is_expected=environment_maintenance.is_draining,
        ),
        BackgroundTask("lifecycle Run Stream projector", lifecycle_projector.run),
        *(
            (BackgroundTask("Worker execution loop", execution_loop.run, execution_loop.is_draining),)
            if execution_loop is not None
            else ()
        ),
        *(
            (BackgroundTask("Runner discovery loop", runner_discovery.run, runner_discovery.is_draining),)
            if runner_discovery is not None
            else ()
        ),
    )


__all__ = ["build_worker_runtime"]
