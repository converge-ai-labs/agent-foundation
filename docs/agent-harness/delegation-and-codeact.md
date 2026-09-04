# Delegation and CodeAct

Agent Harness provides two advanced orchestration features without adding a workflow engine:

- subagent execution runs an exact declared child inline or through an asynchronous operator;
- CodeAct runs restricted Python over an explicitly eligible subset of the active tool surface.

Both execute through the canonical Harness and Pydantic AI boundaries, so policy, events, usage, cancellation, result validation, and cleanup remain consistent.

## Inline Delegation

### Declare Child Topology

A parent definition owns a finite collection of immediate child definitions:

```python
from a13n_harness import (
    AgentDefinition,
    HarnessBuilder,
    SubagentDefinition,
)
from a13n_harness.capabilities import SubagentCapability
from pydantic_ai.agent.spec import AgentSpec

child = AgentDefinition(
    agent=AgentSpec(),
    output_type=str,
    model=reviewer_model,
)

parent = HarnessBuilder().build(
    AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        model=coordinator_model,
        capabilities=(SubagentCapability(),),
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="Review one bounded change.",
                agent=child,
            ),
        ),
    )
)
```

Build recursively validates a finite acyclic graph and unique sibling names. Each child becomes immutable reusable `ExecutableAgent` build output contained by the root executable.

### Inline Execution and Continuation

The default Capability needs no Host scheduler or child-binding callback. Its private inline executor:

1. selects one declared child;
2. derives fresh child instance lineage and intersects authored usage ceilings;
3. applies the edge context policy;
4. borrows the parent's already-entered Environment mapping without re-entering or closing adapters;
5. runs the child through its canonical `ExecutableAgent.stream()` path;
6. forwards validated child observations into the parent stream;
7. stores complete nested child continuation before returning a bounded result.

`delegate(subagent, prompt)` creates a new inline continuation and returns its `execution_id`. `resume_subagent(execution_id, prompt)` advances only that exact compatible nested `HarnessState`. Inline child state never publishes borrowed Environment state independently, and the child cannot mount, replace, unmount, or change the default Environment.

### Asynchronous Children

A child that may outlive the parent Run requires a Host-owned `SubagentOperator`:

```python
from a13n_harness.capabilities import SubagentCapability

operator = ApplicationSubagentOperator(thread_service, environment_service)
capability = SubagentCapability(
    async_enabled=True,
    operator=operator,
)
```

The operator implements complete `delegate`, `info`, `wait`, `steer`, `cancel`, and `resume` use cases. Harness resolves the exact child, derived Identity, applied context, intersected usage limits, and detached parent correlation before admission. It also supplies the originating tool-call ID and assembled name separately when available. The operator may use that correlation to derive its own idempotency identity and replay scope; Harness does not generate either. The operator then owns child Thread creation, fresh `RunBindings`, Environment association and re-entry, recursive Harness invocation, storage, checkpoints, observation, wake, cancellation, resume, loss, cleanup, and retention.

Harness provides no default async manager, execution store, background task registry, parent-state execution mirror, or shutdown method. Parent Run or Environment closure does not cancel accepted child work or close the operator. `subagent_info` and `wait_subagent` query the Host directly on every call.

### Deliberate Boundary

Inline execution is the only built-in child executor. Async support does not provide:

- a process-local scheduler or default storage;
- a required database or checkpoint protocol;
- completion callbacks or a wake ledger;
- cross-process cancellation routing;
- a parent `HarnessState` copy of Host child status or state.

Implement those semantics behind `SubagentOperator` according to the owning Host. Do not retain a live parent `AgentContext` or entered Environment after admission, and do not turn parent state into a second child store.

### Deferred Tools in Child Runs

Every invocation with `parent_agent_instance_id is not None` is non-suspending. This applies to inline Harness children and to Host-scheduled children that use the ordinary child bindings:

| Deferred path                                                                                                                              | Child behavior                                                                                                          |
| ------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| A prepared `ToolDefinition.defer` is true, including external and declaratively approval-gated tools                                       | The definition and its first-party guidance are absent from the child tool surface                                      |
| An ordinary function, validator, Capability hook, custom Toolset, or managed policy raises `CallDeferred` or `ApprovalRequired` at runtime | The mandatory Harness boundary returns `ToolDenied` for every request, and Pydantic continues the same child model loop |
| An unexpected custom or upstream bypass still terminates with `DeferredToolRequests`                                                       | The child fails closed with `subagent_deferred_unsupported`; its parent is never suspended                              |

Root runs are unchanged. They can suspend with native deferred requests or use a Pydantic `HandleDeferredToolCalls` Capability to resolve requests inline. The Harness boundary declines root requests so normal Pydantic accumulation dispatch still applies.

This distinction matters when authoring a plugin, Toolset, validator, or policy. A function can be statically ordinary but request interaction only for particular arguments:

```python
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred


def publish_report(*, destination: str, require_review: bool) -> str:
    if require_review:
        raise ApprovalRequired({"destination": destination})
    if destination.startswith("client://"):
        raise CallDeferred({"destination": destination})
    return publish_without_external_interaction(destination)
```

A root model receives normal approval or external-execution behavior. A child model receives a normal denied tool result with the message `Deferred tool interaction is unavailable in subagent runs.` and can choose another argument, another tool, or a final answer. The function stays visible because the deferral decision is argument- or policy-specific; the Harness does not permanently hide it after one denial. If restricted CodeAct calls that function, the same handler chain produces the denial, CodeAct ends that runner invocation as a bounded failure because it cannot suspend a Monty frame, and the surrounding child model loop continues.

When designing extension behavior:

1. Set native Pydantic `requires_approval` or external tool kinds honestly. Do not disguise a declaratively deferred tool as an ordinary function merely to keep it visible to children.
2. Treat `ToolDenied` as an ordinary expected outcome. Give the model enough tool description or adjacent ordinary tools to make progress without Host interaction when child use is intended.
3. If a tool fundamentally requires a user, browser client, or external executor, accept that it is root-only. Configure a child definition without depending on that tool for its required output path.
4. Keep argument-sensitive deferral dynamic. Do not remove a function from later child requests merely because one argument or one live policy decision required approval.
5. Do not install an auto-approving `HandleDeferredToolCalls` handler to bypass child policy. The mandatory child boundary resolves the complete batch first; custom handlers still work for roots.
6. Treat only a terminal root `HarnessRunResult(status="suspended")` and its exact `deferred` value as suspension authority. Deferred stream events are observations, and child jobs have no deferred-response lifecycle.
7. Test the same extension as both a root and a child. Cover static surface omission, dynamic `CallDeferred`, dynamic `ApprovalRequired`, managed-policy approval, mixed ordinary/deferred batches, and the model's denial recovery path.

Usage limits remain the bound on a model that repeatedly retries denied interactions. An inline child receives the strictest per-field intersection of the parent effective limit, its own `AgentSpec.usage_limits`, and the authored subagent edge. An async Host receives that same ceiling and may only narrow it. Harness does not add a second hidden retry counter or mutate the extension definition.

## CodeAct

`CodeActCapability` can expose:

- `run_code`, for inline restricted Python;
- `run_program`, for a `*.codeact.py` program read through the current Environment.

The runtime is based on Monty and has no ambient filesystem, network, process, environment, credential, or clock access.

### Publish Eligible Tools

A tool owner explicitly wraps eligible tools with a typed policy:

```python
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.toolsets import (
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

Restricted code receives generated typed host functions. A nested call validates and executes through the active final Pydantic AI `ToolManager`, not through a second dispatcher. Therefore ordinary Capability hooks, Harness managed-tool policy, provider enforcement, events, usage, and deferred behavior still apply. A root inline deferred handler can supply the nested result. A child denial or unresolved root request fails only the current CodeAct runner invocation; CodeAct never persists or resumes an interpreter frame.

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
