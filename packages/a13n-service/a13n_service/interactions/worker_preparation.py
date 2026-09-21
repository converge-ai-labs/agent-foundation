"""Coordinate dependency preparation, input restoration, and Attempt resource lifetime."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .agent_runtime import AgentRuntimeAssembler, PreparedRuntime
from .attempt_environments import AttemptEnvironments
from .attempt_resources import attempt_resource_stack
from .attempts import AttemptContext
from .domain import Run
from .harness_results import AttemptCommitter
from .harness_runtime import HarnessInvocation
from .run_control import RunAttemptControl
from .worker_input import WorkerInputMaterializer, WorkerInputSources


class WorkerAttemptPreparer:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        run: Run,
        control: RunAttemptControl,
        runtime: AgentRuntimeAssembler,
        environments: AttemptEnvironments,
        committer: AttemptCommitter,
        sources: WorkerInputSources,
        inputs: WorkerInputMaterializer,
    ) -> None:
        self._sessions = sessions
        self._run = run
        self._control = control
        self._runtime = runtime
        self._environments = environments
        self._committer = committer
        self._sources = sources
        self._inputs = inputs
        self._prepared: PreparedRuntime | None = None

    async def claim_state_writer(self) -> None:
        await self._control.claim_state_writer(self._run)

    async def validate_dependencies(self, context: AttemptContext) -> None:
        self._prepared = await self._runtime.prepare(context)

    @asynccontextmanager
    async def open_runtime(self, context: AttemptContext) -> AsyncIterator[HarnessInvocation[Any]]:
        prepared = self._prepared
        if prepared is None:
            raise RuntimeError("Attempt dependencies have not been validated")
        async with attempt_resource_stack(cleanup_timeout_seconds=context.cleanup_timeout.total_seconds()) as stack:
            stack.callback(self._sources.close)
            invocation = await self._runtime.assemble(prepared, stack)
            input_source, resume = await self._inputs.restore(self._run, self._control, self._committer, self._sessions)
            environment = await self._runtime.source.open_environment(self._environments, stack)
            yield replace(invocation, input=input_source, deferred_resume=resume, environment=environment)
