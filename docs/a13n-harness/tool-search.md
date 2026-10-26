---
title: Grouped ToolSearch
description: Expose large tool collections through search and call tools so the model sees a compact list of tool groups.
---

ToolSearch places selected local and MCP tools behind a fixed proxy. Ordinary tools remain directly visible by default. Grouping exposes `discover_tools`, `describe_tool`, `call_tool`, `list_tools`, and `list_tool_groups` to discover, browse, and invoke members. Discovery never adds member schemas to subsequent model requests.

Tools execute through the current `ToolManager`, their original Toolset, and the Harness execution boundary, preserving validation, policy, credentials, hooks, results, and usage.

## Use the fixed proxy

`ToolSearchCapability` and `ToolSearchPlan` default to `ProxyToolSearchConfig()`, also available as `ToolSearchConfig()`. Host configuration may omit `config.mode` or set it to `proxy`. Control schemas, descriptions, and generated guidance contain no dynamic inventory, supporting model prompt caching. Configure MCP sources with `native=False, local=True`.

To migrate an older configuration, replace `NativeToolSearchConfig` with `ProxyToolSearchConfig`, remove `strategy`, and change `mode: native` to `mode: proxy`. Discovered tools always use `call_tool`. Grouped tools support approval and external/client continuation through the Harness deferred request protocol.

The proxy supports [CodeAct orchestration](#use-with-codeact), including discovery and invocation in one runner call; each target still requires its typed CodeAct policy.

## Group a Toolset

Install one `ToolSearchCapability` with an explicit mapping of domain names to sources. Each `ToolSearchGroup` is a passive configuration value, not a separately installed Capability:

```python
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
from pydantic_ai.toolsets import FunctionToolset


def customer_name(customer_id: int) -> str:
    """Look up the display name for a customer ID."""
    return {42: "Ada"}.get(customer_id, "Unknown")


source = FunctionToolset(
    [customer_name],
    instructions="Use numeric customer IDs, not display names.",
)
proxy = ToolSearchCapability(
    groups={
        "crm": ToolSearchGroup(
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

The result includes the actual `detail` (`summary` or `full`), `tools`, `total`, and `next_offset`. This keyword query returns summaries with `group`, local `tool` name, a short description, and `codeact_eligible`. The model obtains the selected definition with `describe_tool`:

```json
{"group": "crm", "tool": "customer_name"}
```

Description returns `{"detail": "full", "tool": ...}` with the complete description, `parameters_json_schema`, `return_schema`, source `instructions`, and `codeact_eligible`. After reading that contract, the model calls `call_tool` with:

```json
{
  "group": "crm",
  "tool": "customer_name",
  "arguments": {"customer_id": 42}
}
```

Function targets retain their original prepared argument validator. External targets retain their native external-tool semantics; the client validates arguments before execution. The tool remains registered under its grouped canonical name, `crm__customer_name`, but that member schema is not directly advertised to the model. Ungrouped tools stay directly visible. Managed tool IDs do not change.

In proxy mode, each `_` in the group is escaped as `_u` before adding `__` and the unchanged local tool name. For example, group `my_tools` and tool `select` register as `my_utools__select`. Group `a_` with tool `b` and group `a` with tool `_b` therefore remain distinct. Client tools follow this same rule; their original execution names come from retained metadata, never by splitting the generated name.

Use groups for domains such as `crm`, `knowledge`, and `billing`, not one group per tool. A group identifier starts with a letter, contains up to 32 letters, digits, hyphens, or underscores, and cannot contain `__`. Its description is non-blank and at most 512 characters. Different groups can contain the same local tool name. To combine several sources in one group, pass a native `CombinedToolset`, `Capability(toolsets=[...])`, or `CombinedCapability` as that group's source. Member local names must be distinct; duplicate members fail preparation. Prefix sources before grouping when their names would otherwise collide.

## Instructions and dynamic parameters

Source Toolset instructions are returned with complete definitions. Summary listings do not read those instructions or copy schemas. Separate Capability-level instructions retain their normal behavior.

Control tools contain no dynamic `group` enum. Full discovery, listing, and description read the current prepared member schema; `call_tool` keeps a fixed arguments-object shape.

The generated instructions teach the model to:

1. select a domain and discover candidates; read `detail` and describe a selected summary unless its complete current definition is already known;
2. use the returned local name and an arguments object matching the complete schema;
3. reuse a known current schema rather than search before every call;
4. paginate results and refresh discovery when a tool becomes unavailable;
5. inspect uncertain side effects rather than blindly retry a mutation.

An empty or whitespace-only query browses a group or all groups. An explicit `group` filters candidates first. Exact local or registered canonical names rank first, ignoring case and surrounding whitespace but preserving separators. Canonical names include group underscore escaping: tool `b` in group `a_` matches `a_u__b`, while the same local name in group `a_u` matches `a_uu__b`. Other keyword matches remain available: searching `payment` still finds `payment_lookup` and `refund_payment` when `payment` exists. A canonical-name query does not implicitly filter by group; use the returned group and local name to describe the intended tool.

Search splits query keywords at whitespace, punctuation, underscores, hyphens, and camelCase boundaries, then matches any keyword as a case-insensitive substring. Searchable fields are local names, full tool descriptions, group identifiers, input property names, and input schema descriptions. Nested parameters and local schema definitions are scanned without expanding references. Defaults, examples, enum values, return schemas, source instructions, and group descriptions do not contribute matches.

Results rank by exact name, then number of distinct keywords covered, then relevance, then canonical name. Each keyword contributes only its strongest match: whole words before substrings, with local name, tool description, group identifier, parameter name, and parameter description as the field priority within each tier. Repeated keywords or repeated text add no weight. Summaries default to 10 results per page, within the configured count and byte caps. Use short keywords rather than a sentence. A punctuation-only nonempty query must be an exact tool name. CamelCase affects keyword splitting, so differently cased queries can split differently even though exact-name and keyword comparisons ignore case. This is deterministic local search, not semantic retrieval or Chinese word segmentation.

Choose discovery detail per call:

| `detail`         | Behavior                                                                                                                         |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `auto` (default) | Return summaries; expand only a sole exact candidate at offset 0 whose complete response fits 8 KiB and the configured byte cap. |
| `summary`        | Always return candidates; descriptions collapse whitespace and are limited to 256 characters including any ellipsis.             |
| `full`           | Return complete definitions; never silently downgrade to summaries.                                                              |

Uniqueness is checked against the entire candidate set, before pagination. A single fuzzy match, `limit=1`, or a byte-limited page does not make auto expand. The response always identifies its actual representation, and a page never mixes summaries and full definitions. To obtain a known tool directly, call `describe_tool(group=..., tool=...)` without searching first. Discovery and description invoke no business operation.

Follow `next_offset` using the same query, group, and detail; byte limits can shorten a page. Offsets apply to the current directory, not a snapshot or durable cursor. A changing directory can shift pages. Disabling Toolset instructions also disables generated guidance and source instructions in complete definitions.

## Retrieve the complete list

Call `list_tool_groups` for available groups and member counts, then `list_tools` to enumerate tools. Both return `total` and `next_offset`; keep the same options and follow the next page until `next_offset` is null.

`list_tools` accepts an optional `group` filter and `detail` of `summary` (default) or `full`. Full listing returns complete descriptions, input/return schemas, and source instructions without relevance filtering. Count and byte limits bound each page; a full definition that cannot fit alone fails explicitly instead of being truncated. Pagination uses the current directory, so directory changes can shift page boundaries.

## Configure names and discovery limits

```python
from a13n_harness.capabilities import ToolSearchCapability, ProxyToolSearchConfig, ToolSearchGroup

proxy = ToolSearchCapability(
    groups={"crm": ToolSearchGroup(source=source, description="Customer records")},
    config=ProxyToolSearchConfig(
        search_name="find_operations",
        describe_name="describe_operation",
        call_name="invoke_operation",
        list_name="list_operations",
        groups_name="list_domains",
        max_results=10,
        max_search_bytes=32_768,
    )
)
```

Generated descriptions and instructions use the configured names. Names must differ and must not collide with other tools. The defaults avoid colliding with native `ToolSearch`'s `search_tools`; native search can coexist for separate ungrouped tools.

`max_results` accepts 1-100. An omitted or null `limit` defaults to `min(10, max_results)` for summaries and `min(5, max_results)` for full definitions. The UTF-8 JSON response budget accepts 1,024-32,768 bytes and bounds the entire discovery or description response. Entries that do not fit move to the next page; schemas and instructions are never truncated. If one entry cannot fit by itself, full discovery or description fails explicitly. That tool can still appear as a summary. Expose it directly or simplify its definition to read the complete contract.

The old default names `search_proxy_tools` and `call_proxy_tool` are not registered as aliases. Update model instructions and saved programs to the new names; programs that require schemas directly from search should request `detail="full"`.

When no grouped tools survive preparation, neither proxy control tool nor generated proxy instructions are exposed. Grouping reduces model context; sources still initialize and prepare their tools normally. It does not eliminate MCP tool-listing traffic or make a thousand Toolsets free to construct.

## Group a Run-bound MCP Capability

Pass the Capability instance as the group's `source`, especially when it replaces itself during Run binding. Do not extract a `ContextualMCP` Toolset at definition time:

```python
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
from a13n_harness.mcp import (
    ContextualMCP,
    MCPContextHeaderBinding,
    MCPContextHeaders,
    MCPContextHeadersConfig,
)

knowledge = ToolSearchGroup(
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
capabilities = (ToolSearchCapability(groups={"knowledge": knowledge}),)
```

The Host supplies the `tenant_id` Identity claim. Headers are resolved once per logical Run; model-recovery attempts reuse that Run's upstream MCP, while concurrent Runs get separate replacements. Grouping preserves the original native MCP transport and lifecycle. See [MCP tools](mcp.md) for header and execution-selection details.

Select `native=False, local=True`. Provider-native execution, including automatic native fallback, is not a proxy target. Do not combine a grouped source with `defer_loading=True`; proxy discovery and native deferred loading are distinct interfaces.

## Use with CodeAct

In proxy mode, publish the source's typed CodeAct policy before grouping it:

```python
from a13n_harness.capabilities import CodeActCapability, ToolSearchCapability, ToolSearchGroup
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai.toolsets import FunctionToolset

source = CodeActPolicyToolset(
    wrapped=FunctionToolset([customer_name]),
    policy=CodeActToolPolicy(tools={"customer_name": True}),
    reject_unknown_tools=True,
)
proxy = ToolSearchCapability(
    groups={"crm": ToolSearchGroup(source=source, description="Customer records")},
)
capabilities = (proxy, CodeActCapability())
```

The runner directory contains the proxy control tools, not every grouped tool declaration. Restricted Python can discover and call within one `run_code` invocation:

```python
found = await discover_tools(query="customer name", group="crm", detail="full")
match = found["tools"][0]
name = await call_tool(
    group=match["group"],
    tool=match["tool"],
    arguments={"customer_id": 42},
)
name
```

A reusable `lookup.codeact.py` program uses the same bridge:

```python
async def main(inputs):
    return await call_tool(
        group="crm",
        tool="customer_name",
        arguments={"customer_id": inputs["customer_id"]},
    )
```

The model calls `run_program(path="lookup.codeact.py", inputs={"customer_id": 42})`; the file must be accessible through the current Environment. Every host-function call from restricted Python is awaited. Proxy calls are conservatively sequential barriers, including inside `asyncio.gather`.

Grouping does not grant CodeAct eligibility. The bridge resolves the target and checks its own typed policy before dispatch. An MCP source without an explicit owner policy can be used through ordinary model proxy calls but is not automatically CodeAct-callable. See [CodeAct](delegation-and-codeact.md#codeact) for policy and runtime boundaries.

## Host integration

The Host decides presentation when constructing the Agent definition. For already constructed sources, use `ToolSearchCapability`. To group ordinary Harness plugin contributions alongside selected Capabilities, pass a typed `ToolSearchPlan` to `AgentDefinition.tool_search` or `HarnessBuilder.build(tool_search=...)`. Harness retains plugin ownership during its normal once-only binding; no plugin scan or special source factory API is needed.

```python
from a13n_harness import AgentDefinition, AgentSpec
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection
from pydantic_ai.capabilities import MCP

source = MCP("https://mcp.example.com/mcp", native=False, local=True)
definition = AgentDefinition(
    agent=AgentSpec(model="openai:gpt-5"),
    output_type=str,
    capabilities=(source,),
    tool_search=ToolSearchPlan(
        groups={
            "knowledge": ToolSearchSelection(
                description="Find project documentation",
                capabilities=(source,),
            )
        }
    ),
)
```

To include plugin contributions, add `plugins=("plugin-docs",)` to the selection and supply that exact plugin instance through the definition's `plugins`. Multiple instances of the same plugin class are selected by their distinct IDs. Every selected Capability must already be in the definition, and every selected plugin must be available to its builder. Unlisted sources stay direct. Do not install the same source both through a concrete-source proxy and through a build plan.

Grouping retains native sibling ordering, explicit IDs, state, hooks, and custom container instructions; a group does not turn its independent sources into one atomic ordering node. The plan is immutable and consumed during construction.

A Host that exposes JSON, YAML, an API, or a configuration UI should provide:

| Host input                            | Host responsibility                                                                                                                         |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Source ID and source-specific options | Resolve the ID through trusted source factories; construct each selected source once per executable.                                        |
| Direct or grouped presentation        | Install each source either directly or inside a group, not both. Keep unrelated sources direct.                                             |
| Group name and description            | Collect selected sources into one `ToolSearchCapability(groups=...)` per Agent. Use native composition when several sources share a domain. |
| Optional discovery settings           | Validate and construct `ProxyToolSearchConfig`; use defaults when no customization is needed.                                               |

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

Construct a source once, then choose where to put that instance. A third-party `AbstractCapability` works as `ToolSearchGroup(source=capability, description=...)` without a ToolSearch-specific implementation. Native composition preserves its Agent/Run binding, hooks, and Capability-level instructions. Supply the Capability itself for Run-bound sources such as `ContextualMCP`, rather than calling `get_toolset()` early.

The groups mapping is consumed at construction. To change presentation, build a replacement executable; changing the original mapping does not reconfigure an existing Agent. An empty mapping exposes no proxy control tools and leaves direct tools unchanged.

### Plugin-contributed sources

`AbstractHarnessPlugin.get_capabilities()` is a contribution boundary, not a general-purpose source lookup API. Prefer `ToolSearchPlan` to group existing plugins without changing them. Harness calls each plugin's `for_agent()` and `get_capabilities()` exactly once and validates the original source before grouping. Middleware remains installed, and grouping never authorizes reserved Capabilities.

A plugin can alternatively own its presentation using `ToolSearchCapability`, as below. Aggregate groups at one composition point rather than installing competing proxy surfaces.

The following Host-owned plugin illustrates that boundary. Its `source_factory` callable constructs the source Capabilities once per executable in `for_agent()`; `get_capabilities()` selects direct versus grouped presentation. Source Run binding remains native:

```python
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from a13n_harness import AbstractHarnessPlugin, AgentContext
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
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
                groups[name] = ToolSearchGroup(
                    source=source, description=self.group_descriptions[name]
                )
            else:
                direct.append(source)
        if groups:
            direct.append(ToolSearchCapability(groups=groups))
        return tuple(direct)
```

Here source names also serve as group names; a Host with separate source IDs and domain names can combine sources before constructing each descriptor. Validate configured names against available sources in the Host's configuration layer.

Pass `HostToolsPlugin(source_factory=build_sources, group_descriptions={"crm": "Customer records"})` through `HarnessBuilder.build(plugins=(plugin,))`, where `build_sources` is your trusted factory returning the named Capabilities. Passing an empty `group_descriptions` keeps every source direct. Do not also pass those sources through `capabilities=`.

For an existing third-party middleware plugin, a `ToolSearchPlan` selects its ordinary contributions by exact plugin ID. Do not call its `get_capabilities()` manually, instantiate its sources a second time, or remove the plugin when its middleware is still needed. See [Plugin lifecycle](plugins.md#plugin-lifecycle) and [hosting](hosting.md#reconstruct-trusted-definitions). Harness UI exposes this build plan through [Agent tool proxy groups](../a13n-harness-ui/agents-and-subagents.md#tool-search-groups).

## Execution, usage, and limitations

- **One execution path:** target validation, Capability hooks, managed authorization, credentials, timeouts, result bounds, and Toolset dispatch remain native. Search is discovery, not authorization.
- **Native accounting:** a successful ordinary proxy invocation counts the `call_tool` call and the target, for two successful tool calls. CodeAct resolves the `call_tool` call without executing an extra proxy layer: a runner plus one target also counts two; each additional discovery or description adds one. Usage limits are checked before target dispatch.
- **Retries:** ordinary target validation and `ModelRetry` retain the target's native retry state. ToolSearch does not replay effects or maintain a retry engine. CodeAct retains its bounded runner-failure semantics.
- **Approval and external results:** inline native handlers receive the actual target once. Unresolved approval, a function raising `CallDeferred`, or a client tool suspends an ordinary proxy call; resume it with the complete Harness requests and target-keyed results. The Host displays the target while history retains the original proxy call. See [Structured suspension](state-and-resume.md#structured-suspension). CodeAct still cannot resume an interpreter across Runs.
- **Supported targets:** local function tools (including locally executed MCP and approval-gated tools) and external/client tools use the same grouped naming rule. Provider-native tools, control/output tools, CodeAct runners, and deferred-loading tools are not supported as grouped targets.
- **Uncertain effects:** cancellation and unexpected failures do not roll back work. Check provider state before retrying a mutation.
- **Composition scope:** these APIs compose through `HarnessBuilder` or `AgentDefinition`, not a built-in `AgentSpec` serialization name. Harness UI owns its separate per-Agent YAML selection schema; no standalone proxy-group resource is created.
