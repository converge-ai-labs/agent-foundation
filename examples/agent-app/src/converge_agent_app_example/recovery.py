"""Add Host-owned checkpoint selection, fencing, and recovery to the Agent app."""

from __future__ import annotations

from collections.abc import AsyncIterator, MutableSequence
from dataclasses import dataclass
from pathlib import Path

from converge_agent_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    ExecutableAgent,
    NoopEnvironmentRunBinding,
    RunBindings,
)
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ThinkingPart
from pydantic_ai.models.function import AgentInfo, DeltaThinkingPart, FunctionModel

from .application import build_agent
from .store import ExecutionAttemptLease, HostExecutionRecord, HostStoreError, JsonFileHostStore


@dataclass(frozen=True, slots=True)
class HostRecoveryResult:
    state_directory: Path
    execution: HostExecutionRecord
    interrupted_run_id: str
    recovery_run_id: str
    interrupted_model_history_size: int
    recovery_model_history_size: int
    partial_text_recovered: bool
    completed_thinking_recovered: bool
    incomplete_thinking_excluded: bool
    stale_execution_attempt_rejected: bool


def _create_recovery_model(
    observed_history_sizes: MutableSequence[int],
    *,
    simulate_interruption: bool,
) -> FunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | dict[int, DeltaThinkingPart]]:
        del info
        observed_history_sizes.append(len(messages))
        if simulate_interruption:
            yield {0: DeltaThinkingPart(content="complete private reasoning")}
            yield {0: DeltaThinkingPart(signature="signature-1")}
            yield "visible answer before interruption"
            yield {1: DeltaThinkingPart(content="unfinished private reasoning")}
            raise RuntimeError("simulated model stream interruption")
        yield f"offline response after {len(messages)} model messages"

    return FunctionModel(stream_function=stream)


def _build_recovery_agent(
    observed_history_sizes: MutableSequence[int],
    *,
    simulate_interruption: bool,
) -> ExecutableAgent[str]:
    return build_agent(
        model=_create_recovery_model(
            observed_history_sizes,
            simulate_interruption=simulate_interruption,
        ),
        model_id="logical:agent-app-recovery",
        instructions="Continue the Host-owned execution from its selected checkpoint.",
    )


def _create_host_run_bindings(
    execution: HostExecutionRecord,
    attempt: ExecutionAttemptLease,
) -> RunBindings:
    """Recreate current authority rather than restoring it from HarnessState."""

    return RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="example-host", subject="assistant"),
            agent_instance_id=execution.agent_instance_id,
            host_refs={
                "execution": execution.execution_id,
                "execution_attempt": attempt.execution_attempt_id,
            },
        ),
        environment=NoopEnvironmentRunBinding(),
        metadata={"execution_attempt_generation": attempt.generation},
    )


async def run_host_recovery(state_directory: Path) -> HostRecoveryResult:
    """Persist a checkpoint, replace its ExecutionAttempt, resume, and commit."""

    initial_host_store = JsonFileHostStore(state_directory)
    created_execution = await initial_host_store.create_execution(
        execution_id="execution-1",
        definition_revision_ref="definition-1",
        agent_instance_id="agent-1",
    )
    observed_history_sizes: list[int] = []

    interrupted_attempt = await initial_host_store.acquire_execution_attempt(created_execution.execution_id)
    interrupted_execution = await initial_host_store.read_execution(created_execution.execution_id)
    interrupted_agent = _build_recovery_agent(
        observed_history_sizes,
        simulate_interruption=True,
    )
    async with interrupted_agent:
        interrupted_result = await interrupted_agent.run(
            "Record the first durable interaction.",
            bindings=_create_host_run_bindings(interrupted_execution, interrupted_attempt),
        )
    interrupted_state = interrupted_result.state
    if interrupted_result.status != "failed" or interrupted_state is None:
        raise RuntimeError("Interrupted Harness result did not carry continuation state")
    interrupted_responses = [
        message
        for message in interrupted_state.message_history
        if isinstance(message, ModelResponse) and message.state == "interrupted"
    ]
    partial_text_recovered = any(
        isinstance(part, TextPart) and part.content == "visible answer before interruption"
        for response in interrupted_responses
        for part in response.parts
    )
    completed_thinking_recovered = any(
        isinstance(part, ThinkingPart)
        and part.content == "complete private reasoning"
        and part.signature == "signature-1"
        for response in interrupted_responses
        for part in response.parts
    )
    incomplete_thinking_excluded = not any(
        isinstance(part, ThinkingPart) and part.content == "unfinished private reasoning"
        for response in interrupted_responses
        for part in response.parts
    )
    if not partial_text_recovered or not completed_thinking_recovered or not incomplete_thinking_excluded:
        raise RuntimeError("Harness interruption recovery did not produce a safe checkpoint")
    await initial_host_store.commit_checkpoint(
        interrupted_attempt,
        harness_run_id=interrupted_result.run_id,
        harness_state=interrupted_state,
    )

    # A new Host store instance represents recovery after the first worker is
    # gone. Host authority replaces its durable attempt without the lost fence.
    recovery_host_store = JsonFileHostStore(state_directory)
    recovery_attempt = await recovery_host_store.replace_current_execution_attempt(created_execution.execution_id)
    selected_checkpoint = recovery_attempt.starting_checkpoint
    if selected_checkpoint is None:
        raise RuntimeError("The replacement ExecutionAttempt did not freeze its starting checkpoint")
    recovery_execution = await recovery_host_store.read_execution(created_execution.execution_id)

    stale_execution_attempt_rejected = False
    try:
        await recovery_host_store.commit_checkpoint(
            interrupted_attempt,
            harness_run_id=interrupted_result.run_id,
            harness_state=interrupted_state,
        )
    except HostStoreError:
        stale_execution_attempt_rejected = True

    # A replacement worker reconstructs a fresh model, executable, identity,
    # and Environment binding from current Host authority.
    recovery_agent = _build_recovery_agent(
        observed_history_sizes,
        simulate_interruption=False,
    )
    async with recovery_agent:
        recovery_result = await recovery_agent.run(
            "Continue from the Host-selected checkpoint under fresh bindings.",
            bindings=_create_host_run_bindings(recovery_execution, recovery_attempt),
            previous_state=selected_checkpoint.harness_state,
        )
    recovery_result.raise_for_status()
    recovery_state = recovery_result.state
    if recovery_state is None:
        raise RuntimeError("Replacement Harness result did not carry continuation state")
    await recovery_host_store.commit_checkpoint(
        recovery_attempt,
        harness_run_id=recovery_result.run_id,
        harness_state=recovery_state,
    )
    completed_execution = await recovery_host_store.commit_completed(
        recovery_attempt,
        output=recovery_result.output_or_raise(),
    )

    if len(observed_history_sizes) != 2:
        raise RuntimeError("The offline models did not observe exactly two Harness runs")
    return HostRecoveryResult(
        state_directory=state_directory,
        execution=completed_execution,
        interrupted_run_id=interrupted_result.run_id,
        recovery_run_id=recovery_result.run_id,
        interrupted_model_history_size=observed_history_sizes[0],
        recovery_model_history_size=observed_history_sizes[1],
        partial_text_recovered=partial_text_recovered,
        completed_thinking_recovered=completed_thinking_recovered,
        incomplete_thinking_excluded=incomplete_thinking_excluded,
        stale_execution_attempt_rejected=stale_execution_attempt_rejected,
    )


def print_host_recovery_result(result: HostRecoveryResult) -> None:
    """Print the Host persistence and recovery layer result."""

    print(f"state directory: {result.state_directory}")
    print(f"execution: {result.execution.execution_id}")
    print(f"definition revision: {result.execution.definition_revision_ref}")
    print(f"state: {result.execution.state}")
    print(f"selected checkpoint: {result.execution.selected_checkpoint_ref}")
    print(f"interrupted Harness run: {result.interrupted_run_id}")
    print(f"recovery Harness run: {result.recovery_run_id}")
    print(f"interrupted model history size: {result.interrupted_model_history_size}")
    print(f"recovery model history size: {result.recovery_model_history_size}")
    print(f"partial text recovered: {result.partial_text_recovered}")
    print(f"completed thinking recovered: {result.completed_thinking_recovered}")
    print(f"incomplete thinking excluded: {result.incomplete_thinking_excluded}")
    print(f"stale ExecutionAttempt rejected: {result.stale_execution_attempt_rejected}")
