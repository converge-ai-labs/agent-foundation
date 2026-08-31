# Harness State and Resume

## Design Position

`HarnessState` is the complete portable continuation value understood by the process-local Harness. It contains only:

- one stable `thread_id` for the independently advancing message history;
- detached public Pydantic AI message history;
- detached JSON state namespaced by stable Capability ID, including model-facing continuation projections such as managed process references;
- optional portable Environment backend state under one explicit aggregate field.

It contains no executable definition, plugin object, model, Toolset, provider client, Environment mount definition, desired mount set, provider launch state, current authority, usage ledger, event log, Host execution record, lease, queue, or delivery state. A Host may persist the value or embed it in a larger durable record, but the Harness does not choose or commit a durable checkpoint.

Resume creates a new logical Harness Run with fresh `RunBindings`. State preserves Thread identity, messages, explicitly stored Capability data, and provider-defined portable data for already authorized mounts; it never restores authority, desired mounts, or a live Python resource.

```mermaid
flowchart LR
    Host -->|input, fresh bindings, optional state| Run1[Harness Run]
    Run1 -->|HarnessState candidate| Host
    Host -->|selected state and fresh bindings| Run2[New Harness Run]
```

## State Schema

```python
class CapabilityState(BaseModel):
    version: str
    data: JsonValue


class AgentContextStateSnapshot(BaseModel):
    entries: dict[str, CapabilityState]


class HarnessState(BaseModel):
    schema_version: Literal["1"]
    thread_id: str
    message_history: tuple[ModelMessage, ...] = ()
    agent_context_state: AgentContextStateSnapshot = (
        AgentContextStateSnapshot()
    )
    environment_state: EnvironmentState | None = None

    @classmethod
    def new(...) -> HarnessState: ...
```

`HarnessState` and its nested values are frozen detached envelopes. Pydantic message history is round-tripped through `ModelMessagesTypeAdapter`; Capability and Environment payload data are round-tripped through Pydantic `JsonValue`. Public accessors decode fresh copies, so mutable aliases do not cross the state boundary.

`thread_id` is a Harness-generated opaque correlation value consisting of the `thread-` prefix and 32 lowercase hexadecimal characters. `HarnessState.new()` creates a new Thread and generates its ID; direct envelope validation requires the field. Serialization, ordinary copies, exports, and resume preserve it exactly. The field is not accepted through `RunBindings`, metadata, or a run argument. `HarnessState.fork()` copies the messages and portable State payloads into a new envelope with a newly generated ID, which is the required core path for intentionally creating an independently advancing history from an existing checkpoint. Trusted plugins and Host state transformations remain able to construct complete State under the existing trust boundary; the ID is not cryptographic integrity or authority.

`schema_version` versions only the Harness envelope and is `1` for this contract. Import requires the exact supported envelope version and a valid required `thread_id`; validation never invents a replacement identity for malformed input. Each Capability entry has an independent non-blank version owned by that Capability's codec; each Environment mount entry has an independent provider-owned codec version. [Environment Integration](08-environment-integration.md#portable-environment-state) owns its schema and authority boundary.

## AgentContextState

`AgentContextState` is the run-local mutable coordinator:

```python
class AgentContextState:
    async def read[T: BaseModel](
        self,
        capability_id: str,
        state_type: type[T],
        *,
        version: str,
    ) -> T | None: ...

    async def write(
        self,
        capability_id: str,
        value: BaseModel,
        *,
        version: str,
    ) -> None: ...

    async def snapshot(
        self,
    ) -> AgentContextStateSnapshot: ...
```

A read validates the requested namespace, exact entry version, and the owning Pydantic state model. A write atomically replaces one namespace with detached JSON. A snapshot atomically copies all namespaces.

The coordinator does not maintain a Capability registry and does not reject an entry merely because no active Capability reads it in the current run. Unknown or transferred namespaces remain opaque and survive snapshotting. This permits trusted plugin handoff, optional Capability removal and reintroduction, and Host-controlled state migration without a second global codec system. A Capability accepts a namespace only by reading it through its own expected ID, version, and model.

Namespace isolation is a composition convention backed by the typed API, not a sandbox against trusted Python. A trusted plugin or Capability can intentionally replace another entry or the complete `HarnessState`; the Harness does not enforce provenance or ownership allowlists.

Dynamic Environment uses one namespace to preserve its owning Thread ID, bounded `process-N` mapping, one exact opaque operator backend ID, independent next-unread stdout and stderr offsets, monotonic allocation sequence, and last observed status. This state is neither an Environment backend snapshot nor process truth. It cannot recreate, enumerate, or authorize a Host or provider process. On a later Run, the Capability's configured background-capable `ShellOperator` canonically rebinds the stored selector or marks it lost; [Environment Integration](08-environment-integration.md#command-and-background-process-operators) owns matching, output drain, observation, and Host continuity semantics.

Async `SubagentCapability` uses a separate namespace to preserve the current parent Thread ID, bounded `subagent-N` mapping, exact child name and definition ID, delegated prompt, one opaque operator backend ID, observed status, bounded failure, resumability, and optional real Thread correlation. Successful child output and bounded recent activity remain only in canonical operator state and never enter the portable parent projection. Single-execution `subagent_info` fetches current activity detail; a single-child `wait_subagent` fetches complete output and the normal managed-tool boundary spills oversized output to a run-private file. The namespace stores no activity, output, child `HarnessState`, and is not a Session child, scheduler record, task store, or durable execution. A later parent Run uses the same configured `SubagentOperator` to attach its current observer to each nonterminal selector, collect a canonical terminal projection, or mark it lost. Completion after a parent Run closes never mutates the already exported parent state; stable operator hooks may independently wake a Host. [Delegation and Subagents](11-delegation-and-subagents.md#async-parent-projection) owns the manager lifecycle. Inline mode separately stores complete nested child `HarnessState` under its own documented state model.

## Export

`AgentContext.export_state(message_history)` preserves `AgentContext.thread_id` and combines it with a detached message sequence, the current `AgentContextState` snapshot, and a fresh aggregate Environment export:

```python
async def export_state(
    self,
    message_history: Sequence[ModelMessage],
) -> HarnessState: ...
```

The method can await provider-defined portable Environment-state collection but performs no persistence side effect. It captures the current mount set under the aggregate operation fence, so the Environment value contains entries from one complete mount-set observation rather than a mixture of states before and after a mutation. The Environment aggregate validates canonical JSON and preserves provider permissions and bounded operation deadlines; timeout, cancellation, provider failure, or invalid JSON fails the complete export rather than silently dropping a mount. The Host owns capacity admission for input state and exported results before persistence. `HarnessRunStream.export_state()` selects the latest complete message view owned by the stream and delegates to this method.

State export does not require `HarnessState.message_history` to equal a result object's private message view. Normal inner execution produces aligned values, but trusted result middleware may intentionally transfer or replace state. Structural validity is enforced; semantic provenance is part of the trusted plugin contract. A plugin that replaces `environment_state` remains trusted code but cannot make the value authorize or construct a binding on resume.

## Complete Message Boundaries

The Harness stores only public Pydantic `ModelMessage` values. It never serializes raw stream deltas or private graph nodes. When a streamed model response is interrupted, the Harness uses public part lifecycle events to derive one explicitly interrupted response containing only parts that are safe to replay; it never marks an unfinished part complete.

| Time                             | Exported messages                                                              |
| -------------------------------- | ------------------------------------------------------------------------------ |
| Before first `ModelAttempt`      | Imported message history                                                       |
| During model or tool work        | Latest complete public message view exposed by `AgentRunEvents`                |
| Between `ModelAttempt` values    | Normalized interrupted history used by the next `ModelAttempt`                 |
| At completion or deferred output | `AgentRunResult.all_messages()`                                                |
| After normalized cancellation    | Complete messages supplied by `RunCancelled` or the latest observable boundary |

A new semantic continuation prompt is input to the next `ModelAttempt`, not retroactively inserted into an earlier completed message.

## Interrupted History Normalization

Recovery normalizes only a terminal message explicitly marked `state="interrupted"`.

Before tool-call closure, the Harness observes `PartStartEvent`, `PartDeltaEvent`, and `PartEndEvent` values for the current model response and applies these replay rules. Observations are scoped to the exact response boundary established by the preceding public message count; part indices are local to a response and never identify a boundary. If the raw interrupted tail has no lifecycle event observed for that exact response, the Harness discards the complete tail rather than trusting unobserved internal parts. A tool-return part that is atomic and has no delta form is complete at `PartStartEvent`; streamed tool-call parts still require `PartEndEvent`.

| Interrupted response part                        | Replay rule                                                                             |
| ------------------------------------------------ | --------------------------------------------------------------------------------------- |
| Non-empty `TextPart`                             | Retain all append-only text already emitted, whether or not the part ended              |
| `ThinkingPart`                                   | Retain only after `PartEndEvent`; preserve the finalized content and provider signature |
| Unfinished `ThinkingPart`                        | Exclude without discarding surrounding safe text or finalized parts                     |
| Finalized ordinary tool-call or tool-return part | Retain with its complete identity and payload                                           |
| Matched finalized native call/return pair        | Retain one call followed by one same-name return for one unique provider-native ID      |
| Unmatched native call or return                  | Reject the complete partial response; no portable synthetic native result is invented   |
| Unfinished tool-call or tool-return part         | Reject the complete partial response and fall back to the preceding canonical history   |
| Other unfinished or unsupported response part    | Do not reconstruct it into continuation history                                         |

The finalization observation is process-local and is applied before state export. It is not inferred later from text, provider metadata, or the presence of a signature. Consequently, a Host that selects a Harness-produced state can replay partial visible text and finalized reasoning while never replaying an unfinished thinking block. Raw live events remain non-authoritative observations and may already have been displayed; the Harness cannot retract them.

After that filtering, if the tail is an interrupted `ModelResponse`, the Harness appends one `ModelRequest` containing failed `ToolReturnPart` values for finalized ordinary tool calls that have no recorded result. Provider-native calls and returns must already form strict one-to-one pairs inside the response: each unique `tool_call_id` identifies exactly one call followed later by exactly one return with the same `tool_name`. A duplicate ID, return before its call, name mismatch, or otherwise unmatched native part causes the interrupted response to be discarded because the Harness has no portable synthetic native-result representation. If the tail is an interrupted `ModelRequest`, the Harness finds the preceding response and appends missing failed tool returns to that request. Existing `ToolReturnPart` and `RetryPromptPart` results remain authoritative and are not duplicated.

Each synthesized failed result says:

> No tool result was recorded because execution was interrupted. The operation may have partially or fully completed. Check the current state before deciding whether to retry it.

This transformation closes the public conversation shape. It does not claim that the external operation failed, did not execute, rolled back, or is safe to repeat.

No interrupted-history normalization occurs for ordinary complete history or for a provider-suspended response. Provider-suspended continuation remains native Pydantic behavior.

## System Prompt Reconciliation

The current Harness `AgentSpec` owns the complete ordered static system prompt. `HarnessState` retains public Pydantic messages, including the system-prompt parts materialized for the definition that produced its selected checkpoint, but those historical parts do not override the definition selected for a later model request.

Before passing non-empty imported history into a new Pydantic model request, the Harness creates a detached canonical history projection. It removes every `SystemPromptPart` from every `ModelRequest`, then inserts the current definition's normalized system-prompt blocks at the beginning of the first request. A definition with no system prompt removes historical blocks without replacement. All non-system parts, request instructions, metadata, timestamps, responses, and ordering remain unchanged. The normalized messages become the Pydantic history for the run, so a successful exported checkpoint carries the current definition's prompt rather than requiring a permanent model-bound overlay.

Empty history uses Pydantic AI's native `Agent.from_spec(system_prompt=...)` construction path. System-prompt reconciliation does not reinterpret or merge `ModelRequest.instructions`: static and dynamic instructions retain their native per-request lifecycle.

A provider-suspended response is continuation of an already issued model request rather than a new request. The Host must resume that response with its compatible definition and provider integration; a changed system prompt takes effect only on a later new model request. The Harness does not rewrite a provider-suspended request in place.

## Import and Resume

A new run receives `previous_state` separately from fresh `RunBindings`. Stream construction deep-copies the supplied state. Entry then:

01. enters the new Environment runtime from fresh Host authority and atomically publishes its initial mount set while the runtime remains non-active;
02. restores a present `environment_state` only into compatible, already selected mounts;
03. enters ordered Environment run extensions after successful restore or confirmation that no Environment state was supplied;
04. activates the runtime after every extension enters successfully;
05. invokes the optional `RunInputFactory` against that entered Environment;
06. restores the selected `thread_id` into a read-only field on the fresh `AgentContext`;
07. creates one `AgentContextState` initialized from the imported Capability snapshot;
08. creates the remaining fresh `AgentContext` dependencies and plugin graph;
09. reconciles non-empty imported history with the current definition-owned system prompt before a new model request, while leaving provider-suspended continuation unchanged;
10. passes the resulting messages to the first `ModelAttempt`;
11. lets each Capability read and validate only the namespaces it understands.

Environment restore and ordered run-extension entry finish before runtime activation and input production, never overlap a mount mutation, and never create a mount, select desired mounts, consume Host launch state, or grant access. An unmatched saved mount is ignored with a bounded diagnostic; an incompatible selected mount fails according to the Environment codec contract. The Harness does not require every Capability entry to be consumed before model work. A stateful Capability that requires validation before its own behavior must perform that validation in its Pydantic lifecycle or before invoking the dependent operation.

Identity, policy, credentials, model resolution, Environment authority, desired mounts, tool grants, provider sessions, and Host ownership always come from fresh trusted bindings. Message metadata, Thread identity, Capability state, and portable Environment state grant none of them.

State restores both the independently advancing message history and its provider-neutral `thread_id`; it does not carry a provider session, route, credential, or prompt-cache setting. The fresh model integration reads the restored ID from `AgentContext` and derives or restores matching provider affinity under [the Thread-affinity contract](16-input-model-and-output.md#thread-affinity). If a provider uses an additional opaque selector that cannot be derived, the Host retains it beside the matching `HarnessState`; transient Harness and model-attempt IDs never replace the State-owned key.

Omitting new input is valid when the selected message history is sufficient for native Pydantic continuation. Supplying deferred tool results uses Pydantic AI's own input contract and the exact pending call or approval correlation owned by the integrating Host.

## Host Durable Envelope

A Host can store additional facts beside `HarnessState`:

```python
class HostExecutionState(BaseModel):
    harness: HarnessState
    definition_revision_ref: str
    launch: HostLaunchState
    pending_delivery: HostDeliveryState | None
```

This is an ownership illustration, not a Harness API. Definition selection, desired Environment mount definitions, `ExecutionAttempt` generation, artifact locks, provider provisioning and attachment, provider launch-state codecs, client-tool pending state, asynchronous child lifecycle, and delivery fencing remain Host-owned. `HostLaunchState` is separate from `HarnessState.environment_state`: the former makes a provider resource reachable, while the latter can restore only portable backend-local data after fresh reachability and authority already exist.

The Harness does not define or require a generic provider route pin. Provider-specific continuation facts that cannot be derived from `thread_id` and public Pydantic messages, including an opaque model-session selector, belong to the selected model integration or Host envelope rather than the Harness schema. A broader product-conversation routing key does not replace the distinct prompt-cache affinity required for each independently advancing Agent message history.

## External Effects

State export records observations; it does not make a side effect exactly once. An operation with no authoritative result remains unknown. Resume policy must inspect provider or Environment state before repeating a side-effecting action unless the provider offers a matching idempotency or reconciliation contract.

## Compatibility

Four compatibility axes remain independent:

| Axis                            | Owner                |
| ------------------------------- | -------------------- |
| Harness envelope version        | Harness              |
| Pydantic message codec          | Pydantic AI          |
| Capability entry version        | Owning Capability    |
| Environment mount-state version | Environment provider |

Invalid messages, unsupported envelope versions, blank namespace IDs or versions, and invalid Capability or Environment payloads fail without mutating the supplied value. A Host that changes process-local Agent composition or provider integration decides whether to retain, migrate, or remove incompatible opaque data before resume. Definition-owned system-prompt replacement is the explicit exception for a new model request: the Harness reconciles public prompt parts from the current definition without treating them as opaque Capability or provider state.

## Boundaries

| Concern                                            | Owner                          |
| -------------------------------------------------- | ------------------------------ |
| Envelope, detached encoding, and state coordinator | Harness                        |
| One Capability namespace and semantic migration    | Owning Capability              |
| Environment aggregate and per-mount codec          | Harness core and provider      |
| Trusted complete-state transformation              | Harness plugin or Host adapter |
| Durable selection, launch, retention, and fencing  | Host                           |
| External effect reconciliation                     | Provider and Host              |

## Trade-offs

### Opaque Namespace Preservation vs. Active-set Validation

Preserving unknown namespaces supports code-first composition, plugin handoff, and independent Capability evolution. The Harness cannot claim that every stored entry was produced or accepted by the current Agent; only the owning typed read establishes that fact.

### Portable Continuation vs. Durable Recovery

Messages, Capability JSON, and optional portable Environment JSON remain process-portable. Complete crash recovery still needs the Host definition, desired mount definitions, provider launch, pending-delivery, and reconciliation state outside the Harness.
