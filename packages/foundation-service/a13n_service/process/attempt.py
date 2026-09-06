"""I/O-free assembly and traced ownership of one claimed production Attempt."""

from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any, cast

import httpx2
from a13n_harness import HarnessBuilder, HarnessEvent, HarnessRunResult, HarnessRunResultEvent
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog

from a13n_service.assets.service import AssetService
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.interactions.attempt_executor import CapacitySlot, RunAttemptExecutor
from a13n_service.interactions.attempts import (
    AttemptContext,
    AttemptExecutionService,
    AttemptMutationReceipt,
    AttemptPreparationRejected,
)
from a13n_service.interactions.domain import RunAttemptYieldReason
from a13n_service.interactions.environment_observation import EnvironmentHookObservation
from a13n_service.interactions.harness_results import (
    HarnessOutcomeProjection,
    RunTerminalReceipt,
    StoredHarnessOutcomeAdapter,
)
from a13n_service.interactions.harness_runtime import HarnessDriver
from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler, RedisThreadControlSignals
from a13n_service.interactions.input_runtime import AttemptInputRuntime
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.prepare_harness import ProductionAttemptPreparer
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.scheduling import ClaimedAttempt
from a13n_service.interactions.terminal import DatabaseRunTerminalCommitter
from a13n_service.interactions.wakeups import ThreadControlWakeups
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.observability import ObservabilityRuntime, RunAttemptCorrelation
from a13n_service.observability.runtime import RecoveryReason
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.run_stream import RedisRunStream
from a13n_service.run_stream.publisher import RunStreamHarnessProjector
from a13n_service.settings import Settings
from a13n_service.skills.runtime import SkillRuntimePreparer


class ProductionAttemptFactory:
    def __init__(
        self,
        settings: Settings,
        shared: SharedRuntime,
        execution: ExecutionResources,
        environments: EnvironmentLifecycle,
        external_tools: ExternalToolRuntime,
        skills: SkillRuntimePreparer,
        assets: AssetService,
        input_http: httpx2.AsyncClient,
        stream: RedisRunStream,
        observability: ObservabilityRuntime,
    ) -> None:
        self._settings = settings
        self._shared = shared
        self._execution = execution
        self._environments = environments
        self._external_tools = external_tools
        self._skills = skills
        self._assets = assets
        self._input_http = input_http
        self._stream = stream
        self._observability = observability

    def __call__(
        self, claim: ClaimedAttempt, slot: CapacitySlot, catalog: HarnessPluginFactoryCatalog
    ) -> ProductionAttempt:
        settings, shared, resources = self._settings, self._shared, self._execution
        attempt = claim.attempt
        context = AttemptContext(
            organization_id=attempt.organization_id,
            thread_id=claim.thread_id,
            run_id=attempt.run_id,
            run_attempt_id=attempt.id,
            fence=attempt.fence,
            lease_token=claim.lease_token,
            worker_id=attempt.worker_id,
            worker_generation=attempt.worker_generation,
            worker_build_id=attempt.worker_build_id,
            runtime_lock_digest=attempt.runtime_lock_digest,
            expected_run_version=claim.run_version,
            expected_attempt_version=attempt.version,
            lease_expires_at=attempt.lease_expires_at,
            lease_duration=timedelta(seconds=settings.worker_lease_seconds),
            renewal_interval=timedelta(seconds=settings.worker_renewal_interval_seconds),
            renewal_timeout=timedelta(seconds=settings.worker_renewal_timeout_seconds),
            reconciliation_timeout=timedelta(seconds=settings.worker_reconciliation_timeout_seconds),
            preparation_timeout=timedelta(seconds=settings.worker_preparation_timeout_seconds),
            cleanup_timeout=timedelta(seconds=settings.worker_cleanup_timeout_seconds),
        )
        stack = AsyncExitStack()
        states, payloads = RunStateStore(shared.storage.objects), RunPayloadStore(shared.storage.objects)
        signals = RedisThreadControlSignals(shared.storage.redis)
        inputs = AttemptInputRuntime(
            shared.storage.sessions,
            payloads,
            states,
            self._assets,
            self._input_http,
            stack,
            lambda: control.current_context,
            max_binary_bytes=settings.asset_max_size_bytes,
        )
        execution = AttemptExecutionService(shared.storage.sessions, lifecycle=shared.lifecycle)
        control = RunAttemptControl(
            context=context,
            execution=execution,
            states=states,
            inbox=DatabaseThreadInboxReconciler(shared.storage.sessions, inputs.materialize_inbox),
        )
        projector = _AttemptProjector(self._stream, context)
        driver = HarnessDriver(
            HarnessBuilder(instrumentation=self._observability.harness_instrumentation),
            control=control,
            projector=projector,
        )
        executor = RunAttemptExecutor(
            context=context,
            control=control,
            driver=driver,
            preparer=ProductionAttemptPreparer(
                shared.storage.sessions,
                control,
                catalog,
                inputs,
                self._skills,
                LiveProviderResolver(
                    shared.storage.sessions,
                    resources.model_provider_registry,
                    resources.model_endpoint_policy,
                    shared.secret_protector,
                ),
                resources.native_model_factory,
                self._external_tools,
                stack,
            ),
            wakeups=ThreadControlWakeups(signals, context, poll_interval_seconds=settings.worker_poll_interval_seconds),
            adapter=_AttemptOutcomeAdapter(control, payloads),
            committer=DatabaseRunTerminalCommitter(
                shared.storage.sessions,
                RunOutcomeService(
                    shared.storage.sessions, payloads, lifecycle=shared.lifecycle, control_signals=signals
                ),
                execution,
            ),
            cleanup=_AttemptCleanup(stack, projector),
            capacity_slot=slot,
            environments=self._environments,
        )
        return ProductionAttempt(claim, executor, self._observability)


class ProductionAttempt:
    def __init__(
        self, claim: ClaimedAttempt, executor: RunAttemptExecutor[Any], observability: ObservabilityRuntime
    ) -> None:
        self._claim, self._executor, self._observability = claim, executor, observability

    async def request_handoff(self, reason: RunAttemptYieldReason) -> None:
        await self._executor.request_handoff(reason)

    async def run(self) -> object:
        attempt, run = self._claim.attempt, self._claim.run
        correlation = RunAttemptCorrelation(
            organization_id=run.organization_id,
            workspace_id=self._claim.workspace_id,
            session_id=run.session_id,
            thread_id=run.thread_id,
            run_id=run.id,
            run_attempt_id=attempt.id,
            run_attempt_number=attempt.attempt_number,
            agent_id=run.agent_id,
            agent_revision_id=run.agent_revision_id,
            model_id=attempt.model_execution_observation.model_id,
            replaces_run_attempt_id=attempt.replaces_run_attempt_id,
            recovery_reason=cast(RecoveryReason | None, attempt.recovery_reason),
        )
        with self._observability.run_attempt(correlation) as trace:
            result = await self._executor.run()
            if isinstance(result, RunTerminalReceipt):
                match result.disposition:
                    case "completed" | "waiting":
                        trace.set_outcome("succeeded")
                    case "cancelled":
                        trace.set_outcome("cancelled")
                    case "failed" | "retrying":
                        trace.set_outcome("failed")
            elif isinstance(result, AttemptPreparationRejected):
                trace.set_outcome("failed", failure_code=result.failure.code)
            elif isinstance(result, AttemptMutationReceipt):
                trace.set_outcome("yielded")
            return result


class _AttemptProjector:
    def __init__(self, stream: RedisRunStream, context: AttemptContext) -> None:
        self._stream, self._context = stream, context
        self._projector: RunStreamHarnessProjector | None = None

    def _for_harness(self, harness_run_id: str) -> RunStreamHarnessProjector:
        if self._projector is None:
            context = self._context
            self._projector = RunStreamHarnessProjector(
                self._stream,
                organization_id=context.organization_id,
                run_id=context.run_id,
                thread_id=context.thread_id,
                run_attempt_id=context.run_attempt_id,
                harness_run_id=harness_run_id,
                flush_timeout_seconds=context.cleanup_timeout.total_seconds() / 2,
            )
        return self._projector

    def project(self, event: HarnessEvent | HarnessRunResultEvent[Any]) -> None:
        self._for_harness(event.run_id).project(event)

    def project_environment(self, observation: EnvironmentHookObservation) -> None:
        self._for_harness(observation.harness_run_id).project_environment(observation)

    async def close(self) -> None:
        if self._projector is not None:
            await self._projector.close()


class _AttemptCleanup:
    def __init__(self, stack: AsyncExitStack, projector: _AttemptProjector) -> None:
        self._stack, self._projector = stack, projector

    async def close(self, context: AttemptContext, control: RunAttemptControl, driver: HarnessDriver) -> None:
        try:
            await self._projector.close()
        finally:
            await self._stack.aclose()


class _AttemptOutcomeAdapter:
    def __init__(self, control: RunAttemptControl, payloads: RunPayloadStore) -> None:
        self._control, self._payloads = control, payloads

    async def project[OutputT](self, result: HarnessRunResult[OutputT]) -> HarnessOutcomeProjection:
        context = self._control.current_context
        config = self._control.current_state.envelope.effective_agent_config
        return await StoredHarnessOutcomeAdapter(
            organization_id=context.organization_id,
            run_id=context.run_id,
            payloads=self._payloads,
            max_output_bytes=config.protocol.limits.max_output_bytes,
            inline_output_bytes=min(64 * 1024, config.protocol.limits.max_output_bytes),
            client_tool_surface=[tool.model_dump(mode="json") for tool in config.client_tools] or None,
        ).project(result)
