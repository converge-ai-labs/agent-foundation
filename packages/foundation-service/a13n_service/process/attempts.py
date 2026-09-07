"""Concrete Worker construction of the Foundation-Harness execution boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from typing import cast

from a13n_harness import HarnessBuilder
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from anyio import fail_after

from a13n_service.assets.objects import AssetObjectStore
from a13n_service.connectivity.execution import ExternalToolRuntime
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.interactions.attempt_executor import RunAttemptExecutor
from a13n_service.interactions.attempts import AttemptContext, AttemptExecutionService, read_attempt_lease
from a13n_service.interactions.control_domain import ThreadInboxEntry
from a13n_service.interactions.control_wakeups import AttemptControlWakeups
from a13n_service.interactions.harness_results import StoredHarnessOutcomeAdapter
from a13n_service.interactions.harness_runtime import HarnessDriver
from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler, RedisThreadControlSignals
from a13n_service.interactions.models import RunAttemptRecord, SessionRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.terminal_committer import DatabaseRunTerminalCommitter
from a13n_service.interactions.worker import WorkerCapacitySlot
from a13n_service.interactions.worker_input import WorkerInputSources
from a13n_service.interactions.worker_preparation import WorkerAttemptPreparer
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.observability import ObservabilityRuntime, RunAttemptCorrelation, RunAttemptOutcome
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.run_stream import RedisRunStream, RunReplayStore
from a13n_service.run_stream.attempt_projection import AttemptRunStreamProjector
from a13n_service.skills.runtime import SkillRuntimePreparer
from a13n_service.storage import short_session
from a13n_service.subagents.result_delivery import AsyncSubagentResultMaterializer
from a13n_service.temporal import utc_now


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
        observability: ObservabilityRuntime | None = None,
    ) -> None:
        self._shared = shared
        self._resources = execution
        self._environments = environments
        self._external_tools = external_tools
        self._skills = skills
        self._stream = stream
        self._replay = replay
        self._assets = assets
        self._observability = observability
        self._execution = AttemptExecutionService(shared.storage.sessions, lifecycle=shared.lifecycle)
        self._states = RunStateStore(shared.storage.objects)
        self._payloads = RunPayloadStore(shared.storage.objects)
        self._signals = RedisThreadControlSignals(shared.storage.redis)
        self._committer = DatabaseRunTerminalCommitter(
            shared.storage.sessions,
            RunOutcomeService(
                shared.storage.sessions, self._payloads, lifecycle=shared.lifecycle, control_signals=self._signals
            ),
            self._execution,
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
                row, attempt_row, _ = await read_attempt_lease(session, context, utc_now())
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
        )
        trace_scope = (
            nullcontext(None)
            if self._observability is None
            else self._observability.run_attempt(correlation, input_value=run.input)
        )
        with trace_scope as trace:
            preparer: WorkerAttemptPreparer | None = None

            async def materialize(entry: ThreadInboxEntry):
                if preparer is None:
                    raise RuntimeError("Attempt preparation is not constructed")
                return await preparer.materialize_inbox(entry)

            control = RunAttemptControl(
                context=context,
                execution=self._execution,
                states=self._states,
                inbox=DatabaseThreadInboxReconciler(sessions, materialize),
            )
            sources = WorkerInputSources(
                sessions,
                self._assets,
                self._resources.model_http_client,
                self._resources.model_endpoint_policy,
                run,
                workspace_id,
            )
            preparer = WorkerAttemptPreparer(
                sessions=sessions,
                run=run,
                workspace_id=workspace_id,
                catalog=catalog,
                control=control,
                payloads=self._payloads,
                sources=sources,
                model_resolver=LiveProviderResolver(
                    sessions,
                    self._resources.model_provider_registry,
                    self._resources.model_endpoint_policy,
                    self._shared.secret_protector,
                ),
                model_factory=self._resources.native_model_factory,
                skills=self._skills,
                async_results=AsyncSubagentResultMaterializer(sessions, self._replay),
            )
            projector = AttemptRunStreamProjector(self._stream, context)
            driver = HarnessDriver(
                HarnessBuilder(
                    instrumentation=None if self._observability is None else self._observability.harness_instrumentation
                ),
                control=control,
                projector=projector,
                external_tools=self._external_tools,
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
                cleanup=_AttemptCleanup(sources, projector),
                capacity_slot=slot,
                environments=self._environments,
            )
            await register(control)
            await executor.run()

            if trace is not None:
                async with short_session(sessions) as session:
                    finished = await session.get(RunAttemptRecord, context.run_attempt_id)
                    if finished is not None and finished.status in {"succeeded", "yielded", "failed", "cancelled"}:
                        trace.set_outcome(cast(RunAttemptOutcome, finished.status))


class _AttemptCleanup:
    def __init__(self, sources: WorkerInputSources, projector: AttemptRunStreamProjector) -> None:
        self._sources = sources
        self._projector = projector

    async def close(self, context: AttemptContext, control: RunAttemptControl, driver: HarnessDriver) -> None:
        self._sources.close()
        await self._projector.close()
