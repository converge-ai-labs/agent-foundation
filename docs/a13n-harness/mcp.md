---
title: MCP tools
description: Connect Model Context Protocol (MCP) servers to Harness agents, reuse host-managed FastMCP clients, and inject per-run identity headers with ContextualMCP.
---

Connect an Agent to external tools through an MCP Capability:

| Need                                                            | Capability / helper                                 |
| --------------------------------------------------------------- | --------------------------------------------------- |
| Fixed URL, stdio command, in-process server, or `MCPToolset`    | `pydantic_ai.capabilities.MCP`                      |
| HTTP(S) server needing per-Run identity or metadata headers     | `a13n_harness.mcp.ContextualMCP`                    |
| Bounded startup retries or optional (`on_error="skip"`) servers | `a13n_harness.mcp.MCPServer`                        |
| Ready-made YAML configuration in the playground                 | [Harness UI MCP servers](../a13n-harness-ui/mcp.md) |

## Reuse a host-owned FastMCP client

Keep one entered `Client` open across multiple Runs while passing a fresh `MCPToolset` projection per Run through `RunBindings`:

```python title="mcp_demo.py"
import asyncio
from fastmcp import Client, FastMCP
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings

server = FastMCP("calculator")


@server.tool
def add(a: int, b: int) -> int:
    return a + b


async def respond(messages, info: AgentInfo):
    if isinstance(messages[-1], ModelRequest):
        for part in messages[-1].parts:
            if isinstance(part, ToolReturnPart):
                yield f"Result: {part.content}"
                return
    yield {0: DeltaToolCall(name=info.function_tools[0].name, json_args='{"a": 3, "b": 5}')}


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond)
    )
    async with Client(server, mode="auto") as client:
        for prompt in ("Add 3 and 5", "Add them again"):
            projection = MCPToolset(client, id="calculator")
            bindings = RunBindings.embedded(capabilities=(MCP(id="calculator", local=projection),))
            result = await executable.run(prompt, bindings=bindings)
            print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Scope any shared client to a single identity and header set. Checkpoints store conversation history only; supply live MCP connections on each Run.

## Declarative `MCP` and execution modes

Declare a fixed URL server in `AgentSpec` (or pass `MCP(...)` to `HarnessBuilder().build(..., capabilities=...)`):

```python
from a13n_harness import AgentSpec

agent_spec = AgentSpec.from_dict({
    "capabilities": [{
        "MCP": {
            "url": "https://mcp.example.com/mcp",
            "id": "knowledge",
            "local": True,
            "native": False,
            "allowed_tools": ["search"],
        }
    }]
})
```

| Mode          | Flags                      | Behavior                                                    |
| ------------- | -------------------------- | ----------------------------------------------------------- |
| Local default | `native=False, local=None` | Harness executes MCP calls locally over HTTP/stdio          |
| Local only    | `native=False, local=True` | Require local execution (needed for `allowed_hosts` checks) |
| Automatic     | `native=True, local=None`  | Prefer provider-native MCP with local fallback              |
| Native only   | `native=True, local=False` | Require the Model provider to execute the remote MCP server |

## Inject per-Run headers with `ContextualMCP`

Use `ContextualMCP` when an HTTP(S) server needs headers derived from the active Run's `AgentContext` or `AgentIdentityRef`:

```python
from a13n_harness.mcp import (
    ContextualMCP,
    MCPContextHeaderBinding,
    MCPContextHeaders,
    MCPContextHeadersConfig,
)

mcp = ContextualMCP(
    "https://mcp.example.com/mcp",
    id="knowledge",
    native=False,
    local=True,
    headers={"X-Application": "support"},
    headers_factory=MCPContextHeaders(
        MCPContextHeadersConfig(
            headers={
                "X-Run-ID": MCPContextHeaderBinding("context.run_id"),
                "X-Thread-ID": MCPContextHeaderBinding("context.thread_id"),
                "X-User-ID": MCPContextHeaderBinding("identity.user_id"),
                "X-Request-Context": MCPContextHeaderBinding(
                    "context.metadata.request_context", required=False
                ),
            }
        )
    ),
)
```

Supported declarative selectors: `identity.issuer`, `identity.subject`, `identity.<claim>`, `instance.agent_instance_id`, `instance.parent_agent_instance_id`, `instance.delegation_id`, `instance.actor`, `context.run_id`, `context.thread_id`, and `context.metadata.<top-level-key>` (non-strings serialize as compact sorted-key JSON).

For custom logic (such as async tenant or short-lived token lookups), pass a sync or async callable `(context: AgentContext) -> Mapping[str, str]` to `headers_factory=`.

To advertise directories from the Harness process, pass `roots=` a list of `file://` URIs or a FastMCP Roots handler. The default (`None`) leaves Roots disabled. This option applies to the local MCP client; provider-native MCP does not receive these paths.

## Startup isolation and large tool catalogs

- **Optional servers & init retries:** Wrap initialization in `MCPServer(id=..., prepare=..., on_error="skip")` with `InitializationRetryClient` (retries transient init errors up to 3 times with 1s/2s backoff; `skip` omits a failed server for that Run while keeping other tools active). Set `defer_loading=True` on `MCPServer` to let the Model load the server's tools on demand via `load_capability`.
- **Tool search:** For servers with many tools, pass the local `MCP` or `ContextualMCP` instance directly as a [ToolSearchGroup source](tool-search.md#group-a-run-bound-mcp-capability) (`native=False, local=True`) so schemas load on demand while preserving per-Run headers.
