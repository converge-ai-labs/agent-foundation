"""Concrete Worker construction of the Service-Harness execution boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from typing import cast

from a13n_harness import HarnessBuilder
from a13n_harness.capabilities import SubagentCapability
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_logging import get_logger
from anyio import fail_after

from a13n_service.assets.objects import AssetObjectStore
from a13n_service.assets.runtime import AssetRuntime
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.interactions.attempt_executor import RunAttemptExecutor
from a13n_service.interactions.attempts import AttemptContext, AttemptExecutionService, read_attempt_authority
from a13n_service.interactions.control_wakeups import AttemptControlWakeups
from a13n_service.interactions.harness_results import AttemptDisposition, AttemptOutcome, StoredHarnessOutcomeAdapter
from a13n_service.interactions.harness_runtime import HarnessDriver
from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler, RedisThreadControlSignals, ThreadInboxStore
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, SessionRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.queue_drain import QueueDrain
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.terminal_committer import DatabaseAttemptCommitter
from a13n_service.interactions.worker import WorkerCapacitySlot
from a13n_service.interactions.worker_input import WorkerInputMaterializer, WorkerInputSources
from a13n_service.interactions.worker_preparation import WorkerAttemptPreparer
from a13n_service.observability import ObservabilityRuntime, RecoveryReason, RunAttemptCorrelation, RunAttemptOutcome
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.run_stream import RedisRunStream, RunReplayStore
from a13n_service.run_stream.activation import PublicationActivator
from a13n_service.run_stream.attempt_projection import AttemptRunStreamProjector
from a13n_service.secrets.agent_runtime import AgentSecretRuntime
from a13n_service.skills.runtime import SkillRuntimePreparer
from a13n_service.storage import short_session
from a13n_service.subagents.result_delivery import AsyncSubagentResultMaterializer
from a13n_service.subagents.runtime import ServiceSubagents
from a13n_service.temporal import utc_now
from a13n_service.web.registry import WebProviderRegistry
from a13n_service.web.runtime import WebRuntime

logger = get_logger(__name__)

_RECOVERY_REASONS: dict[str, RecoveryReason] = {
    "lease_expired": "lease_expired",
    "attempt_failed": "retry_after_failure",
    "retry_after_failure": "retry_after_failure",
    "planned_handoff": "planned_handoff",
    "pending_input": "pending_input",
}


class WorkerAttempts:
    def __init__(
        self,
        shared: SharedRuntime,
        execution: ExecutionResources,
        *,
        environments: EnvironmentLifecycle,
        external_tools: ExternalToolRuntime,
        skills: SkillRuntimePreparer,
        stream: RedisRunStream,
        replay: RunReplayStore,
        assets: AssetObjectStore,
        asset_publication: AssetRuntime,
        observability: ObservabilityRuntime | None = None,
        queue_drain: QueueDrain | None = None,
        web_registry: WebProviderRegistry,
    ) -> None:
        self._shared = shared
        self._resources = execution
        self._environments = environments
        self._external_tools = external_tools
        self._skills = skills
        self._stream = stream
        self._replay = replay
        self._assets = assets
        self._asset_publication = asset_publication
        self._secrets = AgentSecretRuntime(shared.storage.sessions, shared.secret_protector)
        self._web = WebRuntime(shared.storage.sessions, shared.secret_protector, web_registry)
        self._observability = observability
        self._queue_drain = queue_drain
        self._execution = AttemptExecutionService(shared.storage.sessions, lifecycle=shared.lifecycle)
        self._states = RunStateStore(shared.storage.objects)
        self._payloads = RunPayloadStore(shared.storage.objects)
        self._signals = RedisThreadControlSignals(shared.storage.redis)
        outcomes = RunOutcomeService(
            shared.storage.sessions, self._payloads, lifecycle=shared.lifecycle, control_signals=self._signals
        )
        self._committer = DatabaseAttemptCommitter(shared.storage.sessions, outcomes, self._execution)
        self._subagents = ServiceSubagents(
            shared.storage.sessions,
            self._states,
            self._payloads,
            ThreadInboxStore(shared.storage.sessions, signals=self._signals),
            outcomes,
            lifecycle=shared.lifecycle,
        )

    async def run(
        self,
        context: AttemptContext,
        catalog: HarnessPluginFactoryCatalog,
        slot: WorkerCapacitySlot,
        register: Callable[[RunAttemptControl], Awaitable[None]],
    ) -> None:
        sessions = self._shared.storage.sessions
        # Construction is bounded below the renewal interval. The executor starts its
        # lease monitor before any dependency preparation or Harness construction.
        with fail_after(context.renewal_timeout.total_seconds()):
            async with short_session(sessions) as session:
                row, attempt_row, _ = await read_attempt_authority(session, context, utc_now())
                run = row.to_resource()
                attempt = attempt_row.to_resource()
                owner = await session.get(SessionRecord, run.session_id)
                if owner is None:
                    raise ValueError("Run Session is unavailable")
                workspace_id = owner.workspace_id

        correlation = RunAttemptCorrelation(
            organization_id=run.organization_id,
            workspace_id=workspace_id,
            session_id=run.session_id,
            thread_id=run.thread_id,
            run_id=run.id,
            run_attempt_id=attempt.id,
            run_attempt_number=attempt.attempt_number,
            agent_id=run.agent_id,
            agent_revision_id=run.agent_revision_id,
            model_id=run.model_execution_observation.model_id,
            replaces_run_attempt_id=attempt.replaces_run_attempt_id,
            recovery_reason=_RECOVERY_REASONS.get(attempt.start_reason or "") if attempt.attempt_number > 1 else None,
        )
        trace_scope = (
            nullcontext(None)
            if self._observability is None
            else self._observability.run_attempt(
                correlation, input_value=run.input, input_external=run.input_object is not None
            )
        )
        with trace_scope as trace:
            sources = WorkerInputSources(
                sessions,
                self._assets,
                self._resources.model_http_client,
                self._resources.model_endpoint_policy,
                run,
                workspace_id,
                context.authorization,
            )

            async_results = AsyncSubagentResultMaterializer(sessions, self._replay)
            inputs = WorkerInputMaterializer(sources, self._payloads, async_results)
            control = RunAttemptControl(
                context=context,
                execution=self._execution,
                states=self._states,
                inbox=DatabaseThreadInboxReconciler(sessions, inputs.inbox),
            )

            def subagent_capability() -> SubagentCapability:
                config = control.current_state.envelope.effective_agent_config
                return (
                    self._subagents.capability(run=run, authority=control)
                    if config.subagent_mode == "async" and config.resolved_subagents
                    else SubagentCapability()
                )

            preparer = WorkerAttemptPreparer(
                sessions=sessions,
                run=run,
                workspace_id=workspace_id,
                catalog=catalog,
                control=control,
                committer=self._committer,
                payloads=self._payloads,
                sources=sources,
                inputs=inputs,
                model_resolver=self._resources.live_model_providers,
                model_factory=self._resources.native_model_factory,
                skills=self._skills,
                async_results=async_results,
                asset_publication=self._asset_publication,
                environments=self._environments,
                external_tools=self._external_tools,
                subagent_capability=subagent_capability,
                secrets=self._secrets,
                web=self._web,
            )
            projector = AttemptRunStreamProjector(self._stream, context)
            driver = HarnessDriver(
                HarnessBuilder(
                    configured_plugins_enabled=False,
                    instrumentation=None
                    if self._observability is None
                    else self._observability.harness_instrumentation,
                ),
                control=control,
                projector=projector,
            )

            def outcome_adapter() -> StoredHarnessOutcomeAdapter:
                config = control.current_state.envelope.effective_agent_config
                limits = config.protocol.limits
                return StoredHarnessOutcomeAdapter(
                    organization_id=context.organization_id,
                    run_id=context.run_id,
                    payloads=self._payloads,
                    max_output_bytes=limits.max_output_bytes,
                    inline_output_bytes=min(65536, limits.max_output_bytes),
                    client_tool_surface=[tool.model_dump(mode="json") for tool in config.client_tools],
                )

            executor = RunAttemptExecutor(
                context=context,
                control=control,
                driver=driver,
                preparer=preparer,
                wakeups=AttemptControlWakeups(self._signals, context),
                adapter=outcome_adapter,
                committer=self._committer,
                capacity_slot=slot,
                activate_publication=PublicationActivator(sessions, self._stream).activate,
            )
            await register(control)
            receipt = await executor.run()

            if trace is not None:
                if isinstance(receipt, AttemptOutcome):
                    trace.set_disposition(receipt.disposition.value)
                async with short_session(sessions) as session:
                    finished = await session.get(RunAttemptRecord, context.run_attempt_id)
                    finished_attempt = finished.to_resource() if finished is not None else None
                    completed = await session.get(RunRecord, context.run_id)
                    # A succeeded Attempt can merely wait or continue. A later Attempt
                    # can also seal this Run before the current Worker's final read.
                    committed = (
                        completed.to_resource()
                        if completed is not None
                        and completed.status == "completed"
                        and completed.sealed_state_committed_by_run_attempt_id == context.run_attempt_id
                        else None
                    )
                if finished_attempt is not None and finished_attempt.status in {
                    "succeeded",
                    "yielded",
                    "failed",
                    "cancelled",
                }:
                    failure = finished_attempt.failure
                    if committed is not None and finished_attempt.status == "succeeded":
                        trace.set_outcome(
                            "succeeded",
                            output_value=committed.output,
                            output_object_digest=(
                                committed.output_object.digest_sha256 if committed.output_object is not None else None
                            ),
                        )
                    else:
                        trace.set_outcome(
                            cast(RunAttemptOutcome, finished_attempt.status),
                            failure_code=failure.code if failure is not None else None,
                        )

        if (
            self._queue_drain is not None
            and isinstance(receipt, AttemptOutcome)
            and receipt.disposition is AttemptDisposition.completed
        ):
            # The source is committed and its execution resources and monitors are closed.
            # Queue preparation cannot extend its lease or change its terminal decision.
            try:
                with fail_after(min(5.0, context.reconciliation_timeout.total_seconds())):
                    await self._queue_drain.consume_thread(organization_id=run.organization_id, thread_id=run.thread_id)
            except Exception as error:
                logger.warning(
                    "queued_submission_drain_deferred",
                    extra={"run_id": run.id, "thread_id": run.thread_id, "error_type": type(error).__name__},
                )
