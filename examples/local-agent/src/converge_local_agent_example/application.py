"""Run an offline Agent through Direct Local tools, working state, and resume."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator, MutableSequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from converge_agent_harness import (
    AgentContext,
    DeferredToolResume,
    DirectLocalEnvironmentConfiguration,
    DirectLocalEnvironmentProviderBinding,
    DirectLocalRootConfiguration,
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentPermissionSet,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    ExecutableAgent,
    HarnessBuilder,
    RunBindings,
    UserInteractionCapability,
    WorkingStateCapability,
    create_environment_run_binding,
)
from converge_agent_harness.tools import (
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolInvocationContext,
)
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

_QUESTION = "Which review style should the plan use?"
_QUESTION_ARGUMENTS = {
    "questions": [
        {
            "header": "Review style",
            "question": _QUESTION,
            "options": [
                {"label": "Focused", "description": "Review only the changed component."},
                {"label": "Broad", "description": "Review the surrounding integration too."},
            ],
            "multiSelect": False,
        }
    ]
}


@dataclass(frozen=True, slots=True)
class LocalAgentDemoResult:
    workspace: Path
    first_run_id: str
    resumed_run_id: str
    output: str
    plan: str
    tool_calls: tuple[str, ...]


class _AllowExampleTools:
    async def __call__(
        self,
        invocation: ToolInvocationContext,
        metadata: HarnessToolMetadata,
        *,
        context: AgentContext,
    ) -> InvocationPolicyDecision:
        del invocation, metadata, context
        return InvocationPolicyDecision.allow()


def _offline_model(tool_calls: MutableSequence[str]) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returned = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        returned_names = {part.tool_name for part in returned}
        if "write" not in returned_names:
            tool_calls.append("write")
            yield {
                0: DeltaToolCall(
                    name="write",
                    json_args=json.dumps(
                        {
                            "file_path": "/workspace/plan.md",
                            "content": "# Local plan\n\nCreated by a managed Direct Local file tool.\n",
                            "mode": "w",
                        }
                    ),
                    tool_call_id="write-plan",
                )
            }
        elif "task_create" not in returned_names:
            tool_calls.append("task_create")
            yield {
                0: DeltaToolCall(
                    name="task_create",
                    json_args=json.dumps(
                        {
                            "subject": "Review the local plan",
                            "description": "Check the generated plan before publishing it.",
                        }
                    ),
                    tool_call_id="create-review-task",
                )
            }
        elif "note" not in returned_names:
            tool_calls.append("note")
            yield {
                0: DeltaToolCall(
                    name="note",
                    json_args=json.dumps({"key": "plan-path", "value": "/workspace/plan.md"}),
                    tool_call_id="remember-plan",
                )
            }
        elif "ask_user_question" not in returned_names:
            tool_calls.append("ask_user_question")
            yield {
                0: DeltaToolCall(
                    name="ask_user_question",
                    json_args=json.dumps(_QUESTION_ARGUMENTS),
                    tool_call_id="choose-review-style",
                )
            }
        else:
            question_result = next(part.content for part in returned if part.tool_name == "ask_user_question")
            if not isinstance(question_result, dict):
                raise RuntimeError("The structured answer was not returned as an object")
            answers = question_result.get("answers")
            if not isinstance(answers, dict) or not isinstance(answers.get(_QUESTION), str):
                raise RuntimeError("The structured answer did not contain the review style")
            review_style = answers[_QUESTION]
            yield f"Local plan created; {review_style.lower()} review selected."

    return FunctionModel(stream_function=stream)


def _build_agent(tool_calls: MutableSequence[str]) -> ExecutableAgent[str]:
    return HarnessBuilder().build_code(
        AgentSpec(model="logical:local-example"),
        output_type=str,
        model=_offline_model(tool_calls),
        capabilities=(
            DynamicEnvironmentCapability(
                DynamicEnvironmentConfiguration(
                    file_tools=True,
                    shell_tools=False,
                    process_tools=False,
                    port_tools=False,
                    max_reference_entries=64,
                )
            ),
            WorkingStateCapability(),
            UserInteractionCapability(),
        ),
    )


def _environment_binding(workspace: Path):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="local-agent-example",
            root=DirectLocalRootConfiguration(path=workspace, ownership="caller_owned"),
        )
    )
    topology = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="workspace",
                binding_revision=1,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(
                    operations=frozenset({EnvironmentAction.FILE_WRITE_TEXT}),
                ),
                default_working_directory="/workspace",
                provider_binding=provider,
            ),
        ),
        default_binding_id="workspace",
    )
    return create_environment_run_binding(
        initial_topology=topology,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


def _fresh_bindings(workspace: Path) -> RunBindings:
    return RunBindings.local(
        environment=_environment_binding(workspace),
        capabilities=(
            InvocationPolicyCapability(
                evaluator=_AllowExampleTools(),
                max_dispatch_retries=0,
            ),
        ),
    )


async def run_local_agent_demo(
    workspace: Path,
    *,
    review_style: str = "Focused",
) -> LocalAgentDemoResult:
    """Run, suspend for structured input, rebuild, and resume with fresh bindings."""

    if review_style not in {"Focused", "Broad"}:
        raise ValueError("review_style must be 'Focused' or 'Broad'")
    await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)
    tool_calls: list[str] = []
    first_agent = _build_agent(tool_calls)
    async with first_agent:
        first = await first_agent.run(
            "Create a local plan and ask which review style to use.",
            bindings=_fresh_bindings(workspace),
        )
    if first.status != "suspended" or first.state is None or first.deferred is None:
        raise RuntimeError("The first run did not suspend for structured user input")
    question = first.deferred.calls[0]

    resumed_agent = _build_agent(tool_calls)
    async with resumed_agent:
        resumed = await resumed_agent.run(
            bindings=_fresh_bindings(workspace),
            previous_state=first.state,
            deferred_resume=DeferredToolResume(
                first.deferred,
                first.deferred.build_results(
                    calls={question.tool_call_id: {"answers": {_QUESTION: review_style}}},
                ),
            ),
        )
    resumed.raise_for_status()
    return LocalAgentDemoResult(
        workspace=workspace,
        first_run_id=first.run_id,
        resumed_run_id=resumed.run_id,
        output=resumed.output_or_raise(),
        plan=await asyncio.to_thread((workspace / "plan.md").read_text),
        tool_calls=tuple(tool_calls),
    )


def _print_result(result: LocalAgentDemoResult) -> None:
    print(f"workspace: {result.workspace}")
    print(f"first Harness run: {result.first_run_id} (suspended)")
    print(f"resumed Harness run: {result.resumed_run_id} (completed)")
    print(f"tool calls: {', '.join(result.tool_calls)}")
    print(f"output: {result.output}")
    print(f"plan:\n{result.plan}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, help="Retain the Direct Local workspace in this directory")
    parser.add_argument("--review-style", choices=("Focused", "Broad"), default="Focused")
    arguments = parser.parse_args()
    if arguments.workspace is not None:
        _print_result(
            asyncio.run(
                run_local_agent_demo(
                    arguments.workspace.resolve(),
                    review_style=arguments.review_style,
                )
            )
        )
        return
    with TemporaryDirectory(prefix="converge-local-agent-") as directory:
        _print_result(
            asyncio.run(
                run_local_agent_demo(
                    Path(directory),
                    review_style=arguments.review_style,
                )
            )
        )


if __name__ == "__main__":
    main()
