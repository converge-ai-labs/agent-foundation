# Agent Control: Input and Continuation

## Design Position

Foundation accepts every new semantic unit of Agent work as a durable Run belonging to exactly one Session and Thread. Start, immediate continuation, continue from an explicit historical Run, waiting feedback, fork, and retry are distinct acceptance forms over that same boundary. An existing-Thread Run submission can instead create an editable queued submission when earlier work prevents immediate continuation. Every successful acceptance creates a new Run; queue admission creates no Run, and neither path reopens or mutates a sealed Run.

This contract owns those acceptance forms, their public command surfaces, and deferred-interaction feedback. Adjacent input, Thread, Run, RunAttempt, and recovery concerns remain with the owners below. Worker takeover and automatic checkpoint recovery do not accept another semantic unit of work and remain outside this contract.

## Boundaries

| Concern                                                                     | Owner                                                                                                                                           | Relationship                                                                     |
| --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Session, Thread, Run, and Item meaning                                      | [Platform Interaction Model](../interaction-model.md)                                                                                           | Supplies the shared interaction identities                                       |
| Ordinary semantic Agent input                                               | [Agent Input](33-agent-input.md)                                                                                                                | Supplies the versioned `AgentInput` accepted by input-bearing commands           |
| Start, immediate continue, continue from, fork, retry, and waiting feedback | This contract                                                                                                                                   | Accepts one new Run or rejects the operation without advancing the Thread        |
| Queue-if-busy existing-Thread Run submission                                | [Agent Control: Queued Submissions](36-agent-control-queued-submissions.md)                                                                     | Stores complete editable submission intent whose later consumption creates a Run |
| Thread version, current Run, and selected head                              | [Durable Thread Persistence](24-thread-persistence.md)                                                                                          | Supplies the exact advancement precondition and commits selected Run references  |
| Run state, lineage, accepted intent, and outcome                            | [Durable Run State](14-run-persistence.md)                                                                                                      | Persists the complete accepted input and its deterministic state key             |
| Worker claim and recovery inside one Run                                    | [Durable Run Attempt Persistence](15-run-attempt-persistence.md) and [Scheduling, Workers, and Recovery](16-scheduling-workers-and-recovery.md) | Creates replacement RunAttempts without accepting another Run                    |
| Asynchronous child acceptance and retained delivery                         | [Async Subagents](18-async-subagents.md)                                                                                                        | Supplies Host-owned child-result facts without redefining native deferred calls  |
| Common Thread inbox persistence                                             | [Agent Control: Active Execution](35-agent-control-active-execution.md#thread-inbox)                                                            | Stores available async-subagent results and their later consumption evidence     |
| Public API conventions and durable mutation evidence                        | [Platform API Conventions](../api-conventions.md) and [Durable Operations and Outbox](06-durable-operations-and-outbox.md)                      | Own shared version, idempotency, retry, and unknown-commit behavior              |

## Acceptance and Lineage

The accepted operation determines the new Run identity, state source, input protocol, and explicit retry correlation:

| Operation         | Public command                                | Thread effect                                                                                 | State and lineage                                                                                                                                                                | Accepted Run input                                    |
| ----------------- | --------------------------------------------- | --------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| Start             | `POST /api/v1/workspaces/{workspace_id}/runs` | Creates a root Thread with its first Run                                                      | `lineage_kind="root"`, `parent_run_id=null`                                                                                                                                      | New `AgentInput`                                      |
| Submit / Continue | `POST /api/v1/threads/{thread_id}/runs`       | Advances an eligible existing Thread immediately or queues the submission behind earlier work | On acceptance, completed head: `lineage_kind="continue"` and exact head parent; null head after failed/cancelled current Run: root-like with `lineage_kind="root"` and no parent | Submitted `AgentInput`                                |
| Continue From     | `POST /api/v1/runs/{source_run_id}/continue`  | Re-selects one completed historical Run and advances its Thread                               | `lineage_kind="continue"`, parent is the exact completed source Run                                                                                                              | New `AgentInput`                                      |
| Feedback          | `POST /api/v1/runs/{waiting_run_id}/feedback` | Advances the existing Thread                                                                  | `lineage_kind="continue"`, parent is the exact waiting Run                                                                                                                       | Complete normalized `WaitingRunFeedback`              |
| Fork              | `POST /api/v1/runs/{run_id}/fork`             | Creates an independent in-Session Thread with its first Run                                   | `lineage_kind="fork"`, parent is the exact completed source Run                                                                                                                  | New `AgentInput`                                      |
| Retry             | `POST /api/v1/runs/{run_id}/retry`            | Advances the existing Thread                                                                  | Copies the failed or cancelled source Run's lineage and state-source edge and records `retry_of_run_id`                                                                          | Copies the source Run's accepted input kind and value |

Start, continue, continue from, feedback, and fork have `retry_of_run_id=null`. Retry never uses the failed or cancelled source as `parent_run_id`: that field continues to name only the exact sealed state source. A retry of a failed root therefore has no parent; a retry of a failed continue, continue from, feedback, or fork copies the source Run's exact `parent_run_id` and `lineage_kind`.

Run acceptance authenticates and authorizes the caller or internal principal, validates the selected Session and Thread, resolves or preserves the exact AgentPresetVersion and profile-compatible Runtime lock required by the operation, resolves other exact revisions and current authority, freezes the new Run's non-secret model execution snapshot under [Model Management](25-model-management.md), applies scoped idempotency, publishes the complete initial Run state, and atomically creates or advances the Thread together with the accepted Run and its lifecycle publication intent. The Run becomes schedulable only after the complete initial state object is durably available.

Every successful Run acceptance returns `202` with this conceptual receipt:

```python
class RunAcceptanceReceipt:
    schema_version: Literal["1"]
    session_id: SessionId
    thread_id: ThreadId
    thread_version: int
    run_id: RunId
    run_version: int
    status: Literal["accepted"]
    hook_subscription_id: HookSubscriptionId | None
```

The response does not wait for Worker claim or Harness completion. The same principal, operation kind, resource scope, idempotency key, and canonical semantic request return the original receipt. Reuse with different content conflicts. A lost response after possible acceptance remains unknown until the caller repeats the same key or reads authoritative Run state.

`POST /api/v1/threads/{thread_id}/runs` instead returns the queue-aware [`ThreadRunSubmissionReceipt`](36-agent-control-queued-submissions.md#public-api). Its `run_accepted` outcome contains this receipt; its `queued` outcome contains the newly created queued submission and no Run identity.

### Common Acceptance Flow

```mermaid
sequenceDiagram
    participant Caller
    participant Control
    participant Objects as Object storage
    participant DB as Relational database
    participant Worker

    Caller->>Control: command whose selected outcome accepts a Run
    Control->>DB: authenticate, authorize, read evidence and candidate state
    DB-->>Control: detached versions, selectors, and source references
    Control->>Objects: acquire or verify input, read parent, publish new state
    Control->>DB: commit optional HookSubscription + Run + facts + evidence + Outbox
    DB-->>Control: committed acceptance receipt
    Control-->>Caller: 202 RunAcceptanceReceipt
    Worker->>DB: later scan and claim
```

No relational transaction spans input acquisition, object storage, Harness state transformation, or Worker execution. The final short transaction repeats every authority, version, current/head, source, digest, and idempotency precondition needed by the operation. Objects published before a losing or rolled-back transaction are non-authoritative cleanup candidates.

Every input-bearing command in this contract can carry the optional inline `hook_subscription` owned by [Hook Notifications](20a-hook-notifications.md#durable-hook-subscriptions). On immediate acceptance it participates in the canonical command request, commits atomically with the accepted Run, and returns its ID in the receipt. When an existing-Thread submission queues, the exact inline input remains part of the editable queued submission intent; Foundation creates no HookSubscription until the queue consumption transaction accepts its Run. It is neither Agent input nor an invocation option and cannot affect execution. Hook shape, authorization, scope, delivery, and idempotency semantics remain with that owner.

## Input-Bearing Operations

Start, existing-Thread Run submission, and continue-from requests carry an [`AgentInput`](33-agent-input.md#agent-input-protocol), a selected stable AgentPreset when permitted, an optional active-Version precondition, optional `selected_skill_names`, an optional policy-permitted tuple of [`EnvironmentSelectionEntry`](19-environment-management.md#environment-selection-and-run-state), and, for start, an optional Session selector. Fork accepts the same ordinary input and policy-permitted selections against its source. Managed trigger provenance is supplied only by its trusted ingress, not as caller-declared metadata on the public start command. Fields other than `AgentInput` are command options and never enter its `content` or `structured_content`.

The existing-Thread route's reusable submitted intent has this conceptual shape. Its fields are top-level fields of the POST request beside `expected_thread_version`; a queued resource retains them together as `submission`.

```python
class ThreadRunSubmissionIntent:
    input: AgentInput
    agent_preset_id: AgentPresetId | None
    agent_preset_version_id: AgentPresetVersionId | None
    selected_skill_names: tuple[str, ...] | None
    environment_selections: tuple[EnvironmentSelectionEntry, ...] | None
    hook_subscription: InlineHookSubscriptionInput | None
```

`selected_skill_names` follows the [Foundation Skill selection contract](27-skill-management.md#agentpresetversion-selection). Acceptance stores its normalized effective selection in `state.json`; that effective value participates in the canonical request digest.

Only start, existing-Thread Run submission, continue from, fork, and equivalent Host-owned initial Run submission can supply ordinary invocation options. A queued existing-Thread submission preserves those options as unaccepted intent and resolves and reauthorizes them only when consumption accepts a Run. Feedback and retry accept no AgentPreset, Version, Skill, Environment, or input override; they preserve the exact selections and accepted intent required by their source Run.

### Start and Continue

Start accepts Agent work that does not continue an existing Thread:

```http
POST /api/v1/workspaces/{workspace_id}/runs
Idempotency-Key: opaque-caller-key
```

The request names one stable `agent_preset_id`, can carry `agent_preset_version_id` only as an active-Version precondition, and supplies one `AgentInput`. Acceptance resolves the Preset's current active Version and Runtime lock, validates the complete request against that exact Version, then selects or creates one Session and creates its root Thread. It initializes `HarnessState.new()` and atomically commits the version `1` Thread row, its first root Run, lifecycle facts, idempotency evidence, and Outbox records. Foundation exposes no standalone empty-Thread create operation.

The existing-Thread Run route accepts a continuation immediately when the Thread is eligible and otherwise queues the complete submission intent:

```http
POST /api/v1/threads/{thread_id}/runs
Idempotency-Key: opaque-caller-key
```

The request carries `expected_thread_version` plus the fields of `ThreadRunSubmissionIntent`. Foundation locks the Thread, verifies the expected version, and applies the queue admission order owned by [Queued Submissions](36-agent-control-queued-submissions.md#admission-and-mutation-rules):

1. if any queued submission exists, append the complete intent after it;
2. otherwise, if the current Run is `accepted`, `running`, or `waiting`, create a queued submission;
3. otherwise, accept the continuation immediately from the completed head or through the null-head root-like rule below when eligible; and
4. otherwise, retain the submission in the queue until Retry, Continue From, Feedback, or another explicit operation establishes an eligible state.

Queue admission creates no Run, does not change `Thread.version`, increments `Thread.queue_version`, and returns `outcome="queued"`. Immediate acceptance returns `outcome="run_accepted"`, creates the Run, and increments `Thread.version`. Both outcomes are selected against locked commit-time state and are preserved by the route's idempotency evidence.

When `head_run_id` names an exact completed Run, Foundation never infers a parent from timestamps. The selected Preset defaults to that parent's stable Preset. Acceptance resolves its current active Version and Runtime lock, rejects incompatible parent-state or input migration, initializes a new Run-owned state from the completed parent, creates the Run with `lineage_kind="continue"`, sets `current_run_id` to it, preserves the head until the successor seals, increments Thread version, and commits the Run and its related durable facts.

When `head_run_id=null` and the current Run is `failed` or `cancelled`, the same route performs root-like acceptance in the existing Thread. The new Run has `lineage_kind="root"`, `parent_run_id=null`, and `retry_of_run_id=null`. A trusted Foundation state adapter constructs a complete empty Harness state carrying the existing Thread's exact `thread_id`; it does not call `HarnessState.new()`, which would create another Thread ID. When omitted, the stable Preset defaults to the current failed or cancelled Run's stable Preset; acceptance resolves its current active Version and the ordinary policy-permitted selections. The acceptance transaction selects the new Run as current, leaves the head null until a waiting or completed outcome seals, and increments Thread version. This is new input rather than Retry of the failed or cancelled intent.

A waiting head is not equivalent to an absent head and is eligible only for Feedback, Retry of an already accepted failed/cancelled feedback successor, or an explicit operation such as Continue From that selects another completed source. An existing-Thread Run submission queues instead of conflicting and remains unconsumed until one of those operations establishes an eligible state.

Schedules, Webhooks, service requests, managed Triggers, and Host-managed asynchronous children use the same Run-acceptance application boundary even when their owning ingress is not one of these public routes.

### Continue From an Explicit Run

Continue From accepts another same-Thread continuation from one caller-selected historical state:

```http
POST /api/v1/runs/{source_run_id}/continue
Idempotency-Key: opaque-caller-key
```

The request has the same `expected_thread_version`, `AgentInput`, Preset, Version-precondition, Skill, Environment, and optional Hook fields as ordinary Continue. The source must be a retained, readable `completed` Run in the target Thread, but it need not be that Thread's current Run or selected head. Foundation also requires that the Thread have no current `accepted` or `running` Run.

Acceptance initializes state from the exact source and creates a same-Thread Run with `lineage_kind="continue"` and `parent_run_id=source_run_id`. In the final short transaction it sets `current_run_id` to the new Run, sets `head_run_id` to the selected source, increments the Thread version, and commits the accepted Run and related facts. If the successor later seals as `waiting` or `completed`, it becomes the new head. If it seals as `failed` or `cancelled`, the selected source remains the head.

Selecting a source other than the prior head intentionally abandons that prior branch as the Thread's active continuation selection. In particular, a historical waiting Run that is no longer both current and head is not eligible for Feedback. The operation does not synthesize rejection or no-response values for that abandoned pending set; the sealed Run remains retained history.

Several retained successors can therefore share the same completed parent over time. The single-active-Run constraint serializes execution, while `parent_run_id` preserves every accepted branch. Continue From preserves the Thread ID and is distinct from Fork, which creates another Thread, and from automatic recovery, which creates no Run.

### Fork

Foundation exposes an explicit in-Session Thread fork from one selected completed Run:

```http
POST /api/v1/runs/{run_id}/fork
Idempotency-Key: opaque-caller-key
```

Any retained and readable completed Run is eligible, including a historical Run that is neither the source Thread's `current_run_id` nor `head_run_id`.

The request carries an `AgentInput` and the compatible policy-permitted selections defined above. Foundation authorizes the source Run and Session, verifies the source's exact frozen state, applies `HarnessState.fork()`, and atomically creates a `role="child"`, `origin_kind="fork"` Thread plus its first accepted Run. The new Run's `parent_run_id` names the source Run even though the Run belongs to the new Thread. Omitting the Preset selection reuses the source Run's exact Preset Version and Runtime lock; an explicit compatible selection resolves its current active Version under the fork policy.

The source Run is part of fork's common idempotency scope. A Session fork that creates a new Session and root Thread remains a distinct Session-domain operation and is never implied by this route.

## Retry of Terminal Intent

```http
POST /api/v1/runs/{run_id}/retry
Idempotency-Key: opaque-caller-key
```

The target must be the Thread's current failed or cancelled Run. The request carries `expected_thread_version` and accepts no new `AgentInput` or invocation option. Acceptance copies the source Run's `input_kind`, exact normalized accepted input or feedback value, `parent_run_id`, `lineage_kind`, AgentPresetVersion, Runtime lock, effective Skill selection, and Environment execution configuration. The copied accepted input retains each binary URL or Environment-path source description or exact immutable `asset_id`; Retry acceptance reauthorizes that source but does not read or copy its file body. Acceptance records the source in `retry_of_run_id`, reauthorizes every retained selector, creates a new Run-owned state from the same eligible state source, and obtains fresh credentials, `RunBindings`, Environment attachments, runtime mounts, `EnvironmentRuntime`, recovery authority, and model execution snapshot for the new Run. Execution treats Retry as a new Run with zero prior model requests and reacquires any binary bytes required by its frozen delivery. An Asset ID never resolves to replacement content; deletion or unavailability fails the new Run explicitly.

Retry never re-runs a client-side effect, changes an accepted feedback decision, or makes the failed source an eligible state parent. If the failed source was a root, the retry initializes another root state. Otherwise it repeats the source operation's state transformation from the same frozen parent. Once another Run advances the Thread, the old terminal Run is no longer retryable.

## Deferred Interaction

Approval, client-tool execution, structured user input, and awaited child results are frozen pending facts inside one sealed waiting Run and its complete state object. They are not independent mutable resources or relational rows. Provider-owned continuation can also seal a Run as waiting, but it is resumed only when its owning internal integration supplies a complete provider result through the same new-Run feedback acceptance boundary. It is not accepted by the public feedback command. A pending set containing `provider_continuation` is therefore ineligible for this route rather than being partially finalized around it.

Feedback-eligible pending kinds remain distinct:

- `approval` asks an authorized human or service to permit a proposed action;
- `client_tool` asks an external client to perform a named effect and return its declared result;
- `user_input` requests structured information without authorizing another effect; and
- `child_result` waits for the exact immutable outcome of one accepted asynchronous child Run.

The exact native deferred requests and Host-owned child wait requests live in `RunStateEnvelope.host.deferred`; the Run row stores only the matching bounded `RunPendingSummary` owned by [Durable Run State](14-run-persistence.md#run-state-object). The public route exposes those facts as read projections beneath the waiting Run and accepts one atomic feedback command:

```http
POST /api/v1/runs/{waiting_run_id}/feedback
Idempotency-Key: opaque-caller-key
```

The submitted resolution union is a serialized wire schema:

```python
class ApprovePendingResolution:
    call_id: str
    action: Literal["approve"]


class RejectPendingResolution:
    call_id: str
    action: Literal["reject"]


class CompletePendingResolution:
    call_id: str
    action: Literal["complete"]
    result: JsonValue


class RespondPendingResolution:
    call_id: str
    action: Literal["respond"]
    response: JsonValue


type SubmittedPendingResolution = (
    ApprovePendingResolution
    | RejectPendingResolution
    | CompletePendingResolution
    | RespondPendingResolution
)


class WaitingRunFeedbackRequest:
    expected_thread_version: int
    sealed_state_digest_sha256: str
    resolutions: tuple[SubmittedPendingResolution, ...] = ()
    hook_subscription: InlineHookSubscriptionInput | None = None
```

The frozen pending kind determines which action and result schema are legal: `approve` and `reject` apply only to approval, `complete` applies to an exact client-tool or authorized child-result request, and `respond` applies only to structured user input. `result` and `response` use the versioned JSON encoding declared by the exact pending request and its locked adapter. The request cannot repeat a call ID, name an unknown call, supply a mismatched action, or override the pending kind.

Submitting this route finalizes the entire feedback-eligible pending set. The caller can supply any subset explicitly; Foundation expands every omitted call in frozen request order before acceptance:

| Pending kind   | Explicit action       | Omitted outcome |
| -------------- | --------------------- | --------------- |
| `approval`     | `approve` or `reject` | `reject`        |
| `client_tool`  | `complete`            | `no_response`   |
| `user_input`   | `respond`             | `no_response`   |
| `child_result` | authorized `complete` | `no_response`   |

An empty `resolutions` tuple therefore rejects every approval and records no response for every other feedback-eligible call. This is fail-closed finalization, not a partial update. The principal or internal caller must be authorized to finalize every pending call because omission also changes the continuation result.

The accepted Run stores the complete normalized value, not only the submitted subset:

```python
type PendingResolutionOutcome = Literal[
    "approve",
    "reject",
    "complete",
    "respond",
    "no_response",
]


class AcceptedPendingResolution:
    call_id: str
    kind: Literal[
        "approval",
        "client_tool",
        "user_input",
        "child_result",
        "provider_continuation",
    ]
    outcome: PendingResolutionOutcome
    result: JsonValue | None


class WaitingRunFeedback:
    schema_version: Literal["1"]
    waiting_run_id: RunId
    sealed_state_digest_sha256: str
    resolutions: tuple[AcceptedPendingResolution, ...]
```

Every accepted `WaitingRunFeedback` covers its complete owning pending set exactly once and preserves frozen request order. For the public route that set contains only feedback-eligible kinds; an internal provider continuation supplies every exact provider-owned result without omission defaults. Foundation expands public defaults and validates the complete typed value before computing the canonical semantic request digest. Explicitly rejecting an approval is therefore equivalent to omitting it; non-approval `no_response` arises only through omission. The accepted feedback is the immutable input of the new Run; the waiting parent and its pending summary never change.

### Harness and Child-Result Mapping

The Worker maps the accepted batch by owning boundary:

| Pending kind            | Mapping                                                                                                                                                                      |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `approval`              | Native `ToolApproved` or `ToolDenied`.                                                                                                                                       |
| `client_tool`           | `complete` becomes the declared native result; `no_response` becomes an explicit failed external-call result, never a missing entry or successful `null`.                    |
| `user_input`            | `respond` is validated against the exact request; `no_response` uses the locked interaction adapter's explicit representation.                                               |
| `child_result`          | Enters through the Host-owned fresh-run input seam in [Async Subagents](18-async-subagents.md), never through `DeferredToolResume` or under the original spawn tool-call ID. |
| `provider_continuation` | Enters through its owning Host/model integration and is never synthesized by the public omission policy.                                                                     |

For native Pydantic requests, Foundation constructs one `DeferredToolResults` whose `calls` and `approvals` maps exactly cover the authoritative `DeferredToolRequests`, then passes both through the Harness [`DeferredToolResume`](../agent-harness/16-input-model-and-output.md#input) with the prior state and fresh `RunBindings` containing a freshly constructed `EnvironmentRuntime`. Defaults are therefore explicit results by the time Harness preflight runs. A batch that also contains child-result feedback can supply its Host-owned fresh-run input at the same new Harness Run boundary without reinterpreting that result as a native deferred call.

A child-result `no_response` closes this waiting batch but does not prove child cancellation, consume its Thread inbox entry, or discard an independently retained result. The child relationship and any available inbox entry continue under the asynchronous-child cancellation, retention, and later-incorporation policy.

## Suspension and Feedback Run

```mermaid
sequenceDiagram
    participant Harness
    participant Worker
    participant Durable as Database and object storage
    participant Responder
    participant NextWorker as Next worker

    Harness-->>Worker: suspended result, complete state, native requests
    Worker->>Durable: publish waiting state candidate
    Worker->>Durable: seal waiting Run and pending summary
    Worker-->>Worker: close RunAttempt resources and release lease
    Responder->>Durable: idempotent feedback with explicit subset
    Durable->>Durable: authorize all calls and expand omitted defaults
    Durable->>Durable: accept complete feedback as a new Run
    NextWorker->>Durable: claim the new Run's first RunAttempt
    NextWorker->>Harness: prior state, complete DeferredToolResume, fresh RunBindings and EnvironmentRuntime
```

Suspension first conditionally publishes the complete waiting state candidate at the Run's deterministic state key. One fenced relational transition verifies that the Run remains current, seals it, terminalizes the source RunAttempt, copies the bounded pending summary to the Run row, selects the exact state digest and checkpoint sequence, retains the waiting Run as current, selects it as the Thread head, increments Thread version, and commits lifecycle facts. The worker then closes Harness, Environment connector, credential, socket, and database resources.

Feedback authenticates the responder, locks the Thread, requires both `current_run_id` and `head_run_id` to name the target waiting Run, verifies the expected Thread version and sealed-state digest, authorizes the complete pending set, validates and expands the submitted subset, and applies scoped idempotency. After publishing the new Run's complete initial state and any object-backed feedback, one short transaction repeats those preconditions, inserts a Run with `input_kind="waiting_feedback"` and `parent_run_id=waiting_run_id`, selects it as current, increments Thread version, and commits lifecycle facts, idempotency evidence, response Items when applicable, and outbox intents. When an explicit child result is selected, that same transaction marks its exact Thread inbox entry consumed with the new Run and initialized state digest at checkpoint zero. The accepted feedback itself records the exact consumed pending identities; no mutable pending-action row is updated.

If the feedback Run later fails or is cancelled, retry can only reaccept that same normalized feedback intent. The waiting parent remains frozen, but it is no longer the Thread's current Run, so another feedback command with different content cannot race the accepted successor.

## Persistence Impact

Related persistence integration is:

| Persistence owner        | Required contract                                                                                                                                                                                                                                                               |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runs`                   | Stores `input_kind` to distinguish `agent_input` from `waiting_feedback`, and nullable `retry_of_run_id` to correlate exact terminal intent without changing the state-parent edge. Existing inline or object-backed JSON columns store the complete accepted descriptor value. |
| Run lineage constraints  | Permit several retained same-Thread successors to share one completed `parent_run_id`; only the partial unique constraint for one `accepted` or `running` Run per Thread serializes active advancement.                                                                         |
| Waiting pending data     | No `pending_actions` table. The waiting Run row stores only `pending_json`; its sealed state stores exact native and Host requests.                                                                                                                                             |
| Async child delivery     | The child relationship and common `thread_inbox` entry remain authoritative; explicit selection consumes the exact entry while batch feedback never runs it into a native deferred call.                                                                                        |
| Inline Hook delivery     | Optional creation commits atomically under [Hook Notifications](20a-hook-notifications.md); a queued submission retains unaccepted Hook input, and no Run column stores callback configuration.                                                                                 |
| Durable command evidence | Existing idempotency, lifecycle, Item, and outbox records commit with the accepted Run under their owning contracts.                                                                                                                                                            |

`input_kind`, `retry_of_run_id`, the exact parent edge, Thread head selection, and the source or current Run's status make every acceptance form queryable without adding an `agent_control_operations` table or another execution resource.

## Failure Semantics

| Condition                                                                                         | Outcome                                                                                                                 |
| ------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Start, immediate continue, continue from, fork, retry, or feedback validation fails before commit | No Thread or Run advancement occurs                                                                                     |
| Concurrent Thread advancement or sealing wins                                                     | The stale command conflicts and changes nothing                                                                         |
| Retry target is not the current failed or cancelled Run                                           | Retry conflicts without creating a Run                                                                                  |
| Feedback contains a duplicate, unknown, wrong-action, invalid-result, stale, or expired call      | No feedback Run is created; the waiting parent remains unchanged                                                        |
| Feedback responder cannot finalize every pending call                                             | Request is denied without disclosing concealed pending content                                                          |
| Native deferred requests and normalized results do not exactly cover one another                  | Feedback is rejected before acceptance when detectable; otherwise the accepted feedback Run fails before new model work |
| Resume surface differs from the suspended surface                                                 | The accepted feedback Run fails before Harness continuation                                                             |
| Child result is omitted                                                                           | The batch records `no_response`; child execution and retained delivery follow their independent policy                  |

## Invariants

01. Start, immediate continue, continue from, feedback, fork, and terminal retry accept another Run; an existing-Thread submission can instead queue without accepting one. Acceptance returns before later Worker claim, RunAttempt allocation, or Harness execution.
02. Immediate Continue and Continue From preserve the Thread ID; Fork creates a distinct Thread ID. Continue with a null head initializes empty state under that existing ID rather than calling `HarnessState.new()`.
03. Retry accepts no new input or invocation option, copies the exact accepted intent and state-source edge, and records `retry_of_run_id`.
04. Ordinary Agent work uses `AgentInput`; waiting feedback uses the separate complete normalized `WaitingRunFeedback` protocol.
05. Feedback finalizes the complete eligible pending set; omitted approvals reject and omitted non-approval calls record no response.
06. A waiting Run is sealed and holds no RunAttempt lease; feedback creates a new Run whose `parent_run_id` names it.
07. Native deferred values remain exactly correlated, Host-owned child delivery never impersonates a native deferred call, and approval, external effect, and child completion remain independent facts.
08. Pending actions are immutable projections of one waiting Run, not independently mutable resources or relational rows.
09. Continue From accepts any retained and readable completed Run in the same Thread, atomically selects it as head while creating its successor, and does not require that source to be the prior current Run or head.
10. Existing-Thread Run submission never bypasses a queued submission. With an empty queue it queues while the current Run is `accepted`, `running`, or `waiting`; otherwise it uses the exact completed head or, only after a failed or cancelled current Run with `head_run_id=null`, accepts a root-like Run with no parent.
