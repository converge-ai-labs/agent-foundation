"""Run two fresh Harness invocations through one Host-owned execution record."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, MutableSequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from converge_agent_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    ExecutableAgent,
    HarnessBuilder,
    NoopEnvironmentRunBinding,
    RunBindings,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ThinkingPart
from pydantic_ai.models.function import AgentInfo, DeltaThinkingPart, FunctionModel

from converge_host_example.store import (
    ExecutionAttemptLease,
    HostExecutionRecord,
    HostStoreError,
    JsonFileHostStore,
)


@dataclass(frozen=True, slots=True)
class HostDemoResult:
    state_directory: Path
    execution: HostExecutionRecord
    first_run_id: str
    replacement_run_id: str
    first_model_message_count: int
    replacement_model_message_count: int
    partial_text_recovered: bool
    completed_thinking_recovered: bool
    incomplete_thinking_excluded: bool
    stale_execution_attempt_rejected: bool


def _offline_model(observed_message_counts: MutableSequence[int]) -> FunctionModel:
    async def stream(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | dict[int, DeltaThinkingPart]]:
        del info
        observed_message_counts.append(len(messages))
        if len(observed_message_counts) == 1:
            yield {0: DeltaThinkingPart(content="complete private reasoning")}
            yield {0: DeltaThinkingPart(signature="signature-1")}
            yield "visible answer before interruption"
            yield {1: DeltaThinkingPart(content="unfinished private reasoning")}
            raise RuntimeError("simulated model stream interruption")
        yield f"offline response after {len(messages)} model messages"

    return FunctionModel(stream_function=stream)


def _build_agent(observed_message_counts: MutableSequence[int]) -> ExecutableAgent[str]:
    return HarnessBuilder().build_code(
        AgentSpec(model="logical:host-example"),
        output_type=str,
        model=_offline_model(observed_message_counts),
    )


def _fresh_bindings(record: HostExecutionRecord, lease: ExecutionAttemptLease) -> RunBindings:
    """Recreate current authority rather than restoring it from HarnessState."""

    return RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="example-host", subject="assistant"),
            agent_instance_id=record.agent_instance_id,
            host_refs={
                "execution": record.execution_id,
                "execution_attempt": lease.execution_attempt_id,
            },
        ),
        environment=NoopEnvironmentRunBinding(),
        metadata={"execution_attempt_generation": lease.generation},
    )


async def run_host_demo(state_directory: Path) -> HostDemoResult:
    """Persist a checkpoint, replace its ExecutionAttempt, resume, and commit."""

    store = JsonFileHostStore(state_directory)
    execution = await store.create_execution(
        execution_id="execution-1",
        definition_revision_ref="definition-1",
        agent_instance_id="agent-1",
    )
    observed_message_counts: list[int] = []
    executable = _build_agent(observed_message_counts)

    async with executable:
        first_lease = await store.acquire_execution_attempt(execution.execution_id)
        first_record = await store.read_execution(execution.execution_id)
        first_result = await executable.run(
            "Record the first durable interaction.",
            bindings=_fresh_bindings(first_record, first_lease),
        )
        first_state = first_result.state
        if first_result.status != "failed" or first_state is None:
            raise RuntimeError("Interrupted Harness result did not carry continuation state")
        interrupted_responses = [
            message
            for message in first_state.message_history
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
        await store.commit_checkpoint(
            first_lease,
            harness_run_id=first_result.run_id,
            harness_state=first_state,
        )

        # The Host selects the failed run's safe continuation candidate but
        # does not turn that Harness-local failure into a Host terminal commit.
        # It invalidates the first owner as if replacing an interrupted worker.
        await store.abandon_execution_attempt_for_recovery(first_lease)
        replacement_lease = await store.acquire_execution_attempt(execution.execution_id)
        selected = replacement_lease.starting_checkpoint
        if selected is None:
            raise RuntimeError("The replacement ExecutionAttempt did not freeze its starting checkpoint")
        replacement_record = await store.read_execution(execution.execution_id)

        stale_execution_attempt_rejected = False
        try:
            await store.commit_checkpoint(
                first_lease,
                harness_run_id=first_result.run_id,
                harness_state=first_state,
            )
        except HostStoreError:
            stale_execution_attempt_rejected = True

        replacement_result = await executable.run(
            "Continue from the Host-selected checkpoint under fresh bindings.",
            bindings=_fresh_bindings(replacement_record, replacement_lease),
            previous_state=selected.harness_state,
        )
        replacement_result.raise_for_status()
        replacement_state = replacement_result.state
        if replacement_state is None:
            raise RuntimeError("Replacement Harness result did not carry continuation state")
        await store.commit_checkpoint(
            replacement_lease,
            harness_run_id=replacement_result.run_id,
            harness_state=replacement_state,
        )
        execution = await store.commit_completed(
            replacement_lease,
            output=replacement_result.output_or_raise(),
        )

    if len(observed_message_counts) != 2:
        raise RuntimeError("The offline model did not observe exactly two Harness runs")
    return HostDemoResult(
        state_directory=state_directory,
        execution=execution,
        first_run_id=first_result.run_id,
        replacement_run_id=replacement_result.run_id,
        first_model_message_count=observed_message_counts[0],
        replacement_model_message_count=observed_message_counts[1],
        partial_text_recovered=partial_text_recovered,
        completed_thinking_recovered=completed_thinking_recovered,
        incomplete_thinking_excluded=incomplete_thinking_excluded,
        stale_execution_attempt_rejected=stale_execution_attempt_rejected,
    )


def _print_result(result: HostDemoResult) -> None:
    print(f"state directory: {result.state_directory}")
    print(f"execution: {result.execution.execution_id}")
    print(f"definition revision: {result.execution.definition_revision_ref}")
    print(f"state: {result.execution.state}")
    print(f"selected checkpoint: {result.execution.selected_checkpoint_ref}")
    print(f"first Harness run: {result.first_run_id}")
    print(f"replacement Harness run: {result.replacement_run_id}")
    print(f"first model message count: {result.first_model_message_count}")
    print(f"replacement model message count: {result.replacement_model_message_count}")
    print(f"partial text recovered: {result.partial_text_recovered}")
    print(f"completed thinking recovered: {result.completed_thinking_recovered}")
    print(f"incomplete thinking excluded: {result.incomplete_thinking_excluded}")
    print(f"stale ExecutionAttempt rejected: {result.stale_execution_attempt_rejected}")


def main() -> None:
    """Run the offline example, optionally retaining its local state files."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, help="Retain Host state in this directory")
    arguments = parser.parse_args()
    if arguments.state_dir is not None:
        _print_result(asyncio.run(run_host_demo(arguments.state_dir)))
        return
    with TemporaryDirectory(prefix="converge-host-example-") as directory:
        _print_result(asyncio.run(run_host_demo(Path(directory))))


if __name__ == "__main__":
    main()
