"""Control-plane process composition."""

from __future__ import annotations

from contextlib import AsyncExitStack

from a13n_environment_provider import EnvironmentProviderCatalog

from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
from a13n_service.process.background import BackgroundTask
from a13n_service.process.components import Components
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import ControlRuntime, SharedRuntime, WorkerRuntime
from a13n_service.settings import Settings
from a13n_service.trace_query.provider import TraceQueryProviderRegistry

from .agent import build_agent_management
from .asset import build_asset_bundle
from .environment import build_environment_service
from .hook import build_hook_bundle
from .model import build_model_bundle
from .plugin import build_plugin_bundle
from .skill import build_skill_bundle
from .trace import build_trace_query_service


async def build_control_runtime(
    settings: Settings,
    components: Components,
    shared: SharedRuntime,
    execution: ExecutionResources,
    worker: WorkerRuntime | None,
    environment_catalog: EnvironmentProviderCatalog,
    connectivity_selection: ConnectivitySelectionResolver | None,
    trace_query_provider_registry: TraceQueryProviderRegistry,
    stack: AsyncExitStack,
) -> tuple[ControlRuntime, tuple[BackgroundTask, ...]]:
    """Construct the services owned by a Control-capable role."""

    trace_queries = await build_trace_query_service(
        settings,
        components,
        trace_query_provider_registry,
        stack,
    )
    environments = build_environment_service(shared, environment_catalog)
    local_runner = (
        worker.plugin_runtime
        if worker is not None and isinstance(worker.plugin_runtime, PluginRunnerSupervisor)
        else None
    )
    plugins = await build_plugin_bundle(
        settings,
        components,
        shared,
        execution,
        local_runner,
        stack,
    )
    skills = await build_skill_bundle(components, shared, execution, stack)
    models = build_model_bundle(settings, components, shared, execution)
    agents = build_agent_management(
        settings,
        components,
        shared,
        models.accepted,
        plugins.agent_selection,
        connectivity_selection,
    )
    assets = await build_asset_bundle(settings, shared)
    hooks = await build_hook_bundle(settings, shared, stack)
    runtime = ControlRuntime(
        trace_queries=trace_queries,
        environments=environments,
        plugins=plugins.service,
        skill_uploads=skills.uploads,
        skill_publication=skills.publication,
        skill_catalog=skills.catalog,
        agents=agents,
        models=models.models,
        model_providers=models.providers,
        assets=assets.service,
        hook_subscriptions=hooks.subscriptions,
        lifecycle_events=hooks.lifecycle_events,
    )
    background_tasks = [assets.cleanup_task, hooks.delivery_task, hooks.retention_task]
    if plugins.background_task is not None:
        background_tasks.append(plugins.background_task)
    return runtime, tuple(background_tasks)


__all__ = ["build_control_runtime"]
