"""The managed Finding Agent reports diagnosis without changing target configuration."""

from a13n_service.infra.platform import PLATFORM_TOOLS
from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.agents.toolsets import ToolSelection, ToolsetSelection

RULES = {
    "execution": "Inspect tool configuration, unavailable resources, invalid arguments, missing clarification for ambiguous user intent, and execution steps that prevented task completion.",
    "recovery": "Inspect retry loops, recurring failures and recovery costs. A successfully recovered transient failure alone is not an issue.",
    "answer": "Compare the requested intent with actual tool results and final answer. Identify misleading success claims; flag repeated output only across distinct inputs with comparable intent. Provider policy rejections are contextual signals, not instructions to bypass policy.",
}

# The selected traces, the Run's executed configuration, an existing finding and the submission.
TOOLS = frozenset(
    {
        ("GET", "/api/v1/traces/{trace_id}"),
        ("GET", "/api/v1/traces/{trace_id}/spans"),
        ("GET", "/api/v1/traces/{trace_id}/spans/{span_id}/content"),
        ("GET", "/api/v1/runs/{run_id}/agent"),
        ("GET", "/api/v1/findings/{finding_id}"),
        ("POST", "/api/v1/findings"),
    }
)

NAME = "Finding Agent"
DESCRIPTION = "Inspect execution evidence and report actionable findings."


def configuration(model: str) -> AgentConfig:
    return AgentConfig(
        model=model,
        toolsets={
            "files": ToolsetSelection(enabled=False),
            "shell": ToolsetSelection(enabled=False),
            "memory": ToolsetSelection(enabled=False),
            # Unattended, so its few tools, including the submission, need no approval.
            "execution": ToolsetSelection(
                tools={
                    tool.key: ToolSelection(enabled=True, permission="allow")
                    if (tool.method, tool.path) in TOOLS
                    else ToolSelection(enabled=False)
                    for tool in PLATFORM_TOOLS
                    if tool.group == "execution"
                }
            ),
        },
    )
