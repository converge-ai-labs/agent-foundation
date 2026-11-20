---
title: Tools and dependencies
description: Attach typed Python function tools to your agent, inject application dependencies via RunContext, and configure retry limits.
---

Agents interact with external systems through **tools**. In Harness, any standard Python function or asynchronous coroutine with type annotations can be exposed to the model as a tool. Harness automatically generates the underlying JSON schema using Pydantic, validates model arguments before invocation, and safely packages return values.

## Run a function tool offline

You can register and test tools offline using a deterministic `FunctionModel` without making external API calls:

```python title="tools_demo.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import Tool


# 1. Define a typed Python tool function
def double(value: int) -> int:
    """Return twice the input integer."""
    return value * 2


# 2. Mock model that deterministically requests double(4) then returns the answer
async def mock_tool_model(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> AsyncIterator[str | ToolCallPart]:
    del info
    has_tool_call = any(
        isinstance(part, ToolCallPart)
        for msg in messages
        if isinstance(msg, ModelResponse)
        for part in msg.parts
    )
    if not has_tool_call:
        yield ToolCallPart(tool_name="double", args={"value": 4})
    else:
        # Extract the tool return value from previous messages
        return_val = next(
            part.content
            for msg in messages
            if hasattr(msg, "parts")
            for part in msg.parts
            if isinstance(part, ToolReturnPart)
        )
        yield f"The doubled result is {return_val}."


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(instructions="Double numbers when requested."),
        output_type=str,
        model=FunctionModel(stream_function=mock_tool_model),
        tools=[Tool(double)],
    )

    result = await executable.run("Compute double of 4")
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Execute with `uv run python tools_demo.py`:

```text
The doubled result is 8.
```

Harness handles the intermediate model exchange: the model requests the tool, Harness executes `double(4)`, feeds `8` back into the conversation messages, and awaits the final answer.

## Access the current Run

When a tool needs access to the active execution context—such as the `run_id`, `thread_id`, or model request metadata—declare a parameter typed as `RunContext`:

```python
from pydantic_ai import RunContext


def audit_logger(ctx: RunContext, event_name: str) -> str:
    """Log an audit event associated with the active run."""
    run_id = getattr(ctx, "run_id", "local_run")
    return f"Logged '{event_name}' under run {run_id}"
```

Harness automatically injects the current context into `ctx` during invocation; the parameter is excluded from the model-visible JSON schema.

## Supply a live application dependency

Real-world tools often require live infrastructure handles such as database session pools, HTTP clients, or tenant identity objects:

```python
from dataclasses import dataclass
from pydantic_ai import RunContext


@dataclass
class DatabaseConnection:
    connection_string: str

    async def query_user(self, user_id: str) -> dict:
        return {"user_id": user_id, "name": "Alice", "role": "admin"}


# Tool function requesting the injected dependency
async def lookup_user(ctx: RunContext[DatabaseConnection], user_id: str) -> dict:
    """Fetch user profile details from the database."""
    db = ctx.deps
    return await db.query_user(user_id=user_id)
```

Pass the dependency instance at runtime via `RunBindings`:

```python
from a13n_harness import RunBindings

db_pool = DatabaseConnection(connection_string="postgresql://localhost:5432/app")

result = await executable.run(
    "Lookup profile for user_123",
    bindings=RunBindings.embedded(deps=db_pool),
)
```

This pattern keeps your agent definitions static and reusable while allowing different requests to bind distinct database sessions or tenant credentials.

## Tool execution paths

Harness supports three execution modes for tools:

| Tool category | Execution mechanism | Typical use case |
| :--- | :--- | :--- |
| **Direct tools** | Executed in-process via async Python functions | Application databases, arithmetic, local calculations |
| **Environment tools** | Dispatched to a sandboxed filesystem or container | Code editing, bash commands, build tools |
| **Remote MCP tools** | Invoked over HTTP/SSE or stdio MCP transports | SaaS integrations, GitHub, Slack, team knowledge bases |

## Errors, retries, and output bounds

Protect your application and model context with defensive tool guardrails:

- **Validation errors:** If the model supplies invalid argument types, Harness intercepts the error and returns a structured validation explanation back to the model, allowing it to self-correct on the next iteration.
- **Output truncation:** Tools returning massive datasets (e.g., querying 10,000 rows) can quickly exhaust context windows. Use `max_content_bytes` policies to automatically truncate oversized responses and save complete outputs to temporary thread storage.
- **Retry budgets:** Configure maximum tool invocation attempts per turn to prevent infinite retry loops when third-party services experience downtime.

## Next steps

1. [Managed tools and permissions](managed-tools.md): Require explicit human approval before executing sensitive tools.
2. [Model Context Protocol (MCP)](mcp.md): Connect external tool servers via standard protocols.
3. [Delegation and CodeAct](delegation-and-codeact.md): Let agents orchestrate multiple tools concurrently in Python.
