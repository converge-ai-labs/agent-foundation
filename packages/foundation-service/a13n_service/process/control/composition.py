"""Control-plane process composition."""

from __future__ import annotations

from contextlib import AsyncExitStack

import httpx2
from a13n_environment_provider import EnvironmentProviderCatalog

from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.gateway import GatewayRuntime
from a13n_service.gateway.a2a import A2AService
from a13n_service.gateway.a2a_import import A2APartImporter
from a13n_service.gateway.a2a_push import A2APushPublisher
from a13n_service.gateway.agui_replay import HostedAguiReplayStore
from a13n_service.gateway.commands import NativeInteractionCommands
from a13n_service.gateway.hosted_agui import HostedAguiService
from a13n_service.gateway.native_streaming import NativeRunStreamService
from a13n_service.gateway.notifications import NotificationService
from a13n_service.gateway.queries import NativeInteractionQueries
from a13n_service.gateway.queue import NativeQueuedSubmissionService
from a13n_service.interactions import (
    QueuedSubmissionStore,
    RedisThreadControlSignals,
    RunAcceptanceService,
    RunOutcomeService,
    RunPayloadStore,
    RunStateStore,
    ThreadInboxStore,
)
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
from a13n_service.process.background import BackgroundTask
from a13n_service.process.components import Components
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import ControlRuntime, SharedRuntime, WorkerRuntime
from a13n_service.run_stream import RedisRunStream, RunReplayStore
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
    gateway_stream = RedisRunStream(
        shared.storage.redis,
        max_events=settings.run_stream_max_events,
        max_event_bytes=settings.run_stream_max_event_bytes,
        closed_ttl_seconds=settings.run_stream_closed_ttl_seconds,
    )
    gateway_replay = RunReplayStore(
        shared.storage.objects,
        max_events=settings.run_replay_max_events,
        max_items=settings.run_replay_max_items,
        max_bytes=settings.run_replay_max_bytes,
    )
    gateway_states = RunStateStore(shared.storage.objects)
    gateway_payloads = RunPayloadStore(shared.storage.objects)
    gateway_control_signals = RedisThreadControlSignals(shared.storage.redis)
    a2a_endpoint_policy = EndpointPolicy.from_operator_allowlist(
        private_domains=settings.webhook_private_endpoint_domains,
        private_cidrs=settings.webhook_private_endpoint_cidrs,
        require_https=True,
    )

    async def validate_a2a_push_request(request: httpx2.Request) -> None:
        await a2a_endpoint_policy.validate(str(request.url), resolve_dns=True)

    a2a_publisher = None
    a2a_import_http_client = None
    if settings.a2a_enabled:
        a2a_import_http_client = await stack.enter_async_context(
            httpx2.AsyncClient(
                follow_redirects=False,
                timeout=settings.connectivity_total_timeout_seconds,
            )
        )
        a2a_http_client = await stack.enter_async_context(
            httpx2.AsyncClient(
                follow_redirects=False,
                timeout=settings.webhook_request_timeout_seconds,
                event_hooks={"request": [validate_a2a_push_request]},
            )
        )
        a2a_publisher = A2APushPublisher(
            shared.storage.sessions,
            a2a_http_client,
            a2a_endpoint_policy,
            shared.secret_protector,
            poll_interval_seconds=settings.webhook_poll_interval_seconds,
            lease_seconds=settings.webhook_claim_lease_seconds,
            claim_limit=settings.webhook_claim_limit,
            max_attempts=settings.webhook_max_attempts,
            retry_base_seconds=settings.webhook_retry_base_seconds,
            retry_max_seconds=settings.webhook_retry_max_seconds,
            delivery_timeout_seconds=settings.webhook_request_timeout_seconds,
            max_response_bytes=settings.webhook_max_response_bytes,
            max_redirects=settings.connectivity_max_redirects,
        )
    gateway_commands = NativeInteractionCommands(
        shared.storage.sessions,
        agents.invocations,
        RunAcceptanceService(
            shared.storage.sessions,
            gateway_states,
            gateway_payloads,
            hooks.inline_validator,
        ),
        gateway_states,
        assets.service,
        EndpointPolicy(),
        outcomes=RunOutcomeService(
            shared.storage.sessions,
            gateway_payloads,
            control_signals=gateway_control_signals,
        ),
        inbox=ThreadInboxStore(
            shared.storage.sessions,
            signals=gateway_control_signals,
        ),
        payloads=gateway_payloads,
        recovery_max_attempts=settings.gateway_run_recovery_max_attempts,
        max_handoffs=settings.gateway_run_max_handoffs,
        queue_name=settings.gateway_run_queue_name,
        priority=settings.gateway_run_priority,
    )
    if settings.a2a_enabled:
        assert a2a_import_http_client is not None
    gateway = GatewayRuntime(
        commands=gateway_commands,
        hosted_agui=HostedAguiService(
            shared.storage.sessions,
            gateway_commands,
            gateway_stream,
            gateway_replay,
            HostedAguiReplayStore(
                shared.storage.objects,
                max_events=settings.run_replay_max_events + 2,
                max_bytes=settings.run_replay_max_bytes,
            ),
            page_size=settings.gateway_stream_page_size,
            poll_interval_seconds=settings.gateway_stream_poll_interval_seconds,
            heartbeat_interval_seconds=settings.gateway_stream_heartbeat_interval_seconds,
            authorization_interval_seconds=settings.gateway_stream_authorization_interval_seconds,
            maximum_lifetime_seconds=settings.gateway_stream_maximum_lifetime_seconds,
        ),
        native_streams=NativeRunStreamService(
            shared.storage.sessions,
            gateway_stream,
            gateway_replay,
            page_size=settings.gateway_stream_page_size,
            poll_interval_seconds=settings.gateway_stream_poll_interval_seconds,
            heartbeat_interval_seconds=settings.gateway_stream_heartbeat_interval_seconds,
            authorization_interval_seconds=settings.gateway_stream_authorization_interval_seconds,
            maximum_lifetime_seconds=settings.gateway_stream_maximum_lifetime_seconds,
        ),
        notifications=NotificationService(shared.storage.sessions),
        queries=NativeInteractionQueries(shared.storage.sessions, gateway_replay),
        queued_submissions=NativeQueuedSubmissionService(
            shared.storage.sessions,
            QueuedSubmissionStore(shared.storage.sessions, hooks.inline_validator),
            gateway_commands,
        ),
        a2a=(
            A2AService(
                shared.storage.sessions,
                gateway_commands,
                shared.secret_protector,
                a2a_endpoint_policy,
                A2APartImporter(
                    assets.service,
                    a2a_import_http_client,
                    EndpointPolicy(),
                    max_redirects=settings.connectivity_max_redirects,
                    timeout_seconds=settings.connectivity_total_timeout_seconds,
                ),
                poll_interval_seconds=settings.a2a_poll_interval_seconds,
                maximum_wait_seconds=settings.a2a_maximum_wait_seconds,
                push_drain_timeout_seconds=(
                    settings.webhook_claim_lease_seconds + settings.webhook_request_timeout_seconds
                ),
            )
            if settings.a2a_enabled
            else None
        ),
    )
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
        gateway=gateway,
    )
    background_tasks = [assets.cleanup_task, hooks.delivery_task, hooks.retention_task]
    if a2a_publisher is not None:
        background_tasks.append(BackgroundTask("A2A push publisher", a2a_publisher.run))
    if plugins.background_task is not None:
        background_tasks.append(plugins.background_task)
    return runtime, tuple(background_tasks)


__all__ = ["build_control_runtime"]
