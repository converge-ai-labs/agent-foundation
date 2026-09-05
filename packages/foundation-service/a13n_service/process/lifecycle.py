"""Supervised process lifecycle for one explicit role composition."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from anyio import create_task_group, move_on_after

from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.agents.plugin_resolution import AgentPluginSelectionResolver
from a13n_service.connectivity.ingress.submission import IngressInputAcceptor
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.gateway.a2a_push import A2A_PUSH_ENABLED_SESSION_INFO_KEY
from a13n_service.hooks import InlineHookValidator
from a13n_service.models.providers import ProviderRegistry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.observability import build_observability_runtime
from a13n_service.process.background import BackgroundTask, run_critical_component
from a13n_service.process.components import Components
from a13n_service.process.connectivity import build_connectivity_runtime
from a13n_service.process.control import build_control_runtime
from a13n_service.process.control.asset import build_asset_bundle
from a13n_service.process.environment import build_environment_catalog
from a13n_service.process.resources import build_execution_resources
from a13n_service.process.roles import owns_connectivity_data, owns_control, owns_worker
from a13n_service.process.runtime import ProcessRuntime, ProcessStatus, SharedRuntime
from a13n_service.process.submission import build_input_commands
from a13n_service.process.worker import build_worker_runtime
from a13n_service.settings import Settings
from a13n_service.storage import open_storage
from a13n_service.trace_query.provider import TraceQueryProviderRegistry

logger = logging.getLogger("a13n_service.process.lifecycle")


@asynccontextmanager
async def open_process_runtime(
    settings: Settings,
    components: Components,
    status: ProcessStatus,
    *,
    trace_query_provider_registry: TraceQueryProviderRegistry,
    model_provider_registry: ProviderRegistry,
    model_endpoint_policy: EndpointPolicy,
) -> AsyncIterator[ProcessRuntime]:
    """Open one supervised runtime for the configured process role."""

    observability = build_observability_runtime(
        enabled=settings.observability_tracing,
        trace_content=settings.observability_trace_content,
        service_name=settings.service_name,
        service_version=settings.build_version,
        deployment_environment=settings.deployment_environment_name,
        service_role=settings.role.value,
        service_instance_id=settings.service_instance_id,
    )
    try:
        async with open_storage(settings.storage_settings()) as storage, AsyncExitStack() as stack:
            storage.sessions.configure(
                info={A2A_PUSH_ENABLED_SESSION_INFO_KEY: settings.a2a_enabled},
            )
            shared = SharedRuntime(
                storage=storage,
                secret_protector=settings.secret_protector(),
            )
            connectivity_selection = ConnectivitySelectionResolver(storage.sessions)
            execution = (
                await build_execution_resources(
                    shared,
                    model_provider_registry,
                    model_endpoint_policy,
                    stack,
                )
                if owns_control(settings.role) or owns_worker(settings.role)
                else None
            )
            environment_catalog = (
                build_environment_catalog(settings, components)
                if owns_control(settings.role) or owns_worker(settings.role)
                else None
            )
            worker = None
            worker_background: tuple[BackgroundTask, ...] = ()
            if owns_worker(settings.role):
                if execution is None or environment_catalog is None:
                    raise RuntimeError("Worker execution resources were not constructed")
                worker, worker_background = await build_worker_runtime(
                    settings,
                    shared,
                    execution,
                    environment_catalog,
                    stack,
                    components.connector_provider_registry,
                )
            control = None
            control_background: tuple[BackgroundTask, ...] = ()
            if owns_control(settings.role):
                if execution is None or environment_catalog is None:
                    raise RuntimeError("Control execution resources were not constructed")
                control, control_background = await build_control_runtime(
                    settings,
                    components,
                    shared,
                    execution,
                    worker,
                    environment_catalog,
                    connectivity_selection,
                    trace_query_provider_registry,
                    stack,
                )
            input_acceptor = components.input_acceptor
            if owns_connectivity_data(settings.role) and input_acceptor is None:
                if control is not None:
                    commands = control.gateway.commands
                else:
                    plugins = components.agent_plugin_selection_resolver or AgentPluginSelectionResolver(
                        storage.sessions,
                        runtime_mode=settings.plugin_runtime_mode,
                        worker_release=settings.build_version,
                    )
                    invocations = components.agent_invocation_resolver or AgentInvocationResolver(
                        storage.sessions,
                        AcceptedModelSelector(storage.sessions, model_provider_registry),
                        plugin_runtime_mode=settings.plugin_runtime_mode,
                        plugin_resolver=plugins,
                        connectivity_resolver=connectivity_selection,
                    )
                    assets = await build_asset_bundle(settings, shared)
                    commands = build_input_commands(
                        settings, shared, invocations, assets.service, InlineHookValidator(EndpointPolicy())
                    )
                input_acceptor = IngressInputAcceptor(storage.sessions, commands)
            connectivity, connectivity_selection, connectivity_background = await build_connectivity_runtime(
                settings,
                storage,
                shared.secret_protector,
                stack,
                ingress_adapters=components.ingress_adapter_registry,
                connector_providers=components.connector_provider_registry,
                input_acceptor=input_acceptor,
                control_plane=owns_control(settings.role),
                data_plane=owns_connectivity_data(settings.role),
            )
            runtime = ProcessRuntime(
                settings=settings,
                status=status,
                request_authenticator=components.request_authenticator,
                observability=observability,
                shared=shared,
                control=control,
                worker=worker,
                connectivity=connectivity,
            )
            background_components = (*worker_background, *control_background, *connectivity_background)
            async with create_task_group() as background_tasks:
                for component in background_components:
                    background_tasks.start_soon(
                        run_critical_component,
                        component.name,
                        component.run,
                        component.return_is_expected,
                    )
                logger.info(
                    "service_started",
                    extra={
                        "event": "service_started",
                        "service": settings.service_name,
                        "role": settings.role.value,
                        "build_version": settings.build_version,
                    },
                )
                status.startup_complete = True
                status.draining = False
                try:
                    yield runtime
                finally:
                    status.draining = True
                    if worker is not None:
                        worker.environment_maintenance.drain()
                        with move_on_after(settings.environment_operation_timeout_seconds):
                            await worker.environment_maintenance.wait_stopped()
                    background_tasks.cancel_scope.cancel()
                    logger.info(
                        "service_stopped",
                        extra={
                            "event": "service_stopped",
                            "service": settings.service_name,
                            "role": settings.role.value,
                        },
                    )
    finally:
        status.startup_complete = False
        await observability.aclose()


__all__ = ["open_process_runtime"]
