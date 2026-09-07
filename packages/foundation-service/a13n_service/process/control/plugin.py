"""Plugin control-plane construction."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx2

from a13n_service.agents.domain import PluginRuntimeMode
from a13n_service.agents.plugin_resolution import AgentPluginSelectionResolver
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
from a13n_service.plugins.runtime_commands import PluginRuntimeCommandCoordinator
from a13n_service.plugins.runtime_resolver import (
    DurableRuntimeCandidateResolver,
    HttpRuntimeDependencyArtifactRetainer,
    UvRuntimeDependencyResolver,
)
from a13n_service.plugins.service import PluginService
from a13n_service.plugins.staging import PluginStaging
from a13n_service.process.background import BackgroundTask
from a13n_service.process.components import Components
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import Settings


@dataclass(frozen=True, slots=True)
class _PluginBundle:
    service: PluginService
    background_task: BackgroundTask | None


async def build_plugin_bundle(
    settings: Settings,
    components: Components,
    shared: SharedRuntime,
    execution: ExecutionResources,
    local_runner: PluginRunnerSupervisor | None,
    agent_selection: AgentPluginSelectionResolver,
    stack: AsyncExitStack,
) -> _PluginBundle:
    """Construct Plugin APIs, Agent selection, and optional runtime coordination."""

    staging = await PluginStaging.create(
        shared.storage.files_root,
        limiter=shared.storage.file_limiter,
    )
    runtime_dispatcher = components.plugin_runtime_command_dispatcher
    candidate_resolver = components.plugin_runtime_candidate_resolver
    staging_authority = components.plugin_runtime_staging_authority or local_runner
    if (candidate_resolver is not None or staging_authority is not None) and (
        settings.plugin_runtime_mode is not PluginRuntimeMode.runner
    ):
        raise RuntimeError("Plugin Runtime coordination is configured outside runner mode")
    if candidate_resolver is not None and staging_authority is None:
        raise RuntimeError("Plugin Runtime coordination requires both a candidate resolver and staging authority")
    if candidate_resolver is None and staging_authority is not None:
        default_index_url = settings.plugin_runtime_default_index_url.get_secret_value()
        index_urls = tuple(value.get_secret_value() for value in settings.plugin_runtime_index_urls)
        dependency_resolver = await UvRuntimeDependencyResolver.create(
            shared.storage.files_root,
            executable=settings.plugin_runtime_resolver_executable,
            default_index_url=default_index_url,
            index_urls=index_urls,
            timeout_seconds=settings.plugin_runtime_resolver_timeout_seconds,
            max_packages=settings.plugin_runtime_resolver_max_packages,
            limiter=shared.storage.file_limiter,
        )
        dependency_http_client = await stack.enter_async_context(httpx2.AsyncClient(follow_redirects=False))
        artifact_retainer = HttpRuntimeDependencyArtifactRetainer(
            dependency_http_client,
            staging,
            execution.plugin_objects,
            max_wheel_bytes=settings.plugin_max_wheel_bytes,
            max_expanded_bytes=settings.plugin_max_expanded_bytes,
            max_archive_members=settings.plugin_max_archive_members,
            index_urls=(default_index_url, *index_urls),
            limiter=shared.storage.file_limiter,
        )
        candidate_resolver = DurableRuntimeCandidateResolver(
            shared.storage.sessions,
            agent_selection.runtime_locks,
            dependency_resolver,
            artifact_retainer,
        )
    command_coordinator: PluginRuntimeCommandCoordinator | None = None
    if runtime_dispatcher is None and candidate_resolver is not None and staging_authority is not None:
        command_coordinator = PluginRuntimeCommandCoordinator(
            shared.storage.sessions,
            candidate_resolver,
            staging_authority,
            poll_interval_seconds=settings.plugin_runtime_command_poll_interval_seconds,
            lease_seconds=settings.plugin_runtime_command_lease_seconds,
        )
        runtime_dispatcher = command_coordinator
    service = PluginService(
        shared.storage.sessions,
        execution.plugin_objects,
        staging,
        runtime_mode=settings.plugin_runtime_mode,
        max_wheel_bytes=settings.plugin_max_wheel_bytes,
        max_expanded_bytes=settings.plugin_max_expanded_bytes,
        max_archive_members=settings.plugin_max_archive_members,
        runtime_command_dispatcher=runtime_dispatcher,
    )
    await service.ensure_runtime_mode()
    for artifact in components.builtin_plugin_artifacts:
        await service.register_builtin(
            registration=artifact.registration,
            body=artifact.body_factory(),
            content_length=artifact.content_length,
        )
    background_task = (
        BackgroundTask("plugin runtime command coordinator", command_coordinator.run)
        if command_coordinator is not None
        else None
    )
    return _PluginBundle(
        service=service,
        background_task=background_task,
    )


__all__ = ["build_plugin_bundle"]
