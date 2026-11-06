---
title: Delegation and CodeAct
description: Give another Agent a task, or use restricted Python to combine tools in the current Agent.
---

Delegation gives a task to another Agent with its own instructions, Model, and tools. CodeAct lets the current Agent combine tool calls with Python loops, conditions, and concurrency.

## Choosing Between Them

| Task                                                               | Use                                                   |
| ------------------------------------------------------------------ | ----------------------------------------------------- |
| Perform one operation                                              | Call the tool directly                                |
| Ask another Agent to review, research, or solve a bounded task     | [Inline delegation](#inline-delegation)               |
| Combine tool results with Python control flow                      | [CodeAct](#codeact)                                   |
| Keep child work running after the parent Run closes                | [Asynchronous children](#asynchronous-children)       |
| Run application Python with installed libraries or native commands | Application code or an [Environment](environments.md) |

The examples below build on the [quickstart's model setup](getting-started.md#use-a-real-model). Set `HARNESS_MODEL` and the provider credential before running them.

## Inline Delegation

Inline delegation waits for a declared child to finish and returns its output to the parent Model. Harness runs the child; your application needs no scheduler.

### Declare a Child

Give the child a complete `AgentDefinition`, then register it under a name in the parent. `SubagentCapability()` exposes the delegation tools to the parent Model.

```python title="delegation_demo.py"
import asyncio
import os

from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder, SubagentDefinition
from a13n_harness.capabilities import SubagentCapability

reviewer = AgentDefinition(
    agent=AgentSpec(
        model=os.environ["HARNESS_MODEL"],
        instructions="Review the supplied plan. Identify concrete risks and missing steps.",
    ),
    output_type=str,
    definition_id="plan-reviewer-v1",
)
parent = HarnessBuilder().build(
    AgentDefinition(
        agent=AgentSpec(
            model=os.environ["HARNESS_MODEL"],
            instructions="Delegate plan reviews to reviewer, then summarize its findings.",
        ),
        output_type=str,
        definition_id="coordinator-v1",
        capabilities=(SubagentCapability(),),
        subagents=(SubagentDefinition(
            name="reviewer", description="Review a plan for concrete risks.", agent=reviewer,
        ),),
    )
)


async def main() -> None:
    result = await parent.run("Review this plan: deploy the update, then run the tests.")
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Run `uv run python delegation_demo.py`. The parent can call `delegate(subagent="reviewer", prompt=...)` and use the child's answer. The wording depends on your Model.

### Continue a Child Conversation

`delegate` returns an `execution_id` with the child's output. The parent Model can call `resume_subagent(execution_id=..., prompt=...)` to continue that conversation. Calling `delegate` again starts a new conversation.

Save and restore the parent's complete `HarnessState` to preserve inline children across Runs. Keep definition IDs stable when rebuilding compatible definitions. See [State and resume](state-and-resume.md) for persistence.

Inline children share the parent's entered Environment and inherited invocation policy. Set each child's budget explicitly: child usage is independent of the parent's budget, and `SubagentDefinition.usage_limits` can narrow the child's definition limits.

Inline children cannot suspend for deferred tool feedback. Use a Host-managed child when that interaction is required.

## CodeAct

CodeAct adds two tools: `run_code` for inline Python and `run_program` for reusable source files. Both execute restricted Python. File, network, process, environment-variable, and credential access goes through eligible tools. Use an Environment shell for ordinary Python packages or native programs.

### Enable CodeAct for Your Tools

Add `CodeActCapability()` and explicitly allow the tools that Python may call. This example exposes a `double` function:

```python title="codeact_demo.py"
import asyncio
import os

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai.capabilities import Capability
from pydantic_ai.toolsets import FunctionToolset


def double(value: int) -> int:
    """Return twice the supplied value."""
    return value * 2


math_tools = Capability(
    id="math-tools",
    toolsets=[CodeActPolicyToolset(
        wrapped=FunctionToolset([double], id="math-functions"),
        policy=CodeActToolPolicy(tools={"double": True}),
        reject_unknown_tools=True,
    )],
)
calculator = HarnessBuilder().build(
    AgentSpec(model=os.environ["HARNESS_MODEL"]),
    output_type=str,
    capabilities=(math_tools, CodeActCapability()),
)


async def main() -> None:
    result = await calculator.run(
        "Use run_code to double each of 2, 4, and 6, then return their sum."
    )
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Run `uv run python codeact_demo.py`. The expected sum is `24`. `CodeActToolPolicy` grants eligibility; normal availability, argument validation, permissions, and approval checks still apply to each call.

### Combine Tool Calls

The Model supplies code like this to `run_code`. These snippets are runner payloads, not standalone Python modules:

```python
values = []
for value in [2, 4, 6]:
    values.append(await double(value=value))
sum(values)
```

Use keyword arguments and await every tool call. `run_code` returns the final expression. To run independent calls concurrently:

```python
import asyncio

values = await asyncio.gather(double(value=2), double(value=4), double(value=6))
sum(values)
```

Keep the runner as the only tool call in its Model response. Compose the needed calls inside its code and return only the data the Agent needs.

For tools with `defer_loading=True`, discover the tool or load its Capability before calling the runner. For large collections, [grouped ToolSearch](tool-search.md#use-with-codeact) supports discovery inside the runner. ToolSearch proxy calls remain sequential, including inside `asyncio.gather`.

### Reuse a Program

Save a UTF-8 file in a readable Environment mount. Define one undecorated `async def main(inputs)` and return a JSON-compatible result:

```python title="double.codeact.py"
async def main(inputs):
    result = await double(value=inputs["value"])
    await store(key="math.result", value=result)
    return result
```

If the file is at `/workspace/double.codeact.py`, the Model calls `run_program` with:

```json
{"path": "/workspace/double.codeact.py", "inputs": {"value": 6}}
```

The result is `12`, also saved as `math.result`. Replace the example path with a path in your bound [Environment](environments.md). Harness reads the file through that Environment; the Model does not need a file-reading tool.

Each invocation reads the source again and starts a fresh interpreter. Supported imports, helpers, dataclasses, and module initialization can accompany `main`. Keep the workflow in `main`; initialization also runs on every invocation under the same tool policy and limits.

### Keep Data Across Calls

| State                              | Lifetime                                                    |
| ---------------------------------- | ----------------------------------------------------------- |
| `run_code` variables and functions | Successful calls within the current Run                     |
| `run_program` variables            | One invocation                                              |
| Values saved with `store`          | Across Runs when the Host saves and restores `HarnessState` |

Use `store` and `load` for explicit JSON data:

```python
await store(key="search.results", value={"ids": [12, 34], "next_page": 3})
results = await load(key="search.results")
results["ids"]
```

`load()` lists keys. `forget(key="search.results")` deletes a value. Loading returns a detached copy; call `store` again to save edits. A missing key fails, while a stored JSON null loads as `None`.

`run_code(restart=True)` clears temporary variables before evaluation. A failed evaluation also clears them. Successful `store` writes survive either operation. To retain those writes after process exit, persist the returned State as described in [State and resume](state-and-resume.md).

### Handle Failures and Set Limits

Failure results identify the error and include a source location when available. Correct the code before retrying. A failed runner may have completed earlier tool calls: inspect their outcomes before repeating writes or other external effects. Completed effects and stored values are not rolled back.

Use `CodeActConfig` to lower execution and storage limits:

```python
from a13n_harness.capabilities import CodeActCapability, CodeActConfig

codeact = CodeActCapability(CodeActConfig(
    max_tool_calls=16,
    max_concurrency=4,
    timeout_seconds=60,
    max_state_entries=64,
    max_state_bytes=1024 * 1024,
))
```

Use this instance in place of `CodeActCapability()` in the builder. Defaults allow 128 nested tool attempts, concurrency 16, and 300 seconds per runner. Stored data defaults to 256 entries and 10 MiB. Parent tool-call limits and provider ceilings still apply.

## Asynchronous Children

Use asynchronous children when work must outlive the parent Run. Your Host implements `SubagentOperator` to schedule children, retain checkpoints, and deliver results. Pass that operator to `SubagentCapability(async_enabled=True, operator=operator)` instead of the inline Capability.

| Model tool                              | Result                                       |
| --------------------------------------- | -------------------------------------------- |
| `delegate(subagent_name, prompt)`       | Admission receipt; work continues separately |
| `subagent_info(...)`                    | Current execution status                     |
| `wait_subagent(...)`                    | Bounded wait for one or more executions      |
| `steer_subagent(execution_id, message)` | Acknowledgement of a steering request        |
| `cancel_subagent(execution_id)`         | Acknowledgement of a cancellation request    |
| `resume_subagent(execution_id, prompt)` | Admission of a linked continuation           |

A wait timeout does not cancel work. A cancellation receipt does not prove cleanup. Query the current execution record to confirm the outcome. Closing the parent Run does not cancel accepted async work.

The Host owns fresh child bindings, storage, shutdown, and recovery. See [Host embedding](hosting.md) for lifecycle integration.

### Host-Managed Feedback

A Host that supports child feedback uses the [structured suspension flow](state-and-resume.md#structured-suspension): save the child's State and deferred request, collect feedback, then resume the child directly with `DeferredToolResume`. Otherwise, set `RunBindings.deferred_tools_supported=False`; Harness omits deferred tools and denies runtime deferral requests. Harness UI uses this setting for async children.
