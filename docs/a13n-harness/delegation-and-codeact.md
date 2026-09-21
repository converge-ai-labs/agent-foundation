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

Deferred support is selected by fresh `RunBindings.deferred_tools_supported`, not by whether an invocation has a parent. The default is `True`. Roots and children can use native deferred calls, approvals, and Host-configured handlers.

A Host without a child-feedback lifecycle sets `deferred_tools_supported=False`. Declaratively deferred tools and their first-party guidance are then omitted. Runtime `CallDeferred` and `ApprovalRequired` become `ToolDenied("Deferred tool interaction is unavailable for this Run.")` results inside the same model loop. A custom handler cannot bypass that setting. Unexpected terminal deferral fails with `deferred_tools_unsupported`. Harness UI currently selects this unsupported mode for async children; ordinary async prompt continuation remains available.

#### Resume a Waiting Inline Child

An inline child releases its execution stack when it suspends. The parent receives a bounded tool failure naming the compact child ID and may continue other work. Its exported state retains the child's exact native requests, metadata, pending Run ID, and complete history. Persist the complete parent checkpoint before collecting feedback.

For a direct child, inspect the typed owned collection and supply a complete trusted batch on a later parent Run:

```python
from a13n_harness import InlineSubagentDeferredResults, RunBindings
from a13n_harness.capabilities import InlineSubagentCollectionState
from pydantic_ai.tools import DeferredToolResults

children = InlineSubagentCollectionState.model_validate(
    parent_state.agent_context_state.entries["a13n.subagents"].data
).children
pending = children[child_id]
assert pending.pending_run_id is not None

# These values come from authenticated Host feedback, never from model text.
results = DeferredToolResults(
    calls={external_call_id: external_result},
    approvals={approval_call_id: True},
)
submission = InlineSubagentDeferredResults(
    child_thread_id=pending.state.thread_id,
    pending_run_id=pending.pending_run_id,
    results=results,
)
result = await executable.run(
    f"Continue inline child {child_id}.",
    previous_state=parent_state,
    bindings=RunBindings.embedded(inline_subagent_results=(submission,)),
)
```

The batch must exactly cover the retained calls and approvals, including metadata required by the native protocol. Parent preflight rejects unknown targets, stale Run IDs, duplicates, and incomplete batches before model or Environment execution. Submissions can target nested children by Thread ID; when an ancestor is resumed, only its subtree's submissions follow it.

The model still invokes `resume_subagent` to advance a selected child. Submission does not automatically schedule it, and that tool's prompt never supplies approvals or answers. A pending child without trusted results stays unchanged. Current tool identities, policy, and resource approval checks still apply; ordinary completed side effects in the pending batch are not replayed. Inspect the next exported state before deciding whether unconsumed submissions should be sent again. Forked children have new Thread IDs and require freshly correlated submissions. Replaying an old checkpoint is not an exactly-once guarantee.

Usage limits are independent per child Run. Each child receives the strictest per-field intersection of its own definition limits and the authored edge, not its parent's budget or accumulator. An async Host may narrow that ceiling. Child events remain attributed separately; Hosts can aggregate usage for display without introducing a shared enforcement cap.

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

### Large Tool Collections with ToolProxy

[Grouped ToolProxy](tool-proxy.md#use-with-codeact) keeps large collections out of the runner's eager directory. CodeAct receives the proxy search/call functions, discovers exact schemas on demand, and dispatches the resolved target through its existing `ToolManager` bridge. Grouping never grants CodeAct eligibility: publish a typed policy on the source tools first. Both `run_code` and `run_program` support this path; proxy calls are conservatively sequential even inside `asyncio.gather`.

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

### Save data explicitly, not the interpreter

With `CodeActCapability`, use meaningful keys to save JSON data from either runner:

```python
await store(key="search.results", value={"ids": [12, 34], "next_page": 3})
```

A later feed, program, or continued run can read it without rerunning the search:

```python
results = await load(key="search.results")
results["ids"]
```

`load()` lists all keys; `forget(key="search.results")` deletes one and returns whether it existed. There is no message/description field. A missing key raises an error, while a stored null loads as `None`. Loaded objects are detached: mutate and call `store` again to publish changes.

Successful writes survive subsequent sandbox failure and `run_code(restart=True)`. Cross-run continuity requires the host to save and restore `HarnessState`; this is not durable storage by itself. Ordinary variables and functions still disappear when a run ends. A new child has separate state; a host-created state fork is a detached copy, never a shared map. Restoring data does not restore old tool permissions.

The model receives only a bounded key directory (up to 32 keys and 4 KiB), not all stored values. Use `load()` to discover omitted keys. `CodeActConfig.max_state_entries` defaults to 256 and `max_state_bytes` to 10 MiB for the whole compact JSON namespace, including keys. Keys must contain 1–256 characters. Values must be finite JSON; rejected writes do not replace existing data. These limits also apply when restoring state.

CodeAct lets Monty resolve Python names rather than rejecting calls with an approximate static scope checker. Actual host calls must still be eligible in the current tool catalog. A later unavailable call can fail after earlier tools have run; completed effects and explicit writes are not rolled back or automatically replayed.
