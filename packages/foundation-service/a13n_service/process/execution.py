"""Execution resource composition shared by on-demand Workers and Runner children."""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.connectors.providers import built_in_connector_provider_registry
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.connectivity.http import cookie_free_jar
from a13n_service.connectivity.mcp.oauth_client import MCPOAuthClient
from a13n_service.connectivity.mcp.refresh import OAuthCredentialRefresh
from a13n_service.connectivity.mcp.transport import RemoteTransport
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.ids import new_object_id
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.interactions.worker import ExecutionPreflight, WorkerExecutionLoop, WorkerIdentity
from a13n_service.observability import ObservabilityRuntime
from a13n_service.process.attempt import ProductionAttemptFactory
from a13n_service.process.control.asset import build_asset_bundle
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.run_stream import RedisRunStream
from a13n_service.settings import Settings
from a13n_service.skills.runtime import SkillRuntimePreparer


async def build_external_tools(
    settings: Settings,
    shared: SharedRuntime,
    stack: AsyncExitStack,
    connector_providers: ConnectorProviderRegistry | None = None,
) -> ExternalToolRuntime:
    endpoint_policy = settings.connectivity_endpoint_policy()
    http = await stack.enter_async_context(
        httpx2.AsyncClient(
            cookies=cookie_free_jar(), timeout=settings.connectivity_total_timeout_seconds, follow_redirects=False
        )
    )
    return ExternalToolRuntime(
        shared.storage.sessions,
        shared.secret_protector,
        connector_providers
        or built_in_connector_provider_registry(
            http, endpoint_policy, response_max_bytes=settings.connectivity_response_max_bytes
        ),
        RemoteTransport(endpoint_policy, timeout_seconds=settings.connectivity_total_timeout_seconds),
        endpoint_policy,
        http,
        OAuthCredentialRefresh(
            shared.storage.sessions,
            MCPOAuthClient(
                http,
                endpoint_policy,
                response_max_bytes=settings.connectivity_response_max_bytes,
                max_redirects=settings.connectivity_max_redirects,
            ),
            shared.secret_protector,
            instance_id=settings.service_instance_id or new_object_id("svc"),
            lease_seconds=settings.connectivity_connector_reconcile_lease_seconds,
            skew_seconds=settings.connectivity_provider_token_expiry_skew_seconds,
        ),
    )


async def build_attempt_factory(
    settings: Settings,
    shared: SharedRuntime,
    execution: ExecutionResources,
    environments: EnvironmentLifecycle,
    external_tools: ExternalToolRuntime,
    skills: SkillRuntimePreparer,
    run_stream: RedisRunStream,
    stack: AsyncExitStack,
    observability: ObservabilityRuntime,
) -> ProductionAttemptFactory:
    assets = await build_asset_bundle(settings, shared)
    input_http = await stack.enter_async_context(
        httpx2.AsyncClient(
            cookies=cookie_free_jar(),
            timeout=settings.worker_preparation_timeout_seconds,
            follow_redirects=False,
        )
    )
    return ProductionAttemptFactory(
        settings,
        shared,
        execution,
        environments,
        external_tools,
        skills,
        assets.service,
        input_http,
        run_stream,
        observability,
    )


def build_execution_loop(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    preflight: ExecutionPreflight,
    identity: WorkerIdentity,
    *,
    lifecycle: LifecycleWriter,
    runtime_lock_digest: str | None = None,
    claim_gated: bool = False,
) -> WorkerExecutionLoop:
    return WorkerExecutionLoop(
        AttemptScheduler(sessions, lifecycle=lifecycle),
        preflight,
        identity=identity,
        lease_duration=timedelta(seconds=settings.worker_lease_seconds),
        handoff_preference_window=timedelta(
            seconds=settings.worker_handoff_preference_seconds or settings.worker_lease_seconds
        ),
        concurrency=settings.worker_concurrency,
        scan_limit=settings.worker_scan_limit,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
        cleanup_timeout_seconds=settings.worker_cleanup_timeout_seconds,
        runtime_lock_digest=runtime_lock_digest,
        claim_gated=claim_gated,
    )
