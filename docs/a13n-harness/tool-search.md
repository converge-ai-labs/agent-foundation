---
title: Grouped ToolSearch
description: Group a tool collection, discover its definitions on demand, and call the selected tool through a compact model interface.
---

When an Agent has many tools, group related tools by domain so the model can find the operation it needs without receiving every schema on every request. ToolSearch provides five fixed control tools for discovery and invocation. Selected members stay behind those controls; unrelated tools remain directly available.

## Use the fixed proxy

The model follows a short path:

1. Use `discover_tools` to search for candidates, or `list_tool_groups` and `list_tools` to browse.
2. Use `describe_tool` to read a candidate's full definition when the current schema is not already known.
3. Use `call_tool` with the returned group, local tool name, and arguments.

Discovery does not promote member schemas into later model requests. The call still reaches the original Toolset through the current ToolManager and Harness execution boundary, preserving validation, policy, hooks, credentials, and result handling.

`ToolSearchCapability` and `ToolSearchPlan` use `ProxyToolSearchConfig()` by default; `ToolSearchConfig()` is an alias. The fixed control schemas and guidance contain no changing inventory, which helps keep the model's prompt prefix stable. Grouped tools also support approval and [client-tool feedback](client-tools.md).

## Group a Toolset

Use the environment from [Getting started](getting-started.md). Save this complete program as `search_demo.py`. The deterministic model performs discovery, reads the selected schema, and calls the local function. No provider credentials are needed.

```python
import asyncio
import json

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import FunctionToolset


def customer_name(customer_id: int) -> str:
    """Look up the display name for a customer ID."""
    return {42: "Ada"}.get(customer_id, "Unknown")


async def respond(messages, info):
    names = {tool.name for tool in info.function_tools}
    assert "customer_name" not in names and "crm__customer_name" not in names
    returns = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    if not returns:
        name, args = "discover_tools", {"query": "customer name", "group": "crm"}
    elif returns[-1].tool_name == "discover_tools":
        assert returns[-1].content["detail"] == "summary"
        match = returns[-1].content["tools"][0]
        name, args = "describe_tool", {"group": match["group"], "tool": match["tool"]}
    elif returns[-1].tool_name == "describe_tool":
        definition = returns[-1].content["tool"]
        assert "customer_id" in definition["parameters_json_schema"]["properties"]
        name, args = "call_tool", {
            "group": "crm", "tool": "customer_name", "arguments": {"customer_id": 42}
        }
    else:
        assert returns[-1].content == "Ada"
        yield "Customer Ada"
        return
    yield {0: DeltaToolCall(name=name, json_args=json.dumps(args))}


async def main():
    source = FunctionToolset(
        [customer_name], instructions="Use numeric customer IDs, not display names."
    )
    proxy = ToolSearchCapability(
        groups={
            "crm": ToolSearchGroup(source=source, description="Customer records")
        }
    )
    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(proxy,),
    )
    result = await agent.run("Find customer 42")
    assert result.output_or_raise() == "Customer Ada"
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Run it from the checkout root:

```bash
uv run --no-sync python search_demo.py
```

Expected output:

```text
Customer Ada
```

`source` owns the Python function and its instructions. `ToolSearchGroup` assigns that source to the `crm` domain. One `ToolSearchCapability` installs the grouped interface. The assertions show that the model never receives `customer_name` as a directly callable schema, yet its proxy call executes the function and returns `Ada`.

The three tool inputs in `respond()` illustrate the protocol:

| Tool             | Input                                          | Result used by the next step                  |
| ---------------- | ---------------------------------------------- | --------------------------------------------- |
| `discover_tools` | Query `customer name`, group `crm`             | Candidate's group and local name              |
| `describe_tool`  | Group `crm`, tool `customer_name`              | Full parameter schema and source instructions |
| `call_tool`      | Same identity, arguments `{"customer_id": 42}` | Original function result, `Ada`               |

Function targets use their original argument validator. External targets keep their external-tool semantics, so the client validates arguments before execution. Internally, the example target is registered as `crm__customer_name`; managed tool IDs remain unchanged.

Choose domains such as `crm`, `knowledge`, and `billing`, with several related tools in each. A group name starts with a letter, contains up to 32 letters, digits, hyphens, or underscores, and cannot contain `__`. Descriptions must be non-blank and at most 512 characters. Different groups may reuse a local tool name. Within a group, combine sources with `CombinedToolset`, `Capability(toolsets=[...])`, or `CombinedCapability`, keeping member names distinct. Rename colliding ordinary sources before grouping; client declarations use their supported grouping path.

Registered names escape each group underscore as `_u` before joining `__` and the unchanged local name. Thus `my_tools/select` becomes `my_utools__select`, and `a_/b` differs from `a/_b`. For client delivery, use [retained routing metadata](client-tools.md#proxy-discovery), not string splitting.

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

In `search_demo.py`, replace the `proxy` construction inside `main()` with the following block and add its import. Keep `source` from the same function. The deterministic model also needs the new control names in its calls and comparisons.

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

When migrating `NativeToolSearchConfig`, use `ProxyToolSearchConfig`, remove `strategy`, and replace `mode: native` with `mode: proxy`. The old default names `search_proxy_tools` and `call_proxy_tool` are not registered as aliases. Update model instructions and saved programs to the new names; programs that require schemas directly from search should request `detail="full"`.

When no grouped tools survive preparation, neither proxy control tool nor generated proxy instructions are exposed. Grouping reduces model context; sources still initialize and prepare their tools normally. It does not eliminate MCP tool-listing traffic or make a thousand Toolsets free to construct.

## Group a Run-bound MCP Capability

Use this construction fragment when your Host connects to an HTTP MCP server and supplies a tenant identity. Replace the example URL with your server, provide its required authentication through Host-owned headers, and pass the resulting `capabilities` tuple to the builder with your model. The offline `respond()` above only understands the CRM tool and must be replaced for this integration.

Group the `ContextualMCP` Capability itself. It resolves the Run's headers before creating the MCP toolset:

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

To let restricted Python orchestrate the same CRM tool, replace the `source` and `proxy` setup in `main()` with this block. Add the imports and pass the resulting `capabilities` tuple to the builder. Use a model that calls `run_code` with the payload below; the original deterministic model exercises ordinary proxy calls.

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

Choose the API that matches how your Host already constructs sources:

- Use `ToolSearchCapability` when you have Toolset or Capability instances ready to group, as in the first example.
- Use `ToolSearchPlan` when sources are already selected in the Agent definition, including plugin contributions. The plan selects those same instances or exact plugin IDs without reconstructing them.

To try the second form, keep `customer_name()`, `respond()`, and the final Run in `search_demo.py`. Add these imports and replace the source/proxy/builder setup in `main()`:

```python
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection
from pydantic_ai.capabilities import Capability

# Inside main(), replace the source/proxy/builder setup:
customer_tools = Capability(id="customer-tools", tools=[customer_name])
agent = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=respond),
    capabilities=(customer_tools,),
    tool_search=ToolSearchPlan(
        groups={
            "crm": ToolSearchSelection(
                "Customer records", capabilities=(customer_tools,)
            )
        }
    ),
)
```

The program still prints `Customer Ada`. Every selected Capability must also be present in the definition; unlisted sources stay direct. Supply each source once, through either a concrete-source proxy or a build plan. `AgentDefinition.tool_search` accepts the same plan.

For Host configuration, store stable source IDs, group names, descriptions, and validated discovery options. Resolve those IDs through trusted source factories, construct sources once, then choose their presentation. A group is a presentation choice; it grants neither authorization nor CodeAct eligibility. Harness UI provides its own [Agent tool proxy groups](../a13n-harness-ui/agents-and-subagents.md#tool-search-groups) configuration.

Grouping preserves native source ordering, explicit IDs, state, hooks, and container instructions. Pass Run-bound Capabilities such as `ContextualMCP` themselves, so their normal binding occurs before tool preparation. The plan and groups are consumed at construction. Build a replacement executable to change presentation; mutating the original mapping does not reconfigure an existing Agent. An empty group mapping leaves direct tools unchanged and exposes no proxy controls.

### Plugin-contributed sources

Select a plugin by the exact ID returned from `plugin_id`. The following replacement demonstrates that path with the same customer function. Add the imports and class at module level, then replace the setup inside `main()` as indicated:

```python
from a13n_harness import AbstractHarnessPlugin
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection
from pydantic_ai.capabilities import Capability


class CustomerPlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self):
        return "customer-plugin"

    def get_capabilities(self):
        return (Capability(id="customer-tools", tools=[customer_name]),)


# Inside main(), replace the source/proxy/builder setup:
agent = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=respond),
    plugins=(CustomerPlugin(),),
    tool_search=ToolSearchPlan(
        groups={
            "crm": ToolSearchSelection(
                "Customer records", plugins=("customer-plugin",)
            )
        }
    ),
)
```

Run the file again; it still prints `Customer Ada`. Harness owns the plugin's normal `for_agent()` and `get_capabilities()` calls, each once. A plugin that also contributes middleware keeps that middleware installed. Multiple instances of one plugin class need distinct IDs so the plan can select each instance.

A plugin may instead own its grouping by returning a `ToolSearchCapability` from `get_capabilities()`. Aggregate that presentation at one composition point. For an existing plugin, the plan avoids changing the plugin or manually calling its contribution methods. See [Plugin lifecycle](plugins.md#plugin-lifecycle) and [Host reconstruction](hosting.md#reconstruct-trusted-definitions).

## Execution, usage, and limitations

Grouping changes how a tool is found, while the target keeps its execution contract:

| Concern               | Behavior                                                                                                                                                                                                                        |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Validation and policy | Calls retain native validation, hooks, managed authorization, credentials, timeouts, and result bounds. Discovery does not authorize an action.                                                                                 |
| Usage                 | A successful ordinary proxy call counts `call_tool` and its target: two tool calls. CodeAct counts the runner and target, also two, with one more for each discovery or description. Limits are checked before target dispatch. |
| Retries               | Target validation and `ModelRetry` retain native retry state. ToolSearch does not replay effects. CodeAct retains its bounded runner-failure behavior.                                                                          |
| Deferred feedback     | An unresolved approval, runtime `CallDeferred`, or client call suspends an ordinary proxy invocation. Retain the complete Harness requests and resume with target-keyed results.                                                |

For deferred work, the Host displays the actual target while history retains the original proxy call. Native inline handlers receive that target once. Follow [Structured suspension](state-and-resume.md#structured-suspension); CodeAct cannot resume its interpreter across Runs. After an uncertain mutation, inspect provider state before retrying. Cancellation does not roll back the action.

Supported grouped targets are local function tools, locally executed MCP, approval-gated tools, and external/client tools. Keep provider-native tools, control/output tools, CodeAct runners, and deferred-loading tools outside groups. Configure grouped MCP with `native=False, local=True` and without `defer_loading=True`.

These APIs compose through `HarnessBuilder` or `AgentDefinition`. They are not built-in `AgentSpec` serialization names, and they do not create a separate proxy-group resource.
