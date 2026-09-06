"""Execution-only lifespan inside a verified, lock-scoped Runner interpreter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from functools import partial

from anyio import CancelScope, create_task_group, fail_after, move_on_after, to_thread
from pydantic_ai import prices
from sqlalchemy import text

from a13n_service.database import DatabaseMigrator
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.interactions.domain import RunAttemptYieldReason
from a13n_service.interactions.preflight import RunnerExecutionPreflight
from a13n_service.interactions.worker import WorkerExecutionLoop, WorkerIdentity
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.observability import build_observability_runtime
from a13n_service.plugins.runner_bootstrap import BootstrappedPluginRuntime
from a13n_service.process.components import Components
from a13n_service.process.environment import build_environment_catalog
from a13n_service.process.execution import build_attempt_factory, build_execution_loop, build_external_tools
from a13n_service.process.resources import build_execution_resources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.run_stream import RedisRunStream
from a13n_service.settings import Settings
from a13n_service.skills.runtime import SkillRuntimePreparer
from a13n_service.storage import open_storage, short_session


@dataclass(slots=True)
class RunnerExecution:
    loop: WorkerExecutionLoop
    settings: Settings
    scope: CancelScope

    async def run(self) -> None:
        with self.scope:
            await self.loop.run()
        if not self.loop.is_draining():
            raise RuntimeError("Runner execution stopped unexpectedly")

    async def drain(self, reason: RunAttemptYieldReason = RunAttemptYieldReason.service_drain) -> None:
        with move_on_after(self.settings.worker_drain_timeout_seconds):
            await self.loop.drain(reason)
            await self.loop.wait_stopped()
        self.scope.cancel()
        # Joining the loop also joins every Attempt and its bounded resource cleanup.
        await self.loop.wait_stopped()


@asynccontextmanager
async def open_runner_execution(
    settings: Settings,
    runtime: BootstrappedPluginRuntime,
    identity: WorkerIdentity,
) -> AsyncIterator[RunnerExecution]:
    migrator = DatabaseMigrator(settings.database_config(), settings.migration_config())
    await to_thread.run_sync(partial(migrator.current, check_heads=True, verbose=False))
    observability = build_observability_runtime(
        enabled=settings.observability_tracing,
        trace_content=settings.observability_trace_content,
        service_name=settings.service_name,
        service_version=settings.build_version,
        deployment_environment=settings.deployment_environment_name,
        service_role="worker",
        service_instance_id=identity.generation,
    )
    try:
        async with open_storage(settings.storage_settings()) as storage, AsyncExitStack() as stack:
            if settings.pricing_auto_update:
                stack.enter_context(prices.update_in_background())
            shared = SharedRuntime(storage, settings.secret_protector())
            resources = await build_execution_resources(
                shared,
                built_in_provider_registry(),
                EndpointPolicy.from_operator_allowlist(
                    private_domains=settings.model_private_endpoint_domains,
                    private_cidrs=settings.model_private_endpoint_cidrs,
                ),
                stack,
            )
            environments = EnvironmentLifecycle(
                storage.sessions,
                build_environment_catalog(settings, Components()),
                shared.secret_protector,
                storage.files_root,
                timeout_seconds=settings.environment_operation_timeout_seconds,
            )
            external_tools = await build_external_tools(settings, shared, stack)
            stream = RedisRunStream(
                storage.redis,
                max_events=settings.run_stream_max_events,
                max_event_bytes=settings.run_stream_max_event_bytes,
                closed_ttl_seconds=settings.run_stream_closed_ttl_seconds,
            )
            factory = await build_attempt_factory(
                settings,
                shared,
                resources,
                environments,
                external_tools,
                SkillRuntimePreparer(storage.sessions, resources.skill_package_store),
                stream,
                stack,
                observability,
            )
            loop = build_execution_loop(
                settings,
                storage.sessions,
                RunnerExecutionPreflight(runtime, factory),
                identity,
                runtime_lock_digest=runtime.runtime_lock.digest,
                claim_gated=True,
            )
            execution = RunnerExecution(loop, settings, CancelScope())
            with fail_after(settings.database_readiness_timeout_seconds):
                async with short_session(storage.sessions) as database:
                    await database.execute(text("SELECT 1"))
                await storage.redis.ping()
            async with create_task_group() as tasks:
                tasks.start_soon(execution.run)
                await loop.wait_started()
                try:
                    yield execution
                finally:
                    with CancelScope(shield=True):
                        await execution.drain()
    finally:
        await observability.aclose()
