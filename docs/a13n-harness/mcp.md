# MCP tools

Use MCP to connect tools supplied by another process or service. Harness composes Pydantic AI's MCP Capability instead of adding another transport client.

| Need                                                                   | Choose                                                    |
| ---------------------------------------------------------------------- | --------------------------------------------------------- |
| A static server, stdio process, in-process server, or prebuilt Toolset | Native `MCP`                                              |
| URL server headers derived from the current Harness identity or Run    | `ContextualMCP`                                           |
| A command/JSON setup for the terminal product                          | [Harness UI MCP configuration](../a13n-harness-ui/mcp.md) |

A Harness SDK `AgentSpec` does **not** accept Harness UI's top-level `mcp_servers` resource field. The SDK uses Capabilities; the application owns resource IDs and configuration files.

## Native MCP

MCP uses Pydantic AI's native `MCP` Capability. Keep it in `AgentSpec.capabilities`; Agent Harness does not define a second MCP client, protocol, server schema, or peer `mcp_servers` field.

A local URL server can be reconstructed directly from an AgentSpec document:

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

The default `a13n-harness` installation includes Pydantic AI's MCP client runtime, so local URL and stdio transports need no separate Harness extra. For richer process-local inputs such as an in-process server, transport, script path, or prebuilt `MCPToolset`, construct `pydantic_ai.capabilities.MCP` in trusted code and pass it through definition Capability composition. A host that owns fresh authenticated clients or toolsets for one execution can instead attach an exact upstream `MCP` instance to `RunBindings.capabilities`; never reuse that authenticated instance across runs. `defer_loading=True` uses upstream `load_capability` under the same Harness tool boundaries. Use `native=True, local=False` when the selected model provider should execute a URL MCP server natively.

## Run-scoped headers with `ContextualMCP`

Use `ContextualMCP` when a URL-based MCP server needs headers derived from the current logical Harness run. The definition stores an inert URL recipe. During Pydantic Capability run binding, it resolves headers and constructs a fresh upstream `MCP` before native tools or a local MCP Toolset are extracted.

For common Identity, lineage, run, and metadata values, use the declarative resolver:

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

`RunBindings.metadata` is the intended place for additional per-run JSON values. Put an exact top-level key there, then select it through `context.metadata.<key>`. Do not attach ad hoc attributes to `AgentContext` or encode a nested reflection path.

The declarative resolver supports these exact source families:

| Source                              | Resolved value                                        |
| ----------------------------------- | ----------------------------------------------------- |
| `identity.issuer`                   | Workload Identity issuer                              |
| `identity.subject`                  | Workload Identity subject                             |
| `identity.<claim>`                  | One exact Identity claim such as `user_id`            |
| `instance.agent_instance_id`        | Current Host-owned Agent instance ID                  |
| `instance.parent_agent_instance_id` | Optional parent Agent instance ID                     |
| `instance.delegation_id`            | Optional delegation correlation                       |
| `instance.actor`                    | Optional actor string                                 |
| `context.run_id`                    | Current logical Harness run ID                        |
| `context.thread_id`                 | Current independently advancing Thread ID             |
| `context.metadata.<top-level-key>`  | One exact value from immutable `RunBindings.metadata` |

A selected string is sent unchanged. JSON numbers, booleans, objects, and arrays use finite, sorted-key, compact JSON. For example, `{"region": "us-east", "labels": ["interactive"]}` becomes `{"labels":["interactive"],"region":"us-east"}`. A missing value or `None` fails a required binding and omits an optional binding.

Header names from `headers=` and the resolved factory result must not overlap case-insensitively. `authorization_token`, `allowed_tools`, `description`, and `defer_loading` retain upstream MCP behavior.

## Custom header factories

Use a custom synchronous or asynchronous factory when the curated selectors are not enough. It receives the complete trusted `AgentContext` for the logical run and returns an exact string-to-string mapping:

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

The factory runs once per logical Harness run. Internal model-recovery attempts reuse the same active upstream MCP and header snapshot; another logical run resolves a fresh snapshot. The factory is trusted Host code, so it may read current run services deliberately, but model content cannot choose selectors or call it directly.

## Local and provider-native execution

`ContextualMCP` accepts URL-based upstream execution only:

| Selection     | Arguments                  | Behavior                                                    |
| ------------- | -------------------------- | ----------------------------------------------------------- |
| Local default | `native=False, local=None` | Use upstream URL-based local MCP execution                  |
| Automatic     | `native=True, local=None`  | Prefer provider-native MCP with the upstream local fallback |
| Local only    | `native=False, local=True` | Require local URL-based MCP execution                       |
| Native only   | `native=True, local=False` | Require provider-native MCP execution                       |

Prebuilt clients, transports, in-process servers, scripts, and prebuilt Toolsets already own their connection setup. Use native `MCP` directly for those values rather than combining them with `ContextualMCP`.

The URL is explicit trusted configuration. Harness requires an HTTP(S) URL for `ContextualMCP` and otherwise leaves URL, transport, authorization, and provider validation to upstream MCP integrations; it does not guess whether URL components contain credentials.

## Host-authored configuration

A Host can expose the same URL-based path through its own trusted configuration model. Preserve the `ContextualMCP` fields and exact execution selection rather than inventing a second MCP runtime. Persist only credential-free desired configuration; resolve headers, short-lived credentials, and current routing through process-local factories when constructing the Capability.

An Agent can select multiple MCP servers when each has a unique `id`. Use code-first `ContextualMCP` when configuration requires callable factories, current identity, static headers, or an out-of-band secret resolver.

## Result boundary

Locally executed MCP tools are ordinary dynamically discovered function tools. Their text and JSON returns cross the mandatory Harness result boundary and default to explicit truncation rather than spill when oversized. This bounds the value integrated into model history; it does not impose a transport-body or process-memory limit before the MCP client receives the result. Provider-native MCP execution remains on the provider path and does not cross the local function-tool boundary.
