---
title: Delegation and CodeAct
description: Orchestrate multi-agent workflows with subagents or execute concurrent Python control flow over external tools using CodeAct.
---

Use **Delegation** to isolate specialist roles in separate prompt contexts and models, and **CodeAct** to let an Agent compose loops, filtering, and `asyncio.gather` concurrency over tools in restricted Python without extra model round-trips.

| Requirement                                      | Mechanism                                       |
| ------------------------------------------------ | ----------------------------------------------- |
| Role separation or dedicated reviewer/specialist | Inline `SubagentCapability` (`delegate`)        |
| Background child work outliving the parent turn  | [Asynchronous children](#asynchronous-children) |
| Loops, filtering, or parallel tool fan-out       | `CodeActCapability` (`run_code`, `run_program`) |

## Inline delegation

Declare a child `AgentDefinition` and attach it to the parent with `SubagentCapability` and `SubagentDefinition`:

```python title="delegation_demo.py"
import os
from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder, SubagentDefinition
from a13n_harness.capabilities import SubagentCapability

reviewer = AgentDefinition(
    agent=AgentSpec(
        model=os.environ["HARNESS_MODEL"],
        instructions="Analyze the plan and report concrete architectural risks.",
    ),
    output_type=str,
    definition_id="plan-reviewer-v1",
)

parent = HarnessBuilder().build(
    AgentDefinition(
        agent=AgentSpec(
            model=os.environ["HARNESS_MODEL"],
            instructions="Delegate deep reviews to 'reviewer', then synthesize.",
        ),
        output_type=str,
        definition_id="coordinator-v1",
        capabilities=(SubagentCapability(),),
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="Reviews plans for architectural and security risks.",
                agent=reviewer,
            ),
        ),
    )
)
```

The parent calls `delegate(subagent="reviewer", prompt=...)` to start a child thread and `resume_subagent(execution_id=..., prompt=...)` to continue that same child thread. Child lineages persist inside the parent's `HarnessState`.

## CodeAct

Enable `CodeActCapability` and wrap eligible toolsets in `CodeActPolicyToolset` so the Model can call them as async Python functions inside `run_code` or `run_program`:

```python title="codeact_demo.py"
import os
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import CodeActCapability, CodeActConfig
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai.capabilities import Capability
from pydantic_ai.toolsets import FunctionToolset


def double(value: int) -> int:
    return value * 2


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
    AgentSpec(model=os.environ["HARNESS_MODEL"]),
    output_type=str,
    capabilities=(
        math_tools,
        CodeActCapability(CodeActConfig(max_tool_calls=32, max_concurrency=8, timeout_seconds=60)),
    ),
)
```

Inside `run_code`, the Model can fan out with `await asyncio.gather(double(value=10), double(value=20))`, execute workspace `.codeact.py` scripts via `run_program`, and persist JSON values across turns in `HarnessState` using `await store(key, value)`, `await load(key)`, and `await forget(key)`.

### Submit structured output directly

Set `CodeActConfig(allow_final_output=True)` to let CodeAct submit a structured result in the same model round-trip. For example, configure the Agent with a Pydantic output model:

```python
import os

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import CodeActCapability, CodeActConfig
from pydantic import BaseModel


class Answer(BaseModel):
    total: int


calculator = HarnessBuilder().build(
    AgentSpec(model=os.environ["HARNESS_MODEL"]),
    output_type=Answer,
    capabilities=(CodeActCapability(CodeActConfig(allow_final_output=True)),),
)
```

On non-realtime Runs with structured output, the CodeAct catalog lists the Run's output functions for both `run_code` and `run_program`. For this Agent, the Model can end a snippet with `final_result(total=6 * 7)`. The call stops the snippet at that point and submits the arguments for validation. If schema validation or an output validator rejects the result, Pydantic AI sends the feedback to the Model through its native output retry mechanism. Plain `output_type=str` and realtime Runs keep their existing behavior.

## Asynchronous children

For child runs that outlive the parent turn, supply a host `SubagentOperator`:

```python
capability = SubagentCapability(async_enabled=True, operator=host_operator)
```

This exposes `delegate`, `subagent_info`, `wait_subagent`, `steer_subagent`, `cancel_subagent`, and `resume_subagent`.

### Host-managed feedback

Built-in inline children set `RunBindings.deferred_tools_supported=False` because they cannot pause independently. With an async `SubagentOperator`, a child encountering a deferred question or approval transitions to `suspended`, saves its `HarnessState`, and resumes via `DeferredToolResume` once the Host collects user feedback.
