"""Control-plane process composition."""

from __future__ import annotations

from contextlib import AsyncExitStack
from functools import partial

import httpx2
from a13n_environment import EnvironmentProviderCatalog

from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.gateway import GatewayRuntime
from a13n_service.gateway.a2a import A2AService
from a13n_service.gateway.a2a_import import A2APartImporter
from a13n_service.gateway.a2a_push import A2APushPublisher
from a13n_service.gateway.agui_replay import HostedAguiReplayStore
from a13n_service.gateway.hosted_agui import HostedAguiService
from a13n_service.gateway.labels import InteractionLabels
from a13n_service.gateway.native_streaming import NativeRunStreamService
from a13n_service.gateway.notifications import NotificationService
from a13n_service.gateway.queries import NativeInteractionQueries
from a13n_service.iam.runtime import build_identity_runtime, initialize_identity
from a13n_service.interactions.queue import QueuedSubmissionStore
from a13n_service.interactions.submissions import QueuedSubmissionService
from a13n_service.process.agents import AgentResources
from a13n_service.process.background import BackgroundTask
from a13n_service.process.components import Components
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import ControlRuntime, SharedRuntime
from a13n_service.process.submission import build_input_commands
from a13n_service.run_stream import RedisRunStream, RunReplayStore
from a13n_service.search.service import SearchProviderService
from a13n_service.settings import Settings
from a13n_service.trace_query.provider import TraceQueryProviderRegistry

from .agent import build_agent_management
from .asset import build_asset_bundle
from .collection import build_collection_tasks
from .environment import build_environment_service
from .hook import build_hook_bundle
from .model import build_model_bundle
from .recovery import build_recovery_tasks
from .skill import build_skill_bundle
from .subagent import build_subagent_maintenance
from .trace import build_trace_query_service


async def build_control_runtime(
    settings: Settings,
    components: Components,
    shared: SharedRuntime,
    execution: ExecutionResources,
    environment_catalog: EnvironmentProviderCatalog,
    agent_resources: AgentResources,
    trace_query_provider_registry: TraceQueryProviderRegistry,
    stack: AsyncExitStack,
) -> tuple[ControlRuntime, tuple[BackgroundTask, ...]]:
    """Construct the services owned by a Control-capable role."""

    identity = None
    if components.request_authenticator is None:
        identity = await build_identity_runtime(shared.storage.sessions, settings.identity_configuration())
        await initialize_identity(identity)

    trace_queries = await build_trace_query_service(
        settings,
        components,
        shared,
        trace_query_provider_registry,
        stack,
    )
    environments = await build_environment_service(
        shared, environment_catalog, settings, oss_identity=identity is not None
    )
    skills = await build_skill_bundle(components, shared, execution, stack)
    models = build_model_bundle(settings, components, shared, execution)
    agents = build_agent_management(
        components,
        shared,
        agent_resources,
    )
    assets = await build_asset_bundle(settings, shared)
    hooks = await build_hook_bundle(settings, shared, stack)
    gateway_stream = RedisRunStream(
        shared.storage.redis,
        max_events=settings.runs.stream_max_events,
        max_event_bytes=settings.runs.stream_max_event_bytes,
        closed_ttl_seconds=settings.runs.stream_closed_ttl_seconds,
    )
    gateway_replay = RunReplayStore(
        shared.storage.objects,
        max_events=settings.runs.replay_max_events,
        max_items=settings.runs.replay_max_items,
        max_bytes=settings.runs.replay_max_bytes,
    )
    a2a_endpoint_policy = EndpointPolicy.from_operator_allowlist(
        private_domains=settings.webhooks.private_endpoint_domains,
        private_cidrs=settings.webhooks.private_endpoint_cidrs,
        require_https=True,
    )

    async def validate_a2a_push_request(request: httpx2.Request) -> None:
        await a2a_endpoint_policy.validate(str(request.url), resolve_dns=True)

    a2a_publisher = None
    a2a_import_http_client = None
    if settings.gateway.a2a_enabled:
        a2a_import_http_client = await stack.enter_async_context(
            httpx2.AsyncClient(
                follow_redirects=False,
                timeout=settings.connectivity.total_timeout_seconds,
            )
        )
        a2a_http_client = await stack.enter_async_context(
            httpx2.AsyncClient(
                follow_redirects=False,
                timeout=settings.webhooks.request_timeout_seconds,
                event_hooks={"request": [validate_a2a_push_request]},
            )
        )
        a2a_publisher = A2APushPublisher(
            shared.storage.sessions,
            a2a_http_client,
            a2a_endpoint_policy,
            shared.secret_protector,
            poll_interval_seconds=settings.webhooks.poll_interval_seconds,
            lease_seconds=settings.webhooks.claim_lease_seconds,
            claim_limit=settings.webhooks.claim_limit,
            max_attempts=settings.webhooks.max_attempts,
            retry_base_seconds=settings.webhooks.retry_base_seconds,
            retry_max_seconds=settings.webhooks.retry_max_seconds,
            delivery_timeout_seconds=settings.webhooks.request_timeout_seconds,
            max_response_bytes=settings.webhooks.max_response_bytes,
            max_redirects=settings.connectivity.max_redirects,
        )
    gateway_commands = build_input_commands(
        settings, shared, agents.invocations, assets.catalog, hooks.inline_validator
    )
    if settings.gateway.a2a_enabled:
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
                max_events=settings.runs.replay_max_events + 2,
                max_bytes=settings.runs.replay_max_bytes,
            ),
            page_size=settings.gateway.stream_page_size,
            poll_interval_seconds=settings.gateway.stream_poll_interval_seconds,
            heartbeat_interval_seconds=settings.gateway.stream_heartbeat_interval_seconds,
            authorization_interval_seconds=settings.gateway.stream_authorization_interval_seconds,
            maximum_lifetime_seconds=settings.gateway.stream_maximum_lifetime_seconds,
        ),
        native_streams=NativeRunStreamService(
            shared.storage.sessions,
            gateway_stream,
            gateway_replay,
            page_size=settings.gateway.stream_page_size,
            poll_interval_seconds=settings.gateway.stream_poll_interval_seconds,
            heartbeat_interval_seconds=settings.gateway.stream_heartbeat_interval_seconds,
            authorization_interval_seconds=settings.gateway.stream_authorization_interval_seconds,
            maximum_lifetime_seconds=settings.gateway.stream_maximum_lifetime_seconds,
        ),
        notifications=NotificationService(shared.storage.sessions),
        queries=NativeInteractionQueries(shared.storage.sessions, gateway_replay),
        labels=InteractionLabels(shared.storage.sessions),
        queued_submissions=QueuedSubmissionService(
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
                    assets.uploads,
                    a2a_import_http_client,
                    EndpointPolicy(),
                    max_redirects=settings.connectivity.max_redirects,
                    timeout_seconds=settings.connectivity.total_timeout_seconds,
                ),
                poll_interval_seconds=settings.gateway.a2a_poll_interval_seconds,
                maximum_wait_seconds=settings.gateway.a2a_maximum_wait_seconds,
                push_drain_timeout_seconds=(
                    settings.webhooks.claim_lease_seconds + settings.webhooks.request_timeout_seconds
                ),
            )
            if settings.gateway.a2a_enabled
            else None
        ),
    )
    subagents = build_subagent_maintenance(settings, shared, gateway_replay)
    runtime = ControlRuntime(
        trace_queries=trace_queries,
        environments=environments,
        skill_uploads=skills.uploads,
        skill_publication=skills.publication,
        skill_catalog=skills.catalog,
        agents=agents,
        models=models.models,
        model_providers=models.providers,
        search_providers=SearchProviderService(shared.storage.sessions, shared.secret_protector),
        assets=assets.catalog,
        asset_uploads=assets.uploads,
        hook_subscriptions=hooks.subscriptions,
        lifecycle_events=hooks.lifecycle_events,
        gateway=gateway,
        subagent_maintenance=subagents,
        identity=identity,
    )
    background_tasks = [
        assets.cleanup_task,
        hooks.delivery_task,
        hooks.retention_task,
        BackgroundTask(
            "Subagent reconciliation",
            subagents.run,
            subagents.is_draining,
            shutdown=partial(subagents.shutdown, timeout_seconds=settings.subagents.reconcile_drain_seconds),
        ),
    ]
    background_tasks.extend(build_recovery_tasks(settings, shared, gateway_commands))
    background_tasks.extend(build_collection_tasks(settings, shared))
    if a2a_publisher is not None:
        background_tasks.append(BackgroundTask("A2A push publisher", a2a_publisher.run))
    return runtime, tuple(background_tasks)


__all__ = ["build_control_runtime"]
