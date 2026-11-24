---
title: Tools and dependencies
description: Register typed Python function tools in Capabilities, read RunContext[AgentContext], and bind per-Run application services safely.
---

Expose typed Python functions or async coroutines to the model by registering them in a `Capability` and passing it to `HarnessBuilder().build(...)`. Pydantic AI generates the JSON Schema and validates arguments; Harness enforces output bounds and execution lifecycles.

## Run a function tool offline

Save this self-contained example as `tools_example.py` and run `uv run python tools_example.py`:

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
    print(result.output_or_raise())  # Result: 8


if __name__ == "__main__":
    asyncio.run(main())
```

Type annotations define the model-visible parameters and the docstring explains when to call the tool. For larger tool groups, wrap functions in `FunctionToolset([...], id="...")` from `pydantic_ai.toolsets` inside `Capability(toolsets=[...])`.

## Access the current Run

Declare `ctx: RunContext[AgentContext]` as the first parameter when a tool needs Run identity, Environment, plugins, state, or bounded JSON metadata. Harness injects `ctx` automatically without exposing it in the model schema:

```python
from a13n_harness import AgentContext, RunBindings
from pydantic_ai import RunContext


def request_label(ctx: RunContext[AgentContext]) -> str:
    """Return the application's label for this request."""
    label = ctx.deps.metadata.get("request_label", "unlabeled")
    if not isinstance(label, str):
        raise TypeError("request_label must be a string")
    return label


# Pass per-Run JSON metadata via RunBindings:
# bindings = RunBindings.embedded(metadata={"request_label": "support-triage"})
```

`ctx` exposes messages, usage, and limits; `ctx.deps` (`AgentContext`) exposes `run_id`, `thread_id`, `instance`, `environment`, `plugins`, `state`, and `metadata`. Keep credentials and live clients out of `metadata` and `HarnessState`.

## Supply a live application dependency

Harness fixes the Pydantic AI dependency type to `AgentContext`—do not pass a custom `deps_type`. Choose the service binding pattern that matches your executable's lifetime:

| Application pattern                  | How to supply the dependency                                                                                                                                     |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Build an executable per request      | Close over the authorized service in your tool function                                                                                                          |
| Reuse one executable across requests | Resolve a fresh service via `AbstractCapability[AgentContext].for_run(ctx)` or a [Harness plugin](plugins.md#plugin-lifecycle) (`ctx.deps.plugins.require(...)`) |
| Use a built-in integration           | Pass its typed `RunBindings` field (such as `environment`, `web`, or `document_converter`)                                                                       |

For a single-request executable, close over the pre-authorized service and scope database transactions to individual tool queries:

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

## Tool execution paths, retries, and output bounds

| Action                                           | Integration                                                                  |
| ------------------------------------------------ | ---------------------------------------------------------------------------- |
| Run trusted Python application code              | Python function in `Capability(tools=[...])`                                 |
| Edit files or run commands under sandbox policy  | [Managed tools](managed-tools.md) and [Environments](environments.md)        |
| Call external tool servers                       | [MCP](mcp.md)                                                                |
| Suspend for human approval or frontend execution | [Human-in-the-loop](human-in-the-loop.md) or [Client tools](client-tools.md) |
| Orchestrate multiple tools in Python             | [Delegation and CodeAct](delegation-and-codeact.md)                          |

Argument validation failures feed structured retry messages back to the model up to `AgentSpec.retries` (inspect external state before repeating non-idempotent mutations). Oversized tool outputs are bounded automatically before entering model history.
