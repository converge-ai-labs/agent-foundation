---
title: Delegation and CodeAct
description: Orchestrate multi-agent workflows with subagents or execute concurrent Python control flow over external tools using CodeAct.
---

Complex agentic workflows often require two advanced orchestration patterns: **Delegation** (dividing work among specialized agents with independent prompts and models) and **CodeAct** (letting an agent write and execute Python code to invoke tools with loops, branching, and concurrency in a single round-trip).

## Choosing Between Them

Select the orchestration strategy suited to your architectural requirements:

| Operational requirement                               | Recommended approach                                | Primary advantage                                                                    |
| :---------------------------------------------------- | :-------------------------------------------------- | :----------------------------------------------------------------------------------- |
| Single, direct tool execution                         | Direct tool call                                    | Zero overhead; standard model invocation                                             |
| Bounded review, specialist tasks, or role separation  | [Inline delegation](#inline-delegation)             | Separate prompt contexts, dedicated model selection, and clean result aggregation    |
| Combining multiple tool outputs with loops or filters | [CodeAct](#codeact)                                 | Minimizes model round-trips; filters massive datasets inside Python before returning |
| Parallel tool calls without serial model steps        | [CodeAct concurrency](#combine-tool-calls)          | Executes arbitrary tools concurrently via `asyncio.gather`                           |
| Long-running background jobs surviving the parent run | [Asynchronous children](#asynchronous-children)     | Decoupled execution lifecycle managed by host scheduling                             |
| Native system libraries, package installs, and shell  | Application code or [Environments](environments.md) | Full OS capabilities outside restricted interpreters                                 |

## Inline Delegation

Inline delegation allows a parent agent to invoke a specialized child agent and wait synchronously for its response. Harness manages child execution within the active run lifecycle.

### Declare a Child

Define the child agent using a complete `AgentDefinition`, then register it within the parent builder via `SubagentCapability` and `SubagentDefinition`:

```python title="delegation_demo.py"
import asyncio
import os

from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder, SubagentDefinition
from a13n_harness.capabilities import SubagentCapability

# 1. Define the specialized child agent
reviewer = AgentDefinition(
    agent=AgentSpec(
        model=os.environ["HARNESS_MODEL"],
        instructions="You are an expert security reviewer. Analyze the plan and identify concrete architectural risks.",
    ),
    output_type=str,
    definition_id="plan-reviewer-v1",
)

# 2. Register child within the coordinator agent
parent = HarnessBuilder().build(
    AgentDefinition(
        agent=AgentSpec(
            model=os.environ["HARNESS_MODEL"],
            instructions="Coordinate the overall plan. Delegate deep architectural reviews to the 'reviewer' subagent, then synthesize the final recommendation.",
        ),
        output_type=str,
        definition_id="coordinator-v1",
        capabilities=(SubagentCapability(),),
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="Delegates architectural and security reviews to a specialized reviewer agent.",
                agent=reviewer,
            ),
        ),
    )
)


async def main() -> None:
    result = await parent.run(
        "Review this migration strategy: update database schema directly on production, then run data migrations."
    )
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

When you execute this script, the parent model calls `delegate(subagent="reviewer", prompt=...)`. Harness initializes the reviewer agent, executes its run to completion, and delivers its structured response back to the parent model.

### Continue a Child Conversation

When the child returns a result, Harness provides a unique `execution_id`. To continue the discussion with that same child agent in a follow-up step, the parent model invokes `resume_subagent(execution_id=..., prompt=...)`. Calling `delegate` again creates a fresh, separate child thread.

Child conversation lineages are serialized inside the parent's `HarnessState`. When you save and restore parent state across sessions, all associated child threads and continuation IDs are fully preserved.

## CodeAct

Standard model tool calling requires a round-trip per operation: the model emits a tool call, waits for execution, reads the result, and decides the next step. For tasks involving multiple items or iterative processing, this approach introduces compounding network latency and heavy token costs.

**CodeAct** addresses this by giving the model a restricted Python runtime. The agent writes Python code that directly calls registered tools, processes return values with conditional logic, and returns only the final summary to the model.

### Enable CodeAct for Your Tools

Register `CodeActCapability()` and wrap your target tools with `CodeActPolicyToolset` to grant Python execution access:

```python title="codeact_demo.py"
import asyncio
import os

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai.capabilities import Capability
from pydantic_ai.toolsets import FunctionToolset


def double(value: int) -> int:
    """Return twice the supplied integer value."""
    return value * 2


# Wrap native tools with a CodeAct policy granting Python access
math_tools = Capability(
    id="math-tools",
    toolsets=[
        CodeActPolicyToolset(
            wrapped=FunctionToolset([double], id="math-functions"),
            policy=CodeActToolPolicy(tools={"double": True}),
            reject_unknown_tools=True,
        )
    ],
)

calculator = HarnessBuilder().build(
    AgentSpec(
        model=os.environ["HARNESS_MODEL"],
        instructions="Use Python code through run_code to perform computations efficiently.",
    ),
    output_type=str,
    capabilities=(math_tools, CodeActCapability()),
)


async def main() -> None:
    result = await calculator.run(
        "Use run_code to double each number in [2, 4, 6, 8, 10], sum them, and return the total."
    )
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

### Combine Tool Calls

Within `run_code`, the model generates idiomatic async Python. Tools are exposed as top-level asynchronous functions:

```python
# Sequential iteration over tool calls
values = []
for item in [2, 4, 6]:
    doubled = await double(value=item)
    values.append(doubled)

sum(values)
```

To maximize throughput across independent I/O calls, the model can execute multiple tool calls concurrently using `asyncio.gather`:

```python
import asyncio

# Concurrent tool fan-out
results = await asyncio.gather(
    double(value=10),
    double(value=20),
    double(value=30),
)
sum(results)
```

Only the final evaluated expression or explicit return value is delivered back into model context, keeping the conversation history concise.

### Compose Browser Tools

You can pair `CodeActCapability()` with `BrowserUseCapability()` to perform high-efficiency web scraping and automated browser workflows:

```python
# Inspect rendered web state, extract CSS elements, and query accessibility trees
await browser_observe()
cards = await browser_js(
    expression="[...document.querySelectorAll('.pricing-card')].map(el => el.innerText)"
)
ax_tree = await browser_cdp(method="Accessibility.getFullAXTree", params={})

{"cards": cards, "total_ax_nodes": len(ax_tree.get("nodes", []))}
```

`browser_js` executes inside the page's main JavaScript context and returns parsed JSON values. `browser_cdp` provides direct access to Chrome DevTools Protocol domains for advanced inspection.

### Reuse a Program

For structured, repeatable procedures, save Python source files in a mounted environment rather than generating inline code strings on every turn:

```python title="calculate_metrics.codeact.py"
async def main(inputs: dict) -> dict:
    raw_val = inputs["value"]
    computed = await double(value=raw_val)
    await store(key="last_computed_metric", value=computed)
    return {"input": raw_val, "result": computed}
```

The agent invokes the file using `run_program`:

```json
{
  "path": "/workspace/calculate_metrics.codeact.py",
  "inputs": {"value": 42}
}
```

Harness reads the script through the bound environment, runs its `main()` entry point, and delivers the structured dictionary result back to the model.

### Keep Data Across Calls

Harness provides distinct persistence scopes for CodeAct execution:

| Scope                   | Lifetime                 | Mechanism                                       |
| :---------------------- | :----------------------- | :---------------------------------------------- |
| Ephemeral variables     | Active logical run       | Variables and functions declared in `run_code`  |
| Program scope           | Single script invocation | Local execution scope of `run_program`          |
| Durable key-value state | Across runs and restarts | `await store(key, value)` and `await load(key)` |

Use the built-in storage helpers to persist intermediate computation results without polluting model context:

```python
# Save intermediate data
await store(key="report.data", value={"summary": "Q4 Performance", "score": 98})

# Retrieve saved data in a subsequent turn
saved = await load(key="report.data")

# Delete when no longer needed
await forget(key="report.data")
```

Values saved with `store` are serialized directly into `HarnessState`, ensuring they persist across process restarts.

### Handle Failures and Set Limits

Protect your host environment by configuring execution timeouts, concurrency caps, and memory limits via `CodeActConfig`:

```python
from a13n_harness.capabilities import CodeActCapability, CodeActConfig

codeact_capability = CodeActCapability(
    CodeActConfig(
        max_tool_calls=32,          # Max nested tool invocations per code run
        max_concurrency=8,           # Max parallel asyncio tasks
        timeout_seconds=60,          # Wall-clock execution deadline
        max_state_entries=128,       # Max stored key-value pairs
        max_state_bytes=2 * 1024 * 1024,  # Max storage memory (2 MiB)
    )
)
```

When code execution encounters an unhandled exception or exceeds configured limits, Harness captures the stack trace and line number, returning a descriptive error to the model so it can diagnose the issue and adjust its code.

## Asynchronous Children

When an agent needs to spawn subagents whose execution outlives the parent turn, configure asynchronous subagent execution. In this architecture, the host platform supplies a `SubagentOperator` implementation responsible for scheduling runs, recording checkpoints, and routing results:

```python
from a13n_harness.capabilities import SubagentCapability

# Enable host-managed async delegation
capability = SubagentCapability(async_enabled=True, operator=host_operator)
```

Asynchronous delegation exposes management tools to the model:

| Tool                                    | Purpose                                                      |
| :-------------------------------------- | :----------------------------------------------------------- |
| `delegate(subagent_name, prompt)`       | Dispatches child execution; returns an admission receipt     |
| `subagent_info(execution_id)`           | Queries current progress, run status, and metrics            |
| `wait_subagent(execution_ids)`          | Pauses parent execution until one or more subagents complete |
| `steer_subagent(execution_id, message)` | Delivers steering instructions to an active child run        |
| `cancel_subagent(execution_id)`         | Requests graceful termination of a child run                 |
| `resume_subagent(execution_id, prompt)` | Dispatches a follow-up turn to a completed child             |

### Host-Managed Feedback

For long-running subagents that require human intervention or tool approval, the host coordinates suspension and resumption:

1. When a child encounters a deferred tool or question, Harness transitions the run status to `suspended` and saves its `HarnessState`.
2. The host notifies users or external systems to collect the required feedback.
3. Once the answers are ready, the host invokes `DeferredToolResume` to resume child execution from its exact checkpoint.
