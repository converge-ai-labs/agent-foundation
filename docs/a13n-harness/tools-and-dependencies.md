---
title: Tools and dependencies
description: Register a Python function, call it offline, and supply current-Run context or an application service.
---

A tool lets the model request a Python function call. Start with a typed function, place it in a Capability, and pass that Capability to the builder. Then test the complete call: model request, Python execution, tool result, and final answer.

Use the synchronized checkout from the [quickstart](getting-started.md). Both executable examples below run offline.

## Run a function tool offline

Save this complete program as `tools_example.py`:

```python title="tools_example.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)


def double(value: int) -> int:
    """Return twice the supplied integer."""
    return value * 2


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | DeltaToolCalls]:
    del info
    results = [
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "double"
    ]
    if results:
        yield f"Result: {results[-1]}"
    else:
        yield {
            0: DeltaToolCall(
                name="double", json_args='{"value": 4}', tool_call_id="call-double"
            )
        }


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(Capability(id="math", tools=[double]),),
    )
    result = await executable.run("Double four.")
    assert result.output_or_raise() == "Result: 8"
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Run it and check for `Result: 8`:

```bash
uv run python tools_example.py
```

The `int` annotation defines the model-visible argument type; the docstring explains the function. `Capability(id="math", tools=[double])` registers it at build time. The deterministic responder requests `double(4)`, then reads its `ToolReturnPart` and produces the final text. `output_or_raise()` returns that final answer, not the integer tool return.

For a larger group, import `FunctionToolset` from `pydantic_ai.toolsets` and place it in `Capability(toolsets=[FunctionToolset([...], id="...")])`. Capabilities remain the top-level place to compose tools. Pydantic AI owns the function schema and dispatcher; Harness bounds results and applies its tool lifecycle.

## Access the current Run

Use `RunContext[AgentContext]` as a tool's first parameter when it needs application-supplied context. The runtime injects this parameter; the model supplies only the remaining arguments.

To exercise this in `tools_example.py`, keep its original imports and final `asyncio.run(main())`. Add the two imports below, and replace `double()`, `respond()`, and `main()` with:

```python
from a13n_harness import AgentContext, RunBindings
from pydantic_ai import RunContext


def request_label(ctx: RunContext[AgentContext]) -> str:
    """Return the application's label for this request."""
    label = ctx.deps.metadata.get("request_label", "unlabeled")
    if not isinstance(label, str):
        raise TypeError("request_label must be a string")
    return label


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str | DeltaToolCalls]:
    del info
    for message in reversed(messages):
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and part.tool_name == "request_label":
                yield str(part.content)
                return
    yield {
        0: DeltaToolCall(
            name="request_label", json_args="{}", tool_call_id="call-label"
        )
    }


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(Capability(id="request-context", tools=[request_label]),),
    )
    result = await executable.run(
        "Read the request label.",
        bindings=RunBindings.embedded(metadata={"request_label": "support-triage"}),
    )
    assert result.output_or_raise() == "support-triage"
    print(result.output_or_raise())
```

Run the same file again. It prints `support-triage`, confirming that the tool read the fresh binding. The model sent `{}`: the request label came from your application.

`ctx` exposes native messages, usage, and limits. `ctx.deps` exposes Agent identity, Thread/Run correlation, Environment, plugins, state, and metadata. Use metadata for bounded JSON context such as labels. Keep credentials and live clients in their typed integration or application-owned service; authorize operations using current policy rather than a metadata label.

## Supply a live application dependency

For an application service, first decide who owns the client and how long it remains authorized:

| Application pattern                  | Integration                                                                 |
| ------------------------------------ | --------------------------------------------------------------------------- |
| Build an executable for one request  | Close over the authorized service in a function tool                        |
| Reuse one executable across requests | Resolve a fresh service through a custom Capability or Harness plugin       |
| Use a built-in integration           | Supply its typed `RunBindings` field, such as `web` or `document_converter` |

The following integration sketch assumes your application has acquired `inventory` and supplies a native `model`. The application also closes the service when its scope ends:

```python
from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.capabilities import Capability


async def inspect_inventory(inventory, model):
    async def stock_count(sku: str) -> int:
        """Look up stock for one product in the current authorized inventory."""
        return await inventory.stock_count(sku)

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=model,
        capabilities=(Capability(id="inventory", tools=[stock_count]),),
    )
    return await executable.run("Check stock for SKU-123")
```

The closure selects the service; the model selects a SKU within that service. Enforce tenant and credential scope in the service itself. Keep database transactions around individual operations rather than the entire Agent loop.

For a reusable executable, choose one of these lifecycles:

- **Native Capability:** install an `AbstractCapability[AgentContext]` at build time. Its `for_run(ctx)` resolves an authorized service from current identity and an application-owned factory. Binding runs again for each internal model-recovery attempt.
- **Harness plugin:** bind a fresh plugin once per logical Run. Its tools or contributed Capabilities retrieve it through `ctx.deps.plugins.require(plugin_id, ExpectedPluginType)`. Follow the [plugin lifecycle](plugins.md#plugin-lifecycle) for cleanup.

Keep shared prototypes reentrant; resolve request-specific services without mutating them. The application or extension owns acquisition and cleanup—`for_run()` alone does not close a client.

Harness's dependency type is `AgentContext`. Arbitrary application services do not go in a second `deps_type`, `metadata`, or `HarnessState`. Likewise, ordinary tool Capabilities go to the builder: `RunBindings.capabilities` accepts only the supported upstream MCP and invocation-policy types.

## Tool execution paths

Choose the integration that owns the action:

| Action                                             | Use                                                                          | Execution owner                                             |
| -------------------------------------------------- | ---------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Call trusted application code                      | A Python function, as above                                                  | Your Python process; Harness handles the tool result        |
| Edit files or run commands with Environment policy | [Managed tools](managed-tools.md) and [Environments](environments.md)        | Current policy plus the Environment Provider                |
| Call a connected MCP server                        | [MCP](mcp.md)                                                                | Native MCP transport and local result handling              |
| Use provider search or image tools                 | Provider-native tools                                                        | The model provider                                          |
| Request approval or external execution             | [Human-in-the-loop](human-in-the-loop.md) or [client tools](client-tools.md) | The Host resumes a suspended root Run with correlated input |

Managed tool metadata selects resource and invocation-policy handling. A function name does not select that path, and ordinary Python callbacks retain their process authority. Tool visibility is separate from OS isolation.

## Errors, retries, and output bounds

Check failures at the layer that owns them:

| Failure or limit                         | Configure or inspect                                                       |
| ---------------------------------------- | -------------------------------------------------------------------------- |
| Invalid tool arguments or output         | Native validation and `AgentSpec.retries`                                  |
| Provider transport failure               | Provider client retry policy                                               |
| Denied or approval-required managed call | Invocation policy; optional shell review can add restrictions              |
| Oversized tool result                    | Harness result bounds; local MCP text/JSON defaults to explicit truncation |

A model retry does not establish whether an external mutation took effect. Inspect uncertain outcomes before repeating a side effect. Tool-result bounds apply before model history; they are neither HTTP request-body limits nor durable attachment storage.

Test the visible schema and an actual call, as in the offline examples. Add denial, cancellation, and uncertain-outcome tests for tools that mutate application state.

## Next steps

- [Capabilities](capabilities.md): package reusable tool groups and context.
- [Inputs and outputs](inputs-and-outputs.md): return typed Agent output alongside tool results.
- [Testing](testing.md): turn the offline workflow into regression tests.
