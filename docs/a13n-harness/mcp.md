---
title: MCP tools
description: Connect tools from MCP servers and send headers derived from the current identity or Run.
---

Use MCP to connect tools from another process or service. Choose `MCP` for connection setup and `ContextualMCP` for headers that change with each Run.

| Need                                                                   | Choose                                                    |
| ---------------------------------------------------------------------- | --------------------------------------------------------- |
| A static server, stdio process, in-process server, or prebuilt Toolset | Native `MCP`                                              |
| URL server headers derived from the current Harness identity or Run    | `ContextualMCP`                                           |
| A command or JSON server setup in Harness UI                           | [Harness UI MCP configuration](../a13n-harness-ui/mcp.md) |

In SDK code, add an MCP Capability. In Harness UI, configure an MCP resource.

## Run an MCP tool offline

Save this as `mcp_demo.py` and run `uv run python mcp_demo.py` in the [source setup](getting-started.md#requirements). The example starts an in-process server, calls its `add` tool twice, and uses no provider credentials.

```python title="mcp_demo.py"
import asyncio
from collections.abc import AsyncIterator

from fastmcp import Client, FastMCP
from pydantic_ai.capabilities import MCP
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from a13n_harness import AgentSpec, HarnessBuilder, RunBindings

server = FastMCP("counter")


@server.tool
def add(a: int, b: int) -> int:
    return a + b


async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
    if isinstance(messages[-1], ModelRequest):
        for part in messages[-1].parts:
            if isinstance(part, ToolReturnPart):
                yield f"Result: {part.content}"
                return
    yield {0: DeltaToolCall(name=info.function_tools[0].name, json_args='{"a": 3, "b": 5}')}


async def main() -> None:
    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond))
    async with Client(server, mode="auto") as client:
        for prompt in ("Add 3 and 5", "Add them again"):
            projection = MCPToolset(client, id="calculator", cache_tools=False)
            bindings = RunBindings.embedded(capabilities=(MCP(id="calculator", local=projection),))
            result = await executable.run(prompt, bindings=bindings)
            print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Both Runs print `Result: 8`. The Host keeps the entered client; each Run gets a fresh `MCPToolset` projection.

## Native MCP

Add native `MCP` to `AgentSpec.capabilities` or to the builder's `capabilities=` argument.

A URL server with local execution (`local: True`) can be reconstructed directly from an AgentSpec document:

```python
from a13n_harness import AgentSpec

agent_spec = AgentSpec.from_dict(
    {
        "capabilities": [
            {
                "MCP": {
                    "url": "https://mcp.example.com/mcp",
                    "id": "knowledge",
                    "local": True,
                    "native": False,
                    "allowed_tools": ["search"],
                }
            }
        ]
    }
)
```

The base installation includes the local URL and stdio MCP runtime. For in-process servers, transports, script paths, or prebuilt `MCPToolset` values, construct `MCP` in Python. Set `defer_loading=True` to discover the Capability through `load_capability`. Set `native=True, local=False` for a URL server executed by the selected model provider.

## Host-owned clients

When several Runs should use the same server state, trusted Host code can own an entered FastMCP client and create a new upstream projection for each Run:

```python
from fastmcp import Client
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import MCPToolset

from a13n_harness import RunBindings


async def use_host_client(executable):
    async with Client("https://mcp.example.com/mcp", mode="auto") as client:
        results = []
        for prompt in ("Create a workspace", "Inspect that workspace"):
            projection = MCPToolset(client, id="workspace", cache_tools=False)
            bindings = RunBindings.embedded(
                capabilities=(MCP(id="workspace", local=projection),)
            )
            results.append(await executable.run(prompt, bindings=bindings))
        return results
```

Configure authentication and input handlers before entering the client. `mode="auto"` selects modern discovery or legacy negotiation; explicit `legacy` and `2026-07-28` modes are also available. FastMCP handles multi-round input and request state. Keep clients scoped to the same identity and headers, and create a fresh projection for each Run. If a business call loses its response, check the server outcome before repeating it.

## Run-scoped headers with `ContextualMCP`

Use `ContextualMCP` when a URL server needs the current user, Thread, or Run in its headers. Harness resolves those headers once per Run before connecting the MCP tools.

For common identity, lineage, Run, and metadata values, use the declarative resolver:

```python
from a13n_harness import (
    AgentIdentityRef,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.mcp import (
    ContextualMCP,
    MCPContextHeaderBinding,
    MCPContextHeaders,
    MCPContextHeadersConfig,
)

mcp = ContextualMCP(
    "https://mcp.example.com/mcp",
    id="knowledge",
    native=True,
    local=None,
    headers={"X-Application": "support"},
    headers_factory=MCPContextHeaders(
        MCPContextHeadersConfig(
            headers={
                "X-Run-ID": MCPContextHeaderBinding("context.run_id"),
                "X-Thread-ID": MCPContextHeaderBinding("context.thread_id"),
                "X-User-ID": MCPContextHeaderBinding("identity.user_id"),
                "X-Request-Context": MCPContextHeaderBinding(
                    "context.metadata.request_context",
                    required=False,
                ),
            }
        )
    ),
)

executable = HarnessBuilder().build(
    agent_spec,
    output_type=str,
    model=model,
    capabilities=(mcp,),
)

bindings = RunBindings.embedded(
    identity=AgentIdentityRef(
        issuer="my-host",
        subject="support-agent",
        user_id="user-123",
        agent_id="agent-support",
    ),
    metadata={
        "request_context": {
            "region": "us-east",
            "labels": ["interactive", "priority"],
        }
    },
)
result = await executable.run("Find the account record", bindings=bindings)
```

Put extra JSON values in `RunBindings.metadata`; `context.metadata.<key>` selects one exact top-level key.

The declarative resolver supports these exact sources:

| Source                              | Resolved value                                        |
| ----------------------------------- | ----------------------------------------------------- |
| `identity.issuer`                   | Workload identity issuer                              |
| `identity.subject`                  | Workload identity subject                             |
| `identity.<claim>`                  | One exact identity claim such as `user_id`            |
| `instance.agent_instance_id`        | Current Host-owned Agent instance ID                  |
| `instance.parent_agent_instance_id` | Optional parent Agent instance ID                     |
| `instance.delegation_id`            | Optional delegation correlation                       |
| `instance.actor`                    | Optional actor string                                 |
| `context.run_id`                    | Current logical Harness Run ID                        |
| `context.thread_id`                 | ID of the current Thread                              |
| `context.metadata.<top-level-key>`  | One exact value from immutable `RunBindings.metadata` |

A selected string is sent unchanged. JSON numbers, booleans, objects, and arrays use finite, sorted-key, compact JSON. For example, `{"region": "us-east", "labels": ["interactive"]}` becomes `{"labels":["interactive"],"region":"us-east"}`. A missing value or `None` fails a required binding and omits an optional binding.

Header names from `headers=` and the resolved factory result must not overlap case-insensitively. `authorization_token`, `allowed_tools`, `description`, and `defer_loading` retain upstream MCP behavior.

## Custom header factories

Use a custom synchronous or asynchronous factory when the declarative resolver's sources are not enough. It receives the complete trusted `AgentContext` for the logical Run and returns an exact string-to-string mapping:

```python
from collections.abc import Mapping

from a13n_harness import AgentContext
from a13n_harness.mcp import ContextualMCP


def resolve_mcp_headers(context: AgentContext) -> Mapping[str, str]:
    return {
        "X-Run-ID": context.run_id,
        "X-Agent-Instance-ID": context.instance.agent_instance_id,
        "X-Tenant-ID": context.instance.identity.require_claim("tenant_id"),
    }


mcp = ContextualMCP(
    "https://mcp.example.com/mcp",
    id="tenant-tools",
    headers_factory=resolve_mcp_headers,
    native=True,
    local=None,
)
```

An async factory has the same input and output contract:

```python
async def resolve_mcp_headers(context: AgentContext) -> Mapping[str, str]:
    route = await route_store.resolve(context.instance.identity)
    return {"X-Route": route}
```

The factory runs once per Run; internal model recovery reuses that header snapshot. A new Run resolves new headers.

## Local and provider-native execution

`ContextualMCP` accepts URL-based upstream execution only:

| Selection     | Arguments                  | Behavior                                                    |
| ------------- | -------------------------- | ----------------------------------------------------------- |
| Local default | `native=False, local=None` | Use upstream URL-based local MCP execution                  |
| Automatic     | `native=True, local=None`  | Prefer provider-native MCP with the upstream local fallback |
| Local only    | `native=False, local=True` | Require local URL-based MCP execution                       |
| Native only   | `native=True, local=False` | Require provider-native MCP execution                       |

Prebuilt clients, transports, in-process servers, scripts, and prebuilt Toolsets already own their connection setup. Use native `MCP` directly for those values rather than combining them with `ContextualMCP`.

`ContextualMCP` requires an HTTP(S) URL configured by the application.

## Host-authored configuration

A Host can expose the same URL-based path through its own trusted configuration model. Preserve the `ContextualMCP` fields and exact execution selection rather than inventing a second MCP runtime. Persist only credential-free desired configuration; resolve headers, short-lived credentials, and current routing through process-local factories when constructing the Capability.

An Agent can select multiple MCP servers when each has a unique `id`. Use code-first `ContextualMCP` when configuration requires callable factories, current identity, static headers, or an out-of-band secret resolver.

## Group tools from large local MCP servers

Pass a local `MCP` or `ContextualMCP` Capability as a [ToolProxyGroup source](tool-proxy.md#group-a-run-bound-mcp-capability) inside `ToolProxyCapability(groups=...)` to expose grouped discovery instead of every tool schema. Select `native=False, local=True`; provider-native tools and deferred-loading sources are not proxy targets. Native composition preserves fresh Run binding and contextual headers, and calls still use the original MCP Toolset and transport. It reduces model context, not MCP initialization or tool-listing work.

## Result boundary

Locally executed MCP tools are ordinary dynamically discovered function tools. Their text and JSON returns cross the mandatory Harness result boundary and default to explicit truncation rather than spill when oversized. This bounds the value integrated into model history; it does not impose a transport-body or process-memory limit before the MCP client receives the result. Provider-native MCP execution remains on the provider path and does not cross the local function-tool boundary.
