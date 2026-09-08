"""Worker resources entered only after a lock-scoped Runner is activated."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace

from anyio import create_task_group, move_on_after
from pydantic_ai import prices

from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.gateway.a2a_push import append_matching_a2a_push_outbox
from a13n_service.hooks.persistence import write_hook_lifecycle
from a13n_service.interactions.domain import RunAttemptYieldReason
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.object_retention.publication import PublicationObjectStore
from a13n_service.observability import build_observability_runtime
from a13n_service.plugins.runner_bootstrap import BootstrappedPluginRuntime
from a13n_service.process.background import run_critical_component
from a13n_service.process.components import Components
from a13n_service.process.environment import build_environment_catalog
from a13n_service.process.resources import build_execution_resources
from a13n_service.process.runtime import SharedRuntime, WorkerRuntime
from a13n_service.process.worker import build_worker_runtime
from a13n_service.settings import DatabaseBackend, ObjectBackend, RedisBackend, Settings
from a13n_service.storage import open_storage


@asynccontextmanager
async def open_runner_worker(settings: Settings, runner: BootstrappedPluginRuntime) -> AsyncIterator[WorkerRuntime]:
    if (
        settings.database_backend is not DatabaseBackend.postgresql
        or settings.redis_backend is not RedisBackend.redis
        or settings.object_backend is not ObjectBackend.s3
    ):
        raise ValueError("Runner execution requires shared PostgreSQL, Redis, and S3 object storage")
    async with open_storage(settings.storage_settings()) as storage, AsyncExitStack() as stack:
        storage = replace(
            storage,
            objects=PublicationObjectStore(
                storage.objects,
                storage.sessions,
                timeout_seconds=settings.object_publication_timeout_seconds,
            ),
        )
        observability = build_observability_runtime(
            enabled=settings.observability_tracing,
            trace_content=settings.observability_trace_content,
            service_name=settings.service_name,
            service_version=settings.build_version,
            deployment_environment=settings.deployment_environment_name,
            service_role="worker",
            service_instance_id=settings.service_instance_id,
        )
        stack.push_async_callback(observability.aclose)
        if settings.pricing_auto_update:
            stack.enter_context(prices.update_in_background())
        shared = SharedRuntime(
            storage,
            LifecycleWriter(
                (write_hook_lifecycle, append_matching_a2a_push_outbox)
                if settings.a2a_enabled
                else (write_hook_lifecycle,)
            ),
            settings.secret_protector(),
        )
        execution = await build_execution_resources(
            shared,
            built_in_provider_registry(),
            EndpointPolicy.from_operator_allowlist(
                private_domains=settings.model_private_endpoint_domains,
                private_cidrs=settings.model_private_endpoint_cidrs,
            ),
            stack,
        )
        worker, background = await build_worker_runtime(
            settings,
            shared,
            execution,
            build_environment_catalog(settings, Components()),
            stack,
            runner=runner,
            observability=observability,
        )
        async with create_task_group() as tasks:
            for component in background:
                tasks.start_soon(run_critical_component, component.name, component.run, component.return_is_expected)
            try:
                yield worker
            finally:
                if worker.execution_loop is not None:
                    await worker.execution_loop.drain(RunAttemptYieldReason.runner_rotation)
                    await worker.execution_loop.wait_stopped()
                worker.environment_maintenance.drain()
                with move_on_after(settings.environment_operation_timeout_seconds):
                    await worker.environment_maintenance.wait_stopped()
                tasks.cancel_scope.cancel()
