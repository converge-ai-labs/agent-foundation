"""Stable-Host coordination for Runner-owned foreground execution."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

from a13n_harness import HarnessState, RunInputValue
from ag_ui.core import Event
from anyio import Lock
from pydantic import JsonValue, TypeAdapter
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai.usage import RunUsage

from a13n_ui.composition import CompositionService
from a13n_ui.environments import EnvironmentService
from a13n_ui.errors import LivePresentationError, RunCoordinationError
from a13n_ui.runtime_generations import RuntimeGenerationService
from a13n_ui.runtime_generations.codecs import dump_deferred_results
from a13n_ui.runtime_generations.wire import (
    ExecuteRootRun,
    ProviderStateUpdate,
    RunnerAsyncWorkEvent,
    RunnerContinuationCandidate,
    SelectedEnvironmentResource,
)
from a13n_ui.sessions.events import SessionEventHub
from a13n_ui.sessions.models import LocalSession, SessionRunResult, SessionRunStatus
from a13n_ui.sessions.service import SessionService
from a13n_ui.storage import ObjectKind, ObjectRef


@dataclass(frozen=True, slots=True)
class _PreparedSession:
    session: LocalSession
    request: ExecuteRootRun


class ForegroundRunCoordinator:
    """Serialize one Session's Runs while dispatching execution to selected Runners."""

    def __init__(
        self,
        *,
        sessions: SessionService,
        composition: CompositionService,
        environments: EnvironmentService,
        events: SessionEventHub,
        runtime: RuntimeGenerationService,
    ) -> None:
        self._sessions = sessions
        self._composition = composition
        self._environments = environments
        self._events = events
        self._runtime = runtime
        self._registry_lock = Lock()
        self._session_locks: dict[str, Lock] = {}
        self._active: dict[str, str] = {}
        self._wake_tasks: dict[str, asyncio.Task[None]] = {}
        self._async_usage: dict[str, dict[tuple[str, str], RunUsage]] = {}
        self._closed = False
        self._runtime.set_async_work_handler(self.handle_async_work)

    async def run(self, *, session_id: str, input_value: RunInputValue) -> SessionRunResult:
        lock = await self._session_lock(session_id)
        async with lock:
            prepared, deferred = await self._prepare(
                session_id,
                input_value=TypeAdapter(RunInputValue).dump_python(input_value, mode="json"),
                deferred_results=None,
            )
            if deferred is not None:
                raise RunCoordinationError(
                    "The selected Session continuation is suspended and requires deferred results.",
                    code="session_suspended",
                )
            return await self._execute(prepared)

    async def resume(self, *, session_id: str, results: DeferredToolResults) -> SessionRunResult:
        lock = await self._session_lock(session_id)
        async with lock:
            prepared, deferred = await self._prepare(
                session_id,
                input_value=None,
                deferred_results=dump_deferred_results(results),
            )
            if deferred is None:
                raise RunCoordinationError(
                    "The selected Session continuation has no deferred requests.",
                    code="session_not_suspended",
                )
            return await self._execute(prepared)

    async def deferred_requests(self, session_id: str) -> DeferredToolRequests:
        return await self._sessions.selected_deferred_requests(session_id)

    async def cancel(self, session_id: str) -> bool:
        async with self._registry_lock:
            request_id = self._active.get(session_id)
        if request_id is None:
            return False
        return await self._runtime.cancel_root(request_id)

    async def cancel_all(self) -> None:
        async with self._registry_lock:
            request_ids = tuple(self._active.values())
        for request_id in request_ids:
            await self._runtime.cancel_root(request_id)

    @asynccontextmanager
    async def session_guard(
        self,
        session_id: str,
        *,
        cancel_active: bool = False,
    ) -> AsyncGenerator[None]:
        """Serialize one Host lifecycle decision with root and wake Runs for a Session."""
        if cancel_active:
            await self.cancel(session_id)
        lock = await self._session_lock(session_id)
        async with lock:
            yield

    async def handle_async_work(self, event: RunnerAsyncWorkEvent) -> None:
        """Aggregate detached usage and wake an inactive Session from its latest continuation."""

        identity = event.child_thread_id or event.reference
        active_generation_id = (await self._runtime.status()).active_generation_id
        async with self._registry_lock:
            if event.kind == "completion":
                usage = self._async_usage.setdefault(event.session_id, {})
                usage[(event.source, identity)] = deepcopy(event.usage)
            if (
                self._closed
                or event.generation_id != active_generation_id
                or (event.harness_active and event.session_id in self._active)
                or event.session_id in self._wake_tasks
            ):
                return
            task = asyncio.create_task(
                self._wake_session(event.session_id, event.generation_id),
                name=f"async-wake-{event.session_id}",
            )
            self._wake_tasks[event.session_id] = task

    async def async_usage(self, session_id: str) -> RunUsage:
        """Return process-local usage for deduplicated asynchronous executions."""

        async with self._registry_lock:
            values = tuple(deepcopy(value) for value in self._async_usage.get(session_id, {}).values())
        total = RunUsage()
        for value in values:
            total.incr(value)
        return total

    async def forget_session(self, session_id: str) -> None:
        """Discard process-local coordination state after durable Session deletion."""
        async with self._registry_lock:
            wake = self._wake_tasks.pop(session_id, None)
            self._async_usage.pop(session_id, None)
        if wake is not None:
            wake.cancel()
            await asyncio.gather(wake, return_exceptions=True)
        async with self._registry_lock:
            if session_id not in self._active:
                self._session_locks.pop(session_id, None)

    async def close(self) -> None:
        async with self._registry_lock:
            self._closed = True
            wake_tasks = tuple(self._wake_tasks.values())
        for task in wake_tasks:
            task.cancel()
        if wake_tasks:
            await asyncio.gather(*wake_tasks, return_exceptions=True)
        await self.cancel_all()
        await self._events.close()

    async def _wake_session(self, session_id: str, generation_id: str) -> None:
        task = asyncio.current_task()
        try:
            lock = await self._session_lock(session_id)
            async with lock:
                active_generation_id = (await self._runtime.status()).active_generation_id
                async with self._registry_lock:
                    if self._closed or generation_id != active_generation_id or session_id in self._active:
                        return
                prepared, deferred = await self._prepare(
                    session_id,
                    input_value=None,
                    deferred_results=None,
                )
                if deferred is not None:
                    return
                await self._execute(prepared)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Wake Runs are best-effort process-local delivery. A later user Run
            # reconciles the same retained child/process projection.
            return
        finally:
            async with self._registry_lock:
                if task is not None and self._wake_tasks.get(session_id) is task:
                    self._wake_tasks.pop(session_id, None)

    async def _prepare(
        self,
        session_id: str,
        *,
        input_value: JsonValue | None,
        deferred_results: JsonValue | None,
    ) -> tuple[_PreparedSession, DeferredToolRequests | None]:
        session = await self._sessions.get(session_id)
        _state, deferred = await self._sessions.load_continuation(session.continuation)
        await self._composition.agent(session.agent_snapshot)
        await self._composition.environment(session.environment_snapshot)
        resources = (await self._environments.availability(session_id)).resources
        selected = tuple(
            SelectedEnvironmentResource(
                resource=resource,
                provider_state=(
                    ObjectRef(
                        object_kind=ObjectKind.provider_state,
                        object_schema_version="1",
                        logical_digest=resource.provider_state_digest,
                    )
                    if resource.provider_state_digest is not None
                    else None
                ),
            )
            for resource in resources
        )
        request = ExecuteRootRun(
            request_id=f"request-{uuid4().hex}",
            generation_id="runtime-pending",
            session_id=session_id,
            continuation=ObjectRef(
                object_kind=ObjectKind.session_continuation,
                object_schema_version="1",
                logical_digest=session.continuation.object_digest,
            ),
            agent_snapshot=ObjectRef(
                object_kind=ObjectKind.agent_snapshot,
                object_schema_version="1",
                logical_digest=session.agent_snapshot.object_digest,
            ),
            environment_snapshot=ObjectRef(
                object_kind=ObjectKind.environment_snapshot,
                object_schema_version="1",
                logical_digest=session.environment_snapshot.object_digest,
            ),
            environment_resources=selected,
            skill_selections=session.skill_selections,
            input_value=input_value,
            deferred_results=deferred_results,
        )
        return _PreparedSession(session=session, request=request), deferred

    async def _execute(self, prepared: _PreparedSession) -> SessionRunResult:
        session_id = prepared.session.session_id
        request_id = prepared.request.request_id
        await self._register(session_id, request_id)
        try:
            terminal = await self._runtime.execute_root(
                prepared.request,
                on_event=lambda run_id, events: self._append_events(session_id, run_id, events),
                on_provider_state=self._persist_provider_state,
            )
        finally:
            await self._unregister(session_id, request_id)
        if terminal.failure is not None:
            raise RunCoordinationError(
                "The runtime Runner could not execute the Harness Run.",
                code=_failure_code(terminal.failure, "run_execution_failed"),
                details={"failure": terminal.failure},
            )
        if terminal.candidate is None:
            raise RunCoordinationError("The runtime Runner returned no Harness result.", code="run_result_missing")
        result = await self._save_candidate(session_id, terminal.candidate)
        if terminal.cleanup_failure is not None:
            raise _cleanup_error(result, terminal.cleanup_failure)
        return result

    async def _append_events(self, session_id: str, run_id: str, raw_events: tuple[JsonValue, ...]) -> None:
        try:
            events = tuple(TypeAdapter(Event).validate_python(item) for item in raw_events)
            await self._events.append(session_id=session_id, run_id=run_id, events=events)
        except LivePresentationError:
            pass

    async def _persist_provider_state(self, update: ProviderStateUpdate) -> None:
        await self._environments.persist_runner_state(
            session_id=update.session_id,
            mount_name=update.mount_name,
            provider_key=update.provider_key,
            provider_spec_digest=update.provider_spec_digest,
            state_version=update.state_version,
            provider_state=update.provider_state,
            status=update.status,
        )

    async def _save_candidate(
        self,
        session_id: str,
        candidate: RunnerContinuationCandidate,
    ) -> SessionRunResult:
        if candidate.status == "suspended" and (candidate.harness_state is None or candidate.deferred_requests is None):
            raise RunCoordinationError(
                "A suspended Harness result has no complete continuation and deferred requests.",
                code="continuation_invalid",
            )
        continuation = None
        if candidate.harness_state is not None:
            state = HarnessState.model_validate(candidate.harness_state, strict=True)
            deferred = (
                TypeAdapter(DeferredToolRequests).validate_python(candidate.deferred_requests)
                if candidate.status == "suspended" and candidate.deferred_requests is not None
                else None
            )
            continuation = await self._sessions.publish_continuation(state, deferred)
            await self._sessions.select_continuation(session_id, continuation)
        if candidate.status == "completed":
            return SessionRunResult(
                run_id=candidate.run_id,
                status=SessionRunStatus.completed,
                output=candidate.output,
                continuation=continuation,
            )
        if candidate.status == "suspended":
            if continuation is None:
                raise RunCoordinationError(
                    "A suspended Harness result has no complete continuation.",
                    code="continuation_missing",
                )
            return SessionRunResult(
                run_id=candidate.run_id,
                status=SessionRunStatus.suspended,
                continuation=continuation,
            )
        if candidate.status == "failed":
            return SessionRunResult(
                run_id=candidate.run_id,
                status=SessionRunStatus.failed,
                failure=candidate.failure or {"code": "run_failed"},
                continuation=continuation,
            )
        return SessionRunResult(
            run_id=candidate.run_id,
            status=SessionRunStatus.cancelled,
            failure={"code": "run_cancelled", "message": "The Harness Run was cancelled."},
            continuation=continuation,
        )

    async def _session_lock(self, session_id: str) -> Lock:
        async with self._registry_lock:
            return self._session_locks.setdefault(session_id, Lock())

    async def _register(self, session_id: str, request_id: str) -> None:
        async with self._registry_lock:
            if session_id in self._active:
                raise RunCoordinationError(
                    "The Session already has an active Run in this Host.",
                    code="session_run_active",
                )
            self._active[session_id] = request_id

    async def _unregister(self, session_id: str, request_id: str) -> None:
        async with self._registry_lock:
            if self._active.get(session_id) == request_id:
                self._active.pop(session_id, None)


def _failure_code(failure: JsonValue, default: str) -> str:
    if isinstance(failure, dict):
        code = failure.get("code")
        if isinstance(code, str):
            return code
    return default


def _cleanup_error(result: SessionRunResult, failure: JsonValue) -> RunCoordinationError:
    continuation = result.continuation
    selected = continuation is not None
    return RunCoordinationError(
        (
            "The Harness result was saved, but runtime cleanup failed."
            if selected
            else "The Harness Run finished, but runtime cleanup failed."
        ),
        code="run_saved_cleanup_failed" if selected else "run_cleanup_failed",
        details={
            "run_id": result.run_id,
            "status": result.status.value,
            "continuation_object_digest": continuation.object_digest if continuation is not None else None,
            "failure": cast(JsonValue, failure),
        },
    )


__all__ = ["ForegroundRunCoordinator"]
