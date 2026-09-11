# Grouped ToolProxy

ToolProxy exposes large collections of local tools through two model-facing entries: `search_proxy_tools` and `call_proxy_tool`. The model sees a compact list of domains, discovers exact argument schemas when needed, and invokes the selected tool with an arguments object.

ToolProxy changes discovery and call presentation only. Pydantic AI's current `ToolManager`, the original Toolset, and the normal Harness execution boundary still own validation, policy, credentials, hooks, results, and usage. It is not a second execution engine or an MCP client.

## Group a Toolset

Install one `ToolProxyCapability` with an explicit mapping of domain names to sources. Each `ToolProxyGroup` is a passive configuration value, not a separately installed Capability:

```python
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import ToolProxyCapability, ToolProxyGroup
from pydantic_ai.toolsets import FunctionToolset


def customer_name(customer_id: int) -> str:
    """Look up the display name for a customer ID."""
    return {42: "Ada"}.get(customer_id, "Unknown")


source = FunctionToolset(
    [customer_name],
    instructions="Use numeric customer IDs, not display names.",
)
proxy = ToolProxyCapability(
    groups={
        "crm": ToolProxyGroup(
            source=source,
            description="Customer records and account operations",
        )
    },
)
agent = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=model,  # Supply your configured Pydantic AI Model.
    capabilities=(proxy,),
)
```

The model's discovery call is:

```json
{"query": "customer name", "group": "crm"}
```

The result includes `tools`, `total`, and `next_offset`. Each match contains its `group`, local `tool` name, description, full `parameters_json_schema`, `return_schema`, source `instructions`, and `codeact_eligible` flag. The model then calls `call_proxy_tool` with:

```json
{
  "group": "crm",
  "tool": "customer_name",
  "arguments": {"customer_id": 42}
}
```

The original prepared validator checks and converts `arguments`. The tool remains registered under its grouped canonical name, `crm__customer_name`, but that member schema is not directly advertised to the model. Ungrouped tools stay directly visible. Managed tool IDs do not change.

Use groups for domains such as `crm`, `knowledge`, and `billing`, not one group per tool. A group identifier starts with a letter, contains up to 32 letters, digits, hyphens, or underscores, and cannot contain `__`. Its description is non-blank and at most 512 characters. Different groups can contain the same local tool name. To combine several sources in one group, pass a native `CombinedToolset`, `Capability(toolsets=[...])`, or `CombinedCapability` as that group's source. Member local names must be distinct; duplicate members fail preparation. Prefix sources before grouping when their names would otherwise collide.

## Instructions and dynamic parameters

Only current group summaries are placed in the control descriptions. Source Toolset instructions are returned with discovered tools instead of eagerly filling every model request. Separate Capability-level instructions keep their native behavior.

The prepared controls include a current `group` enum. Search returns the current member schema after source preparation and Harness surface resolution, so unavailable or superseded tools do not remain callable through an old index. The call envelope stays small: it does not contain a union of every member's arguments.

The generated guidance teaches the model to:

1. select a domain and discover the exact schema;
2. use the returned local name and an arguments object matching that schema;
3. reuse a known current schema rather than search before every call;
4. paginate results and refresh discovery when a tool becomes unavailable;
5. inspect uncertain side effects rather than blindly retry a mutation.

An empty query browses a group or all groups. Keyword matching uses group names, tool names, and descriptions; it is deterministic local search, not semantic retrieval. `offset` and `next_offset` apply to the current step's query results, not a durable cursor. Disabling Toolset instructions also disables generated proxy guidance and source instructions in search results.

## Configure names and discovery limits

```python
from a13n_harness.capabilities import ToolProxyCapability, ToolProxyConfig, ToolProxyGroup

proxy = ToolProxyCapability(
    groups={"crm": ToolProxyGroup(source=source, description="Customer records")},
    config=ToolProxyConfig(
        search_name="find_operations",
        call_name="invoke_operation",
        max_results=10,
        max_search_bytes=32_768,
    )
)
```

Generated descriptions and instructions use the configured names. Names must differ and must not collide with other tools. The defaults avoid colliding with native `ToolSearch`'s `search_tools`; native search can coexist for separate ungrouped tools.

`max_results` accepts 1-100. Search's omitted or null `limit` defaults to `min(5, max_results)`. The UTF-8 JSON search budget accepts 1,024-32,768 bytes. A page contains only complete schemas and instructions: entries that do not fit move to the next page. If one entry cannot fit by itself, search fails explicitly. Expose that tool directly or simplify its schema instead of relying on a truncated contract.

When no grouped tools survive preparation, neither proxy control nor generated proxy instructions are exposed. Grouping reduces model context; sources still initialize and prepare their tools normally. It does not eliminate MCP tool-listing traffic or make a thousand Toolsets free to construct.

## Group a run-bound MCP Capability

Pass the Capability instance as the group's `source`, especially when it replaces itself during run binding. Do not extract a `ContextualMCP` Toolset at definition time:

```python
from a13n_harness.capabilities import ToolProxyCapability, ToolProxyGroup
from a13n_harness.mcp import (
    ContextualMCP,
    MCPContextHeaderBinding,
    MCPContextHeaders,
    MCPContextHeadersConfig,
)

knowledge = ToolProxyGroup(
    source=ContextualMCP(
        "https://mcp.example.com/mcp",
        id="knowledge-server",
        native=False,
        local=True,
        headers_factory=MCPContextHeaders(
            MCPContextHeadersConfig(
                headers={
                    "X-Run-ID": MCPContextHeaderBinding("context.run_id"),
                    "X-Tenant-ID": MCPContextHeaderBinding("identity.tenant_id"),
                }
            )
        ),
    ),
    description="Search and read the tenant's knowledge base",
)
capabilities = (ToolProxyCapability(groups={"knowledge": knowledge}),)
```

The Host supplies the `tenant_id` Identity claim. Headers are resolved once per logical Run; model-recovery attempts reuse that Run's upstream MCP, while concurrent Runs get separate replacements. Grouping preserves the original native MCP transport and lifecycle. See [MCP tools](mcp.md) for header and execution-selection details.

Select `native=False, local=True`. Provider-native execution, including automatic native fallback, is not a proxy target. Do not combine a grouped source with `defer_loading=True`; proxy discovery and native deferred loading are distinct interfaces.

## Use with CodeAct

Publish the source's typed CodeAct policy before grouping it:

```python
from a13n_harness.capabilities import CodeActCapability, ToolProxyCapability, ToolProxyGroup
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai.toolsets import FunctionToolset

source = CodeActPolicyToolset(
    wrapped=FunctionToolset([customer_name]),
    policy=CodeActToolPolicy(tools={"customer_name": True}),
    reject_unknown_tools=True,
)
proxy = ToolProxyCapability(
    groups={"crm": ToolProxyGroup(source=source, description="Customer records")},
)
capabilities = (proxy, CodeActCapability())
```

The runner directory contains the proxy controls, not every grouped tool declaration. Restricted Python can discover and call within one `run_code` invocation:

```python
found = await search_proxy_tools(query="customer name", group="crm")
match = found["tools"][0]
name = await call_proxy_tool(
    group=match["group"],
    tool=match["tool"],
    arguments={"customer_id": 42},
)
name
```

A reusable `lookup.codeact.py` program uses the same bridge:

```python
async def main(inputs):
    return await call_proxy_tool(
        group="crm",
        tool="customer_name",
        arguments={"customer_id": inputs["customer_id"]},
    )
```

Call `run_program(path="lookup.codeact.py", inputs={"customer_id": 42})` with the file accessible through the current Environment. Every host call is awaited. Proxy calls are conservatively sequential barriers, including inside `asyncio.gather`.

Grouping does not grant CodeAct eligibility. The bridge resolves the target and checks its own typed policy before dispatch. An MCP source without an explicit owner policy can be used through ordinary model proxy calls but is not automatically CodeAct-callable. See [CodeAct](delegation-and-codeact.md#codeact) for policy and runtime boundaries.

## Host integration

The Host decides presentation when constructing the Agent definition. Harness accepts concrete Toolset or Capability instances; it does not resolve plugin IDs into sources, scan installed plugins for tools, or take over their runtime execution.

A Host that exposes JSON, YAML, an API, or a configuration UI should provide:

| Host input                            | Host responsibility                                                                                                                        |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Source ID and source-specific options | Resolve the ID through trusted source factories; construct each selected source once per executable.                                       |
| Direct or grouped presentation        | Install each source either directly or inside a group, not both. Keep unrelated sources direct.                                            |
| Group name and description            | Collect selected sources into one `ToolProxyCapability(groups=...)` per Agent. Use native composition when several sources share a domain. |
| Optional discovery settings           | Validate and construct `ToolProxyConfig`; use defaults when no customization is needed.                                                    |

For example, a Host-owned document could contain:

```yaml
tool_sources:
  - source_id: customer-tools
    presentation: proxy
    group: crm
    description: Customer records and account operations
  - source_id: host-status
    presentation: direct
```

This is an **illustrative Host schema**, not a built-in Harness or Harness UI resource. Store stable IDs and validated options, not serialized Python objects, import targets, or literal credentials. Source factories and credential resolution remain trusted Host/plugin code. Group descriptions guide discovery; they do not grant authorization or CodeAct eligibility.

Construct a source once, then choose where to put that instance. A third-party `AbstractCapability` works as `ToolProxyGroup(source=capability, description=...)` without a ToolProxy-specific implementation. Native composition preserves its Agent/run binding, hooks, and Capability-level instructions. Supply the Capability itself for run-bound sources such as `ContextualMCP`, rather than calling `get_toolset()` early.

The groups mapping is consumed at construction. To change presentation, build a replacement executable; changing the original mapping does not reconfigure an existing Agent. An empty mapping exposes no proxy controls and leaves direct tools unchanged.

### Plugin-contributed sources

`AbstractHarnessPlugin.get_capabilities()` is a contribution boundary, not a general-purpose source lookup API. ToolProxy explicitly supports this construction source; grouping does not make otherwise disallowed reserved Capabilities valid plugin contributions. Harness calls it on the Agent-bound plugin after `for_agent()`. A plugin that owns its sources can accept a presentation option or expose a source factory that a Host composition layer can use. Aggregate all selected groups at one composition point rather than installing one proxy per plugin.

The following Host-owned plugin illustrates that boundary. Its factory constructs source definitions once per executable; `get_capabilities()` selects direct versus grouped presentation. Source run binding remains native:

```python
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from a13n_harness import AbstractHarnessPlugin, AgentContext
from a13n_harness.capabilities import ToolProxyCapability, ToolProxyGroup
from pydantic_ai.capabilities import AbstractCapability


@dataclass
class HostToolsPlugin(AbstractHarnessPlugin):
    source_factory: Callable[[], Mapping[str, AbstractCapability[AgentContext]]]
    group_descriptions: Mapping[str, str]
    sources: Mapping[str, AbstractCapability[AgentContext]] = field(default_factory=dict)

    @property
    def plugin_id(self) -> str:
        return "host-tools"

    def for_agent(self):
        return replace(self, sources=dict(self.source_factory()))

    def get_capabilities(self) -> Sequence[AbstractCapability[AgentContext]]:
        direct = []
        groups = {}
        for name, source in self.sources.items():
            if name in self.group_descriptions:
                groups[name] = ToolProxyGroup(
                    source=source, description=self.group_descriptions[name]
                )
            else:
                direct.append(source)
        if groups:
            direct.append(ToolProxyCapability(groups=groups))
        return tuple(direct)
```

Here source names also serve as group names; a Host with separate source IDs and domain names can combine sources before constructing each descriptor. Validate configured names against available sources in the Host's configuration layer.

Pass `HostToolsPlugin(source_factory=build_sources, group_descriptions={"crm": "Customer records"})` through `HarnessBuilder.build(plugins=(plugin,))`, where `build_sources` is your trusted factory returning the named Capabilities. Passing an empty `group_descriptions` keeps every source direct. Do not also pass those sources through `capabilities=`.

For an existing third-party middleware plugin, use its supported configuration/composition interface. If it always contributes tools directly and exposes no such interface, those tools remain direct until the plugin or Host integration provides one. Do not call another plugin's `get_capabilities()` manually before native `for_agent()`, instantiate its sources a second time, or remove the plugin when its middleware is still needed. When separating sources from middleware, the plugin must explicitly support suppressing its original contribution while remaining installed. See [Plugin lifecycle](plugins.md#plugin-lifecycle) and [hosting](hosting.md#reconstruct-trusted-definitions).

## Execution, usage, and limitations

- **One execution path:** target validation, Capability hooks, managed authorization, credentials, timeouts, result bounds, and Toolset dispatch remain native. Search is discovery, not authorization.
- **Native accounting:** a successful ordinary proxy invocation counts the envelope and target, for two successful tool calls. CodeAct resolves the envelope without executing an extra proxy layer: a runner plus one target also counts two; one additional search makes three. Usage limits are checked before target dispatch.
- **Retries:** ordinary target validation and `ModelRetry` retain the target's native retry state. ToolProxy does not replay effects or maintain a retry engine. CodeAct retains its bounded runner-failure semantics.
- **Inline approval only:** native handlers can approve or deny within the current invocation. Unresolved approval or external deferral fails the proxy call rather than suspending a nested continuation. Expose tools requiring cross-turn Host interaction directly.
- **Supported targets:** local function tools, including locally executed MCP tools and inline approval-gated tools. External/client tools, provider-native tools, control/output tools, CodeAct runners, and deferred-loading tools are not supported as grouped targets.
- **Uncertain effects:** cancellation and unexpected failures do not roll back work. Check provider state before retrying a mutation.
- **Code-first scope:** these APIs compose through `HarnessBuilder` or `AgentDefinition`; they do not add a built-in `AgentSpec` serialization name or Harness UI configuration resource.
