# Delegation and Subagents

## Design Position

Subagents are complete child Agent definitions built into an immutable `SubagentCollection`. The collection is authority-neutral topology: it names the immediate children of one `ExecutableAgent` without selecting a model-visible presentation, scheduler, storage system, Environment location, or child runtime authority.

The first-party `SubagentCapability` selects one of two mutually exclusive standard surfaces:

```python
class SubagentCapability(AbstractCapability[AgentContext]):
    def __init__(
        self,
        *,
        async_enabled: bool = False,
        operator: SubagentOperator | None = None,
    ) -> None: ...
```

- The default inline mode requires no Host operator. Harness recursively executes the child inside the parent Run's lifetime and retains continuation only in parent Agent state.
- Async mode requires an explicit Host-owned `SubagentOperator`. Harness exposes standard asynchronous tools but owns no scheduler, execution registry, child store, wake mechanism, or background task.

Construction rejects async mode without an operator and rejects an operator in inline mode. There is no fallback between modes, per-call lifecycle-mode argument, default async manager, or Harness execution-store protocol.

The Capability owns standard tool names, schemas, guidance, authored child resolution, context and identity ceilings, usage-limit intersection, and bounded model-facing projections. The Host operator owns every complete asynchronous use case and every lifecycle fact behind it. This boundary is part of the [Hosting Contract](13-hosting-contract.md); cross-Run consequences follow [Async Subagent Lifecycle](20-async-components-and-lifecycle.md).

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
    run_bindings_factory: Callable[[RunBindings], RunBindings] | None = None


@dataclass(frozen=True, slots=True)
class BuiltSubagent:
    declaration: SubagentDefinition
    definition: AgentDefinition[Any]
    executable: ExecutableAgent[Any]


class SubagentCollection(Mapping[str, BuiltSubagent]):
    def require(self, name: str) -> BuiltSubagent: ...
```

Harness validates the finite recursive definition graph and builds children before their parent. Each child owns its complete Model, prompt, output contract, Capabilities, plugins, recovery policy, Environment requirements, and nested children. Every parent `AgentContext` receives the exact immutable immediate collection from its executable.

`run_bindings_factory` is an optional trusted, process-local function from baseline child `RunBindings` to completed child `RunBindings`. Harness invokes it once for each child invocation, including a resumed continuation. The returned value must preserve the baseline child instance, borrowed Environment, and inherited invocation-policy Capability. The baseline carries inherited policy, model resolution, metadata, and the tool result directory for its borrowed Environment, but not the parent's feature providers, skill selection, or client tool overrides. Each child still owns and cleans its own Run-private spill leaves. Explicit task-state sharing supplies a borrowed cell and rejects a conflicting factory `task_state`. The factory owns no entered resources; the Host owns collaborator cleanup. The callable is never serialized into State and grants no additional authority. Hosts reconstruct it from their accepted child configuration. Async child execution binds its resources through its own Host Run.

The collection contains no current Identity, credential, State, Environment adapter, entered facade, scheduler, task, execution mode, backend selector, or Host record. A child executable is reusable process-local build output and is never serialized as durable payload. A distributed Host persists its own reconstructable definition reference and rebuilds the exact trusted definition on the selected worker.

A hosted source schema may support references or authoring inheritance, but trusted resolution produces the same complete finite child definitions before Harness build. Harness does not resolve Host resource keys, clone a live parent, infer child Environment associations, or consult a mutable parent definition during execution.

## Standard Tool Surfaces

Inline mode exposes exactly two standard tools:

- `delegate(subagent, prompt)` creates a new inline child continuation, waits for a complete child result, and returns its bounded output plus an `execution_id` local to parent state;
- `resume_subagent(execution_id, prompt)` advances one retained compatible inline continuation and waits for its complete result.

Async mode exposes exactly six standard tools:

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

- async `delegate` returns after Host admission rather than child completion;
- `subagent_info` queries one execution or one bounded Host page;
- `wait_subagent` performs one bounded wait for one execution or one bounded Host fan-in query;
- `steer_subagent` and `cancel_subagent` return Host acknowledgements without inventing completion;
- async `resume_subagent` first resolves the retained execution through the operator, requires resumability and the same stable child roster name in the current collection, then asks the Host to create a linked continuation under the current resolved plan.

The two surfaces use distinct internal Toolset identities and never register duplicate external names in one Agent. Tool descriptions state whether `delegate` blocks for completion or returns after admission. `timeout_seconds` bounds one wait call and never implies cancellation.

An async `execution_id` is a bounded public Host execution reference. It is not a provider resource ID, credential, Environment reference, or necessarily a Harness-generated value. Standard results never expose Host-private storage IDs, raw child state, raw business output, credentials, or native exceptions. A single-execution info and wait return the same bounded execution view; no-ID list and fan-in return bounded summaries. Harness output policy still bounds the serialized view.

## Harness-Resolved Async Authority

Before async admission or resume, Harness resolves the authored child and constructs a detached immutable plan:

```python
@dataclass(frozen=True, slots=True)
class SubagentOperatorContext:
    parent_thread_id: str
    parent_run_id: str
    parent_agent_instance_id: str
    host_refs: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class SubagentToolCallContext:
    tool_call_id: str | None
    tool_name: str | None


@dataclass(frozen=True, slots=True)
class ResolvedDelegationContext:
    input: RunInputValue
    policy: DelegationContextPolicy


@dataclass(frozen=True, slots=True)
class SubagentDelegationPlan:
    child: BuiltSubagent
    child_identity: AgentIdentityRef
    context: ResolvedDelegationContext
    usage_limits: UsageLimits | None
    parent: SubagentOperatorContext
```

The plan contains the exact currently built roster child, derived child Identity, already-applied child input and context policy, intersection of parent Run limits, child definition limits, and authored edge limits, plus detached parent correlation. Initial `delegate` executes that exact child; the Host may narrow policy but cannot substitute another definition.

On async resume, the current roster child can differ from the definition recorded by the prior execution. A Host that exposes a separately authorized mutable child-Thread configuration may resolve that retained Thread's current Agent definition instead of the roster child's definition. That selection comes only from Host authority, never from model arguments, and the stable roster name remains the parent-side admission gate.

The replacement uses the plan's already-resolved child input and context policy. The Host reapplies the current roster edge's `SubagentIdentityPolicy` to the replacement definition: inherited `agent_id` remains inherited, while a definition-derived `agent_id` names the replacement definition. Final usage limits intersect the plan ceiling with the replacement definition's own limits. The Host can narrow these values further but cannot broaden them. It validates checkpoint-schema compatibility and current authorization, then returns the replacement definition ID in `AsyncExecutionView`. A Host without mutable child definitions resumes with the exact current roster child.

Neither the plan nor operator context contains a live `AgentContext`, `RunContext`, entered `BoundEnvironment`, mutable parent state coordinator, model client, credential, or Run-scoped callback. Accepted async work therefore does not retain parent Run authority after parent closure. `host_refs` are immutable correlation selected by the Host; they are not authorization by themselves.

Each standard async tool also projects its originating Pydantic tool-call correlation into a detached `SubagentToolCallContext` supplied separately to the operator method. Harness forwards the available `tool_call_id` and assembled `tool_name` without generating an idempotency key, defining a replay scope, or assigning persistence meaning. An operator may combine that correlation with the plan, request, and its own Host authority to implement idempotency or diagnostics. A missing call ID remains `None`; Harness does not synthesize one. The correlation grants no authority and does not become parent or child state.

`BuiltSubagent` is a process-local invocation reference. A durable or remote operator persists its own exact definition revision and reconstructs the authorized child rather than serializing the executable object.

## Host Operator Contract

`SubagentOperator` is the complete asynchronous use-case boundary:

```python
class SubagentOperator(ABC):
    async def delegate(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncDelegateRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> AsyncExecutionView: ...

    async def info(
        self,
        context: SubagentOperatorContext,
        request: SubagentInfoRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentInfoResult: ...

    async def wait(
        self,
        context: SubagentOperatorContext,
        request: SubagentWaitRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentWaitResult: ...

    async def steer(
        self,
        context: SubagentOperatorContext,
        request: SubagentSteerRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentSteerResult: ...

    async def cancel(
        self,
        context: SubagentOperatorContext,
        request: SubagentCancelRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentCancelResult: ...

    async def resume(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncResumeRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> AsyncExecutionView: ...
```

Each standard tool validates its bounded request, invokes the corresponding complete Host use case, validates the exact standard result type and addressed execution, and applies Harness output policy. Delegate results must identify the plan child definition. Resume results may identify the Host-authorized replacement definition described above; they still require the exact stable roster name and `resumed_from` correlation. Harness does not decompose the operator into `open_child`, task-start, observer-attach, checkpoint-store, or forced-cleanup callbacks.

The operator owns:

- async execution and child Thread IDs;
- retained-checkpoint lookup, current authorization, optional Host-managed child-definition selection, and compatibility for linked resume across child-definition changes;
- admission, scheduling, recursive Harness invocation, and runtime location;
- Environment association selection, current state loading, and fresh adapter construction;
- child `RunBindings`, Host credentials, provider runtimes, and Host metadata within the plan ceilings, including replacement Identity and usage-limit derivation;
- execution storage, checkpoint acknowledgement, and bounded activity projection;
- wait and wake behavior, steering, cancellation, linked resume, loss, cleanup, and retention;
- Host shutdown policy and any background task or worker lifecycle.

Harness does not require a storage interface. An operator can use memory, files, SQLite, PostgreSQL, a workflow engine, or remote workers without adapting its persistence to a Harness-owned begin/append/commit protocol.

The operator instance is Host-owned. Closing one parent Run does not close the operator, cancel accepted children, detach Host observers, or invoke a global forced shutdown method. A Host performs operator shutdown and scoped cancellation under its own owner lifecycle.

## Inline Execution

The built-in inline executor is Harness-private and is not a `SubagentOperator`. It performs this sequence inside one model tool call:

1. resolve one exact immediate child;
2. derive child Identity and intersect usage limits;
3. apply the authored context policy to produce child input;
4. create fresh child instance lineage under the current parent instance;
5. borrow the parent's already-entered provider-neutral Environment mapping;
6. recursively execute the child and forward canonical child events;
7. store the latest complete child `HarnessState` before returning success;
8. return only a JSON-compatible bounded projection.

Inline continuation state is:

```python
class InlineSubagentState(BaseModel):
    child_instance_id: str
    subagent_name: str
    child_definition_id: str
    state: HarnessState


class InlineSubagentCollectionState(BaseModel):
    children: dict[str, InlineSubagentState]
```

A compact inline ID has the form `{subagent_name}-{suffix}` and is unique only inside that parent state. It is not a Host execution ID, child Thread ID, backend ID, or authority token. A new ID starts a new child Thread. Resume requires an exact stored ID, child name, and child definition ID; incompatible restored state fails before execution.

The child receives a fresh `AgentInstanceContext` whose parent and delegation references match the current parent instance and inline ID. Harness borrows only explicit safe Run inputs needed by nested execution, including the current invocation policy, model resolver, tool-instruction override, metadata, model-cost accounting, and an explicitly shared embedded task view when the authored task policy permits it. It does not copy arbitrary parent Run Capabilities.

### Borrowed Environment Scope

Inline execution uses the exact active parent `BoundEnvironment` mapping rather than constructing or re-entering provider adapters. Its private borrowed runtime is single-use and denies mount, replacement, unmount, and default-selection mutation. Child completion, failure, cancellation, or state-export failure does not close the parent adapters.

The child can use ordinary Environment operations through the borrowed facade while the parent remains active, but it cannot publish that facade as an independently owned Host Environment. Inline child continuation therefore stores no `environment_states`; the Host-authoritative parent Environment mapping remains owned and exported by the parent Run.

Calls for different inline IDs may run concurrently. Competing calls for the same ID are rejected while one advance is active. Parent cancellation cancels the nested child stack. Inline execution disables deferred tools and does not suspend the parent stack or retain pending feedback. Unexpected terminal deferral returns a bounded tool failure. An inline advance becomes durable only when the Host selects a complete parent checkpoint containing it.

### Host-Owned Deferred Support

`RunBindings.deferred_tools_supported` defaults to `True` and selects current Host support independently of instance lineage. A Host that cannot collect deferred feedback sets it to `False`. The tool-surface and runtime denial rules belong to [Tool Execution](07-tool-execution.md).

The built-in inline executor always sets this flag to `False`, even when the parent supports deferred tools. An authored child bindings factory cannot enable it. Inline state contains child checkpoints, not pending-request envelopes or trusted result submissions. Ordinary `resume_subagent(execution_id, prompt)` remains prompt continuation, not a deferred-feedback API. Harness provides no parent-child deferred bridge or scheduler.

A Host-managed child can use native `ExecutableAgent.run()` or `stream()` with deferred support enabled. Harness builds every child with both its business output and native `DeferredToolRequests` support; the authored business output must not include that reserved type. A suspended `HarnessRunResult` contains the exact pending requests and complete checkpoint, not a completed business output. The Host owns their retention, feedback correlation, authorization, scheduling, and a later direct child invocation with fresh bindings, `previous_state`, and `DeferredToolResume`. It must not infer a resumable pending batch from observation events alone. Returning native requests as ordinary delegate-tool output does not suspend the parent.

### Child Usage Limits

Hosts obtain each built child's definition baseline through `ExecutableAgent.definition_usage_limits()`. Its detached-copy semantics are defined in [Execution Context and Lifecycle](06-execution-context-and-lifecycle.md#usage-limits-and-native-retries).

Harness intersects every non-`None` ceiling from the current roster child Agent definition and the authored child edge. The parent's own usage limits and accumulator do not constrain children: each child Run has an independent usage accumulator, shared only by its own ModelAttempts. Each numeric field uses the smallest present value, and `count_tokens_before_request` is enabled when any contributing limit enables it. Inline execution passes the result directly to the nested child. Async execution places the same detached ceiling in `SubagentDelegationPlan`; when a Host-authorized resume replacement is selected, its definition limits are intersected once more. The Host can narrow but not broaden the result.

## Context, Identity, and State Authority

`derive_child_identity()` starts from the parent Identity, preserves `issuer`, `subject`, and string claims, and applies `SubagentIdentityPolicy.inherit_agent_id`:

- `False` selects the child definition ID as the `agent_id` claim;
- `True` retains the parent's existing `agent_id` claim and synthesizes none when absent.

The helper establishes a deterministic ceiling, not complete authorization. Inline Harness execution creates fresh instance lineage. An async Host constructs complete fresh child authority from the authorized plan and its own trusted resources. State never restores Identity or authority.

```python
@dataclass(frozen=True, slots=True)
class DelegationContextPolicy:
    include_task: bool = True
    history: Literal["none", "summary", "selected"] = "none"
    task_state: Literal["shared", "isolated"] = "shared"
```

Harness applies the exact edge policy to child input. Selected history uses canonical message encoding. Summary history reads the accepted handoff or compaction summary from canonical history when available; retained user inputs and restoration protocol text are not summaries. Working-State sharing follows [Context and Working State](09-context-and-memory.md). Inline execution can isolate an embedded child task scope or borrow the active embedded parent task scope under a fresh child owner; provider-mode child tasks require Host-owned async execution because Harness has no generic provider attachment factory. All non-task Capability state remains isolated unless another owning contract explicitly defines inheritance.

Parent `AgentContextState` is authoritative only for inline continuation. Async status, bounded closed activity, and resumability are queried from the Host operator and are not mirrored into parent Harness state. Async child `HarnessState` belongs to the Host's child Thread checkpoint.

## Observation and Completion

Inline execution forwards child Harness events with their original child Run and Thread provenance into the parent stream, then emits parent-scoped delegation completion or failure. Each Run reports its own usage, excluding child usage. Hosts may aggregate the attributed parent and child usage records for display without applying a shared tree-wide enforcement budget.

For async work, the Host owns observation storage, subscriptions, wake policy, and activity compaction. The operator consumes each child `HarnessRunStream` as the narrow ordered public-recording boundary; Harness emits only public `HarnessStreamEvent` values and imports no AG-UI or Host presentation type. Standard result models provide bounded status and closed activity projections when the operator returns them. Activity contains no separate raw `output`; a final answer is represented by closed text activity. Harness does not retain a weak observer, enqueue completion into a later parent Run, or infer current status from an old parent projection. Host wake and protocol delivery are separate from execution authority as defined by [Observation](19-observation-model.md) and the [Agent Stream Protocol](../a13n-stream-protocol/README.md).

## Failure and Cancellation Semantics

| Condition                                      | Inline mode                                                              | Async Host operator                                                          |
| ---------------------------------------------- | ------------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| Unknown child                                  | Tool failure before child execution                                      | Tool failure before Host admission                                           |
| Invalid context, Identity, or limit resolution | Tool failure before child execution                                      | Tool failure before Host admission                                           |
| Child dispatch or execution failure            | Preserve the latest complete retained state and return bounded failure   | Host records and projects its authoritative accepted outcome                 |
| Child completion                               | Store complete child state before tool success                           | Host reports success according to its own checkpoint acknowledgement         |
| Child suspension                               | Unsupported; unexpected terminal deferral becomes a bounded tool failure | Supporting Host retains exact requests and checkpoint for native resume      |
| Parent cancellation                            | Cancels the nested child stack                                           | Does not imply child cancellation                                            |
| Operator unavailable                           | Not applicable                                                           | Async configuration or operation fails; no inline fallback                   |
| Wait timeout                                   | Not applicable                                                           | One Host wait result; child remains active unless Host status says otherwise |
| Steer or cancel race                           | Not applicable                                                           | Operator acknowledgement is authoritative; Harness invents no terminal state |
| Host process or worker loss                    | Parent and inline child are lost together                                | Host defines loss, recovery, and retention                                   |
| Parent Run close                               | Child is already complete or cancelled with the stack                    | Does not close operator or accepted execution                                |
| Child definition changed before resume         | Exact stored definition ID is required                                   | Current roster name resolves; Host validates checkpoint compatibility        |

## Invariants

01. `SubagentCapability()` provides inline delegate and resume without a Host scheduler or store.
02. Async mode requires an explicit `SubagentOperator` and never falls back to inline execution.
03. Inline and async standard Toolsets are mutually exclusive.
04. Harness resolves the current roster child, context, Identity policy, and usage ceilings before Host admission; an authorized async resume replacement must derive its own definition identity and intersect its own limits within those ceilings.
05. Operator inputs contain detached correlation, never live parent Run or Environment authority.
06. Inline continuation is stored only under the Subagent Capability namespace in parent Agent state.
07. Async child state and current execution status remain Host-owned and are not projected into parent Harness state.
08. Inline Environment borrowing cannot mutate mounts, close parent adapters, or publish independent Environment state.
09. Parent Run closure does not cancel or force-close Host-owned async work.
10. Async linked resume does not require equality with the prior child definition ID; the Host owns checkpoint compatibility, current authorization, and any retained child-definition selection.
11. Harness contains no concrete async manager, execution-store lifecycle, background child registry, or Host shutdown policy.
