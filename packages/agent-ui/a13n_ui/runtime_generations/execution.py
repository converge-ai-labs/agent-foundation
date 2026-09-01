"""Runner-owned Harness, Model, and Environment execution."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from a13n_environment_provider import (
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    EnvironmentProviderFactoryCatalog,
    EnvironmentProviderResourceState,
    EnvironmentProviderSpec,
    EnvironmentResource,
)
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    DeferredToolResume,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessRunStream,
    RunBindings,
    RunCleanupError,
    RunInputValue,
    derive_child_identity,
)
from a13n_harness.capabilities import (
    ManagedProcess,
    ProcessEvent,
    ProcessExecutionSnapshot,
    ProcessInputSnapshot,
    ProcessManager,
    ProcessOutputChunk,
    ProcessOutputPage,
    ProcessSignalSnapshot,
    SkillSelectionRunCapability,
    SubagentBindingOperator,
    SubagentEvent,
    SubagentManager,
)
from a13n_harness.context import AgentContext, BuiltSubagent
from a13n_harness.environment import EnvironmentAccess, EnvironmentError, EnvironmentPermissionSet
from a13n_harness.environment.advanced import (
    EnvironmentRuntimeMount,
    create_empty_environment_runtime,
    create_environment_provider_binding,
    create_environment_runtime,
)
from a13n_harness.environment.commands import BoundProcessHandle, CommandRequest, ProcessInfo
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_stream_protocol import HarnessAguiObserver
from ag_ui.core import Event
from pydantic import JsonValue, TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models import infer_model
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers import infer_provider_class
from pydantic_ai.usage import UsageLimits

from a13n_ui.composition import (
    ResolvedAgentNode,
    ResolvedAgentSnapshot,
    ResolvedEnvironmentMountDefinition,
    ResolvedEnvironmentSnapshot,
    ResolvedSubagentEdge,
)
from a13n_ui.composition.reconstruction import AgentReconstructor, ExecutableCache
from a13n_ui.configuration.models import canonical_json_value
from a13n_ui.environments import EnvironmentResourceStatus, ProviderRuntimeResolver
from a13n_ui.errors import RunCoordinationError, StoreIntegrityError

from .codecs import load_deferred_results
from .object_loader import RunnerObjectLoader
from .wire import (
    ExecuteEnvironmentCommand,
    ExecuteRootRun,
    ProviderStateUpdate,
    RunnerAsyncWorkEvent,
    RunnerContinuationCandidate,
    RunnerEnvironmentResult,
    RunnerRunResult,
)

_EventCallback = Callable[[str, str, tuple[JsonValue, ...]], Awaitable[None]]
_StateCallback = Callable[[ProviderStateUpdate], Awaitable[None]]
_AsyncWorkCallback = Callable[[RunnerAsyncWorkEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _ParentBinding:
    request: ExecuteRootRun
    snapshot: ResolvedAgentSnapshot
    environment: ResolvedEnvironmentSnapshot
    node: ResolvedAgentNode
    instance: AgentInstanceContext
    persist_state: _StateCallback
    acknowledged_provider_states: dict[str, EnvironmentProviderResourceState]


_TERMINAL_PROCESS_PHASES = frozenset({"exited", "signaled", "timed_out", "cancelled", "failed"})


class _EnvironmentManagedProcess:
    """Keep a fresh Agent UI Environment binding alive for one detached process."""

    def __init__(
        self,
        *,
        backend_id: str,
        environment: BoundEnvironment,
        handle: BoundProcessHandle,
        stack: AsyncExitStack,
        initial: ProcessInfo,
    ) -> None:
        self._backend_id = backend_id
        self._environment = environment
        self._handle = handle
        self._stack = stack
        self._latest = initial
        self._close_lock = asyncio.Lock()
        self._closed = False

    @property
    def backend_id(self) -> str:
        return self._backend_id

    async def inspect(self) -> ProcessExecutionSnapshot:
        if not self._closed:
            self._latest = await self._environment.processes.inspect(self._handle)
        return self._snapshot(self._latest)

    async def wait(self) -> ProcessExecutionSnapshot:
        while self._latest.status.phase not in _TERMINAL_PROCESS_PHASES:
            try:
                self._latest = await self._environment.processes.wait(
                    self._handle,
                    condition="tree_cleaned",
                    timeout_seconds=60,
                )
            except EnvironmentError as exc:
                if exc.code != "environment_timeout":
                    raise
                self._latest = await self._environment.processes.inspect(self._handle)
        return self._snapshot(self._latest)

    async def read_output(
        self,
        stdout_offset: int,
        stderr_offset: int,
        wait_seconds: float,
        max_bytes: int,
    ) -> ProcessOutputPage:
        result = await self._environment.processes.read_output(
            self._handle,
            stdout_start_offset=stdout_offset,
            stderr_start_offset=stderr_offset,
            wait_seconds=wait_seconds,
            policy=EnvironmentOutputPolicy(
                max_inline_bytes=max_bytes,
                max_output_bytes=max_bytes,
                overflow="truncate",
            ),
        )
        self._latest = result.process
        stdout_data = b"".join(segment.data for segment in result.stdout.chunks)[:max_bytes]
        stderr_budget = max_bytes - len(stdout_data)
        stderr_data = b"".join(segment.data for segment in result.stderr.chunks)[:stderr_budget]
        return ProcessOutputPage(
            snapshot=self._snapshot(result.process),
            stdout=self._chunk(result.stdout.capture, result.stdout.chunks, stdout_offset, stdout_data),
            stderr=self._chunk(result.stderr.capture, result.stderr.chunks, stderr_offset, stderr_data),
        )

    async def write_stdin(self, data: bytes, close_after_write: bool) -> ProcessInputSnapshot:
        result = await self._environment.processes.write_stdin(
            self._handle,
            data,
            close_after_write=close_after_write,
        )
        self._latest = await self._environment.processes.inspect(self._handle)
        return ProcessInputSnapshot(
            accepted_bytes=result.accepted_bytes,
            snapshot=self._snapshot(self._latest),
        )

    async def signal(self, signal: Literal["interrupt", "terminate"]) -> ProcessSignalSnapshot:
        result = await self._environment.processes.signal(self._handle, signal)
        self._latest = result.process
        return ProcessSignalSnapshot(
            accepted=result.accepted,
            snapshot=self._snapshot(result.process),
        )

    async def kill(self) -> ProcessExecutionSnapshot:
        result = await self._environment.processes.kill(self._handle)
        self._latest = result.process
        return self._snapshot(result.process)

    async def force_close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            errors: list[Exception] = []
            try:
                if self._latest.status.phase not in _TERMINAL_PROCESS_PHASES:
                    try:
                        result = await self._environment.processes.kill(self._handle)
                        self._latest = result.process
                    except Exception as exc:
                        errors.append(exc)
                try:
                    await self._environment.processes.release(self._handle)
                except Exception as exc:
                    errors.append(exc)
            finally:
                self._closed = True
                try:
                    await self._stack.aclose()
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise ExceptionGroup("Managed Environment process cleanup failed.", errors)

    def _snapshot(self, info: ProcessInfo) -> ProcessExecutionSnapshot:
        return ProcessExecutionSnapshot(
            backend_id=self._backend_id,
            status=info.status.model_copy(deep=True),
            stdin_open=info.stdin_open,
            stdout_produced_bytes=info.output.stdout.produced_bytes,
            stderr_produced_bytes=info.output.stderr.produced_bytes,
        )

    @staticmethod
    def _chunk(capture, segments, requested_offset: int, data: bytes) -> ProcessOutputChunk:
        start_offset = segments[0].start_offset if segments else max(requested_offset, capture.available_start)
        return ProcessOutputChunk(
            data=data,
            start_offset=start_offset,
            available_start=capture.available_start,
            available_end=capture.produced_bytes,
            producer_complete=capture.producer_complete,
            content_complete=capture.content_complete,
        )


class RunnerExecutionService:
    """Execute concurrent generation-bound root Runs inside one Runner process."""

    def __init__(
        self,
        *,
        generation_id: str,
        objects: RunnerObjectLoader,
        provider_factories: EnvironmentProviderFactoryCatalog,
        provider_runtimes: ProviderRuntimeResolver,
        notify_async_work: _AsyncWorkCallback,
    ) -> None:
        self._generation_id = generation_id
        self._objects = objects
        self._provider_factories = provider_factories
        self._provider_runtimes = provider_runtimes
        self._cache = ExecutableCache()
        self._notify_async_work = notify_async_work
        self._parents: dict[str, _ParentBinding] = {}
        self._inline_subagents = SubagentBindingOperator(self._open_child)
        self._subagents = SubagentManager(
            self._open_child,
            event_hooks=(self._on_subagent_event,),
        )
        self._processes = ProcessManager(
            self._launch_process,
            event_hooks=(self._on_process_event,),
        )
        self._reconstructor = AgentReconstructor(
            objects.skill_package,
            self._cache,
            inline_subagents=self._inline_subagents,
            async_subagents=self._subagents,
            shell_operator=self._processes,
        )
        self._streams: dict[str, HarnessRunStream[Any]] = {}
        self._terminal_parents: set[str] = set()
        self._pending_cancellations: set[str] = set()

    async def execute(
        self,
        request: ExecuteRootRun,
        *,
        emit_events: _EventCallback,
        persist_state: _StateCallback,
    ) -> RunnerRunResult:
        if request.generation_id != self._generation_id:
            return RunnerRunResult(
                request_id=request.request_id,
                failure={"code": "runtime_generation_mismatch", "message": "The Run selected another generation."},
            )
        if request.request_id in self._pending_cancellations:
            self._pending_cancellations.discard(request.request_id)
            return RunnerRunResult(
                request_id=request.request_id,
                candidate=RunnerContinuationCandidate(run_id=request.request_id, status="cancelled"),
            )
        candidate: RunnerContinuationCandidate | None = None
        cleanup_failure: JsonValue | None = None
        try:
            agent = await self._objects.agent_snapshot(request.agent_snapshot)
            environment = await self._objects.environment_snapshot(request.environment_snapshot)
            previous_state, deferred_requests = await self._objects.continuation(request.continuation)
            executable = await self._reconstructor.executable(agent, environment)
            input_value = (
                TypeAdapter(RunInputValue).validate_python(request.input_value)
                if request.input_value is not None
                else None
            )
            deferred_resume = None
            if request.deferred_results is not None:
                if deferred_requests is None:
                    raise RunCoordinationError(
                        "The selected continuation has no deferred requests.",
                        code="session_not_suspended",
                    )
                results = load_deferred_results(request.deferred_results)
                deferred_resume = DeferredToolResume(requests=deferred_requests, results=results)
            elif deferred_requests is not None:
                raise RunCoordinationError(
                    "The selected continuation is suspended and requires deferred results.",
                    code="session_suspended",
                )
            model_resolver = _model_resolver(agent)
            root_node = next(node for node in agent.resolved_agents if node.agent_revision == agent.root_agent)
            root_instance = AgentInstanceContext(
                identity=AgentIdentityRef(issuer="a13n.agent-ui", subject=request.session_id),
                agent_instance_id=f"agent-{uuid4().hex[:16]}",
                actor="a13n.agent-ui",
                host_refs={"session_id": request.session_id},
            )
            acknowledged_provider_states: dict[str, EnvironmentProviderResourceState] = {}
            self._register_parent(
                _ParentBinding(
                    request=request,
                    snapshot=agent,
                    environment=environment,
                    node=root_node,
                    instance=root_instance,
                    persist_state=persist_state,
                    acknowledged_provider_states=acknowledged_provider_states,
                )
            )
            try:
                async with _run_environment(
                    request,
                    environment,
                    objects=self._objects,
                    factories=self._provider_factories,
                    runtimes=self._provider_runtimes,
                    persist_state=persist_state,
                    acknowledged_provider_states=acknowledged_provider_states,
                ) as runtime:
                    capabilities: tuple[Any, ...] = _run_capabilities(request, root_node)
                    bindings = RunBindings(
                        instance=root_instance,
                        environment=runtime,
                        model_resolver=model_resolver,
                        capabilities=capabilities,
                        metadata={
                            "session_id": request.session_id,
                            "agent_snapshot": agent.logical_agent_digest,
                            "environment_snapshot": environment.logical_environment_digest,
                        },
                    )
                    stream = executable.stream(
                        input_value,
                        bindings=bindings,
                        previous_state=previous_state,
                        deferred_resume=deferred_resume,
                    )
                    self._streams[request.request_id] = stream
                    if request.request_id in self._pending_cancellations:
                        self._pending_cancellations.discard(request.request_id)
                        stream.cancel()
                    try:
                        candidate = await _consume_stream(
                            request.request_id,
                            stream,
                            emit_events,
                            on_terminal=lambda: self._terminal_parents.add(root_instance.agent_instance_id),
                        )
                    except RunCleanupError as exc:
                        if exc.outcome is not None:
                            candidate = _candidate(exc.outcome)
                        cleanup_failure = _safe_exception(exc, code="run_cleanup_failed")
                    finally:
                        if self._streams.get(request.request_id) is stream:
                            self._streams.pop(request.request_id, None)
            finally:
                self._parents.pop(root_instance.agent_instance_id, None)
                self._terminal_parents.discard(root_instance.agent_instance_id)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt | SystemExit):
                raise
            if candidate is not None:
                cleanup_failure = _safe_exception(exc, code="run_cleanup_failed")
            else:
                self._pending_cancellations.discard(request.request_id)
                return RunnerRunResult(
                    request_id=request.request_id,
                    failure=_safe_exception(exc, code="run_execution_failed"),
                )
        self._pending_cancellations.discard(request.request_id)
        return RunnerRunResult(
            request_id=request.request_id,
            candidate=candidate,
            cleanup_failure=cleanup_failure,
        )

    async def _launch_process(
        self,
        context: AgentContext,
        request: CommandRequest,
        alias: str | None,
    ) -> ManagedProcess:
        parent = self._parents.get(context.instance.agent_instance_id)
        if parent is None:
            raise RunCoordinationError(
                "The parent process-binding scope is no longer active.",
                code="process_binding_unavailable",
            )
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            runtime = await stack.enter_async_context(
                _run_environment(
                    parent.request,
                    parent.environment,
                    objects=self._objects,
                    factories=self._provider_factories,
                    runtimes=self._provider_runtimes,
                    persist_state=parent.persist_state,
                    acknowledged_provider_states=parent.acknowledged_provider_states,
                )
            )
            environment = await stack.enter_async_context(
                runtime.bind(
                    run_id=f"process-{uuid4().hex[:16]}",
                    instance=context.instance,
                )
            )
            await runtime._activate()
            started = await environment.processes.start(request, alias=alias)
            return _EnvironmentManagedProcess(
                backend_id=f"process-backend-{uuid4().hex[:16]}",
                environment=environment,
                handle=started.process.handle,
                stack=stack,
                initial=started.process,
            )
        except BaseException:
            await stack.aclose()
            raise

    def _open_child(
        self,
        context: AgentContext,
        child: BuiltSubagent,
        input_value: RunInputValue,
        child_instance_id: str,
        continuation: bool,
        usage_limits: UsageLimits | None,
    ):
        del input_value, continuation, usage_limits
        parent = self._parents.get(context.instance.agent_instance_id)
        if parent is None:
            raise RunCoordinationError(
                "The parent child-binding scope is no longer active.",
                code="subagent_binding_unavailable",
            )
        edge = next(
            (item for item in parent.node.subagents if item.name == child.declaration.name),
            None,
        )
        if edge is None:
            raise RunCoordinationError(
                "The requested child does not match the pinned Agent snapshot.",
                code="subagent_binding_invalid",
            )
        return self._child_bindings(parent, child, edge, child_instance_id)

    @asynccontextmanager
    async def _child_bindings(
        self,
        parent: _ParentBinding,
        child: BuiltSubagent,
        edge: ResolvedSubagentEdge,
        child_instance_id: str,
    ):
        child_node = next(node for node in parent.snapshot.resolved_agents if node.agent_revision == edge.target_agent)
        identity = derive_child_identity(
            parent.instance.identity,
            child_node.agent_id,
            child.declaration.identity,
        )
        instance = AgentInstanceContext(
            identity=identity,
            agent_instance_id=f"agent-{uuid4().hex[:16]}",
            parent_agent_instance_id=parent.instance.agent_instance_id,
            delegation_id=child_instance_id,
            actor="a13n.agent-ui",
            host_refs={"session_id": parent.request.session_id},
        )
        binding = _ParentBinding(
            request=parent.request,
            snapshot=parent.snapshot,
            environment=parent.environment,
            node=child_node,
            instance=instance,
            persist_state=parent.persist_state,
            acknowledged_provider_states=parent.acknowledged_provider_states,
        )
        self._register_parent(binding)
        try:
            async with _child_environment(
                parent.request,
                parent.environment,
                edge,
                objects=self._objects,
                factories=self._provider_factories,
                runtimes=self._provider_runtimes,
                persist_state=parent.persist_state,
                acknowledged_provider_states=parent.acknowledged_provider_states,
            ) as runtime:
                capabilities: tuple[Any, ...] = _run_capabilities(parent.request, child_node)
                yield RunBindings(
                    instance=instance,
                    environment=runtime,
                    model_resolver=_model_resolver(parent.snapshot),
                    capabilities=capabilities,
                    metadata={
                        "session_id": parent.request.session_id,
                        "agent_snapshot": parent.snapshot.logical_agent_digest,
                        "environment_snapshot": parent.environment.logical_environment_digest,
                        "subagent_execution_id": child_instance_id,
                    },
                )
        finally:
            self._parents.pop(instance.agent_instance_id, None)

    def _register_parent(self, binding: _ParentBinding) -> None:
        instance_id = binding.instance.agent_instance_id
        if instance_id in self._parents:
            raise RuntimeError("Agent instance child-binding scope is already active.")
        self._parents[instance_id] = binding

    async def _on_subagent_event(self, event: SubagentEvent) -> None:
        if event.kind == "started":
            return
        session_id = event.host_refs.get("session_id")
        if session_id is None:
            return
        await self._notify_async_work(
            RunnerAsyncWorkEvent(
                generation_id=self._generation_id,
                session_id=session_id,
                harness_active=(
                    event.agent_instance_id in self._parents and event.agent_instance_id not in self._terminal_parents
                ),
                source="async_subagent",
                kind=event.kind,
                thread_id=event.thread_id,
                run_id=event.run_id,
                agent_instance_id=event.agent_instance_id,
                reference=event.subagent_id,
                child_thread_id=event.child_thread_id,
                status=event.status,
                usage=event.usage,
            )
        )

    async def _on_process_event(self, event: ProcessEvent) -> None:
        session_id = event.host_refs.get("session_id")
        if session_id is None:
            return
        await self._notify_async_work(
            RunnerAsyncWorkEvent(
                generation_id=self._generation_id,
                session_id=session_id,
                harness_active=(
                    event.agent_instance_id in self._parents and event.agent_instance_id not in self._terminal_parents
                ),
                source="background_process",
                kind=event.kind,
                thread_id=event.thread_id,
                run_id=event.run_id,
                agent_instance_id=event.agent_instance_id,
                reference=event.process_id,
                status=event.status.phase,
            )
        )

    async def execute_environment(
        self,
        request: ExecuteEnvironmentCommand,
        *,
        persist_state: _StateCallback,
    ) -> RunnerEnvironmentResult:
        """Execute one detached Environment lifecycle command."""

        if request.generation_id != self._generation_id:
            return RunnerEnvironmentResult(
                request_id=request.request_id,
                failure={"code": "runtime_generation_mismatch", "message": "The command selected another generation."},
            )
        try:
            if request.action in {"pause", "destroy"}:
                await self.force_close_session_work(request.session_id)
            snapshot = await self._objects.environment_snapshot(request.environment_snapshot)
            await _execute_environment_command(
                request,
                snapshot,
                objects=self._objects,
                factories=self._provider_factories,
                runtimes=self._provider_runtimes,
                persist_state=persist_state,
            )
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt | SystemExit):
                raise
            return RunnerEnvironmentResult(
                request_id=request.request_id,
                failure=_safe_exception(exc, code="environment_operation_failed"),
            )
        return RunnerEnvironmentResult(request_id=request.request_id)

    async def force_close_session_work(self, session_id: str) -> None:
        results = await asyncio.gather(
            self._subagents.force_close_matching({"session_id": session_id}),
            self._processes.force_close_matching({"session_id": session_id}),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise BaseExceptionGroup("Session asynchronous work cleanup failed.", errors)

    def cancel(self, request_id: str, *, admitted: bool = False) -> bool:
        stream = self._streams.get(request_id)
        if stream is not None:
            stream.cancel()
            return True
        if admitted:
            self._pending_cancellations.add(request_id)
            return True
        return False

    async def wait_idle(self) -> None:
        """Wait for process-local asynchronous work during generation drain."""

        await asyncio.gather(
            self._subagents.wait_idle(),
            self._processes.wait_idle(),
        )

    async def close(self) -> None:
        for stream in tuple(self._streams.values()):
            stream.cancel()
        self._pending_cancellations.clear()
        await asyncio.gather(
            self._subagents.force_close(),
            self._processes.force_close(),
        )
        self._parents.clear()
        self._terminal_parents.clear()
        await self._cache.clear()


async def _consume_stream(
    request_id: str,
    stream: HarnessRunStream[Any],
    emit_events: _EventCallback,
    *,
    on_terminal: Callable[[], None],
) -> RunnerContinuationCandidate:
    observer = HarnessAguiObserver()
    terminal: RunnerContinuationCandidate | None = None
    event_adapter = TypeAdapter(Event)
    async with stream:
        async for item in stream:
            events = tuple(event_adapter.dump_python(event, mode="json") for event in observer.observe(item))
            if events:
                await emit_events(request_id, item.run_id, events)
            if isinstance(item, HarnessRunResultEvent):
                on_terminal()
                terminal = _candidate(item.result)
    if terminal is None:
        raise RunCoordinationError("The Harness stream ended without a terminal result.", code="run_result_missing")
    return terminal


def _candidate(result: HarnessRunResult[Any]) -> RunnerContinuationCandidate:
    deferred = TypeAdapter(Any).dump_python(result.deferred, mode="json") if result.deferred is not None else None
    failure = result.failure.model_dump(mode="json", by_alias=True) if result.failure is not None else None
    return RunnerContinuationCandidate(
        run_id=result.run_id,
        status=result.status,
        output=canonical_json_value(result.output) if result.status == "completed" else None,
        failure=failure,
        harness_state=result.state.model_dump(mode="json") if result.state is not None else None,
        deferred_requests=deferred,
    )


def _model_resolver(snapshot: ResolvedAgentSnapshot):
    definitions = {node.model.definition.model_id: node.model.definition for node in snapshot.resolved_agents}

    async def resolve(context: object, model_id: str):
        del context
        definition = definitions.get(model_id)
        if definition is None:
            raise RunCoordinationError(
                "The Harness requested a Model outside the pinned Agent snapshot.",
                code="model_reference_invalid",
            )
        if definition.model_name == "test":
            if definition.settings.get("test_deferred") is True:
                return _deferred_test_model()
            if definition.settings.get("test_async_subagent") is True:
                name = definition.settings.get("test_subagent_name")
                return _async_subagent_test_model(
                    name if isinstance(name, str) else "child-worker",
                    wait_for_child=definition.settings.get("test_wait_for_subagent") is not False,
                )
            if definition.settings.get("test_background_shell") is True:
                command = definition.settings.get("test_shell_command")
                return _background_shell_test_model(
                    command if isinstance(command, str) else "printf async-shell-done",
                    wait_for_process=definition.settings.get("test_wait_for_process") is not False,
                )
            output = definition.settings.get("test_output")
            delay = definition.settings.get("test_delay_seconds")
            if isinstance(delay, int | float) and not isinstance(delay, bool) and delay > 0:
                delayed_input = definition.settings.get("test_delayed_input")
                return _delayed_test_model(
                    delay_seconds=float(delay),
                    output=output if isinstance(output, str) else "completed by delayed test model",
                    delayed_input=delayed_input if isinstance(delayed_input, str) else None,
                )
            return TestModel(custom_output_text=output if isinstance(output, str) else None)
        credential = None
        if definition.credential_ref is not None:
            credential = os.environ.get(_credential_environment_name(definition.credential_ref))
            if credential is None:
                raise RunCoordinationError(
                    "The selected Model credential is unavailable.",
                    code="model_credential_unavailable",
                    details={"credential_ref": definition.credential_ref},
                )

        def provider_factory(provider_name: str):
            provider_class = infer_provider_class(provider_name)
            parameters = inspect.signature(provider_class).parameters
            arguments: dict[str, object] = {}
            if credential is not None:
                if "api_key" not in parameters:
                    raise RunCoordinationError(
                        "The selected provider does not accept an API-key credential.",
                        code="model_credential_unsupported",
                    )
                arguments["api_key"] = credential
            if definition.endpoint is not None:
                if "base_url" not in parameters:
                    raise RunCoordinationError(
                        "The selected provider does not accept a custom endpoint.",
                        code="model_endpoint_unsupported",
                    )
                arguments["base_url"] = definition.endpoint
            return provider_class(**arguments)

        try:
            return infer_model(definition.model_name, provider_factory=provider_factory)
        except RunCoordinationError:
            raise
        except Exception as exc:
            raise RunCoordinationError(
                "The selected native Model could not be constructed.",
                code="model_construction_failed",
                details={"model_id": model_id},
            ) from exc

    return resolve


def _delayed_test_model(
    *,
    delay_seconds: float,
    output: str,
    delayed_input: str | None,
) -> FunctionModel:
    async def stream(messages: list[ModelMessage], _info: AgentInfo):
        should_delay = delayed_input is None or any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, UserPromptPart) and part.content == delayed_input for part in message.parts)
            for message in messages
        )
        if should_delay:
            await asyncio.sleep(delay_seconds)
        yield output

    return FunctionModel(stream_function=stream)


def _async_subagent_test_model(subagent_name: str, *, wait_for_child: bool) -> FunctionModel:
    async def stream(messages: list[ModelMessage], _info: AgentInfo):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        delegated = next((part for part in reversed(returns) if part.tool_name == "delegate"), None)
        waited = next((part for part in reversed(returns) if part.tool_name == "wait_subagent"), None)
        if delegated is None:
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args=json.dumps(
                        {"subagent_name": subagent_name, "prompt": "Complete the child task."},
                        separators=(",", ":"),
                    ),
                    tool_call_id="delegate-1",
                )
            }
            return
        if waited is None:
            content = delegated.content
            if not isinstance(content, dict) or not isinstance(content.get("execution_id"), str):
                raise RuntimeError("The async-subagent test delegate result is invalid.")
            if not wait_for_child:
                yield f"child-started:{content['execution_id']}"
                return
            yield {
                0: DeltaToolCall(
                    name="wait_subagent",
                    json_args=json.dumps(
                        {"execution_id": content["execution_id"], "timeout_seconds": 30},
                        separators=(",", ":"),
                    ),
                    tool_call_id="wait-1",
                )
            }
            return
        content = waited.content
        if not isinstance(content, dict) or content.get("status") != "succeeded":
            raise RuntimeError("The async-subagent test execution did not succeed.")
        yield f"child:{content.get('output')}"

    return FunctionModel(stream_function=stream)


def _background_shell_test_model(command: str, *, wait_for_process: bool) -> FunctionModel:
    async def stream(messages: list[ModelMessage], _info: AgentInfo):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        started = next((part for part in reversed(returns) if part.tool_name == "shell_exec"), None)
        waited = next((part for part in reversed(returns) if part.tool_name == "shell_wait"), None)
        wake_notification = any(
            isinstance(message, ModelRequest)
            and any(
                isinstance(part, UserPromptPart)
                and isinstance(part.content, str)
                and "Background process" in part.content
                for part in message.parts
            )
            for message in messages
        )
        collect_requested = any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, UserPromptPart) and part.content == "collect process" for part in message.parts)
            for message in messages
        )
        if started is None:
            yield {
                0: DeltaToolCall(
                    name="shell_exec",
                    json_args=json.dumps(
                        {"command": command, "background": True},
                        separators=(",", ":"),
                    ),
                    tool_call_id="shell-start-1",
                )
            }
            return
        if waited is None and (wait_for_process or wake_notification or collect_requested):
            content = started.content
            if not isinstance(content, dict) or not isinstance(content.get("process_id"), str):
                raise RuntimeError("The background-shell test start result is invalid.")
            yield {
                0: DeltaToolCall(
                    name="shell_wait",
                    json_args=json.dumps(
                        {"process_id": content["process_id"], "timeout_seconds": 30},
                        separators=(",", ":"),
                    ),
                    tool_call_id="shell-wait-1",
                )
            }
            return
        if waited is None:
            content = started.content
            if not isinstance(content, dict) or not isinstance(content.get("process_id"), str):
                raise RuntimeError("The background-shell test start result is invalid.")
            yield f"process-started:{content['process_id']}"
            return
        content = waited.content
        if not isinstance(content, dict) or content.get("ok") is not True:
            error = content.get("error") if isinstance(content, dict) else None
            if isinstance(error, dict) and error.get("code") == "environment_reference_stale":
                yield "process-lost"
                return
            raise RuntimeError("The background-shell test wait result is invalid.")
        stdout = content.get("stdout")
        text = stdout.get("text") if isinstance(stdout, dict) else None
        yield f"process:{text}"

    return FunctionModel(stream_function=stream)


def _deferred_test_model() -> FunctionModel:
    async def stream(messages: list[ModelMessage], _info: AgentInfo):
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="ask_user_question",
                    json_args=(
                        '{"questions":[{"header":"Scope","question":"Which scope should be used?",'
                        '"options":[{"label":"Focused","description":"Use focused scope."},'
                        '{"label":"Broad","description":"Use broad scope."}],"multiSelect":false}]}'
                    ),
                    tool_call_id="question-1",
                )
            }
        else:
            yield f"answer:{returns[-1].content}"

    return FunctionModel(stream_function=stream)


def _credential_environment_name(reference: str) -> str:
    return "A13N_CREDENTIAL_" + reference.replace("-", "_").upper()


@asynccontextmanager
async def _run_environment(
    request: ExecuteRootRun,
    snapshot: ResolvedEnvironmentSnapshot,
    *,
    objects: RunnerObjectLoader,
    factories: EnvironmentProviderFactoryCatalog,
    runtimes: ProviderRuntimeResolver,
    persist_state: _StateCallback,
    acknowledged_provider_states: dict[str, EnvironmentProviderResourceState],
    mount_names: frozenset[str] | None = None,
):
    provider_state_snapshot = {
        mount_name: state.model_copy(deep=True) for mount_name, state in acknowledged_provider_states.items()
    }
    selected = {item.resource.mount_name: item for item in request.environment_resources}
    all_mounts = {item.mount_name: item for item in snapshot.mounts}
    if set(selected) != set(all_mounts):
        raise StoreIntegrityError(
            "Session Environment resources differ from the pinned desired mounts.",
            code="environment_resource_mismatch",
        )
    mounts = {name: mount for name, mount in all_mounts.items() if mount_names is None or name in mount_names}
    if mount_names is not None and set(mounts) != set(mount_names):
        raise StoreIntegrityError(
            "A child Environment policy selects an unknown mount.",
            code="environment_resource_mismatch",
        )
    resources: list[tuple[ResolvedEnvironmentMountDefinition, EnvironmentResource]] = []
    resource_scopes = AsyncExitStack()
    attachments = AsyncExitStack()
    try:
        for mount in mounts.values():
            item = selected[mount.mount_name]
            resource_row = item.resource
            if (
                resource_row.session_id != request.session_id
                or resource_row.provider_key != mount.provider_key
                or resource_row.provider_schema_version != mount.provider_schema_version
                or resource_row.model_alias != mount.model_alias
            ):
                raise StoreIntegrityError(
                    "A selected Environment resource does not match its snapshot.",
                    code="environment_resource_mismatch",
                )
            provider_state = provider_state_snapshot.get(mount.mount_name)
            if provider_state is None and item.provider_state is not None:
                stored = await objects.provider_state(item.provider_state, resource_row)
                provider_state = EnvironmentProviderResourceState.model_validate(stored.provider_state, strict=True)
                if provider_state.state_version != stored.state_version:
                    raise StoreIntegrityError(
                        "A selected provider state has an inconsistent version.",
                        code="provider_state_invalid",
                    )
                acknowledged_provider_states[mount.mount_name] = provider_state
            runtime = await runtimes.resolve(mount.provider_key)
            provider = factories.create_provider(
                EnvironmentProviderSpec(
                    provider_key=mount.provider_key,
                    schema_version=mount.provider_schema_version,
                    parameters=mount.normalized_parameters,
                ),
                runtime=runtime,
            )
            operation = _operation(
                EnvironmentManagementAction.CREATE if provider_state is None else EnvironmentManagementAction.RESUME,
                request.session_id,
                mount.mount_name,
            )
            resource = (
                await provider.create(operation=operation)
                if provider_state is None
                else await provider.resume(provider_state, operation=operation)
            )
            await resource_scopes.enter_async_context(resource)
            resources.append((mount, resource))
            await persist_state(
                _state_update(request, resource_row, resource.state, EnvironmentResourceStatus.available)
            )
            acknowledged_provider_states[mount.mount_name] = resource.state.model_copy(deep=True)

        runtime_mounts: dict[str, EnvironmentRuntimeMount] = {}
        for mount, resource in resources:
            attachment = await attachments.enter_async_context(resource.acquire_attachment())
            runtime_mounts[mount.model_alias] = EnvironmentRuntimeMount(
                binding=create_environment_provider_binding(attachment),
                permission_ceiling=_permissions(mount),
                working_directory="/",
            )
        default_name = snapshot.definition.default_mount
        default_mount = mounts[default_name].model_alias if default_name in mounts else None
        yield create_environment_runtime(mounts=runtime_mounts, default_mount=default_mount)
    finally:
        try:
            await attachments.aclose()
        finally:
            await resource_scopes.aclose()


@asynccontextmanager
async def _child_environment(
    request: ExecuteRootRun,
    snapshot: ResolvedEnvironmentSnapshot,
    edge: ResolvedSubagentEdge,
    *,
    objects: RunnerObjectLoader,
    factories: EnvironmentProviderFactoryCatalog,
    runtimes: ProviderRuntimeResolver,
    persist_state: _StateCallback,
    acknowledged_provider_states: dict[str, EnvironmentProviderResourceState],
):
    policy = edge.environment
    if policy.mode == "none":
        yield create_empty_environment_runtime()
        return
    mount_names = frozenset(policy.mounts or (mount.mount_name for mount in snapshot.mounts))
    if policy.mode == "shared_root":
        async with _run_environment(
            request,
            snapshot,
            objects=objects,
            factories=factories,
            runtimes=runtimes,
            persist_state=persist_state,
            acknowledged_provider_states=acknowledged_provider_states,
            mount_names=mount_names,
        ) as runtime:
            yield runtime
        return
    async with _dedicated_child_environment(
        request,
        snapshot,
        mount_names,
        factories=factories,
        runtimes=runtimes,
    ) as runtime:
        yield runtime


@asynccontextmanager
async def _dedicated_child_environment(
    request: ExecuteRootRun,
    snapshot: ResolvedEnvironmentSnapshot,
    mount_names: frozenset[str],
    *,
    factories: EnvironmentProviderFactoryCatalog,
    runtimes: ProviderRuntimeResolver,
):
    selected = {item.resource.mount_name: item for item in request.environment_resources}
    mounts = {mount.mount_name: mount for mount in snapshot.mounts if mount.mount_name in mount_names}
    if set(mounts) != set(mount_names):
        raise StoreIntegrityError(
            "A child Environment policy selects an unknown mount.",
            code="environment_resource_mismatch",
        )
    resources: list[tuple[ResolvedEnvironmentMountDefinition, EnvironmentResource]] = []
    resource_scopes = AsyncExitStack()
    attachments = AsyncExitStack()
    try:
        for mount in mounts.values():
            row = selected[mount.mount_name].resource
            if (
                row.session_id != request.session_id
                or row.provider_key != mount.provider_key
                or row.provider_schema_version != mount.provider_schema_version
                or row.model_alias != mount.model_alias
            ):
                raise StoreIntegrityError(
                    "A selected Environment resource does not match its snapshot.",
                    code="environment_resource_mismatch",
                )
            runtime = await runtimes.resolve(mount.provider_key)
            provider = factories.create_provider(
                EnvironmentProviderSpec(
                    provider_key=mount.provider_key,
                    schema_version=mount.provider_schema_version,
                    parameters=mount.normalized_parameters,
                ),
                runtime=runtime,
            )
            correlation = f"{mount.mount_name}:child-{uuid4().hex[:12]}"
            resource = await resource_scopes.enter_async_context(
                provider.ephemeral(resource_correlation=f"{request.session_id}:{correlation}")
            )
            resources.append((mount, resource))
        runtime_mounts: dict[str, EnvironmentRuntimeMount] = {}
        for mount, resource in resources:
            attachment = await attachments.enter_async_context(resource.acquire_attachment())
            runtime_mounts[mount.model_alias] = EnvironmentRuntimeMount(
                binding=create_environment_provider_binding(attachment),
                permission_ceiling=_permissions(mount),
                working_directory="/",
            )
        default_name = snapshot.definition.default_mount
        default_mount = mounts[default_name].model_alias if default_name in mounts else None
        yield create_environment_runtime(mounts=runtime_mounts, default_mount=default_mount)
    finally:
        try:
            await attachments.aclose()
        finally:
            await resource_scopes.aclose()


async def _execute_environment_command(
    request: ExecuteEnvironmentCommand,
    snapshot: ResolvedEnvironmentSnapshot,
    *,
    objects: RunnerObjectLoader,
    factories: EnvironmentProviderFactoryCatalog,
    runtimes: ProviderRuntimeResolver,
    persist_state: _StateCallback,
) -> None:
    selected = request.selected_resource
    row = selected.resource
    mount = next((item for item in snapshot.mounts if item.mount_name == row.mount_name), None)
    if (
        mount is None
        or row.session_id != request.session_id
        or row.provider_key != mount.provider_key
        or row.provider_schema_version != mount.provider_schema_version
        or row.model_alias != mount.model_alias
    ):
        raise StoreIntegrityError(
            "A selected Environment resource does not match its snapshot.",
            code="environment_resource_mismatch",
        )
    state: EnvironmentProviderResourceState | None = None
    if selected.provider_state is not None:
        stored = await objects.provider_state(selected.provider_state, row)
        state = EnvironmentProviderResourceState.model_validate(stored.provider_state, strict=True)
        if state.state_version != stored.state_version:
            raise StoreIntegrityError(
                "A selected provider state has an inconsistent version.",
                code="provider_state_invalid",
            )
    runtime = await runtimes.resolve(mount.provider_key)
    provider = factories.create_provider(
        EnvironmentProviderSpec(
            provider_key=mount.provider_key,
            schema_version=mount.provider_schema_version,
            parameters=mount.normalized_parameters,
        ),
        runtime=runtime,
    )
    if request.action == "destroy":
        if state is not None:
            await provider.destroy(
                state,
                operation=_operation(EnvironmentManagementAction.DESTROY, request.session_id, mount.mount_name),
            )
        await persist_state(_cleared_state_update(request, row))
        return

    resource: EnvironmentResource | None = None
    try:
        resource = (
            await provider.create(
                operation=_operation(EnvironmentManagementAction.CREATE, request.session_id, mount.mount_name)
            )
            if state is None
            else await provider.resume(
                state,
                operation=_operation(EnvironmentManagementAction.RESUME, request.session_id, mount.mount_name),
            )
        )
        await resource.__aenter__()
        if request.action == "pause":
            mode = request.pause_mode or EnvironmentPauseMode.FILESYSTEM
            paused = await provider.pause(
                resource,
                operation=_operation(EnvironmentManagementAction.PAUSE, request.session_id, mount.mount_name),
                mode=mode,
            )
            await persist_state(_state_update(request, row, paused, EnvironmentResourceStatus.paused))
        else:
            await persist_state(_state_update(request, row, resource.state, EnvironmentResourceStatus.available))
    except BaseException:
        retained = resource.state if resource is not None else state
        if retained is not None:
            await persist_state(_state_update(request, row, retained, EnvironmentResourceStatus.unavailable))
        raise
    finally:
        if resource is not None:
            await resource.__aexit__(None, None, None)


def _state_update(
    request: ExecuteRootRun | ExecuteEnvironmentCommand,
    resource,
    state: EnvironmentProviderResourceState,
    status: EnvironmentResourceStatus,
) -> ProviderStateUpdate:
    return ProviderStateUpdate(
        update_id=f"update-{uuid4().hex}",
        request_id=request.request_id,
        session_id=request.session_id,
        mount_name=resource.mount_name,
        provider_key=resource.provider_key,
        provider_spec_digest=resource.provider_spec_digest,
        state_version=state.state_version,
        provider_state=state.model_dump(mode="json"),
        status=status,
    )


def _cleared_state_update(
    request: ExecuteEnvironmentCommand,
    resource,
) -> ProviderStateUpdate:
    return ProviderStateUpdate(
        update_id=f"update-{uuid4().hex}",
        request_id=request.request_id,
        session_id=request.session_id,
        mount_name=resource.mount_name,
        provider_key=resource.provider_key,
        provider_spec_digest=resource.provider_spec_digest,
        status=EnvironmentResourceStatus.unprovisioned,
    )


def _operation(action: EnvironmentManagementAction, session_id: str, mount_name: str) -> EnvironmentOperationContext:
    return EnvironmentOperationContext(
        operation_id=f"operation-{uuid4().hex}",
        action=action,
        resource_correlation=f"{session_id}:{mount_name}",
        attempt=1,
    )


def _permissions(mount: ResolvedEnvironmentMountDefinition) -> EnvironmentPermissionSet:
    return EnvironmentAccess(mount.access).permission_set()


def _run_capabilities(
    request: ExecuteRootRun,
    node: ResolvedAgentNode,
) -> tuple[SkillSelectionRunCapability, ...]:
    selection = next((item for item in request.skill_selections if item.agent_node_id == node.agent_id), None)
    if not node.skills:
        return ()
    if selection is not None and selection.mode == "exact":
        names = frozenset(selection.names)
    elif node.default_skill_names is not None:
        names = frozenset(node.default_skill_names)
    else:
        return ()
    return (SkillSelectionRunCapability(names=names),)


def _safe_exception(error: BaseException, *, code: str) -> JsonValue:
    if isinstance(error, RunCoordinationError):
        return {
            "code": error.code,
            "message": str(error),
            "details": error.details,
        }
    return {"code": code, "message": str(error) or error.__class__.__name__}


__all__ = ["RunnerExecutionService"]
