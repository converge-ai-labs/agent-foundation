"""Worker process composition."""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta

from a13n_service.agents.domain import PluginRuntimeMode
from a13n_service.environments.catalog import FoundationEnvironmentProviderCatalog
from a13n_service.environments.domain import EnvironmentTargetRetentionBehavior
from a13n_service.environments.keepalive import (
    EnvironmentKeepaliveLoop,
    EnvironmentKeepaliveSourceResolver,
    EnvironmentKeepaliveStore,
    KeepaliveSourceBinding,
    PreparedEnvironmentKeepalive,
)
from a13n_service.ids import new_object_id
from a13n_service.plugins.materialization import PluginRuntimeMaterializer
from a13n_service.plugins.on_demand import OnDemandPluginRuntime
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
from a13n_service.plugins.runtime import (
    WorkerReleaseManifest,
    default_runtime_target,
    installed_distribution_versions,
    installed_harness_version,
)
from a13n_service.process.background import BackgroundTask
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime, WorkerRuntime
from a13n_service.run_stream import LifecycleRunStreamProjector, RedisRunStream, RunReplayStore
from a13n_service.settings import ServiceSettings
from a13n_service.skills.runtime import SkillRuntimePreparer


class _NoEnvironmentKeepaliveSources:
    async def prepare(self, source: KeepaliveSourceBinding) -> PreparedEnvironmentKeepalive | None:
        del source
        return None


async def build_worker_runtime(
    settings: ServiceSettings,
    shared: SharedRuntime,
    execution: ExecutionResources,
    environment_catalog: FoundationEnvironmentProviderCatalog,
    keepalive_sources: EnvironmentKeepaliveSourceResolver | None,
    stack: AsyncExitStack,
) -> tuple[WorkerRuntime, tuple[BackgroundTask, ...]]:
    """Construct the components owned by a Worker-capable role."""

    materializer = await PluginRuntimeMaterializer.create(
        shared.storage.files_root,
        execution.plugin_objects,
        WorkerReleaseManifest(
            worker_release=settings.build_version,
            harness_version=installed_harness_version(),
            runtime_target=default_runtime_target(),
            distributions=installed_distribution_versions(),
        ),
        executable=settings.plugin_runtime_resolver_executable,
        max_wheel_bytes=settings.plugin_max_wheel_bytes,
        max_expanded_bytes=settings.plugin_max_expanded_bytes,
        max_archive_members=settings.plugin_max_archive_members,
        max_runtime_bytes=settings.plugin_runtime_max_materialized_bytes,
        timeout_seconds=settings.plugin_runtime_resolver_timeout_seconds,
        limiter=shared.storage.file_limiter,
    )
    if settings.plugin_runtime_mode is PluginRuntimeMode.runner:
        plugin_runtime: OnDemandPluginRuntime | PluginRunnerSupervisor = await stack.enter_async_context(
            PluginRunnerSupervisor(
                materializer,
                ready_timeout_seconds=settings.plugin_runner_ready_timeout_seconds,
                command_timeout_seconds=settings.plugin_runner_command_timeout_seconds,
                shutdown_timeout_seconds=settings.plugin_runner_shutdown_timeout_seconds,
                max_processes=settings.plugin_runner_max_processes,
            )
        )
    else:
        plugin_runtime = OnDemandPluginRuntime(materializer)

    retained_provider_keys = tuple(
        entry.provider_key
        for entry in environment_catalog.entries()
        if entry.retention_behavior is EnvironmentTargetRetentionBehavior.while_execution_active
    )
    if retained_provider_keys and keepalive_sources is None:
        raise RuntimeError("retaining Environment Providers require a Worker keepalive source resolver")
    environment_keepalive = EnvironmentKeepaliveLoop(
        EnvironmentKeepaliveStore(
            shared.storage.sessions,
            compatible_provider_keys=retained_provider_keys,
        ),
        keepalive_sources or _NoEnvironmentKeepaliveSources(),
        worker_generation=new_object_id("envkw"),
        poll_interval_seconds=settings.environment_keepalive_poll_interval_seconds,
        lease_seconds=settings.environment_keepalive_lease_seconds,
        retention_window_seconds=settings.environment_keepalive_retention_window_seconds,
        refresh_margin_seconds=settings.environment_keepalive_refresh_margin_seconds,
        call_timeout_seconds=settings.environment_keepalive_call_timeout_seconds,
        retry_backoff_seconds=settings.environment_keepalive_retry_backoff_seconds,
        tombstone_retention_seconds=settings.environment_target_tombstone_retention_seconds,
        max_concurrency=settings.environment_keepalive_max_concurrency,
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
    )
    runtime = WorkerRuntime(
        plugin_materializer=materializer,
        plugin_runtime=plugin_runtime,
        native_model_factory=execution.native_model_factory,
        skill_runtime=SkillRuntimePreparer(shared.storage.sessions, execution.skill_package_store),
        environment_keepalive=environment_keepalive,
        run_stream=run_stream,
        run_replay=run_replay,
    )
    return runtime, (
        BackgroundTask(
            name="environment_keepalive",
            run=environment_keepalive.run,
            return_is_expected=environment_keepalive.is_draining,
        ),
        BackgroundTask("lifecycle Run Stream projector", lifecycle_projector.run),
    )


__all__ = ["build_worker_runtime"]
