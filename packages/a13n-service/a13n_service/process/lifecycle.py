"""Supervised process lifecycle for one explicit role composition."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from functools import partial

import httpx2
from a13n_harness.plugin_factories import build_harness_plugin_factory_catalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.memory import MemoryProviderCatalog
from anyio import create_task_group, to_thread
from pydantic_ai import prices

from a13n_service.bots.connectivity.ingress import BotIngress
from a13n_service.bots.connectivity.replies import ReplyObservations
from a13n_service.bots.connectivity.service import BotService
from a13n_service.bots.memory.behavior import ConversationMemory
from a13n_service.bots.memory.verification import BotMemoryVerifier
from a13n_service.connectivity.http import cookie_free_jar
from a13n_service.connectivity.ingress.submission import IngressInputAcceptor
from a13n_service.gateway.a2a_push import append_matching_a2a_push_outbox
from a13n_service.hooks import InlineHookValidator
from a13n_service.hooks.persistence import write_hook_lifecycle
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.memory.behaviors import MemoryBehaviors
from a13n_service.memory.composition import build_memory_service
from a13n_service.memory.ordinary import OrdinaryMemory
from a13n_service.models.providers import ProviderRegistry
from a13n_service.object_retention.publication import PublicationObjectStore
from a13n_service.observability import build_observability_runtime
from a13n_service.process.agents import build_agent_resolver, build_agent_resources
from a13n_service.process.background import BackgroundTask, run_critical_component, shutdown_background_components
from a13n_service.process.components import Components
from a13n_service.process.connectivity import build_connectivity_runtime
from a13n_service.process.connectivity_clients import connectivity_http_timeout
from a13n_service.process.control import build_control_runtime
from a13n_service.process.control.asset import build_asset_bundle
from a13n_service.process.environment import build_environment_catalog
from a13n_service.process.resources import build_execution_resources
from a13n_service.process.roles import owns_connectivity_data, owns_control, owns_worker
from a13n_service.process.runtime import ProcessRuntime, ProcessStatus, SharedRuntime
from a13n_service.process.submission import build_input_commands
from a13n_service.process.worker import build_worker_runtime
from a13n_service.provider_plugins import ProviderCatalogs
from a13n_service.settings import Settings
from a13n_service.storage import open_storage
from a13n_service.trace_query.provider import TraceQueryProviderRegistry
from a13n_service.web.registry import WebProviderRegistry

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
    provider_catalogs: ProviderCatalogs,
) -> AsyncIterator[ProcessRuntime]:
    """Open one supervised runtime for the configured process role."""

    observability = build_observability_runtime(
        enabled=settings.observability.tracing,
        trace_content=settings.observability.trace_content,
        service_name=settings.service.name,
        service_version=settings.service.build_version,
        deployment_environment=settings.service.deployment_environment_name,
        service_role=settings.service.role.value,
        service_instance_id=settings.service.instance_id,
    )
    try:
        async with open_storage(settings.storage_settings()) as storage, AsyncExitStack() as stack:
            storage = replace(
                storage,
                objects=PublicationObjectStore(
                    storage.objects,
                    storage.sessions,
                    timeout_seconds=settings.objects.publication_timeout_seconds,
                ),
            )
            if owns_worker(settings.service.role) and settings.pricing.auto_update:
                stack.enter_context(prices.update_in_background())
            protector = settings.secret_protector()
            memory_catalog = (
                components.memory_provider_catalog
                if components.memory_provider_catalog is not None
                else MemoryProviderCatalog(provider_catalogs.memory)
            )
            bot_verifier = None
            memory_http = None
            if owns_control(settings.service.role) or owns_worker(settings.service.role):
                memory_http = await stack.enter_async_context(
                    httpx2.AsyncClient(
                        cookies=cookie_free_jar(), follow_redirects=False, timeout=connectivity_http_timeout(settings)
                    )
                )
                bot_verifier = BotMemoryVerifier(
                    storage.sessions,
                    memory_http,
                    settings.connectivity_endpoint_policy(),
                    protector,
                    timeout_seconds=settings.connectivity.total_timeout_seconds,
                )
            shared = SharedRuntime(
                storage=storage,
                lifecycle=LifecycleWriter(
                    (write_hook_lifecycle, append_matching_a2a_push_outbox)
                    if settings.gateway.a2a_enabled
                    else (write_hook_lifecycle,)
                ),
                secret_protector=protector,
                memories=build_memory_service(
                    settings.memory,
                    storage.sessions,
                    protector,
                    memory_catalog,
                )
                if owns_control(settings.service.role) or owns_worker(settings.service.role)
                else None,
            )
            memory_service = shared.memories or build_memory_service(
                settings.memory,
                storage.sessions,
                protector,
                memory_catalog,
            )
            shared = replace(
                shared,
                memory_behaviors=MemoryBehaviors(
                    storage.sessions,
                    default=OrdinaryMemory(memory_service),
                    behaviors=(ConversationMemory(memory_service, bot_verifier),),
                ),
            )
            agent_resources = build_agent_resources(
                components,
                shared,
                model_provider_registry,
                WebProviderRegistry(provider_catalogs.web),
                memory_catalog,
            )
            execution = (
                await build_execution_resources(
                    shared,
                    model_provider_registry,
                    model_endpoint_policy,
                    stack,
                )
                if owns_control(settings.service.role) or owns_worker(settings.service.role)
                else None
            )
            environment_catalog = (
                build_environment_catalog(settings, components, provider_catalogs)
                if owns_control(settings.service.role) or owns_worker(settings.service.role)
                else None
            )
            worker = None
            worker_background: tuple[BackgroundTask, ...] = ()
            if owns_worker(settings.service.role):
                plugin_catalog = components.plugin_factory_catalog
                if plugin_catalog is None:
                    plugin_catalog = await to_thread.run_sync(
                        partial(build_harness_plugin_factory_catalog, plugin_keys=settings.plugins.keys)
                    )
                if execution is None or environment_catalog is None:
                    raise RuntimeError("Worker execution resources were not constructed")
                worker, worker_background = await build_worker_runtime(
                    settings,
                    shared,
                    execution,
                    environment_catalog,
                    stack,
                    components.connector_provider_registry,
                    provider_catalogs=provider_catalogs,
                    plugin_catalog=plugin_catalog,
                    invocations=agent_resources.invocations,
                    configuration_resolver=build_agent_resolver(components, shared, agent_resources),
                    observability=observability,
                    observations=ReplyObservations(storage.sessions),
                )
            control = None
            control_background: tuple[BackgroundTask, ...] = ()
            if owns_control(settings.service.role):
                if execution is None or environment_catalog is None:
                    raise RuntimeError("Control execution resources were not constructed")
                control, control_background = await build_control_runtime(
                    settings,
                    components,
                    shared,
                    execution,
                    environment_catalog,
                    agent_resources,
                    trace_query_provider_registry,
                    stack,
                    provider_catalogs,
                )
            input_acceptor = components.input_acceptor
            if owns_connectivity_data(settings.service.role) and input_acceptor is None:
                if control is not None:
                    commands = control.gateway.commands
                else:
                    assets = await build_asset_bundle(settings, shared)
                    commands = build_input_commands(
                        settings,
                        shared,
                        agent_resources.invocations,
                        assets.catalog,
                        InlineHookValidator(EndpointPolicy()),
                    )
                input_acceptor = IngressInputAcceptor(
                    storage.sessions,
                    commands,
                    contributions={"slack": BotIngress(), "lark": BotIngress(), "github": BotIngress()},
                )
            connectivity, connectivity_background = await build_connectivity_runtime(
                settings,
                storage,
                shared.secret_protector,
                stack,
                ingress_adapters=components.ingress_adapter_registry,
                connector_providers=components.connector_provider_registry,
                provider_catalogs=provider_catalogs,
                input_acceptor=input_acceptor,
                control_plane=owns_control(settings.service.role),
                data_plane=owns_connectivity_data(settings.service.role),
                memory_catalog=memory_catalog,
            )
            bot_service = None
            if connectivity is not None and connectivity.control is not None:
                assert memory_http is not None
                bot_service = BotService(
                    storage.sessions,
                    memory_http,
                    settings.connectivity_endpoint_policy(),
                    protector,
                    public_origin=connectivity.control.public_origin,
                    accounts=connectivity.control.accounts,
                    timeout_seconds=settings.connectivity.total_timeout_seconds,
                )
            runtime = ProcessRuntime(
                settings=settings,
                status=status,
                request_authenticator=components.request_authenticator
                or (control.identity.authenticator if control is not None and control.identity is not None else None),
                observability=observability,
                shared=shared,
                control=control,
                worker=worker,
                connectivity=connectivity,
                bots=bot_service,
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
                        "service": settings.service.name,
                        "role": settings.service.role.value,
                        "build_version": settings.service.build_version,
                    },
                )
                status.startup_complete = True
                status.draining = False
                try:
                    yield runtime
                finally:
                    runtime.begin_drain()
                    await shutdown_background_components(background_components)
                    background_tasks.cancel_scope.cancel()
                    logger.info(
                        "service_stopped",
                        extra={
                            "event": "service_stopped",
                            "service": settings.service.name,
                            "role": settings.service.role.value,
                        },
                    )
    finally:
        status.startup_complete = False
        await observability.aclose()


__all__ = ["open_process_runtime"]
