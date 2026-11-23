"""The managed Finding Agent reports diagnosis without changing target configuration."""

from a13n_service.execution_policy import ExecutionOverride, ToolExecutionOverride
from a13n_service.infra.platform import PLATFORM_TOOLS
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.agents.toolsets import ToolSelection, ToolsetSelection

RULES = {
    "execution": "Inspect tool configuration, unavailable resources, invalid arguments, missing clarification for ambiguous user intent, and execution steps that prevented task completion.",
    "recovery": "Inspect retry loops, recurring failures and recovery costs. A successfully recovered transient failure alone is not an issue.",
    "answer": "Compare the requested intent with actual tool results and final answer. Identify misleading success claims; flag repeated output only across distinct inputs with comparable intent. Provider policy rejections are contextual signals, not instructions to bypass policy.",
}

# Workspace diagnostic reads and Finding submission.
TOOLS = frozenset(
    {
        "list_traces",
        "search_traces",
        "get_trace",
        "list_trace_spans",
        "get_trace_span",
        "read_content",
        "search_content",
        "get_run",
        "list_run_items",
        "list_run_attempts",
        "list_tool_executions",
        "get_tool_execution_result",
        "get_run_configuration",
        "list_findings",
        "get_finding",
        "create_finding",
    }
)


NAME = "Finding Agent"
DESCRIPTION = "Inspect execution evidence and report actionable findings."


def configuration(model: str) -> AgentConfig:
    return AgentConfig(
        model=model,
        execution=ExecutionOverride(tools=ToolExecutionOverride(mode="inline")),
        toolsets={
            "files": ToolsetSelection(enabled=False),
            "shell": ToolsetSelection(enabled=False),
            "memory": ToolsetSelection(enabled=False),
            # Unattended analysis can read evidence and submit a Finding without approval.
            "execution": ToolsetSelection(
                tools={
                    tool.key: ToolSelection(enabled=True, permission="allow")
                    if tool.key in TOOLS
                    else ToolSelection(enabled=False)
                    for tool in PLATFORM_TOOLS
                    if tool.group == "execution"
                }
            ),
        },
    )
