"""Supervised process lifecycle for one explicit role composition."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from functools import partial

import httpx2
from a13n_harness.plugin_factories import build_harness_plugin_factory_catalog
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.model.definition import ModelProviderDefinition
from anyio import create_task_group, to_thread
from pydantic_ai import prices

from a13n_service.background import PeriodicTask
from a13n_service.bots.connectivity.ingress import BotIngress
from a13n_service.bots.connectivity.replies import ReplyObservations
from a13n_service.bots.connectivity.service import BotService
from a13n_service.bots.memory.behavior import ConversationMemory
from a13n_service.bots.memory.files import authorize_management
from a13n_service.bots.memory.organization import authorize_organization
from a13n_service.bots.memory.verification import BotMemoryVerifier
from a13n_service.bots.progress.delivery import CardDelivery
from a13n_service.bots.progress.replies import CardReplies
from a13n_service.bots.progress.service import ProgressService
from a13n_service.bots.routines.cards import RoutineCards
from a13n_service.bots.routines.runtime import RoutineTools
from a13n_service.bots.routines.scheduler import RoutineScheduler
from a13n_service.bots.routines.service import RoutineService
from a13n_service.connectivity.http import cookie_free_jar
from a13n_service.connectivity.ingress.attachments import AttachmentInputs
from a13n_service.connectivity.ingress.submission import IngressInputAcceptor
from a13n_service.environments.devices import DeviceDiscovery
from a13n_service.environments.file_access import ExistingEnvironmentFiles
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.websocket.device_reads import DeviceReadClient
from a13n_service.gateway.a2a_push import append_matching_a2a_push_outbox
from a13n_service.hooks import InlineHookValidator
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.memory.behaviors import MemoryBehaviors
from a13n_service.memory.composition import build_memory_service
from a13n_service.memory.ordinary import OrdinaryMemory
from a13n_service.memory.organization import admit_organization
from a13n_service.object_retention.publication import PublicationObjectStore
from a13n_service.observability import build_observability_runtime
from a13n_service.process.agents import build_agent_resolver, build_agent_resources
from a13n_service.process.background import BackgroundTask, run_critical_component, shutdown_background_components
from a13n_service.process.client_environments import build_relay_responses
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

logger = logging.getLogger("a13n_service.process.lifecycle")


@asynccontextmanager
async def open_process_runtime(
    settings: Settings,
    components: Components,
    status: ProcessStatus,
    *,
    trace_query_provider_registry: TraceQueryProviderRegistry,
    model_provider_catalog: ProviderCatalog[ModelProviderDefinition],
    model_endpoint_policy: EndpointPolicy,
    provider_catalogs: ProviderCatalogs,
) -> AsyncIterator[ProcessRuntime]:
    """Open one supervised runtime for the configured process role."""

    observability = build_observability_runtime(
        enabled=settings.observability.tracing,
        metrics_enabled=settings.observability.metrics,
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
                else provider_catalogs.memory
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
                    (append_matching_a2a_push_outbox, admit_organization)
                    if settings.gateway.a2a_enabled
                    else (admit_organization,)
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
            memory_service.authorize_conversation = authorize_management
            memory_service.authorize_organization = authorize_organization
            memory_service.verify_organization = bot_verifier.verify_organization if bot_verifier else None
            shared = replace(
                shared,
                memory_behaviors=MemoryBehaviors(
                    storage.sessions,
                    default=OrdinaryMemory(memory_service),
                    behaviors=(ConversationMemory(memory_service, bot_verifier),),
                ),
            )
            environment_catalog = (
                build_environment_catalog(settings, components, provider_catalogs)
                if owns_control(settings.service.role) or owns_worker(settings.service.role)
                else None
            )
            if environment_catalog is not None:
                memory_service.files = ExistingEnvironmentFiles(
                    EnvironmentLifecycle(
                        storage.sessions,
                        environment_catalog,
                        protector,
                        timeout_seconds=settings.environments.operation_timeout_seconds,
                    )
                )
            agent_resources = build_agent_resources(
                components,
                shared,
                model_provider_catalog,
                provider_catalogs.web,
                memory_catalog,
            )
            execution = (
                await build_execution_resources(
                    shared,
                    model_provider_catalog,
                    model_endpoint_policy,
                    stack,
                )
                if owns_control(settings.service.role) or owns_worker(settings.service.role)
                else None
            )
            relay_responses = (
                await build_relay_responses(settings, storage, environment_catalog, stack)
                if environment_catalog is not None
                else None
            )
            shared = replace(
                shared,
                relay_responses=relay_responses,
                devices=DeviceDiscovery(
                    protector,
                    relay=DeviceReadClient(storage.redis, relay_responses) if relay_responses is not None else None,
                ),
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
                assert memory_http is not None
                worker, worker_background = await build_worker_runtime(
                    settings,
                    shared,
                    execution,
                    environment_catalog,
                    stack,
                    components.connector_providers,
                    components.connector_http,
                    provider_catalogs=provider_catalogs,
                    plugin_catalog=plugin_catalog,
                    invocations=agent_resources.invocations,
                    configuration_resolver=build_agent_resolver(components, shared, agent_resources),
                    observability=observability,
                    tool_contributions=(RoutineTools(RoutineService(storage.sessions)),),
                    observations=ReplyObservations(
                        storage.sessions,
                        cards=CardReplies(
                            CardDelivery(
                                storage.sessions,
                                memory_http,
                                settings.connectivity_endpoint_policy(),
                                protector,
                                public_origin=settings.iam.public_origin,
                            )
                        ),
                    ),
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
                    attachment_uploads = control.asset_uploads
                else:
                    assets = await build_asset_bundle(settings, shared)
                    attachment_uploads = assets.uploads
                    commands = build_input_commands(
                        settings,
                        shared,
                        agent_resources.invocations,
                        assets.catalog,
                        InlineHookValidator(EndpointPolicy()),
                    )
                attachment_http = await stack.enter_async_context(
                    httpx2.AsyncClient(cookies=cookie_free_jar(), follow_redirects=False, timeout=15)
                )
                input_acceptor = IngressInputAcceptor(
                    storage.sessions,
                    commands,
                    contributions={"slack": BotIngress(), "lark": BotIngress(), "github": BotIngress()},
                    attachments=AttachmentInputs(
                        storage.sessions,
                        attachment_uploads,
                        protector,
                        attachment_http,
                        settings.connectivity_endpoint_policy(),
                    ),
                )
            connectivity, connectivity_background = await build_connectivity_runtime(
                settings,
                storage,
                shared.secret_protector,
                stack,
                ingress_adapters=components.ingress_adapter_registry,
                connector_providers=components.connector_providers,
                connector_http=components.connector_http,
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
            progress_background: tuple[BackgroundTask, ...] = ()
            if control is not None:
                assert memory_http is not None
                progress = ProgressService(
                    storage.sessions,
                    control.gateway.commands,
                    memory_http,
                    settings.connectivity_endpoint_policy(),
                    protector,
                    public_origin=settings.iam.public_origin,
                )
                progress_loop = PeriodicTask(
                    "bot_task_progress",
                    progress.scan,
                    interval_seconds=2,
                    timeout_seconds=240,
                )

                async def shutdown_progress() -> None:
                    await progress_loop.shutdown(timeout_seconds=5)

                progress_background = (
                    BackgroundTask(
                        "bot task progress",
                        progress_loop.run,
                        return_is_expected=progress_loop.is_draining,
                        shutdown=shutdown_progress,
                    ),
                )
                routines = RoutineScheduler(
                    storage.sessions,
                    control.gateway.commands,
                    RoutineCards(storage.sessions, memory_http, protector, settings.connectivity_endpoint_policy()),
                )
                routine_loop = PeriodicTask("bot_routines", routines.scan, interval_seconds=5, timeout_seconds=650)

                async def shutdown_routines() -> None:
                    await routine_loop.shutdown(timeout_seconds=5)

                progress_background += (
                    BackgroundTask(
                        "bot routines",
                        routine_loop.run,
                        return_is_expected=routine_loop.is_draining,
                        shutdown=shutdown_routines,
                    ),
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
            relay_background = (
                (
                    BackgroundTask(
                        "Environment relay responses",
                        relay_responses.run,
                        relay_responses.is_closed,
                        relay_responses.close,
                    ),
                )
                if relay_responses is not None
                else ()
            )
            background_components = (
                *worker_background,
                *control_background,
                *connectivity_background,
                *progress_background,
                *relay_background,
            )
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
