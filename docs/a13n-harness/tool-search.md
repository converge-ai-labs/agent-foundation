---
title: Toolset exposure and discovery
description: Choose direct or progressive schemas for native Toolsets while retaining one execution path.
---

Toolset exposure controls how the model sees tools it already has. `direct` exposes ordinary schemas; `progressive` starts with a compact directory and discovers definitions when needed. Native Toolset IDs identify sources, and final tool names stay unchanged. Selection, permissions, credentials and execution remain with their existing owners.

## Configure a Toolset

Pass immutable `ToolExposure` to `AgentDefinition.tool_exposure` or `HarnessBuilder.build(tool_exposure=...)`. Each mapping key is the source's native `ToolDefinition.toolset_id`:

```python
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.tools import ToolExposure, ToolsetExposure
from pydantic_ai.capabilities import Capability
from pydantic_ai.toolsets import FunctionToolset


def customer_name(customer_id: int) -> str:
    """Look up the display name for a customer ID."""
    return {42: "Ada"}.get(customer_id, "Unknown")


source = FunctionToolset(
    [customer_name],
    id="crm",
    instructions="Use numeric customer IDs, not display names.",
)
agent = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=model,  # Supply your configured Pydantic AI Model.
    capabilities=(Capability(toolsets=[source]),),
    tool_exposure=ToolExposure(
        toolsets={
            "crm": ToolsetExposure(
                exposure="progressive",
                description="Customer records and account operations",
            ),
        },
    ),
)
```

A `ToolsetExposure` has three fields:

- `exposure`: `direct` by default, or `progressive`.
- `tool_exposure`: overrides keyed by exact final tool name. Each override wins over the source default.
- `description`: the source's purpose, nonblank and at most 512 characters. Progressive sources require a purpose; Harness supplies defaults for built-in Toolsets.

For example, `tool_exposure={"customer_name": "direct"}` keeps one tool directly visible within a progressive source. ID-less tools remain direct. Exposure adds no prefixes or aliases; existing name collisions still fail.

## Discover, describe and execute

Harness discovery is the default. When progressive tools are available, the model receives five fixed controls:

| Control                             | Use                                                                |
| ----------------------------------- | ------------------------------------------------------------------ |
| `list_toolsets(offset=0)`           | Browse native source IDs, purposes, counts and name previews       |
| `list_tools(toolset, offset=0)`     | Browse summaries within a source                                   |
| `search_tools(query, toolset=None)` | Search current tool names, descriptions and parameter descriptions |
| `describe_tools(names)`             | Read complete definitions and source instructions for exact names  |
| `execute_tool(name, arguments)`     | Invoke an enabled target through the native manager                |

Search returns summaries, even for an exact match. Read a complete definition before constructing unfamiliar arguments:

```python
found = await search_tools(query="customer name", toolset="crm")
definitions = await describe_tools(names=["customer_name"])
result = await execute_tool(name="customer_name", arguments={"customer_id": 42})
result
```

This example is a CodeAct program; ordinary model calls use the same names and argument objects. A known exact name can be executed without another search. `execute_tool` also accepts directly exposed callable tools.

Lists use pages of at most 20 entries with `next_offset`. Search requires a nonempty query and returns up to 20 summaries with a total and partial indicator; narrow a partial search or browse a source. Description accepts 1–20 names. Responses fit 32 KiB of UTF-8 JSON. Complete definitions are never truncated: oversized requests fail with guidance to request fewer definitions or simplify the source schema.

Exact case-insensitive names rank first, followed by keyword coverage and relevance. Keywords split punctuation and camel case. Search reads current schemas without expanding references or fetching external resources. Defaults, examples and enum values are not search text. Discovery is deterministic local matching, not semantic retrieval.

The schema-free source directory is bounded to 8 KiB and five name previews per source. It is injected once per logical Run when changed; an unchanged retained digest suppresses reinjection on later Runs. Progressive source instructions appear with complete descriptions rather than filling every request. Disabling Toolset instructions suppresses that guidance too.

## Select native tool search

Choose provider-native discovery with the same source policies:

```python
policy = ToolExposure(
    progressive_search="native",
    progressive_search_strategy="bm25",
    toolsets={
        "crm": ToolsetExposure(
            exposure="progressive",
            description="Customer records and account operations",
        ),
    },
)
```

Omit `progressive_search_strategy` for the provider default, or choose `bm25` or `regex`. Strategy is valid only in native mode. The provider validates strategy support. Unsupported models fail with `native_tool_search_unsupported` before a model request; Harness does not fall back.

Native mode uses upstream deferred tool search instead of the five controls. Discovered tools are called directly under their unchanged names with ordinary validation, usage and deferred feedback. This is native discovery of locally executed tools, not provider-side MCP execution.

## MCP and plugin sources

Keep MCP Capabilities in their ordinary contribution path. Configure local execution with `native=False, local=True`, provide a stable native ID, and reference that ID in `ToolExposure.toolsets`. For Run-bound sources such as `ContextualMCP`, retain the Capability itself instead of extracting its Toolset early. Headers resolve once per logical Run and remain isolated across concurrent Runs. See [MCP tools](mcp.md).

Plugins likewise retain their normal `for_agent()`, `get_capabilities()`, middleware and Run binding. Exposure does not instantiate sources again. A Host plugin factory can provide `describe_toolsets(context)` to describe native IDs and purposes before construction. Store stable IDs and policy, not serialized Python objects or credentials. Host validation rejects blank or duplicate native source IDs.

Progressive exposure reduces model context, not source initialization or MCP tool-listing cost. Sources remain enabled or disabled through their existing selection controls.

## Use with CodeAct

Source owners must publish typed CodeAct eligibility. In Harness mode, the five controls are available inside the runner while progressive target schemas remain out of its initial directory. The bridge resolves `execute_tool` to the actual target and checks that target's eligibility, policy and arguments. Eligibility on the control grants nothing to targets.

In native mode, discover a tool outside CodeAct first. Eligible discovered tools then appear in runner directories and can be called under their ordinary sandbox names. See [CodeAct](delegation-and-codeact.md#codeact) for owner policy and runner requirements.

Every host-function call in a program is awaited. An ordinary successful `execute_tool` call consumes two successful calls: envelope and target. CodeAct dispatches the target without another envelope call, so one runner plus one target also consumes two. Additional searches or descriptions each consume one call. Limits are checked before dispatch.

## Approvals, client tools and continuation

`execute_tool` supports local functions, approval-gated tools, external client tools and structured user questions. Validation, permissions, hooks, credentials, timeouts, native retries and result bounds remain authoritative. Discovery controls, output tools, CodeAct runners and provider-native tools cannot be targets. Execution-control tools remain eligible.

Native approval and external deferral suspend normally. Pending metadata identifies the validated actual target while native history keeps the outer call ID. Hosts use `project_tool_call` to display approvals, deliver external calls and interpret answers, retaining original pending facts for resume. Approval argument overrides are translated once by Harness. There is no parallel pending store or resume protocol.

A first argument-validation failure includes the full current definition when it fits the budget. Cancellation and unexpected errors do not roll back effects; inspect uncertain outcomes before retrying mutations.

Handoff and compaction retain a bounded working set of called-tool definitions and the current directory. Searches and descriptions alone do not add tools to that working set. Reconstructed discovery uses current definitions and grants no new permissions.

For Host configuration examples, see [Harness UI source exposure](../a13n-harness-ui/agents-and-subagents.md#toolset-exposure) and [Service tools](../a13n-service/tools.md#progressive-tool-discovery).
