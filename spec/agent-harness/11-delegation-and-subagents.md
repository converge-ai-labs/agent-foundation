# Delegation and Subagents

## Design Position

Subagents are complete child Agent definitions built into an immutable `SubagentCollection`. The collection is authority-neutral topology: it names the immediate children of one `ExecutableAgent` without selecting a model-visible presentation, execution mode, scheduler, storage system, or child authority.

The first-party `SubagentCapability` is the standard presentation and execution adapter. Construction fixes both behavioral inputs:

```python
type SubagentExecutionMode = Literal["inline", "async"]


class SubagentCapability(AbstractCapability[AgentContext]):
    def __init__(
        self,
        *,
        execution: SubagentExecutionMode,
        operator: SubagentOperator,
    ) -> None: ...
```

The Capability owns Toolset selection, tool schemas, guidance, compact model references, result projection, parent-state projection, and Pydantic lifecycle. Inline execution exposes one blocking delegation tool. Async execution exposes the standard start, info, wait, steer, cancel, and resume tools. A Host configures or subclasses the operator and never supplies, replaces, or branches on a first-party Toolset.

The mode and operator are trusted process-local definition inputs. They are not serialized into `HarnessState`, selected by `RunBindings`, changed by a tool argument, or inferred from backend availability. An unavailable operator operation returns a bounded tool failure without changing the fixed Toolset. Shared async admission, observation, cleanup, loss, generation ownership, and complete Host takeover are owned by [Async Components and Lifecycle](20-async-components-and-lifecycle.md).

There is no `SubagentConfiguration`, max-subagent permission setting, run binding that selects execution mode, generic Job abstraction, or Session-visible Harness child registry. Capability presence and its fixed mode determine the model surface. Exact child definitions, edge context policy, native `UsageLimits`, current tool policy, and Host authorization constrain execution without becoming tool-availability switches.

## Child Definitions and Built Collection

```python
@dataclass(frozen=True, slots=True)
class SubagentIdentityPolicy:
    inherit_agent_id: bool = False


@dataclass(frozen=True, slots=True)
class SubagentDefinition:
    name: str
    description: str
    agent: AgentDefinition[Any]
    context: DelegationContextPolicy = DelegationContextPolicy()
    identity: SubagentIdentityPolicy = SubagentIdentityPolicy()
    usage_limits: UsageLimits | None = None


@dataclass(frozen=True, slots=True)
class BuiltSubagent:
    declaration: SubagentDefinition
    definition: AgentDefinition[Any]
    executable: ExecutableAgent[Any]


class SubagentCollection(Mapping[str, BuiltSubagent]):
    def require(self, name: str) -> BuiltSubagent: ...
```

The Harness validates the finite recursive definition graph and builds children before their parent. Each child owns its complete Model, Prompt, output contract, Capabilities, plugins, recovery policy, Environment requirements, and nested children. Every parent `AgentContext` borrows the exact immutable immediate collection from its executable.

The collection contains no Identity, credential, current State, Environment runtime, scheduler, task, execution mode, operator, backend selector, or Host record. A child executable is reusable process-local build output and is never serialized as durable payload. A distributed Host persists its own target revision and reconstructs the exact definition on the selected worker.

A hosted source schema may support authoring conveniences such as inheritance or references, but trusted resolution produces the same complete finite child definitions before Harness build. The Harness does not resolve Host resource keys, clone a live parent, infer child authority, or consult a parent definition during execution.

## Operator and Default Manager

`SubagentOperator` is the public execution boundary. It is a normal overridable Python class, not a Toolset, factory stack, storage provider framework, or transport protocol. `open_child()` opens one fresh child authority scope and yields its `RunBindings`; this single operation is shared by inline and default asynchronous execution and is deliberately distinct from attaching a parent observer. The async operations start, attach, snapshot, observe bounded activity, wait, steer, and cancel canonical child work by an opaque backend ID. `attach()` replaces the observer for the current parent Run and atomically returns the latest snapshot; it neither resumes nor recreates the child. `snapshot()` is a one-shot read that does not change observation.

Every operation receives the current borrowed `AgentContext` for correlation and authorization. An operator may read bounded context metadata while handling the call, but it must not retain the live `AgentContext` or Pydantic `RunContext`. The operator implementation and its injected dependencies determine execution and storage; there is no per-execution storage metadata, namespace field, provider field, or permission configuration in Harness state.

The Harness provides `SubagentManager` as the concrete default `SubagentOperator`. It accepts one Host `open_child` callback and optional stable Host event hooks. It:

- executes inline children through the same fresh child authority scope;
- validates that each opened scope has a distinct child Agent instance, the requested parent instance, and the exact delegation ID before admitting execution;
- owns async child tasks, streams, exact input, bounded recent activity, latest complete child `HarnessState`, output, failure, steering, and cancellation in memory;
- uses opaque backend IDs to find those canonical live records;
- retains only weak or replaceable run observers so a completed parent Run is not kept alive;
- marks restored references `lost` when no matching in-memory record remains;
- force-cancels its owned children only when the Host invokes Manager `force_close()`.

The default Manager lifecycle and replacement by a real independently managed Host Thread follow [Async Components and Lifecycle](20-async-components-and-lifecycle.md). The Host owns operator construction, dependencies, stable hooks, and forced shutdown. A stable default Manager spans every executable or Runner-generation Run that can address its work; parent Run closure does not close it or cancel admitted children.

## Subagent Completion Observation

Subagent completion uses the independent current-Run observer and stable Host-hook paths defined by [Async Components and Lifecycle](20-async-components-and-lifecycle.md#active-observers-and-stable-host-hooks). While the exact parent Harness Run remains active, its projection applies the latest snapshot and enqueues a concise notice directing the model to `wait_subagent`; `subagent_info` remains available for status and bounded activity. The operator always dispatches its configured stable hooks regardless of parent activity. A failed observer or enqueue leaves canonical completion available through explicit tools and later reconciliation.

## Standard Toolsets

Inline mode exposes exactly one standard tool:

- `delegate` selects an immediate child, executes it inside the tool call, and returns the bounded child result plus a stable inline child reference.

Async mode exposes exactly six standard tools:

- `delegate` starts one async child and returns a compact `subagent-N` reference with `running` after operator acceptance; backend queueing is not a Harness status;
- `subagent_info` without an execution ID returns a paged lightweight status list. With an execution ID it returns that execution's exact input and bounded process-local activity detail in addition to status. Activity can include a rolling model-output preview and bounded current or recent Tool calls, arguments, outcomes, and result previews. Neither form returns the complete child output;
- `wait_subagent` performs one bounded wait for one child or one bounded fan-in snapshot. A single-child wait returns the complete canonical output; the ordinary managed-tool output policy spills an oversized result to a run-private file and returns its bounded disclosure rather than paging or truncating it inside the subagent manager. Fan-in remains summary-only and paged;
- `steer_subagent` offers one input to a compatible live execution;
- `cancel_subagent` requests cancellation from canonical operator state;
- `resume_subagent` starts a new linked execution from one retained resumable backend reference.

The standard async parameters are intentionally small:

```python
delegate(subagent_name: str, prompt: str)
subagent_info(execution_id: str | None = None, execution_offset: int = 0, execution_limit: int = 20)
wait_subagent(
    execution_id: str | None = None,
    timeout_seconds: float | None = None,
    execution_offset: int = 0,
    execution_limit: int = 20,
)
steer_subagent(execution_id: str, message: str)
cancel_subagent(execution_id: str)
resume_subagent(execution_id: str, prompt: str)
```

`subagent_name` selects one exact declared child. `prompt` carries the bounded task, constraints, and expected result. `execution_id` is a compact `subagent-N` reference, never an opaque backend ID. Info paging applies only when the ID is omitted. Wait paging applies only to no-ID fan-in summaries. `timeout_seconds` bounds one wait attempt and does not cancel the child. Steering addresses one running execution; resume addresses one terminal resumable execution and creates a new compact reference.

Neither surface has an execution-mode argument. Inline mode never exposes manager tools, and async `delegate` never waits for child completion. Tool guidance is contributed with the exact selected Toolset. A Host-specific Capability may define another presentation, but it cannot replace the standard Toolset while retaining the first-party Capability identity.

## Child Identity and Context

A Host operator creates a fresh `AgentInstanceContext` for every child execution. The public `derive_child_identity()` helper starts from the parent Identity, preserves `issuer`, `subject`, and string claims, and applies `SubagentIdentityPolicy.inherit_agent_id`:

- `False` selects the child definition ID or the Host's exact logical child Agent ID;
- `True` retains the parent's existing `agent_id` claim and synthesizes none when it is absent.

The helper is deterministic convenience, not authorization. The operator remains responsible for fresh credentials, Environment, model resolver, run Capabilities, and internal instance correlation. State never restores Identity or authority.

```python
@dataclass(frozen=True, slots=True)
class DelegationContextPolicy:
    include_task: bool = True
    history: Literal["none", "summary", "selected"] = "none"
    task_state: Literal["shared", "isolated"] = "shared"
```

The Harness builds bounded child input under the exact edge policy. Selected history reuses existing message-selection semantics. Working-State sharing uses the explicit typed task-cell contract owned by [Context and Memory](09-context-and-memory.md#working-state-capability). Messages, notes, every non-task Capability namespace, live plugins, model clients, Environment attachments, credentials, callbacks, and mutable context values remain private to one Agent.

A fresh child authority scope cannot alias consumed parent `RunBindings` or implicitly inherit background process, async-subagent, client-tool, or mount authority. An ordinary async child receives no background shell or nested subagent tools unless its exact definition includes those Capabilities and their own operators.

## Inline Execution

The inline operator opens one complete fresh child authority scope through `open_child()`. The parent Capability builds input, intersects limits, validates lineage, enters the returned `RunBindings`, executes the child, forwards canonical child observations, and owns nested continuation state.

```python
class InlineSubagentState(BaseModel):
    child_instance_id: str
    subagent_name: str
    child_definition_id: str
    state: HarnessState


class InlineSubagentManagerState(BaseModel):
    children: dict[str, InlineSubagentState]
```

A compact inline reference has the form `{subagent_name}-{suffix}` and is unique only inside that parent state. It is not the Host `agent_instance_id`, child Thread ID, backend ID, or authority. A new reference starts a new child `HarnessState` and Thread. Reusing a valid reference selects the exact nested state while the operator reauthorizes fresh run authority.

Each complete delivered child result atomically advances its inline state before the parent tool result is delivered. A live child context, binding, task, lock, provider handle, or credential never enters state. Calls targeting different references may run concurrently; competing calls for the same reference cannot overwrite one another.

Parent cancellation cancels and drains the active inline child. A child cannot suspend the parent through deferred tools; child deferred requests are denied or normalized to the bounded subagent failure contract. An inline advance becomes durable only when a complete parent boundary exports and the Host selects the resulting parent `HarnessState`.

## Async Operator Contract

The conceptual async boundary is:

```python
class SubagentExecutionSnapshot(BaseModel):
    backend_id: str
    status: Literal["running", "succeeded", "failed", "cancelled"]
    output: JsonValue | None = None
    failure: JsonValue | None = None
    resumable: bool = False
    thread_id: str | None = None


class SubagentToolCallSnapshot(BaseModel):
    tool_call_id: str
    tool_name: str
    status: Literal["running", "success", "failed", "denied", "interrupted"]
    arguments: JsonValue | None = None
    result: JsonValue | None = None


class SubagentActivitySnapshot(BaseModel):
    sequence: int
    output_preview: str
    output_truncated: bool
    active_tool_calls: tuple[SubagentToolCallSnapshot, ...]
    recent_tool_calls: tuple[SubagentToolCallSnapshot, ...]
    dropped_tool_calls: int


class SubagentOperator:
    def open_child(
        self,
        context: AgentContext,
        child: BuiltSubagent,
        input: RunInputValue,
        child_instance_id: str,
        continuation: bool,
        usage_limits: UsageLimits | None,
    ) -> AbstractAsyncContextManager[RunBindings]: ...

    async def start(
        self,
        context: AgentContext,
        child: BuiltSubagent,
        input: RunInputValue,
        subagent_id: str,
        resume_from: str | None,
        usage_limits: UsageLimits | None,
        observer: SubagentBackendEventHook,
    ) -> SubagentExecutionSnapshot: ...

    async def attach(
        self,
        context: AgentContext,
        backend_id: str,
        observer: SubagentBackendEventHook,
    ) -> SubagentExecutionSnapshot | None: ...

    async def snapshot(
        self,
        context: AgentContext,
        backend_id: str,
    ) -> SubagentExecutionSnapshot | None: ...

    async def activity(
        self,
        context: AgentContext,
        backend_id: str,
    ) -> SubagentActivitySnapshot | None: ...

    async def wait(
        self,
        context: AgentContext,
        backend_id: str,
        timeout_seconds: float | None,
    ) -> SubagentExecutionSnapshot | None: ...

    async def steer(
        self,
        context: AgentContext,
        backend_id: str,
        message: str,
    ) -> str | None: ...

    async def cancel(
        self,
        context: AgentContext,
        backend_id: str,
    ) -> bool: ...

    async def force_close(self) -> None: ...
```

This is a process-local class contract, not a wire protocol. `backend_id` is the only opaque locator stored by the parent projection. The implementation and its dependencies determine where canonical state lives. A later snapshot must preserve the same backend ID; changing it fails without retargeting the compact reference.

`resume_from` identifies one prior canonical record. Resume creates a new compact parent reference and a new canonical execution; it never overwrites the prior record. The default Manager resumes from its in-memory latest complete child `HarnessState`. A custom operator decides how a real Thread continues.

`None` from attach, snapshot, or wait means the operator no longer retains that execution. The projection becomes `lost`; it never substitutes another backend, child, or Thread. `None` from `activity()` alone means that the backend does not provide live activity detail; it does not mark an otherwise observable execution lost.

## Async Parent Projection

The async Capability stores only compact references and bounded portable observations:

```python
class ManagedSubagentState(BaseModel):
    subagent_id: str
    subagent_name: str
    child_definition_id: str
    backend_id: str
    prompt: str
    status: Literal["running", "succeeded", "failed", "cancelled", "lost"]
    resumed_from: str | None = None
    failure: JsonValue | None = None
    resumable: bool = False
    thread_id: str | None = None


class SubagentManagerState(BaseModel):
    owner_thread_id: str
    next_sequence: int
    subagents: dict[str, ManagedSubagentState]
```

`subagent-N` is allocated monotonically and is unique only inside one parent Thread's `AgentContextState`. The exact delegated prompt is a portable input snapshot; it grants no authority. Status, bounded failure, resumability, and child Thread correlation are non-authoritative observations reconciled with the operator. Successful output remains only in canonical operator state and is fetched by a single-child `wait_subagent`; it never enters the portable parent projection. Bounded activity is also canonical process-local observation and is fetched only for single-execution info. State contains no activity, output, child `HarnessState`, live Thread, Session ID, task, stream, callback, credential, Environment, `RunBindings`, queue, lease, storage metadata, retry ledger, or wake record.

At parent Run entry, a mismatched `owner_thread_id`, including one produced by `HarnessState.fork()`, invalidates copied references. Otherwise the projection validates child-definition correlation and attaches the current Run observer to each nonterminal backend ID. Missing default-Manager records after restart become `lost`. `info`, `wait`, `steer`, `cancel`, and `resume` resolve only the exact compact reference and backend ID.

A successful spawn is an ordinary completed tool call, not `DeferredToolRequests`. Child completion does not satisfy the original tool-call ID. The Harness defines no generic task record, acceptance ledger, retry worker, lease, delivery ledger, or automatic parent scheduler.

## Usage

Inline execution intersects parent effective `UsageLimits`, the child `AgentSpec` baseline, edge `usage_limits`, and any operator narrowing. Numeric fields use the strictest non-`None` value, and the child shares the parent's native usage accumulator.

Async execution owns an independent child run and usage accumulator unless a custom operator deliberately maps it otherwise. The Capability supplies exact child and edge ceilings. An operator may narrow them but cannot widen authored or current Host policy. Limits constrain admitted work; they do not enable or disable tools.

## Failure and Completion

| Condition                                  | Outcome                                                      |
| ------------------------------------------ | ------------------------------------------------------------ |
| Unknown child name                         | Tool failure before operator dispatch                        |
| Invalid operator                           | Definition construction failure                              |
| Operator unavailable or denies context     | Bounded tool failure; Toolset remains unchanged              |
| Fresh child authority is invalid or reused | Dispatch stops before child stream entry                     |
| Inline child fails with complete state     | Child state advances and parent receives bounded failure     |
| Async snapshot changes backend ID          | Operation fails without retargeting the compact reference    |
| Async backend record missing               | Parent projection becomes `lost` on reconciliation           |
| Steering unsupported or execution terminal | Bounded current-state tool result or failure                 |
| Parent Run closes while async child runs   | Canonical child continues under operator ownership           |
| Default Manager or process is lost         | Restored compact reference becomes `lost`                    |
| Active enqueue fails                       | Explicit wait/info and later reconciliation remain available |
| Stable Host hook fails                     | Canonical state and other hooks remain unaffected            |

Inline completion is part of the parent tool call. Async completion is independent operator state. The default Manager publishes a terminal async snapshot only after the child stream and fresh child authority scope have both finished cleanup; a cleanup failure can therefore replace a provisional success with failure before any waiter observes it. Neither completion is durable merely because the Harness observed it; only a custom Host operator can provide durable child lifecycle.

## Ownership Summary

| Concern                                                          | Owner                                                             |
| ---------------------------------------------------------------- | ----------------------------------------------------------------- |
| Exact child topology and executable build                        | `AgentDefinition`, `SubagentDefinition`, and `SubagentCollection` |
| Execution mode and standard model-visible Toolset                | `SubagentCapability`                                              |
| Inline compact references and nested child state                 | Inline Capability projection                                      |
| Async compact references and portable parent projection          | Async Capability projection                                       |
| Fresh child authority scopes and canonical async execution       | `SubagentOperator`                                                |
| Default process-local child tasks, streams, state, and lifecycle | `SubagentManager`                                                 |
| Active-run completion enqueue                                    | Current Harness run projection                                    |
| Stable completion hook and optional parent wake                  | Operator and Host                                                 |
| Durable Session, Thread, retry, and delivery                     | Custom Host or service                                            |

## Invariants

01. `SubagentCapability` fixes one execution mode, one operator, and one Harness-owned Toolset at definition construction.
02. The Host configures or subclasses the operator and never supplies or replaces the first-party Toolset.
03. No `RunBindings` value selects inline versus async behavior or supplies subagent execution authority.
04. Inline parent state stores complete nested child `HarnessState`; async parent state never does.
05. Async state contains only Thread-scoped compact references, exact input snapshots, and bounded status observations over operator-owned canonical state; activity and successful output are never portable parent state.
06. The default `SubagentManager` is process-local and survives parent Run closure until its owner invokes `force_close()`; forced close cancels live children instead of waiting for natural completion.
07. Active-run enqueue and stable Host-hook dispatch are independent; the operator always dispatches hooks.
08. Ordinary async children receive only Capabilities in their exact definitions.
09. Tool availability is not selected by max-count, depth, task, storage, or metadata configuration.
10. The Harness does not become a generic durable job or distributed workflow framework.
