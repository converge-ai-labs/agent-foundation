# Tools and dependencies

Tools let a model request application actions. Use ordinary typed Python functions, group them with Pydantic AI Capabilities or Toolsets, and give each Run only the dependencies it should use.

Harness does not replace Pydantic AI's tool schema or dispatcher. It adds a mandatory result boundary and, for explicitly managed tools, invocation policy and resource handling.

## Run a function tool offline

This complete example calls `double(4)` through the real Agent loop without network access. Save it as `tools_example.py` in the [source quickstart](getting-started.md) workspace and run `uv run python tools_example.py`.

```python
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

1. The type annotation defines the argument schema; the docstring explains the action to the model.
2. `Capability(tools=[double])` includes the function in native composition.
3. The deterministic Model emits a tool call, receives the result, and returns text.
4. `output_or_raise()` returns the final validated answer, not the individual tool return.

For larger groups, use `Capability(toolsets=[FunctionToolset([...], id="...")])` with `FunctionToolset` from `pydantic_ai.toolsets`. Tools remain inside their owning Capability; there is no parallel Harness tool registration API.

## Access the current Run

A context-aware function takes `RunContext[AgentContext]` as its first parameter. Pydantic AI injects that argument; the model cannot supply it.

```python
from a13n_harness import AgentContext, RunBindings
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability


def request_label(ctx: RunContext[AgentContext]) -> str:
    """Return the application's label for this request."""
    label = ctx.deps.metadata.get("request_label", "unlabeled")
    if not isinstance(label, str):
        raise TypeError("request_label must be a string")
    return label


capability = Capability(id="request-context", tools=[request_label])
bindings = RunBindings.embedded(metadata={"request_label": "support-triage"})
```

Pass `capability` at build time and the fresh `bindings` to `run()` or `stream()`. `ctx` contains native messages, usage, and limits; `ctx.deps` contains Harness identity, Thread/Run correlation, Environment, plugins, state, and metadata.

Metadata is bounded application context, **not authorization**. Do not put keys, tokens, or a live database session in it. A trusted identity claim also needs current policy before it grants an operation.

## Supply a live application dependency

Harness fixes the native dependency type to `AgentContext`; it does not accept a second application `deps_type`. For a per-request service, construct a fresh run Capability that closes over the authorized service. This sketch assumes your application owns `inventory` and its lifetime:

```python
from a13n_harness import RunBindings
from pydantic_ai.capabilities import Capability


async def inspect_inventory(inventory, executable):
    async def stock_count(sku: str) -> int:
        """Look up stock for one product in the current authorized inventory."""
        return await inventory.stock_count(sku)

    bindings = RunBindings.embedded(
        capabilities=(Capability(id="inventory", tools=[stock_count]),),
    )
    return await executable.run("Check stock for SKU-123", bindings=bindings)
```

The service must enforce its own scope; do not let the model choose a tenant or credential. Keep database transactions around individual operations, not around the whole model/tool loop. For reusable feature implementations, a typed run-bound Capability can own the same dependency instead of a closure.

Build-time Capabilities describe reusable behavior. Run Capabilities supply fresh collaborators or per-run behavior. Never serialize a client or reuse an authenticated run-bound instance as continuation state.

## Native, managed, and provider-native tools

| Tool path                | Example                                     | Boundary                                                                         |
| ------------------------ | ------------------------------------------- | -------------------------------------------------------------------------------- |
| Trusted Python function  | `double`, your application service          | Native tool dispatch plus Harness result handling; Python code owns side effects |
| Managed Environment tool | File edit or shell execution                | Typed resources, current policy, Provider enforcement, and bounded results       |
| Local MCP tool           | Tool from a connected MCP server            | Native MCP transport and local function-tool result boundary                     |
| Provider-native tool     | Model-provider search or image tool         | Executed by the provider; not a local Python tool call                           |
| Deferred tool            | Human approval or external/client execution | Root suspends; Host supplies correlated input in a new Run                       |

A name such as `safe_shell` does not make a tool managed. Managed metadata selects the additional policy path. A Python callback can still access its process's ambient authority; tool visibility is not OS isolation.

[Environment tools](environments.md), [MCP tools](mcp.md), and [deferred resume](state-and-resume.md) explain the corresponding paths.

## Errors, retries, and output bounds

- Native tool argument validation and model-visible retry behavior belong to Pydantic AI. Configure tool/output retries through `AgentSpec.retries`.
- Transport retries belong to the provider client. Do not treat a model retry as permission to repeat an uncertain side effect.
- Managed invocation policy can deny or require approval before dispatch. Optional shell review can only add restrictions.
- Tool results are bounded before entering model history. Oversized local MCP text/JSON defaults to explicit truncation; this is not an incoming HTTP-body limit or a durable attachment store.

Test both the visible schema and the actual dependency call. For mutation tools, also test denial, cancellation, and uncertain outcomes at the application boundary.

## Next steps

- [Capabilities](capabilities.md): compose feature behavior and custom declarative types.
- [Inputs and outputs](inputs-and-outputs.md): distinguish tool results from the Agent's final output.
- [Testing](testing.md): verify tools and continuation without live providers.
