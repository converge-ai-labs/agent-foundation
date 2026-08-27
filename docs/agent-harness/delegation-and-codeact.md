# Delegation and CodeAct

Agent Harness provides two advanced orchestration features without adding a workflow engine:

- inline delegation runs a declared child Agent and waits for its result;
- CodeAct runs restricted Python over an explicitly eligible subset of the active tool surface.

Both execute through the canonical Harness and Pydantic AI boundaries, so policy, events, usage, cancellation, result validation, and cleanup remain consistent.

## Inline Delegation

### Declare Child Topology

A parent definition owns a finite collection of immediate child definitions:

```python
from a13n_harness import (
    AgentDefinition,
    DelegationCapability,
    HarnessBuilder,
    SubagentDefinition,
)
from pydantic_ai.agent.spec import AgentSpec

child = AgentDefinition(
    agent=AgentSpec(model="logical:reviewer"),
    output_type=str,
    model=reviewer_model,
)

parent = HarnessBuilder().build_code(
    AgentSpec(model="logical:coordinator"),
    output_type=str,
    model=coordinator_model,
    capabilities=(DelegationCapability(),),
    subagents=(
        SubagentDefinition(
            name="reviewer",
            description="Review one bounded change.",
            agent=child,
        ),
    ),
)
```

Build recursively validates a finite acyclic graph and unique sibling names. Each child becomes an owned `ExecutableAgent` and closes with the root executable.

### Supply Fresh Child Authority

The definition describes topology, not current authority. Each parent run supplies a `DelegationRunCapability` whose binder creates fresh child bindings under the authored edge ceilings.

`DelegationCapability` exposes one blocking `delegate` tool over the declared children. A call:

1. selects one declared child;
2. asks the fresh run binder for child authority and context;
3. runs the child through its canonical `ExecutableAgent.stream()` path;
4. forwards validated child observations into the parent stream;
5. waits for one child terminal result;
6. returns a bounded `DelegateResult` to the parent model.

A returned `child_instance_id` can continue only that child's private nested `HarnessState` through the same parent invocation context.

### Deliberate Boundary

Inline delegation is blocking and process-local. It does not provide:

- background submission;
- durable child executions or workers;
- receipts, polling, or result inboxes;
- cross-process cancellation routing;
- durable child scheduling or delivery.

A Host can implement those as ordinary Host-owned tools and lifecycle records. Do not reinterpret inline delegation as a background protocol.

## CodeAct

`CodeActCapability` can expose:

- `run_code`, for inline restricted Python;
- `run_program`, for a `*.codeact.py` program read through the current Environment.

The runtime is based on Monty and has no ambient filesystem, network, process, environment, credential, or clock access.

### Publish Eligible Tools

A tool owner explicitly wraps eligible tools with a typed policy:

```python
from a13n_harness import (
    CodeActCapability,
    CodeActPolicyToolset,
    CodeActToolPolicy,
)
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

capabilities = (
    math_tools,
    CodeActCapability(),
)
```

Eligibility is not inferred from arbitrary metadata, model visibility, or a tool name. The owner must publish it explicitly.

### Nested Dispatch

Restricted code receives generated typed host functions. A nested call validates and executes through the active final Pydantic AI `ToolManager`, not through a second dispatcher. Therefore ordinary Capability hooks, Harness managed-tool policy, provider enforcement, events, usage, and deferred behavior still apply.

A nested call that may have reached an external side effect is never reported as safely retryable merely because the Python program failed later.

### Runtime State

`run_code` state lasts for the current logical Harness run. Calls can retain ordinary interpreter values across invocations and clear them with `restart=True`. It does not enter `HarnessState` and does not survive a new run.

`run_program`:

- accepts only a strict UTF-8 `*.codeact.py` file;
- reads it through the current Environment;
- requires exactly `async def main(inputs)`;
- creates a fresh interpreter session for each invocation;
- receives JSON-compatible inputs.

The Environment path grants no additional tool eligibility. The code can call only injected host functions.

## Choosing Between Them

| Need                                                               | Use                                                                        |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| Give a named child Agent one bounded task and wait                 | Inline delegation                                                          |
| Combine several eligible tool calls with local Python control flow | CodeAct                                                                    |
| Submit durable work that outlives the current process/run          | Host-owned asynchronous child or task service                              |
| Execute arbitrary trusted application Python                       | Ordinary application code, not CodeAct                                     |
| Run untrusted OS code                                              | An actual isolated Environment provider, not the CodeAct interpreter alone |

Both features are optional. A simple Agent application should begin with ordinary native Capabilities and tools, then add delegation or CodeAct only when the execution shape requires them.
