"""Worker process composition."""

from __future__ import annotations

from contextlib import AsyncExitStack

from a13n_service.agents.domain import PluginRuntimeMode
from a13n_service.plugins.materialization import PluginRuntimeMaterializer
from a13n_service.plugins.on_demand import OnDemandPluginRuntime
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
from a13n_service.plugins.runtime import (
    WorkerReleaseManifest,
    default_runtime_target,
    installed_distribution_versions,
    installed_harness_version,
)
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime, WorkerRuntime
from a13n_service.settings import ServiceSettings
from a13n_service.skills.runtime import SkillRuntimePreparer


async def build_worker_runtime(
    settings: ServiceSettings,
    shared: SharedRuntime,
    execution: ExecutionResources,
    stack: AsyncExitStack,
) -> WorkerRuntime:
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

    return WorkerRuntime(
        plugin_materializer=materializer,
        plugin_runtime=plugin_runtime,
        native_model_factory=execution.native_model_factory,
        skill_runtime=SkillRuntimePreparer(shared.storage.sessions, execution.skill_package_store),
    )


__all__ = ["build_worker_runtime"]
