---
title: Grouped ToolSearch
description: Scale Python agents to hundreds of tools with on-demand grouped ToolSearch discovery, reducing prompt token overhead while keeping schemas intact.
---

When an Agent connects to dozens or hundreds of tools, group related sources by domain behind five fixed proxy controls (`discover_tools`, `describe_tool`, `call_tool`, `list_tool_groups`, `list_tools`) so the Model loads only the schemas needed for the current turn.

## Group a Toolset

Pass a mapping of domain names to `ToolSearchGroup(source=..., description=...)` into `ToolSearchCapability`:

```python title="search_demo.py"
import asyncio
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import ProxyToolSearchConfig, ToolSearchCapability, ToolSearchGroup
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.toolsets import FunctionToolset


def customer_name(customer_id: int) -> str:
    """Look up the display name for a customer ID."""
    return {42: "Ada"}.get(customer_id, "Unknown")


source = FunctionToolset([customer_name], instructions="Use numeric customer IDs.")
proxy = ToolSearchCapability(
    groups={"crm": ToolSearchGroup(source=source, description="Customer records")},
    config=ProxyToolSearchConfig(max_results=10, max_search_bytes=32_768),
)
```

At runtime, the Model follows a three-step path (or `detail="full"` in one call):

| Control tool                      | Purpose                                                                      |
| --------------------------------- | ---------------------------------------------------------------------------- |
| `discover_tools`                  | Keyword search (`detail="auto"`, `"summary"`, or `"full"`; optional `group`) |
| `describe_tool`                   | Load a candidate's full JSON schema and source Toolset instructions          |
| `call_tool`                       | Invoke `(group, tool, arguments)` through normal validation and permissions  |
| `list_tool_groups` / `list_tools` | Browse available domains and paginate member tools (`next_offset`)           |

Grouping changes schema discovery only—calls still pass through normal validation, [managed tool permissions](managed-tools.md#select-tool-permissions), approvals, and [client-tool feedback](client-tools.md). To make grouped tools callable inside `CodeAct`, wrap the underlying source in `CodeActPolicyToolset` (see [CodeAct](delegation-and-codeact.md#codeact)).

## Group a Run-bound MCP Capability

Pass a local `MCP` or `ContextualMCP` (`native=False, local=True`, without `defer_loading=True`) directly as `ToolSearchGroup(source=...)` so per-Run headers resolve before tool preparation:

```python
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
from a13n_harness.mcp import ContextualMCP

knowledge = ToolSearchGroup(
    source=ContextualMCP("https://mcp.example.com/mcp", id="knowledge", native=False, local=True),
    description="Search and read the tenant's knowledge base",
)
capabilities = (ToolSearchCapability(groups={"knowledge": knowledge}),)
```

## Host integration

When Capabilities or plugins are already attached to the Agent definition, pass a `ToolSearchPlan` to `HarnessBuilder().build(..., tool_search=...)` (or `AgentDefinition.tool_search`) instead of wrapping sources manually:

```python
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection
from pydantic_ai.capabilities import Capability

customer_tools = Capability(id="customer-tools", tools=[customer_name])
agent = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=model,
    capabilities=(customer_tools,),
    tool_search=ToolSearchPlan(
        groups={
            "crm": ToolSearchSelection(
                "Customer records",
                capabilities=(customer_tools,),  # or plugins=("customer-plugin",)
            )
        }
    ),
)
```

For Harness UI's declarative YAML equivalent, see [Tool search groups](../a13n-harness-ui/agents-and-subagents.md#tool-search-groups).
