# Durable Run State

## Design Position

| Dimension               | Core question                              | Foundation choice                                                                                                                                                                                                                                     |
| ----------------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Identity and boundary   | When are Run and RunAttempt created?       | Accepting a new Agent request creates a Run. Starting or restarting a worker for that same accepted request creates a `RunAttempt` under the existing Run; its input, parent, selections, and state key do not change                                 |
| Logical history         | How do Runs form history?                  | `parent_run_id` forms a Git-like DAG; the independent Thread row selects current and continuation-head Runs, continue preserves Thread identity, and fork creates a new Thread                                                                        |
| Persistence             | Where is a Run persisted?                  | Run metadata lives in the relational database; resumable state and large Run inputs and outputs live in object storage                                                                                                                                |
| Stored data             | What does a Run persist?                   | The Run row holds metadata and object references; `RunStateEnvelope` holds immutable Environment execution configuration, effective Skill selection, and Harness and Host continuation state; `RunPayloadEnvelope` holds large Run inputs and outputs |
| State advancement       | Which service instance can commit updates? | At most one worker service instance is lease-authorized through the current fenced `RunAttempt`; only its relational and conditional object writes can commit, while stale or partitioned instances are rejected                                      |
| Completion and recovery | How do checkpoint, seal, and resume work?  | Checkpoint publication conditionally overwrites the object at the same key; relational sealing selects the exact candidate digest; recovery reads the complete checkpoint from the latest conditionally committed version of that same state object   |

Foundation Service persists each accepted Thread advancement as one relational `Run` row. The Run is the durable Agent-work, scheduling, recovery, and Git-like history boundary; it owns the parent edge, input, exact selections, finite recovery budget, lifecycle, and sealed outcome.

[Durable Thread Persistence](24-thread-persistence.md) separately owns the versioned Thread row. Run acceptance atomically creates or advances that row, and Run outcome commit verifies that the sealing Run remains current and either selects a new continuation head or preserves the prior head. A Run row does not infer Thread existence, current selection, or head state by timestamp.

Each Run also owns one complete state object at a tenant- and Run-derived key. The control plane initializes it from a new root state or the selected parent's frozen state. The current fenced `RunAttempt` conditionally replaces it at complete Harness state boundaries, and sealing makes it immutable. Foundation stores no separate `base_state`, `result_state`, or selectable checkpoint history.

Worker recovery can resume the same Run from its latest valid state object; `continue` and `fork` instead initialize a new Run from frozen parent state. The [Run Attempt contract](15-run-attempt-persistence.md#run-and-runattempt-allocation-boundary) owns the complete identity-allocation boundary. No state write for the new Run mutates the parent.

This is a Foundation Host policy above the Harness state API. Harness exports complete detached state but does not select or authorize a durable recovery point; Foundation selects only the conditionally committed value at the Run's deterministic state key.

The shared [interaction model](../interaction-model.md) owns `Session`, `Thread`, `Run`, and `Item` meaning. Every Foundation-managed Agent invocation, including schedules, webhooks, and asynchronous children, accepts a Run; non-Agent maintenance uses its owning domain's work model. [`RunAttempt`](15-run-attempt-persistence.md) remains a subordinate worker generation, and Foundation defines no generic `Execution` resource.

## Boundaries

| Concern                                                                     | Owner                                                                                      | Contract                                                                                               |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| Session, Thread, Run, and Item meaning                                      | [Platform Interaction Model](../interaction-model.md)                                      | Defines public identity and relationships                                                              |
| Thread row, Session membership, version, current Run, and continuation head | [Durable Thread Persistence](24-thread-persistence.md)                                     | Serializes accepted advancement and selects the exact resumable history head                           |
| Portable messages, Capability namespaces, Environment data, and Thread ID   | [Harness State](../agent-harness/10-snapshot-and-resume.md)                                | Supplies detached state without Host authority                                                         |
| Run row, parent edge, state selection, and outcome                          | Foundation Run domain                                                                      | Forms the authoritative interaction history and Run-level recovery boundary                            |
| Connector selections and accepted Trigger source                            | [Connectors, Connections, and Triggers](23-connectors-connections-and-triggers.md)         | Defines the exact Connector-owned facts frozen at Run acceptance                                       |
| Environment selection and execution configuration                           | [Environment Management](19-environment-management.md#environment-selection-and-run-state) | Freezes exact connection targets, connector locks, Secret references, and routing in state             |
| Effective managed Skill selection                                           | [Foundation Skill Management](27-skill-management.md#agentpresetversion-selection)         | Resolves an optional invocation override within the AgentPresetVersion catalog and freezes it in state |
| Scheduling, recovery budget, and current-attempt selection                  | Foundation Run domain                                                                      | Authorizes initial dispatch, bounded recovery, and one sealed outcome                                  |
| Worker generation, lease, and stale-writer fencing                          | [Run Attempt Persistence](15-run-attempt-persistence.md)                                   | Authorizes one worker generation and preserves its immutable attempt audit                             |
| Current complete Run state                                                  | One deterministic Run state object                                                         | Stores active Harness and Host state; waiting or completed sealing selects its exact frozen identity   |
| Object storage operations                                                   | [Object storage](03-storage.md#object-storage)                                             | Supplies atomic whole-object publication and expected-version replacement                              |
| Lifecycle events, stream messages, and Items                                | [Lifecycle and Stream Persistence](17-lifecycle-and-stream-persistence.md)                 | Stores ordered facts, transports live observations, and retains presentation projections               |
| Thread inbox entries and active steering                                    | [Agent Control: Active Execution](35-agent-control-active-execution.md)                    | Persists accepted active-Run input and couples consumption to one complete state checkpoint            |
| Pending calls and approvals                                                 | Waiting Run plus its frozen Run state                                                      | Stores a bounded relational summary and the complete deferred value without a separate table           |
| Unresolved Agent tool calls                                                 | `RunAttempt` dispatch summary plus fresh Host model context                                | Preserves `unknown_outcome` without replaying the call or blocking semantic resume                     |
| Credentials and invocation authority                                        | Foundation Secret and policy boundaries                                                    | Resolves fresh authority; plaintext credentials never enter Run state                                  |

## Durable Run Model

The following Python-like schema is conceptual. It defines durable field meaning rather than a public wire representation or concrete ORM class.

```python
type RunLineageKind = Literal["root", "continue", "fork"]
type RunInputKind = Literal["agent_input", "waiting_feedback"]
type RunStatus = Literal[
    "accepted",
    "running",
    "waiting",
    "completed",
    "failed",
    "cancelled",
]
type RunWaitReason = Literal[
    "approval",
    "client_tool",
    "user_input",
    "child_result",
    "provider_continuation",
    "multiple",
]
type PendingCallKind = Literal[
    "approval",
    "client_tool",
    "user_input",
    "child_result",
    "provider_continuation",
]


class RunPayloadObjectRef:
    object_key: str
    digest_sha256: str
    size_bytes: int
    content_type: str
    schema_version: str


class PendingCallSummary:
    call_id: str
    kind: PendingCallKind
    tool_name: str | None
    provider_type: str | None
    arguments_digest_sha256: str | None
    presentation: JsonObject | None


class RunPendingSummary:
    schema_version: Literal["1"]
    calls: tuple[PendingCallSummary, ...]
    resolution_policy: Literal["all"]


class RecoveryUsage:
    schema_version: Literal["1"]
    model_requests: int
    input_tokens: int
    output_tokens: int
    tool_invocations: int
    billable_units: dict[str, int]


class RecoveryUsageLimit:
    schema_version: Literal["1"]
    model_requests: int | None
    input_tokens: int | None
    output_tokens: int | None
    tool_invocations: int | None
    billable_units: dict[str, int]


class RecoveryBudget:
    policy_version: str
    max_recovery_attempts: int
    max_handoffs: int
    recovery_deadline_at: datetime | None
    max_usage: RecoveryUsageLimit | None


class SealedRunState:
    digest_sha256: str
    size_bytes: int
    content_type: str
    envelope_schema_version: str
    harness_schema_version: str
    checkpoint_seq: int
    committed_by_run_attempt_id: str | None


class Run:
    id: str
    version: int
    tenant_id: str

    session_id: str
    thread_id: str
    parent_run_id: str | None
    retry_of_run_id: str | None
    lineage_kind: RunLineageKind

    trigger_type: str
    trigger_entity_type: str | None
    trigger_entity_id: str | None
    parent_agent_instance_id: str | None
    delegation_id: str | None
    parent_tool_call_id: str | None

    agent_preset_id: AgentPresetId
    agent_preset_version_id: AgentPresetVersionId
    runtime_lock_digest: str
    model_execution_snapshot: ModelExecutionSnapshot | None
    model_execution_observation: ModelExecutionObservation
    connector_selections: tuple[ConnectorRunSelection, ...]
    accepted_trigger: AcceptedTriggerSource | None

    priority: int
    queue_name: str
    available_at: datetime
    current_run_attempt_id: str | None
    next_attempt_fence: int

    recovery_budget: RecoveryBudget
    attempts_started: int
    recovery_attempts_started: int
    handoffs_completed: int
    usage_charged: RecoveryUsage

    idempotency_key: str | None
    request_fingerprint: str

    status: RunStatus
    wait_reason: RunWaitReason | None
    input_kind: RunInputKind
    input: JsonValue | None
    input_object: RunPayloadObjectRef | None
    input_text: str | None
    output: JsonValue | None
    output_object: RunPayloadObjectRef | None
    output_text: str | None
    failure: SafeFailure | None
    pending: RunPendingSummary | None
    sealed_state: SealedRunState | None

    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    waiting_at: datetime | None
    completed_at: datetime | None
    sealed_at: datetime | None
```

`RunPendingSummary` is a bounded read and query projection, not a collection of mutable action rows. `resolution_policy="all"` means one accepted feedback or owning internal continuation finalizes the applicable frozen set; partial responses never mutate this summary. The exact request values remain in the sealed Run state.

`id` is the Foundation-owned Run identity and follows [Platform Data Conventions](../data-conventions.md). `version` is the positive Run object version used for compare-and-swap relational mutation. The state object key is derived from `tenant_id` and `id`; it is not duplicated in the row and is never accepted from a caller.

`session_id`, `thread_id`, `parent_run_id`, `retry_of_run_id`, lineage, `input_kind`, accepted input, `agent_preset_id`, `agent_preset_version_id`, `runtime_lock_digest`, `model_execution_observation`, Connector selections, accepted Trigger source, accepted recovery policy, idempotency identity, and request fingerprint are immutable after acceptance. Every version of the Run state must carry `run_id` and `thread_id` equal to the owning Run. Foundation rejects another identity rather than rewriting it during read.

The [Model Management contract](25-model-management.md) owns `ModelExecutionSnapshot` and `ModelExecutionObservation`. The snapshot is present while a Run is `accepted` or `running`, is reused by every replacement RunAttempt, and can transition only to null in the sealing transaction. The safe observation remains after sealing. Neither value is part of the Harness continuation state.

The [Environment Management contract](19-environment-management.md#environment-selection-and-run-state) owns `EnvironmentExecutionConfig`. It is stored in the Run state envelope, not the relational Run row. It contains ordered entries resolved from exact named revisions or Run-owned inline configurations, connection targets, desired mount names and default mount routing, exact connector locks, and non-secret credential references. Every replacement RunAttempt reuses that complete configuration and resolves current eligible credentials.

The [Foundation Skill Management contract](27-skill-management.md#agentpresetversion-selection) owns effective managed Skill selection. Run acceptance resolves an optional `selected_skill_names` invocation override against the exact available locks and defaults in the selected AgentPresetVersion. The resulting ordered tuple is stored in the Run state envelope, not the relational Run row, and every replacement RunAttempt reuses it.

The [Connector contract](23-connectors-connections-and-triggers.md#connection-selection-and-runattempt-preparation) defines `ConnectorRunSelection` and `AcceptedTriggerSource`. A Run with no Connector declarations stores an empty `connector_selections`; only a Run accepted from a managed Trigger stores `accepted_trigger`, and its `trigger_entity_id` equals that accepted Trigger ID.

`sealed_state` is absent while the Run is active. A `waiting` or `completed` sealing transaction always records the exact digest, size, schema versions, and checkpoint sequence of the state object frozen with the Run. A Worker-originated `failed` Run can record a complete state prepared under its fence or leave `sealed_state` null. An interrupt-driven `cancelled` Run always leaves it null because interrupt performs no object I/O. After any seal, no later object value is authoritative; only a recorded `sealed_state` selects bytes as part of the Run outcome. When a sealed state is present, its fields identify the exact terminal bytes without introducing a second base or result object. `committed_by_run_attempt_id` is null only when a relational fail-closed decision seals a Run without an attempt-originated state change.

Every Run can own zero or more immutable `RunAttempt` values over its lifetime, with at most one current and lease-authorized attempt. The [allocation contract](15-run-attempt-persistence.md#run-and-runattempt-allocation-boundary) owns when those attempts are created.

`RecoveryBudget` is the accepted recovery-policy snapshot. `max_recovery_attempts` includes the first Attempt and every successor created after retryable failure or expired-lease takeover; a successor created after a planned handoff does not consume it. `max_handoffs` bounds successful planned handoffs independently. `attempts_started` counts every Attempt for audit, `recovery_attempts_started` counts only Attempts charged to the recovery budget, and `handoffs_completed` counts committed `yielded` transitions. `recovery_deadline_at` is a fixed UTC deadline, and `usage_charged` aggregates every Attempt, including known usage from yielded or failed work. Every counter and limit is non-negative. Missing required usage is never treated as zero; if durable usage evidence is insufficient to prove that a configured ceiling remains, no new Attempt is admitted. The Run row is the sole authority for whether another Attempt or planned handoff is permitted.

The exact `AgentPresetId`, `AgentPresetVersionId`, and internal Plugin Runtime lock digest are fixed at Run acceptance. The selected immutable Version owns the Agent behavior and exact `model_id`; the lock digest owns exact Plugin and Python dependency selection. Acceptance resolves the model's current configuration into the Run-owned snapshot. Worker resume never resolves mutable Preset config, another active Version, another Runtime lock, or current ModelConfig. An ordinary compatible continuation is another Run and resolves the selected Preset's then-active Version, current ModelConfig, and the on-demand Version-owned or runner-active Runtime lock selected by the deployment profile. Authenticated waiting feedback and an explicit retry instead preserve the source Run's exact Preset Version and Runtime lock. A fork either preserves the source selection or resolves an explicitly selected Preset according to the public fork contract.

Exactly one of `input` and `input_object` is present. `input_kind` selects its owning protocol: `agent_input` stores the accepted [`AgentInput`](33-agent-input.md#agent-input-protocol), while `waiting_feedback` stores the complete normalized [`WaitingRunFeedback`](34-agent-control-input-and-continuation.md#deferred-interaction). Start, continue, continue from, and fork use `agent_input`; feedback uses `waiting_feedback`; retry copies the source Run's exact kind and value. `retry_of_run_id` is present only for retry and names the same-Thread failed or cancelled Run that was current when its accepted intent was copied. It is correlation, not another state or history edge.

At most one of `output` and `output_object` is present, and neither is present before a completed outcome. The `JsonValue` annotation is the storage encoding, not an open input schema. Inline values are bounded structured data suitable for direct Run reads. Oversized payloads use immutable objects. Retry copies the exact accepted descriptor value, publishes a new Run-owned payload envelope when that JSON is object-backed, and lets execution reacquire any required binary source for the new Run. `input_text` and `output_text` are optional bounded derived projections and never replace exact data, accepted source descriptions, or the complete message history.

`waiting` is a sealed Run outcome. It contains a bounded `pending` summary; the frozen Run state contains the authoritative deferred requests and effective client-tool surface. Authenticated feedback is the accepted input of a new Run whose `parent_run_id` names the waiting Run and whose state is initialized from that waiting state.

### Run Lifecycle

```mermaid
stateDiagram-v2
    [*] --> accepted
    accepted --> running: RunAttempt leased and fenced
    accepted --> failed: terminal pre-Run failure
    accepted --> cancelled
    running --> running: attempt replacement or bounded backoff
    running --> waiting: deferred outcome sealed
    running --> completed: state and output sealed
    running --> failed
    running --> cancelled
    waiting --> [*]
    completed --> [*]
    failed --> [*]
    cancelled --> [*]
```

`accepted` means the complete Run row, accepted input, exact selections, recovery budget, scheduling fields, and initial complete state object are durable; no attempt is current; and a Worker may claim the Run once `available_at` is reached. It is only the initial pre-claim scheduling state. Once the first Attempt is created, that Run never returns to `accepted`. It is not a queued user-input entry, and Run defines no `queued` state.

`running` means execution of this Run has begun and the Run remains active. It normally selects one non-terminal `RunAttempt`; during retryable backoff or after a planned handoff it can temporarily have no current Attempt. An expired selected lease grants no worker authority while awaiting transactional takeover. `started_at` records the first Harness Run entry and never changes during recovery or planned handoff.

`waiting`, `completed`, `failed`, and `cancelled` are sealed outcomes. `sealed_at` and any available `sealed_state` are selected in the same relational transaction. A sealed Run never changes any column and its state key is never overwritten.

The Run remains the budget and lifecycle authority after attempt failure or lease expiry. The [Run Attempt recovery contract](15-run-attempt-persistence.md#recovery-and-budget-enforcement) keeps structurally resumable, in-budget work `running` across replacement attempts and seals invalid, incompatible, non-retryable, or exhausted work as `failed`.

### Key Entity Relationships

```mermaid
flowchart LR
    Run["Run<br/>one Agent-work and recovery boundary"]
    State["Run state.json<br/>one current complete state"]
    Attempt["RunAttempt<br/>one worker generation and fence"]
    Run["Harness Run<br/>one process-local logical run"]
    Model["ModelAttempt<br/>one model-loop invocation"]

    Run -->|"owns one deterministic key"| State
    Run -->|"zero before dispatch; zero or more over its lifecycle"| Attempt
    Attempt -->|"starts at most one"| Run
    Run -->|"zero or more sequential attempts"| Model
    Attempt -->|"conditionally replaces while current"| State
```

The complete identity-allocation decision table is owned by [Run Attempt Persistence](15-run-attempt-persistence.md#run-and-runattempt-allocation-boundary).

## Relational Run Table

The conceptual `Run` materializes as one row in `runs`; supported relational backends preserve the same validation and query semantics.

| Column group         | Columns                                                                                                                                                                                                                                        | Relational shape and contract                                                                                                          |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Identity             | `id`, `version`, `tenant_id`                                                                                                                                                                                                                   | Opaque text IDs and a positive integer CAS version; `id` is the primary key                                                            |
| Interaction lineage  | `session_id`, `thread_id`, `parent_run_id`, `retry_of_run_id`, `lineage_kind`                                                                                                                                                                  | Immutable state-source edge plus optional same-Thread terminal-intent retry correlation                                                |
| Scheduling           | `priority`, `queue_name`, `available_at`, `current_run_attempt_id`, `next_attempt_fence`                                                                                                                                                       | Durable claim order and the sole current worker generation                                                                             |
| Recovery budget      | `recovery_policy_version`, `max_recovery_attempts`, `max_handoffs`, `recovery_deadline_at`, `max_usage_json`, `attempts_started`, `recovery_attempts_started`, `handoffs_completed`, `usage_charged_json`                                      | Accepted finite limits, complete Attempt audit count, and atomically charged recovery, handoff, and usage consumption                  |
| Idempotency          | `idempotency_key`, `request_fingerprint`                                                                                                                                                                                                       | Optional retry-safe acceptance identity and exact bounded request fingerprint                                                          |
| Trigger correlation  | `trigger_type`, `trigger_entity_type`, `trigger_entity_id`, `parent_agent_instance_id`, `delegation_id`, `parent_tool_call_id`                                                                                                                 | Bounded typed correlation; never state-lineage authority                                                                               |
| Agent selection      | `agent_preset_id`, `agent_preset_version_id`, `runtime_lock_digest`                                                                                                                                                                            | Stable Preset, exact immutable Version, and internal Plugin Runtime lock selected at acceptance                                        |
| Model selection      | `model_execution_snapshot_json`, `model_execution_observation_json`                                                                                                                                                                            | Active non-secret execution snapshot plus retained safe observation                                                                    |
| Connector acceptance | `connector_selections_json`, `accepted_trigger_json`                                                                                                                                                                                           | Bounded immutable Connector selections and optional exact Trigger occurrence                                                           |
| Lifecycle            | `status`, `wait_reason`, `pending_json`                                                                                                                                                                                                        | Enum-constrained state; bounded pending summary exists exactly for `waiting`                                                           |
| Input                | `input_kind`, `input_json`, `input_object_key`, `input_object_digest_sha256`, `input_object_size_bytes`, `input_object_content_type`, `input_object_schema_version`, `input_text`                                                              | Exactly one protocol-valid accepted input as inline JSON or an immutable object reference; explicit owner and optional text projection |
| Output               | `output_json`, `output_object_key`, `output_object_digest_sha256`, `output_object_size_bytes`, `output_object_content_type`, `output_object_schema_version`, `output_text`                                                                     | Exactly one representation for completed Runs; absent otherwise                                                                        |
| Failure              | `failure_json`                                                                                                                                                                                                                                 | Bounded safe structured failure only; no raw exception                                                                                 |
| Sealed state         | `sealed_state_digest_sha256`, `sealed_state_size_bytes`, `sealed_state_content_type`, `sealed_state_envelope_schema_version`, `sealed_state_harness_schema_version`, `sealed_state_checkpoint_seq`, `sealed_state_committed_by_run_attempt_id` | Exact frozen state identity for waiting/completed and valid failure snapshots; object key is derived rather than stored                |
| Time                 | `created_at`, `updated_at`, `started_at`, `waiting_at`, `completed_at`, `sealed_at`                                                                                                                                                            | UTC instants; lifecycle checks govern nullability                                                                                      |

Object-reference columns form all-or-none groups. Bounded values are validated before relational mutation; object keys and digests grant no authority.

## Git-Like Run DAG

`parent_run_id` names the exact sealed Run whose frozen state initialized the new Run. It is the sole interaction-history edge. The operation-specific acceptance rules are owned by [Agent Control: Input and Continuation](34-agent-control-input-and-continuation.md#acceptance-and-lineage).

```mermaid
flowchart LR
    T0["Run T0<br/>Thread A<br/>completed"] --> T1["Run T1<br/>Thread A<br/>waiting"]
    T0 --> C1["Run C1<br/>Thread A<br/>continue from T0"]
    T1 --> T2["Run T2<br/>Thread A<br/>feedback"]
    T1 --> R2["Run R2<br/>Thread A<br/>retry of failed feedback T2"]
    T0 --> F1["Run F1<br/>Thread B<br/>fork"]
    F1 --> F2["Run F2<br/>Thread B<br/>continue"]
```

The lineage rules are:

1. A root Run has `parent_run_id=null` and `lineage_kind=root`. The first Run of a new Thread initializes state with `HarnessState.new()`. A root-like Run accepted later in an existing Thread is permitted only while `head_run_id=null` after the current Run failed or was cancelled; its trusted Host transformation constructs empty state while preserving that Thread's exact Harness-owned `thread_id`.
2. A continuation has `lineage_kind=continue`, uses a completed parent in the same Thread, and initializes its state from the parent's frozen state while preserving `thread_id`. Ordinary Continue selects the current completed head; Continue From can explicitly reselect any retained and readable completed historical Run in that Thread.
3. Authenticated feedback has `lineage_kind=continue`, uses the exact waiting parent, initializes its state from that parent's frozen deferred state, and preserves `thread_id`.
4. A fork has `lineage_kind=fork`, uses a completed parent, transforms its frozen Harness state with `HarnessState.fork()`, retains only portable Host continuation, and initializes a new state with the new `thread_id`.
5. A retry names its failed or cancelled source in `retry_of_run_id`, copies that source's `parent_run_id`, `lineage_kind`, `input_kind`, and accepted input, and repeats the same state initialization without using the terminal source as a parent.
6. A child Agent that starts with independent empty history is a root of its own Thread. Structural or causal parentage uses trigger and delegation fields.
7. Worker resume preserves the Run, Thread, parent edge, and state key. It does not add a DAG node.
8. Failed and cancelled Runs remain queryable but are not eligible parents.

The independent Thread row serializes every accepted advancement. Several retained Runs can share the same `(thread_id, parent_run_id)` and represent historical sibling continuations, but at most one Run in the Thread can be `accepted` or `running`. Acceptance locks the Thread, verifies its exact version and operation-specific current/head selection, then advances it atomically with the new Run. An explicit fork creates a new Thread row and first Run in the same transaction.

A lineage read follows `parent_run_id` from an explicitly selected head. It is tenant-scoped, cycle-safe, and bounded. Created time and event order are not lineage authority.

The relational implementation uses one bounded recursive query, verifies that traversal reaches a root without a cycle or truncation, and returns at most 1,000 Run rows. Its recursive step runs once per ancestor. For `A` ancestors before a fork and `L` ancestors in the fork's local lineage, it visits `A + L + 1` rows in one database round trip, with `O(A + L)` work and temporary path state.

The public route, response, authorization, and failure contract are owned by [Run Lineage Read](21-management-api.md#run-lineage-read).

## Run State Object

Each accepted Run owns one `RunStateEnvelope`. It combines Harness portable state with Host continuation required to resume the same Run or initialize a new Run from a selected parent. It contains data and correlation, never current authority.

```python
type RunStateCheckpointKind = Literal[
    "initial",
    "progress",
    "waiting",
    "completed",
]
type RunInputDisposition = Literal["pending", "applied"]


class HostPendingRequest:
    call_id: str
    kind: Literal["child_result", "provider_continuation"]
    owner_type: str
    request_schema_version: str
    request: JsonObject


class DeferredContinuationState:
    schema_version: Literal["1"]
    requests: JsonObject
    effective_client_tool_surface: JsonValue | None
    effective_surface_digest_sha256: str | None
    host_requests: tuple[HostPendingRequest, ...]


class ConsumedThreadInboxEntry:
    inbox_entry_id: ThreadInboxEntryId
    kind: ThreadInboxKind


class HostContinuationState:
    schema_version: Literal["1"]
    deferred: DeferredContinuationState | None
    consumed_inbox_entries: tuple[ConsumedThreadInboxEntry, ...] = ()


class RunStateOutcomeCandidate:
    outcome: Literal["waiting", "completed"]
    wait_reason: RunWaitReason | None
    pending: RunPendingSummary | None
    output: JsonValue | None
    output_object: RunPayloadObjectRef | None
    output_text: str | None


class RunStateEnvelope:
    schema_version: Literal["1"]
    run_id: str
    thread_id: str
    checkpoint_seq: int
    checkpoint_kind: RunStateCheckpointKind
    input_disposition: RunInputDisposition
    last_checkpoint_run_attempt_id: str | None
    last_checkpoint_fence: int

    agent_preset_id: AgentPresetId
    agent_preset_version_id: AgentPresetVersionId
    runtime_lock_digest: str
    environment_config: EnvironmentExecutionConfig
    selected_skill_names: tuple[str, ...]
    harness_schema_version: str
    harness: HarnessState
    host: HostContinuationState
    outcome_candidate: RunStateOutcomeCandidate | None
```

This is the complete serialized outer schema. `HarnessState` is encoded through its owning public adapter and carries the same `thread_id`. `checkpoint_seq` starts at zero and increases monotonically for each successful semantic state replacement. The initial value has `checkpoint_kind=initial`, `input_disposition=pending`, no attempt identity, fence zero, and no outcome candidate.

`environment_config` is resolved and authorized at Run acceptance. Its exact connection targets, named or inline sources, connector locks, Secret references, permission ceilings, desired mount definitions, and digest remain byte-for-byte equivalent across every checkpoint replacement for that Run. A worker reauthorizes its references and resolves fresh Secret values, but cannot rewrite the accepted configuration.

`selected_skill_names` is the complete effective selection resolved at Run acceptance. It is ordered by the selected AgentPresetVersion's available catalog, contains no duplicate or unknown name, and remains byte-for-byte equivalent across every checkpoint replacement. A worker revalidates it against the exact revision locks and current authority but cannot replace it with Agent defaults or another invocation value.

The first checkpoint after the accepted Run input has crossed the Harness input boundary sets `input_disposition=applied`. Every later progress or outcome checkpoint retains `applied`. This field prevents a later attempt from injecting the same accepted input twice: a pending initial state receives the Run input; an applied state resumes directly from its exported Harness and Host state.

`checkpoint_kind=progress` contains no outcome candidate. `checkpoint_kind=waiting` or `completed` contains the matching complete `outcome_candidate`. A waiting candidate has a non-empty pending summary, no output, and complete deferred state in `host.deferred`. A completed candidate has an output representation, no pending summary, and no deferred state. The candidate is durable preparation for the relational outcome transaction; it is not a sealed Run outcome by itself.

`requests` is the exact serialized native Pydantic `DeferredToolRequests` value; Foundation classifies its approval, client-tool, and structured-user-input calls in the relational summary without reconstructing the native value from that summary. `host_requests` carries child-result and provider-continuation values that are not native Pydantic deferred calls. IDs across both collections are disjoint, and their union equals the waiting pending summary. Host-owned child results later enter the Harness through a fresh-run input seam; they never impersonate the original asynchronous spawn call or enter `DeferredToolResume`.

`consumed_inbox_entries` is the bounded receipt set for [`thread_inbox`](35-agent-control-active-execution.md#thread-inbox) entries incorporated into this Run. Each receipt correlates the exact inbox identity and kind with Harness or Host state already present in the same envelope. Replacement Attempts of this Run preserve the receipts. A new Run does not inherit its parent's receipts; it records only inbox entries consumed by that new Run's own acceptance.

The envelope separates four state classes:

| State class                  | Contents                                                                                                       | Restore rule                                                                                  |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Environment execution config | Exact desired mounts, connection targets, connector locks, Secret references, permissions, and default routing | Immutable for the Run; reauthorized before fresh attachments and runtime mounts are created   |
| Effective Skill selection    | Exact ordered names within the selected AgentPresetVersion's available Skill locks                             | Immutable for the Run; locks and current authority are revalidated before materialization     |
| Harness portable state       | Thread ID, messages, Capability namespaces, and portable provider-defined Environment data                     | Validated by Harness and owning codecs after fresh mounts are selected                        |
| Host continuation state      | Optional complete deferred-request values, effective client surface, and consumed inbox receipts               | Validated and consumed by Foundation and selected integrations before or around Harness entry |

The envelope contains data and correlation only; current policy, credentials, live resources, and process-local objects are resolved afresh.

Fork portability is determined by Harness state and each owning deferred-value contract. Harness portable state contains no external resource identity, credentials, connector clients, or live attachment. Each new Run independently freezes its accepted Environment execution configuration.

### State Initialization Matrix

| Concern                                                                                                           | Start                                                    | Continue                                                                               | Feedback                                                           | Fork                                                                       |
| ----------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| Run and state identity                                                                                            | Allocate a Run and deterministic Run-owned state key     | Allocate a Run and key; name the completed head, or no parent under the null-head rule | Allocate a Run and key; name the exact waiting Run                 | Allocate a Run, Thread, and key; name the completed source                 |
| Harness state                                                                                                     | Create `HarnessState.new()`                              | Copy the frozen parent, or construct empty state preserving the existing `thread_id`   | Copy frozen waiting state and preserve `thread_id`                 | Apply `HarnessState.fork()` and use its new `thread_id`                    |
| Host deferred continuation                                                                                        | Start empty                                              | Start empty after clearing the parent outcome, or empty with no parent                 | Retain exact pending requests until complete feedback is applied   | Clear source outcome and deferred data                                     |
| Accepted input                                                                                                    | Store new `AgentInput` as pending                        | Store new `AgentInput` as pending                                                      | Store complete `WaitingRunFeedback` as pending                     | Store new `AgentInput` as pending                                          |
| Environment execution configuration                                                                               | Resolve and freeze accepted desired mounts               | Resolve and freeze the new Run's accepted desired mounts                               | Copy the waiting parent's exact configuration and reauthorize it   | Resolve and freeze the accepted desired mounts                             |
| Effective managed Skill selection                                                                                 | Resolve request override or Version default              | Resolve request override or Version default and validate portable state                | Copy the waiting parent's exact tuple and validate inherited state | Resolve request override or Version default and validate portable state    |
| Definition and integration                                                                                        | Resolve active Version and profile-selected Runtime lock | Resolve active selections and validate compatibility                                   | Preserve the waiting parent's exact Version and Runtime lock       | Preserve source selection unless an explicit compatible Preset is selected |
| Policy, Secrets, `RunBindings`, Environment attachments, runtime mounts, `EnvironmentRuntime`, tools, and clients | Resolve fresh                                            | Resolve fresh and reauthorize retained selectors                                       | Resolve fresh and reauthorize retained selectors                   | Resolve fresh and reauthorize retained portable selectors                  |

Initialization of a new Run always writes a complete Run-owned envelope with `checkpoint_seq=0`. It does not reference the parent state as a base, retain the parent's outcome candidate as the new Run's outcome, or create another state field on the new Run. Parent state is only immutable source data for this initialization. It clears the parent's consumed-inbox receipts after applying any inbox value explicitly selected by the accepting operation; the new Run records only inbox entries that it consumes itself.

An explicit retry repeats the source Run's row in this matrix: it copies the source's `parent_run_id`, `lineage_kind`, complete accepted input kind and value, AgentPresetVersion, Runtime lock, selected Skill names, and Environment execution configuration, then rebuilds a complete state from the same eligible parent. It records `retry_of_run_id`, accepts no invocation override, and resolves current authority, fresh live resources, a fresh recovery budget, and a new Run-owned model execution snapshot.

### State Key, Conditional Writes, and Fencing

The state key is deterministic and stable for the lifetime of the Run:

```text
tenants/{tenant_id}/runs/{run_id}/state.json
```

The content type is `application/vnd.converge.run-state+json`. Object metadata records `schema-version`, `run-id`, `thread-id`, `checkpoint-seq`, `writer-fence`, and the lowercase SHA-256 digest of the canonical body. Object stat supplies exact byte size and the opaque current object version.

Acceptance publishes the initial object create-only. A current attempt does not write until it has conditionally claimed the current object version for its monotonic Run fence. Every state replacement then supplies the exact object version returned by the claim or previous successful write. The replacement is visible as the complete new object or not visible at all.

Before each write, Foundation verifies that the Run remains unsealed and that the attempt ID, fence, lease, tenant, and Run version are current. It holds no database transaction across object I/O. Expected-version replacement serializes the object writes: after a newer attempt claims the key, an older attempt's known object version can no longer overwrite it. A conflict causes a fresh read of Run and object authority; it is never retried as an unconditional put.

Foundation exposes no checkpoint object ID and never selects an older object version. A storage backend can retain physical versions internally, but those versions are backup or provider implementation details, not application-visible checkpoint objects. Logically, one Run has one key and one current state.

### Resume Semantics

Foundation writes state only after `HarnessRunStream.export_state()` produces a complete structurally valid state and Host continuation has been serialized under its bounds. Raw token deltas, incomplete private graph nodes, live `RunBindings`, Environment runtimes and mounts, and process-local handles never enter the object.

After the [Run Attempt allocation contract](15-run-attempt-persistence.md#run-and-runattempt-allocation-boundary) authorizes a later attempt for the same Run, the worker claims the existing state key, validates the envelope and its owned state codecs, reconstructs fresh `RunBindings`, attachments, runtime mounts, and `EnvironmentRuntime`, and resumes:

- `input_disposition=pending` starts from the initialized state and supplies the Run's exact accepted input;
- `input_disposition=applied` resumes from the checkpoint without supplying the accepted input again;
- a valid waiting or completed outcome candidate can be committed idempotently without repeating model or tool work when its relational outcome was not yet sealed.

The same resume rule applies after a predecessor Attempt commits `yielded`. Recovery reads the latest valid complete value at the deterministic key; it does not require that value to have been written by the yield path or selected by a separate relational checkpoint reference.

`input_disposition` governs only whether accepted semantic input crosses the Harness input boundary again. It is not evidence that an `environment_path` file should be rewritten. Same-Run file rematerialization is derived from existing charged model-request usage under the [Agent Input contract](33-agent-input.md#binary-source-and-delivery).

The resume point is the current state object, not an event cursor, latest object listing result, lifecycle timestamp, retained Item, or state from another Run. If work occurred after the last successful conditional write, that work is not part of the resume point. The [Run Attempt recovery contract](15-run-attempt-persistence.md#recovery-and-budget-enforcement) owns unresolved tool-call handling after that boundary.

## Object Storage Schemas

All JSON objects serialize as UTF-8 RFC 8785 canonical JSON after typed values are converted to declared JSON strings. Digests and sizes cover those exact bytes. Non-finite numbers and duplicate object keys are invalid.

Foundation Run persistence uses these serialized object types:

| Object type          | Content type                                | Owner                                                   |
| -------------------- | ------------------------------------------- | ------------------------------------------------------- |
| `RunStateEnvelope`   | `application/vnd.converge.run-state+json`   | One deterministic, conditionally replaced Run state key |
| `RunPayloadEnvelope` | `application/vnd.converge.run-payload+json` | Immutable oversized Run input or output                 |

[Lifecycle and Stream Persistence](17-lifecycle-and-stream-persistence.md) separately owns `RunReplaySnapshot`.

### Run Payload Object

```python
class RunPayloadEnvelope:
    schema_version: Literal["1"]
    run_id: str
    payload_kind: Literal["input", "output"]
    payload_schema_version: str
    payload: JsonValue
```

For `payload_kind="input"`, `payload_schema_version` is the exact owning `AgentInput` or waiting-feedback schema version and `payload` is that protocol's complete accepted JSON value. It is not an independent extensible payload schema.

The object key is content-addressed beneath the Run:

```text
tenants/{tenant_id}/runs/{run_id}/payloads/{payload_kind}/{digest_sha256}.json
```

[`BinaryContent`](33-agent-input.md#binary-source-and-delivery) in an accepted input retains only its normalized URL or Environment-path source description. Run payload objects never contain inline file bytes or a Foundation-managed binary body object.

### Retention

Retention never removes a state or Run payload object while a retained Run or successor depends on it. A parent state remains frozen and reachable while any successor or lineage policy requires it. Reference-aware deletion of those state and payload objects never relies on object age alone. Binary source bytes remain outside Foundation persistence; their availability and lifetime belong to the caller-provided URL or external Environment.

## Run Acceptance, Checkpoint, and Outcome Commit

Run acceptance creates or advances the Thread row together with the Run row and its initial state as one externally indivisible acceptance operation:

1. validate authorization, Thread version, current/head selection, lineage, selections, the effective managed Skill names, current enabled ModelConfig, and any parent state, then build the complete Run-owned initial state containing the exact Environment execution configuration and effective Skill selection plus the separate model execution snapshot;
2. publish object-backed input and `state.json` create-only;
3. in one short transaction, insert or advance the Thread, insert the `accepted` Run, and commit required lifecycle facts, idempotency evidence, and outbox intents.

```mermaid
sequenceDiagram
    participant Control as Control plane
    participant Objects as Object storage
    participant DB as Relational database

    Control->>Control: Validate Thread, parent, policy, Skill selection, and current ModelConfig
    Control->>Objects: Read frozen parent state when required
    Control->>Control: Build new Run initial state
    Control->>Objects: Create new Run state.json and optional input payload
    Control->>DB: Commit Thread advancement, Run, and lifecycle facts
    alt transaction commits
        DB-->>Control: Thread advanced and Run accepted
    else transaction fails
        DB-->>Control: Thread unchanged; objects remain cleanup candidates
    end
```

### Checkpoint Triggers and Refresh

During an active Harness Run, Foundation requests a progress checkpoint only at a complete public state boundary:

1. after the accepted Run input has crossed the Harness input boundary and before the first model request;
2. when the complete [steer-consumption flow](35-agent-control-active-execution.md#steer-consumption-and-state-commitment) requires durable incorporation before the Agent can act on accepted steer;
3. after a complete tool batch and its results have entered the next complete message boundary, before another model request;
4. between internal `ModelAttempt` values when the Harness exposes a new complete normalized state.

A waiting or completed Harness outcome always triggers its matching outcome checkpoint. Raw stream deltas, an in-flight model response, an incomplete tool batch, an active inline child, a lease heartbeat, and elapsed time alone never trigger state publication. Adjacent progress triggers with no Harness or Host state change are coalesced rather than creating duplicate checkpoints.

A process-local graceful-handoff request is observed at the same boundaries. It does not make an in-flight provider request, partial streamed response, incomplete tool batch, active inline child or sibling batch, or state publication safe. At the first boundary where no model, tool, nested execution, or state publication is in flight, all tool results are present in complete Harness message history, and the current Attempt still owns its Run, lease, and fence, Foundation exports the complete Harness, Host, and portable Environment state and publishes the next progress checkpoint. If the already published state is byte-equivalent to that complete boundary, the Worker can reuse it without another replacement. If a waiting or completed outcome is already available, the Worker commits that ordinary outcome instead of yielding.

During execution, a checkpoint operation:

1. exports complete Harness state and builds bounded Host state;
2. validates current Run, attempt, fence, lease, and expected object version;
3. conditionally replaces the same `state.json` with the next checkpoint sequence and matching metadata;
4. treats only the returned object version as the next valid write token.

When the checkpoint incorporates Thread inbox entries, the complete envelope contains their `ConsumedThreadInboxEntry` receipts. The active-control contract owns their relational consumption, recovery, and terminal-race semantics; this contract owns only the complete state representation and conditional object write.

Checkpoint writes do not create a Run row, attempt row, lifecycle transition, or historical checkpoint selector. A failed or unknown put is reconciled by `stat` and exact body validation before any retry.

A graceful handoff likewise creates no checkpoint identity or relational checkpoint selection. The Worker must confirm that the current complete safe boundary is present before it can commit `yielded`, but the yield transaction does not store or compare the state digest, size, schema, checkpoint sequence, or opaque object version. If publication cannot be confirmed, the Worker keeps its Attempt lease and retries until the drain deadline; lease-expiry recovery can still use the last earlier valid checkpoint.

A waiting outcome and a completed outcome without a prepared queued successor commit in this order:

1. publish any immutable object-backed output;
2. conditionally replace `state.json` with a complete matching outcome candidate;
3. in one short transaction, revalidate current Run and `RunAttempt`, satisfy the [active-control outcome precondition](35-agent-control-active-execution.md#completion-and-control-races), select the candidate's exact digest and checkpoint sequence as `sealed_state`, copy its bounded output or pending summary into the Run row, terminalize the attempt, charge known usage, append lifecycle facts, verify that this Run is still the Thread's current Run, clear the model execution snapshot, seal it, select it as the continuation head, and increment the Thread version;
4. after commit, reject every later write to the state key.

A completed outcome with queued input can instead use the [state-first combined handoff](36-agent-control-queued-submissions.md#completion-time-combined-handoff). After publishing the source completed candidate, Foundation performs bounded detached preflight, builds and create-only publishes the queued successor's complete initial state and any object-backed input, then opens one short transaction. That transaction starts with the source Run still current and running, repeats the exact RunAttempt fence, active-control, queue-order, version, state, selection, and authority checks, and atomically:

1. seals the source as `completed` and selects its exact completed candidate;
2. terminalizes its current RunAttempt and charges known usage;
3. consumes the first queued submission and records the successor correlation;
4. inserts the already-state-backed successor as `accepted` with the completed source as `parent_run_id`;
5. selects the completed source as Thread head and the accepted successor as Thread current; and
6. advances the Thread version once for sealing and once for acceptance, plus the queue version once for consumption.

The combined commit has the same result as an ordinary completed seal followed by queue consumption, but exposes no intermediate relational state. If successor preflight, object publication, or any final combined precondition fails, no queue or successor mutation commits. Foundation re-enters the ordinary source-outcome path under the active-control race rules; the completed Run can seal independently and the queue remains editable for later recovery drain. Prepared successor objects that no accepted Run selects are non-authoritative cleanup candidates.

Every other transaction that seals a Run as failed or cancelled also clears the model execution snapshot while retaining the safe model observation and applies the pending-steer disposition owned by the active-control contract.

If the object write succeeds but the relational transaction does not commit, the Run remains active and the outcome candidate remains a valid resumable state, not a sealed outcome. The current attempt or an authorized later attempt can retry the exact relational commit after reconciliation. Object timestamps or listings never authorize that adoption.

A Worker-originated failed outcome can select a complete state already prepared under its current fence. An interrupt-driven cancelled outcome performs no object I/O and selects no new sealed state; its `sealed_state` is null. Either terminal transaction verifies that the Run remains current, preserves it as the Thread's current Run, preserves the prior continuation head, and increments the Thread version. Neither outcome makes that Run eligible as a parent.

## Accounting Boundary

A separate accounting contract owns Run acceptance audit, usage aggregation, pricing revisions, and settlement lifecycle. Those records may correlate by `run_id` but never select Run state, mutate a sealed Run, or authorize another attempt.

## Relational Constraints and Queries

The `runs` table follows the [Relational Schema Lifecycle](04-relational-schema.md) and preserves these constraints:

1. `id` is the primary key, `(tenant_id, id)` is unique so parent references remain same-tenant, `(tenant_id, thread_id)` references one durable Thread in the same tenant and Session, and a present retry source references a Run in that same tenant and Thread.
2. Immutable acceptance fields never change; versions, fences, checkpoint sequences, sizes, and recovery counters satisfy their positive or non-negative field bounds.
3. Input has exactly one inline or object-backed representation and matches `input_kind`. `retry_of_run_id` is null for start, continue, continue from, feedback, and fork and otherwise names a same-Thread failed or cancelled Run that was current at retry acceptance and whose accepted kind and value match the retry. Outcome fields satisfy their status-specific nullability, and output exists only for `completed`.
4. Every present sealed-state digest matches the selected envelope identity, checkpoint, and outcome candidate. It is required for `waiting` and `completed`; a `failed` Run can omit it, and an interrupt-driven `cancelled` Run does omit it. Sealed rows reject all relational updates. Active-control preconditions and inbox disposition follow the owning [race contract](35-agent-control-active-execution.md#completion-and-control-races).
5. `current_run_attempt_id` is absent for `accepted` and sealed Runs and can exist only for `running`. A `running` Run may omit it during bounded retry backoff. Active updates require expected Run version; attempt-originated updates also require the current attempt, fence, and lease.
6. Continue uses the completed head when present; only a null-head Continue after a failed or cancelled current Run can have no parent and use root lineage. Continue From and fork require a completed parent. Waiting feedback requires the exact waiting parent and consumes one complete normalized batch. Retry copies the terminal source's eligible state-parent edge; failed and cancelled Runs are never themselves eligible parents.
7. `recovery_attempts_started` does not exceed `max_recovery_attempts`, and `handoffs_completed` does not exceed `max_handoffs`; the total `attempts_started` remains the complete audit count. Recovery, handoff, deadline, and usage limits remain Run-owned authority.

The accepted access paths are:

| Access path                              | Index or uniqueness contract                                                                                                     |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Worker claim and retry scan              | `(tenant_id, queue_name, status, available_at, priority, created_at, id)` for `accepted` and current-attempt-free `running` Runs |
| Idempotent acceptance                    | Unique `(tenant_id, idempotency_key)` when the key exists                                                                        |
| Session activity                         | `(tenant_id, session_id, created_at, id)`                                                                                        |
| Thread activity and stable paging        | `(tenant_id, thread_id, created_at, id)`                                                                                         |
| DAG successor traversal                  | `(tenant_id, parent_run_id, id)`                                                                                                 |
| Retry correlation                        | `(tenant_id, retry_of_run_id, id)`                                                                                               |
| DAG ancestor traversal                   | Unique `(tenant_id, id)` parent lookup at each recursive step                                                                    |
| One active Run per Thread                | Partial unique `(tenant_id, thread_id)` for `accepted` and `running`                                                             |
| One live or selected root Run per Thread | Partial unique `(tenant_id, thread_id)` for `accepted`, `running`, `waiting`, and `completed` when parent is null                |

## Security and Protection

Run input, output, Thread inbox payloads and consumption receipts, Environment execution configuration, effective Skill selection, Harness state, Capability state, Environment state, pending summaries, deferred requests, and Run-scoped audit and usage records are sensitive tenant data. Relational and object reads are tenant-scoped and reauthorized. External resource IDs and private endpoints are protected connection data; object keys and Run IDs grant no access by possession.

State and payload objects use authenticated integrity verification and deployment-approved encryption at rest. Plaintext credentials, bearer authorization, Secret values, and ephemeral credential leases never enter a Run row, state object, event, Item, error, trace, or ordinary log.

Fork, continuation, feedback, retry, and worker resume re-evaluate current policy, definition eligibility, provider availability, Capability composition, tool grants, and Environment execution configuration compatibility. Persisted state is data and correlation, not authority. Unknown Capability or Environment-state payloads survive only under their own forward-compatible rules and are never executed or attached by generic fallback.

## Failure and Recovery Semantics

| Failure or interruption                                                     | Durable outcome                                                                                          | Recovery rule                                                                                                                                                         |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Initial state or input publication fails                                    | No Run is accepted                                                                                       | Retry under the acceptance idempotency contract                                                                                                                       |
| Initial objects publish but relational acceptance fails                     | Objects are non-authoritative orphans                                                                    | Cleanup removes them after proving no accepted Run owns the key                                                                                                       |
| Queued successor state publishes but combined completion rolls back         | Source remains active under its existing outcome candidate; queue remains queued and no successor exists | Retry ordinary source completion under current race rules; clean or reuse only objects whose ownership can be proved                                                  |
| Conditional state write conflicts                                           | Existing complete state remains visible                                                                  | Re-read Run and object versions; stale writers stop                                                                                                                   |
| State write response is lost                                                | Replacement effect is unknown                                                                            | Reconcile by deterministic key, metadata, and opaque version before retrying                                                                                          |
| Terminal candidate writes but relational seal fails                         | Run remains active; candidate is a resumable prepared state                                              | Current or later authorized attempt can retry exact sealing after reconciliation                                                                                      |
| State containing inbox receipts writes but row consumption does not commit  | The object is prepared but inbox consumption is not yet durable                                          | Follow the [active-control race and reconciliation contract](35-agent-control-active-execution.md#completion-and-control-races)                                       |
| Stale `RunAttempt` writes state, lifecycle, usage, pending data, or outcome | Write is rejected by Run and object fencing                                                              | Current attempt continues; stale work is cancelled best-effort                                                                                                        |
| A state write prepared before sealing reaches storage after the seal        | The object outcome can be unknown but is not selected                                                    | The sealed relational outcome remains authoritative; no inbox or continuation fact adopts the object-only write                                                       |
| Worker disappears after a committed checkpoint                              | Latest state remains at the same key                                                                     | After lease expiry, a Worker's transactional takeover creates a later fenced attempt that reconstructs fresh `RunBindings` and Environment runtime mounts and resumes |
| Worker disappears after uncheckpointed work                                 | Only the prior checkpoint is recoverable                                                                 | The replacement attempt projects unmatched durable Agent tool dispatches as `unknown_outcome` before resuming                                                         |
| Handoff checkpoint commits but the yield transaction does not               | The new value remains the active Run's latest complete checkpoint                                        | The current Attempt can retry yield while its lease remains valid; after process loss, ordinary lease-expiry recovery uses the same state                             |
| Recovery budget is exhausted                                                | Run seals as `failed`                                                                                    | The sealed Run receives no later attempt; see the [allocation boundary](15-run-attempt-persistence.md#run-and-runattempt-allocation-boundary)                         |
| Required state, schema, or codec is permanently incompatible                | No model or tool work starts                                                                             | Apply an explicit compatible reader or fail the Run                                                                                                                   |
| Frozen artifact or dependency is temporarily unavailable                    | Selected state remains unchanged                                                                         | Apply bounded backoff only when policy classifies the condition retryable and budget remains                                                                          |
| State object is confirmed missing or fails integrity validation             | Run seals as `failed` without selecting invalid state bytes                                              | Freeze the exact deterministic key, record a bounded failure, and never substitute listing results                                                                    |
| Waiting feedback is invalid or mismatches frozen deferred requests          | Waiting Run remains unchanged                                                                            | Reject input; do not create a new Run                                                                                                                                 |
| A required parent is absent, unauthorized, unsealed, or ineligible          | Run acceptance fails                                                                                     | Caller re-reads authorized history and chooses an eligible operation                                                                                                  |
| Write is initiated after Run sealing                                        | Frozen state and outcome remain unchanged                                                                | Reject before object I/O even if the caller has process-local bytes or a stale object version                                                                         |

Cancellation before durable acceptance creates no Run. Interrupt after acceptance seals the Run without selecting in-flight state and does not make the Run an eligible parent. A lost client response after possible acceptance is reconciled through the API idempotency contract.

## Compatibility

The compatibility axes remain independent:

| Version                                     | Owner                                |
| ------------------------------------------- | ------------------------------------ |
| Run domain object version                   | Foundation Run mutation contract     |
| Relational schema revision                  | Foundation Service migration history |
| `RunStateEnvelope.schema_version`           | Foundation Run state contract        |
| Run payload object schema version           | Foundation Run payload contract      |
| `HarnessState.schema_version`               | Agent Harness                        |
| Capability state version                    | Owning Capability                    |
| `EnvironmentExecutionConfig.schema_version` | Foundation Environment Configuration |
| Environment state version                   | Owning Environment provider          |
| Agent definition revision                   | Foundation immutable Agent domain    |
| Model execution snapshot schema             | Foundation Model Management domain   |

An unknown required state, payload, Harness, Capability, Environment, or provider version fails explicitly unless its owner supplies a compatible reader or migration. A sealed parent state is never rewritten for compatibility with a new Run; initialization reads and transforms it into the new Run-owned state. An active Run migration, when supported, is another fenced conditional replacement of the same key.

Relational migrations never reinterpret state bytes through current defaults. Adding a Trigger kind is additive only when old readers preserve it as unknown without executing or authorizing it.

## Trade-offs

One stable state key removes the duplicated base/result state model and makes a Run's current recovery value direct. It also introduces durable writes during execution and makes conditional-write fencing part of recovery correctness.

Each new Run owns a complete state copy, so initialization cost grows with the retained Thread state. Checkpointing reduces repeated model and tool work but cannot make external effects exactly once; unresolved calls follow the [Run Attempt recovery contract](15-run-attempt-persistence.md#recovery-and-budget-enforcement).

## Invariants

01. The Run is Foundation's durable Agent-work and recovery boundary; each Run owns one deterministic state key and no base, result, or selectable checkpoint-history object.
02. Start, continue, continue from, feedback, fork, and retry initialize a complete Run-owned state without mutating or aliasing the parent key. A null-head root-like Continue initializes empty state under the existing Thread ID.
03. Only the current leased and fenced `RunAttempt` can conditionally replace active state; `checkpoint_seq` and expected object versions prevent stale overwrite.
04. `input_disposition` prevents accepted input from being injected twice.
05. A consumed Thread inbox entry is represented in complete Run state by its stable receipt; a replacement Attempt reconciles that receipt before delivering pending inbox input.
06. `parent_run_id` is the sole semantic history edge. Waiting feedback creates another Run rather than mutating the sealed parent; `retry_of_run_id` records copied terminal intent without becoming another history edge.
07. Waiting and completed outcomes become authoritative only when relational sealing selects the matching state candidate; the sealed Run and state are immutable.
08. Persisted state restores data and correlation, never current authority. Events, Items, streams, accounting records, listings, and worker memory never select state.
09. Every accepted Run retains one non-secret model execution snapshot until sealing and one immutable safe model observation for history; a replacement attempt never reads current ModelConfig as a fallback.
10. Every accepted Run stores one exact non-secret Environment execution configuration and one exact effective managed Skill name tuple in `state.json`; checkpoint replacement cannot change either, and no relational snapshot column duplicates them.
11. `accepted` exists only before the first Attempt. Replacement Attempts and retry backoff keep the same Run `running`; a `failed` Run is sealed and never returns to `accepted`.
12. Several retained same-Thread Runs can share one completed parent; only the Thread's selected head and current Run determine the active continuation branch, and at most one Run in that Thread is active.
13. Before a Thread has selected any waiting or completed head, several failed or cancelled root-lineage Runs can precede another root-like acceptance; at most one root-lineage Run can be active or selected as waiting or completed.
14. A completed source Run, first queued submission, and already-state-backed successor can commit as one combined handoff; the final Thread head names the completed source, the current Run names the accepted successor, and no accepted successor can lack its complete initial state.
15. Graceful Attempt handoff conditionally updates the same state key and adds no checkpoint resource or selector; `yielded` releases execution authority without sealing the Run.
