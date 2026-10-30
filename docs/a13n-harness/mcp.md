---
title: MCP tools
description: Call an MCP tool, choose who owns the client, and add headers from the current identity and Run.
---

Connect an Agent to tools supplied by another process or service through an MCP Capability. Start with the in-process calculator below to learn the connection lifecycle without a remote server or model account. Then choose the connection form your application needs:

| Application need                                                      | Connection form                                      |
| --------------------------------------------------------------------- | ---------------------------------------------------- |
| Fixed URL, stdio process, in-process server, or existing Toolset      | Native `MCP`                                         |
| HTTP(S) server with headers selected from the current identity or Run | `ContextualMCP`                                      |
| Configure a server in the interactive playground                      | [Harness UI MCP resource](../a13n-harness-ui/mcp.md) |

## Run an MCP tool offline

Use the Python 3.13 source environment from [Getting started](getting-started.md#requirements). Its dependencies include FastMCP. Save this complete program as `mcp_demo.py`:

```python
import asyncio
from collections.abc import AsyncIterator

from fastmcp import Client, FastMCP
from pydantic_ai.capabilities import MCP
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

from a13n_harness import AgentSpec, HarnessBuilder, RunBindings

server = FastMCP("calculator")
calls = []


@server.tool
def add(a: int, b: int) -> int:
    calls.append((a, b))
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
            assert result.output_or_raise() == "Result: 8"
            print(result.output_or_raise())

    assert calls == [(3, 5), (3, 5)]


if __name__ == "__main__":
    asyncio.run(main())
```

Run it from the checkout root:

```bash
uv run --no-sync python mcp_demo.py
```

Expected output:

```text
Result: 8
Result: 8
```

The `calls` assertion confirms that the server function actually ran twice. The deterministic model selects the discovered tool and reads its return.

There are three separate lifetimes in the example:

1. The Host enters `Client(server)` once and closes it when the `async with` block exits.
2. Each Run receives a fresh `MCPToolset` projection through `RunBindings`.
3. Each `executable.run()` owns that Run's tool use and cleanup.

These are independent Runs sharing one entered client. To continue a conversation as well, pass the previous result's state as described in [State and Resume](state-and-resume.md). A server connection and a Harness Thread are separate resources.

## Native MCP

For a fixed server, install `MCP` in `AgentSpec.capabilities` or the builder's `capabilities=` argument. The following construction fragment declares a URL server, runs its tools locally, and limits discovery to `search`:

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

Replace the example URL with a reachable MCP server that provides `search`. Build this `agent_spec` with your configured model, then call the executable as in the first example. Configure the server's authentication before running; the URL declaration itself supplies no credentials.

The base installation includes local URL and stdio MCP support. Use Python construction for in-process servers, transport objects, script paths, or an existing `MCPToolset`. For on-demand Capability loading, set `defer_loading=True` and use `load_capability`. For a compatible provider to execute a URL server itself, select `native=True, local=False`; the execution choices are compared below.

## Host-owned clients

Keep one entered FastMCP client when several Runs should reuse its server connection or protocol state. The first program already follows this pattern. For a remote server, replace `Client(server, mode="auto")` with a client configured for your server URL and authentication, while keeping the fresh projection inside the Run loop.

Set up authentication and input handlers before entering the client. Scope the shared client to one identity and header configuration. FastMCP manages multi-round input and request state. `mode="auto"` chooses modern discovery or legacy negotiation; explicit `legacy` and `2026-07-28` modes are also available.

Keep the client open until its Runs finish, then exit its context. A checkpoint stores conversation state, not that connection. If a mutating call loses its response, inspect the server outcome before repeating it.

## Run-scoped headers with `ContextualMCP`

Use `ContextualMCP` when the server needs current user, Thread, or Run values in headers. Harness resolves the selected values once per logical Run, before preparing the MCP connection. A new Run gets a fresh snapshot; internal model-recovery attempts reuse the current snapshot.

The following integration helper accepts your configured model, a reachable MCP URL, and a Host-authenticated identity containing `user_id`. It uses local MCP execution and adds a fixed application header, current identifiers, and sample request metadata. Save it in your application module and call `await query_knowledge(model, url, identity)` from the application's async entrypoint. Configure any additional server-required authentication in the Capability before use.

```python
from a13n_harness import (
    AgentIdentityRef,
    AgentSpec,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.mcp import (
    ContextualMCP,
    MCPContextHeaderBinding,
    MCPContextHeaders,
    MCPContextHeadersConfig,
)

from pydantic_ai.models import Model


async def query_knowledge(model: Model, url: str, identity: AgentIdentityRef) -> str:
    mcp = ContextualMCP(
        url,
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
                        "context.metadata.request_context",
                        required=False,
                    ),
                }
            )
        ),
    )

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model,
        capabilities=(mcp,),
    )

    bindings = RunBindings.embedded(
        identity=identity,
        metadata={
            "request_context": {
                "region": "us-east",
                "labels": ["interactive", "priority"],
            }
        },
    )
    result = await executable.run("Find the account record", bindings=bindings)
    return result.output_or_raise()
```

The server receives `X-Application: support`, the current Run and Thread IDs, the supplied `user_id`, and a compact JSON `X-Request-Context`. Adjust the sample metadata to the values your application deliberately shares. `context.metadata.<key>` selects one exact top-level key; it does not traverse arbitrary context attributes.

Use these selectors for declarative bindings:

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

Use a factory when headers require application logic, such as a tenant lookup or short-lived credential. The factory receives the trusted `AgentContext` and returns a string-to-string mapping, synchronously or asynchronously.

This construction fragment requires the Host identity to contain a string `tenant_id` claim. Replace the server URL, and pass the resulting `mcp` Capability to your builder as in the integration helper:

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
    native=False,
    local=True,
)
```

For an asynchronous route lookup, replace the factory with the following function. `route_store` is your application-owned service, and `resolve()` must return the header string:

```python
async def resolve_mcp_headers(context: AgentContext) -> Mapping[str, str]:
    route = await route_store.resolve(context.instance.identity)
    return {"X-Route": route}
```

The Host owns the route store's setup and cleanup. Both factory forms run once per logical Run, before MCP tool preparation; they are reconstructed by the Host rather than saved in portable state.

## Local and provider-native execution

Choose where MCP calls run before configuring credentials or grouping tools:

| Selection     | Arguments                  | Behavior                                                    |
| ------------- | -------------------------- | ----------------------------------------------------------- |
| Local default | `native=False, local=None` | Use upstream URL-based local MCP execution                  |
| Automatic     | `native=True, local=None`  | Prefer provider-native MCP with the upstream local fallback |
| Local only    | `native=False, local=True` | Require local URL-based MCP execution                       |
| Native only   | `native=True, local=False` | Require provider-native MCP execution                       |

`ContextualMCP` takes an application-configured HTTP(S) URL. For a prebuilt client, transport, in-process server, script, or Toolset, use native `MCP`; those objects already have their own connection setup. Provider-native execution needs a model/provider that supports it. When your Run enforces allowed-host restrictions, use local execution so Harness can enforce them.

## Host-authored configuration

An application can expose server URLs, stable IDs, execution selection, and context-header bindings in its own configuration. Parse those values into the existing MCP APIs. Give each server a unique `id` when selecting several servers for one Agent.

Persist credential-free desired configuration. Reconstruct current identity, route lookups, headers, and short-lived credentials in trusted Host code. Use code-first `ContextualMCP` for callable factories and current-context resolution. For Harness UI's ready-made resource format, follow its [MCP configuration guide](../a13n-harness-ui/mcp.md).

## Group tools from large local MCP servers

If a server exposes many tools, pass its local `MCP` or `ContextualMCP` Capability itself as a [ToolSearchGroup source](tool-search.md#group-a-run-bound-mcp-capability). Grouping the Capability preserves Run binding and header resolution before tool preparation.

Select `native=False, local=True` and leave deferred loading off. The model then discovers schemas through ToolSearch, while calls use the original MCP Toolset and transport. This reduces schemas sent to the model; the client still initializes the source and lists its tools normally.

## Result boundary

Locally executed MCP tools pass text and JSON results through the Harness function-tool result boundary. Oversized values are explicitly truncated by default rather than spilled to a file. This bounds what enters model history, not the transport body or memory used before the client receives the result.

Provider-native MCP follows the provider's execution path and does not cross this local boundary. Choose local execution when your integration depends on Harness's local tool-result handling.
