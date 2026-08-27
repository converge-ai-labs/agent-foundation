"""Add local tools, working state, structured suspension, and resume."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, MutableSequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from a13n_environment_provider import (
    DirectLocalProviderRuntime,
    EnvironmentManagementAction,
    EnvironmentManager,
    EnvironmentOperationContext,
    EnvironmentProviderSpec,
    build_environment_provider_factory_catalog,
)
from a13n_harness import (
    AgentContext,
    DeferredToolResume,
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentPermissionSet,
    EnvironmentProviderBinding,
    EnvironmentRunBinding,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    ExecutableAgent,
    RunBindings,
    UserInteractionCapability,
    WorkingStateCapability,
    create_environment_provider_binding,
    create_environment_run_binding,
)
from a13n_harness.tools import (
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolInvocationContext,
)
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from .application import build_agent

_DIRECT_LOCAL_RESOURCE_CORRELATION = "resource-agent-app-local"
_REVIEW_STYLE_QUESTION = "Which review style should the plan use?"
_REVIEW_STYLE_QUESTION_ARGUMENTS = {
    "questions": [
        {
            "header": "Review style",
            "question": _REVIEW_STYLE_QUESTION,
            "options": [
                {"label": "Focused", "description": "Review only the changed component."},
                {"label": "Broad", "description": "Review the surrounding integration too."},
            ],
            "multiSelect": False,
        }
    ]
}


@dataclass(frozen=True, slots=True)
class LocalWorkspaceResult:
    workspace: Path
    suspended_run_id: str
    resumed_run_id: str
    output: str
    plan_content: str
    tool_call_names: tuple[str, ...]


class _AllowTeachingWorkspaceTools:
    async def __call__(
        self,
        invocation: ToolInvocationContext,
        metadata: HarnessToolMetadata,
        *,
        context: AgentContext,
    ) -> InvocationPolicyDecision:
        del invocation, metadata, context
        return InvocationPolicyDecision.allow()


def _create_local_model(observed_tool_call_names: MutableSequence[str]) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        tool_return_parts = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        returned_tool_names = {part.tool_name for part in tool_return_parts}
        if "write" not in returned_tool_names:
            observed_tool_call_names.append("write")
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
        elif "task_create" not in returned_tool_names:
            observed_tool_call_names.append("task_create")
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
        elif "note" not in returned_tool_names:
            observed_tool_call_names.append("note")
            yield {
                0: DeltaToolCall(
                    name="note",
                    json_args=json.dumps({"key": "plan-path", "value": "/workspace/plan.md"}),
                    tool_call_id="remember-plan",
                )
            }
        elif "ask_user_question" not in returned_tool_names:
            observed_tool_call_names.append("ask_user_question")
            yield {
                0: DeltaToolCall(
                    name="ask_user_question",
                    json_args=json.dumps(_REVIEW_STYLE_QUESTION_ARGUMENTS),
                    tool_call_id="choose-review-style",
                )
            }
        else:
            structured_answer = next(
                part.content for part in tool_return_parts if part.tool_name == "ask_user_question"
            )
            if not isinstance(structured_answer, dict):
                raise RuntimeError("The structured answer was not returned as an object")
            answers = structured_answer.get("answers")
            if not isinstance(answers, dict) or not isinstance(answers.get(_REVIEW_STYLE_QUESTION), str):
                raise RuntimeError("The structured answer did not contain the review style")
            selected_review_style = answers[_REVIEW_STYLE_QUESTION]
            yield f"Local plan created; {selected_review_style.lower()} review selected."

    return FunctionModel(stream_function=stream)


def _build_local_agent(observed_tool_call_names: MutableSequence[str]) -> ExecutableAgent[str]:
    return build_agent(
        model=_create_local_model(observed_tool_call_names),
        model_id="logical:agent-app-local",
        instructions="Create the requested plan and ask for the review style.",
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


def _create_direct_local_manager(workspace: Path) -> EnvironmentManager:
    catalog = build_environment_provider_factory_catalog(builtin_keys=("a13n.direct-local",))
    return catalog.create_manager(
        EnvironmentProviderSpec(
            provider_key="a13n.direct-local",
            schema_version="1",
            parameters={
                "environment_id": "agent-app-local",
                "root": {"path": str(workspace)},
            },
        ),
        runtime=DirectLocalProviderRuntime(),
    )


def _create_workspace_environment_binding(
    provider_binding: EnvironmentProviderBinding,
) -> EnvironmentRunBinding:
    requested_topology = EnvironmentTopologyRequest(
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
                provider_binding=provider_binding,
            ),
        ),
        default_binding_id="workspace",
    )
    return create_environment_run_binding(
        initial_topology=requested_topology,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


@asynccontextmanager
async def _create_local_run_bindings(
    environment_manager: EnvironmentManager,
    *,
    run_sequence: int,
) -> AsyncGenerator[RunBindings]:
    managed_environment = await environment_manager.create(
        operation=EnvironmentOperationContext(
            operation_id=f"operation-create-{run_sequence}",
            action=EnvironmentManagementAction.CREATE,
            resource_correlation=_DIRECT_LOCAL_RESOURCE_CORRELATION,
            attempt=1,
        )
    )
    provider_state = managed_environment.state
    try:
        async with managed_environment:
            async with managed_environment.acquire_attachment() as attachment:
                provider_binding = create_environment_provider_binding(attachment)
                yield RunBindings.local(
                    environment=_create_workspace_environment_binding(provider_binding),
                    capabilities=(
                        InvocationPolicyCapability(
                            evaluator=_AllowTeachingWorkspaceTools(),
                            max_dispatch_retries=0,
                        ),
                    ),
                )
    finally:
        await environment_manager.destroy(
            provider_state,
            operation=EnvironmentOperationContext(
                operation_id=f"operation-destroy-{run_sequence}",
                action=EnvironmentManagementAction.DESTROY,
                resource_correlation=_DIRECT_LOCAL_RESOURCE_CORRELATION,
                attempt=1,
            ),
        )


async def run_local_workspace(
    workspace: Path,
    *,
    review_style: str = "Focused",
) -> LocalWorkspaceResult:
    """Run, suspend for structured input, rebuild, and resume with fresh bindings."""

    if review_style not in {"Focused", "Broad"}:
        raise ValueError("review_style must be 'Focused' or 'Broad'")
    await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)
    observed_tool_call_names: list[str] = []
    environment_manager = _create_direct_local_manager(workspace)

    initial_agent = _build_local_agent(observed_tool_call_names)
    async with initial_agent, _create_local_run_bindings(environment_manager, run_sequence=1) as initial_bindings:
        suspended_result = await initial_agent.run(
            "Create a local plan and ask which review style to use.",
            bindings=initial_bindings,
        )
    if suspended_result.status != "suspended" or suspended_result.state is None or suspended_result.deferred is None:
        raise RuntimeError("The first run did not suspend for structured user input")
    deferred_review_question = suspended_result.deferred.calls[0]

    resumed_agent = _build_local_agent(observed_tool_call_names)
    async with resumed_agent, _create_local_run_bindings(environment_manager, run_sequence=2) as resumed_bindings:
        resumed_result = await resumed_agent.run(
            bindings=resumed_bindings,
            previous_state=suspended_result.state,
            deferred_resume=DeferredToolResume(
                suspended_result.deferred,
                suspended_result.deferred.build_results(
                    calls={deferred_review_question.tool_call_id: {"answers": {_REVIEW_STYLE_QUESTION: review_style}}},
                ),
            ),
        )
    resumed_result.raise_for_status()
    plan_path = workspace / "plan.md"
    return LocalWorkspaceResult(
        workspace=workspace,
        suspended_run_id=suspended_result.run_id,
        resumed_run_id=resumed_result.run_id,
        output=resumed_result.output_or_raise(),
        plan_content=await asyncio.to_thread(plan_path.read_text),
        tool_call_names=tuple(observed_tool_call_names),
    )


def print_local_workspace_result(result: LocalWorkspaceResult) -> None:
    """Print the local Capability and Environment layer result."""

    print(f"workspace: {result.workspace}")
    print(f"suspended Harness run: {result.suspended_run_id}")
    print(f"resumed Harness run: {result.resumed_run_id}")
    print(f"tool calls: {', '.join(result.tool_call_names)}")
    print(f"output: {result.output}")
    print(f"plan:\n{result.plan_content}")
