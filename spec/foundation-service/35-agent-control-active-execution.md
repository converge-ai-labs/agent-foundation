# Agent Control: Active Execution

## Design Position

Foundation controls a running or waiting Thread through a durable ordered inbox and controls an active Run through an explicit interrupt command. Steering appends one accepted user-supplied semantic input without creating another Run. An asynchronous-subagent result is Host-owned Agent input carried through the same delivery order. A running target receives either kind in the current Run; a waiting target records either kind against that waiting source for its direct successor. Interrupt seals an active Run as `cancelled`; later work creates another Run and Harness Run rather than restoring the interrupted process-local Run.

PostgreSQL is authoritative for inbox acceptance, the Thread-level cross-kind FIFO, Run binding, consumption, rollover, and Run lifecycle. A Thread-scoped Redis control Stream only wakes the Worker early. Its consumer-group cursor remains in Redis, its entries are bounded and expire after inactivity, and loss of the Stream never loses an accepted inbox entry or changes Run state. The control API therefore remains stateless and does not route directly to a process-local Agent instance.

[Agent Input](33-agent-input.md) owns the `AgentInput` carried by steering. [Durable Run State](14-run-persistence.md) owns the resulting checkpoint and sealed outcome, while [Run Attempts, Scheduling, and Recovery](15-run-attempt-scheduling-and-recovery.md) owns the current Worker fence, scheduling, and process-local dispatcher invocation points.

## Boundaries

| Concern                                                             | Owner                                                                                                                                           | Relationship                                                                                                                  |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Thread inbox identity, FIFO, Run binding, status, and common store  | This contract                                                                                                                                   | Persists inputs and internal results awaiting one Thread without making Redis authoritative                                   |
| Public steer, steer status, and interrupt commands                  | This contract                                                                                                                                   | Accepts running-Run or waiting-head input, exposes its exact receipt status, or seals the active Run                          |
| Steering `AgentInput` and binary normalization                      | [Agent Input](33-agent-input.md)                                                                                                                | Supplies the same accepted input protocol used by start, continue, and fork                                                   |
| Async-subagent result payload and selection                         | [Async Subagents](18-async-subagents.md)                                                                                                        | Uses the common inbox row while retaining child-result authorization, expiry, and selection semantics                         |
| State envelope, checkpoint write, and sealed-state shape            | [Durable Run State](14-run-persistence.md)                                                                                                      | Supplies complete state and inbox receipts for steer and asynchronous-result consumption                                      |
| Worker lease, fence, scheduling, dispatcher, takeover, and recovery | [Run Attempts, Scheduling, and Recovery](15-run-attempt-scheduling-and-recovery.md)                                                             | Supplies current Worker authority and runs this contract's reconciliation flow at the required execution boundaries           |
| Redis commands and backend behavior                                 | [Foundation Storage Capabilities](03-storage.md#redis-compatible-data-structures)                                                               | Supplies Streams, consumer groups, bounded trimming, and expiry without assigning domain authority                            |
| Authentication, authorization, and idempotency evidence             | [Identity and Access Management](10-identity-and-access-management.md) and [Durable Operations and Outbox](06-durable-operations-and-outbox.md) | Own current permission, replay evidence, and unknown-commit reconciliation; no outbox is required solely for a control signal |
| Common API behavior                                                 | [Management API](21-management-api.md) and [Platform API Conventions](../api-conventions.md)                                                    | Catalogs the exact public commands and receipt read without exposing the internal inbox as a generic resource                 |

## Thread Inbox

A Thread inbox entry is one durable input or internal result offered to an existing Thread independently of Run creation. The entry has its own identity, kind-owned payload, immutable Thread-level delivery order, current Run or waiting-source binding, status, retention, and consumption evidence. It does not replace Run input, Thread advancement, waiting feedback, lifecycle events, or Harness state. Ordinary steer and asynchronous-subagent results share one FIFO; kind-specific payload and authorization do not create separate scheduling lanes.

The following schema is conceptual. The relational table is named `thread_inbox`.

```python
type ThreadInboxKind = Literal[
    "steer",
    "async_subagent_result",
]
type ThreadInboxStatus = Literal[
    "pending",
    "consumed",
    "superseded",
    "suppressed",
    "expired",
    "discarded",
]


class InboxPayloadObjectRef:
    object_key: str
    digest_sha256: str
    size_bytes: int
    content_type: str
    schema_version: str


class ThreadInboxEntry:
    id: ThreadInboxEntryId
    tenant_id: TenantId
    thread_id: ThreadId

    kind: ThreadInboxKind
    delivery_sequence: int
    accepted_against_run_id: RunId | None
    target_run_id: RunId | None
    source_waiting_run_id: RunId | None
    origin_run_id: RunId | None
    payload_schema_version: str
    payload: JsonValue | None
    payload_object: InboxPayloadObjectRef | None

    status: ThreadInboxStatus
    consumed_by_run_id: RunId | None
    consumed_state_digest_sha256: str | None
    consumed_checkpoint_seq: int | None

    expires_at: datetime | None
    created_at: datetime
    finalized_at: datetime | None


class ThreadInboxCounter:
    tenant_id: TenantId
    thread_id: ThreadId
    next_delivery_sequence: int
    pending_count: int
    pending_bytes: int
```

`delivery_sequence` is positive, immutable, and monotonic within one Thread across both kinds. The acceptance transaction locks the Thread's independent `thread_inbox_counters` row and allocates the next value. The PostgreSQL commit order admitted by that counter lock is the authoritative FIFO order. Allocating or finalizing an inbox entry changes neither `Thread.version` nor `Thread.queue_version`. Gaps caused by a terminal inbox status are valid and never permit a later pending entry to bypass an earlier eligible one.

Exactly one of `payload` and `payload_object` is present. The entry kind selects the exact versioned payload contract: `steer` stores one canonical accepted [`AgentInput`](33-agent-input.md#accepted-input-and-canonicalization), while `async_subagent_result` stores the payload owned by [Async Subagents](18-async-subagents.md#result-publication). A kind can be added only with an owning payload, authorization, targeting, consumption, expiry, and compatibility contract.

`accepted_against_run_id` is immutable and present only for public steer. It records the current running or waiting Run named by the accepted command and keeps the public receipt stable even when waiting rollover changes later binding. `origin_run_id` is immutable and present only for an async-subagent result; it equals the spawning `ChildRunRelationship.parent_run_id` and provides the relational lock and index authority for origin-outcome suppression without decoding payload JSON. `target_run_id` names the Run that may currently consume the entry. `source_waiting_run_id` names the sealed waiting Run whose direct successor must receive the entry. A pending entry accepted or published while the Thread is waiting has no target and names that waiting Run as source. Feedback or waiting Continue atomically binds it to the direct successor while retaining the source until consumption or a later waiting rollover. An entry offered directly to an accepted or running Run has a target and no waiting source. An asynchronous result for an otherwise inactive eligible Thread can initially have neither binding while its owner applies queue precedence and automatic successor acceptance.

`pending` is the only consumable state. `consumed` means the kind-specific consumer committed its authoritative destination together with the listed consumption evidence. A steer can become only `consumed` or `superseded`; it does not expire, suppress, or support explicit discard. An async-subagent result can become `consumed`, `superseded`, `suppressed`, `expired`, or `discarded`. `suppressed` means its spawning Run failed or was cancelled before consumption, so the retained result permanently authorizes no active delivery, waiting binding, or automatic successor. Every terminal status is immutable except for retention redaction allowed by the payload owner.

`ThreadInboxCounter.pending_count` and `pending_bytes` are transactionally maintained admission counters, not public Thread fields. Deployment policy bounds both values. Public steer admission rejects an overflow without inserting an entry. Async-result publication uses its separately bounded payload/object form and retention policy, but it must still reserve the common Thread budget before a pending entry becomes accepted; bounded reconciliation retries a result whose publication cannot yet reserve capacity. A result published directly as `suppressed` reserves no pending budget. Finalization releases previously reserved counters exactly once.

An entry is eligible for a Run when it is `pending`, bound to that exact target, next in the Thread FIFO among deliverable entries, within kind-owned retention, still authorized and decodable by its locked adapter, and, for an async result, has a spawning Run that is not `failed` or `cancelled`. A no-longer-eligible async result must first commit its owning `suppressed`, `expired`, or `discarded` status; a steer that cannot be permanently authorized or decoded causes the owning Run's explicit failure classification and later `superseded` disposition rather than silent skipping. “No eligible pending delivery” therefore means the outcome transaction has locked and reconciled every earlier pending row, not that a detached query happened to return none.

The table preserves these constraints and access paths:

01. `(tenant_id, id)` is unique, and the owning Thread belongs to that tenant.
02. Every present accepted-against, target, waiting-source, or origin Run belongs to the same tenant and Thread; target and waiting source cannot identify the same Run.
03. Payload columns form an exactly-one representation group.
04. Consumption fields are absent unless `status="consumed"` and are all required when it is consumed. `finalized_at` is present exactly for a terminal status.
05. Every entry has one unique `(tenant_id, thread_id, delivery_sequence)`. Steer rows require `accepted_against_run_id`, forbid `origin_run_id` and `expires_at`, and follow only consumed or superseded terminalization. Async rows require `origin_run_id` equal to the immutable parent Run of their unique relationship and forbid `accepted_against_run_id`; the async payload owner defines relationship uniqueness and expiry.
06. A pending row has exactly one legal binding shape: active target only, waiting source only, waiting-derived target plus source, or unbound async-result reconciliation. A consumed row has `consumed_by_run_id=target_run_id` as last bound; terminal non-consumed rows retain safe provenance but authorize no delivery.
07. `(tenant_id, thread_id, status, delivery_sequence)` supports strict cross-kind FIFO reconciliation and admission-bound checks.
08. `(tenant_id, target_run_id, status, delivery_sequence)` supports ordered active-Run delivery and the mandatory pending check before completed outcome commit.
09. `(tenant_id, source_waiting_run_id, status, delivery_sequence)` supports direct-successor binding, branch supersession, and waiting rollover.
10. `(tenant_id, kind, status, delivery_sequence)` supports bounded control reconciliation of pending asynchronous results whose Threads have no active Worker.
11. `(tenant_id, origin_run_id, kind, status, delivery_sequence)` supports locked suppression of all not-yet-consumed asynchronous results when their spawning Run fails or is cancelled.
12. `thread_inbox_counters` has exactly one same-tenant row per Thread, a positive next sequence, and non-negative bounded pending counters; it is created atomically with the Thread and is not part of optimistic Thread advancement.

## Steer Command

```http
POST /api/v1/runs/{run_id}/steer
Idempotency-Key: opaque-caller-key
```

The request carries one submitted `AgentInput`. Foundation validates and canonicalizes its binary source descriptions, exact Asset IDs, and delivery selections without acquiring source bytes. The final short transaction authenticates and authorizes `run.steer` from the IAM [stable action registry](10-identity-and-access-management.md#stable-action-registry), revalidates every Asset reference, then locks the owning Thread, named Run, inbox counter, and affected inbox rows in canonical order. It requires the named Run to remain current and either:

- `status="running"`, in which case the new entry binds directly to that Run; or
- `status="waiting"` with `current_run_id=head_run_id=run_id`, in which case the entry records that waiting Run as its source and has no active target.

The transaction reserves pending count and bytes, allocates the next Thread `delivery_sequence`, inserts one `pending` steer, and commits idempotency evidence. It neither creates another Run nor changes Thread head, current-Run selection, `Thread.version`, or `Thread.queue_version`. The exact Run identity and locked current/head precondition make a caller-supplied Run version unnecessary.

A successful command returns `202` with this conceptual receipt:

```python
class SteerReceipt:
    schema_version: Literal["1"]
    session_id: SessionId
    thread_id: ThreadId
    run_id: RunId
    steer_id: ThreadInboxEntryId
    delivery_sequence: int
    accepted_at: datetime
```

The same idempotency key and canonical request return the same entry and receipt. Reuse with different input conflicts. `202` proves durable acceptance, not that a Worker, Harness, model, or external tool has incorporated the input. The following authorized read exposes the exact steer status without making the internal Thread inbox a generic public resource:

```http
GET /api/v1/runs/{run_id}/steers/{steer_id}
```

The read returns safe identity, the immutable accepted-against Run, current target or waiting-source binding, `delivery_sequence`, `pending`, `consumed`, or `superseded` status, consumption correlation when present, and timestamps. It does not return the accepted payload or an object-store locator. Foundation exposes no generic Thread-inbox collection or cross-kind inbox-entry endpoint.

After the relational commit, the control process best-effort appends one business-payload-free reconcile signal to the Thread control Stream. Failure or unknown outcome of that Redis write neither rolls back the accepted inbox entry nor creates an outbox record solely for retrying the signal. The process records bounded diagnostics and readiness follows the shared Redis dependency contract.

## Unified FIFO Delivery and State Commitment

The current Worker reconciles all eligible `pending` entries bound to its Run in ascending `delivery_sequence`. A later entry never bypasses an earlier eligible one because of kind, Redis arrival, payload location, or adapter readiness. Before moving to a later sequence, the Worker must consume the earlier entry or commit its kind-owned `suppressed`, `expired`, `discarded`, or `superseded` disposition. A Redis signal can cause immediate reconciliation, but the Worker also queries PostgreSQL when it starts or takes over an Attempt, before each model request, after complete tool batches, before checkpoints, and before every outcome decision. The one exception is a waiting successor whose accepted feedback input is still `pending`: it may reconcile existing durable receipts and terminal dispositions but does not read or offer bound inbox payloads until the [first-request hook gate](#waiting-binding-and-first-request-hook-delivery) opens.

For each contiguous FIFO batch, the Worker:

1. reads the accepted payloads and revalidates their current binding, per-kind source descriptions, relationship, async-result spawning-Run outcome, authorization, visibility, retention, adapter, current Run, Attempt, fence, and lease outside a long transaction;
2. maps steer through the accepted `AgentInput` binary-delivery rules and maps an async result through the bounded untrusted-content adapter owned by [Async Subagents](18-async-subagents.md#delivery-to-an-active-run);
3. enters the Foundation process-local [run-control critical section](16a-harness-runtime-integration.md#meaning-of-the-run-control-barrier) and offers the adapted values to Harness native enqueue in `delivery_sequence` order without interleaving a later inbox entry;
4. exports complete state containing the resulting Harness messages plus one Host-owned receipt per incorporated entry;
5. conditionally replaces the Run's deterministic `state.json` under the current object version and Attempt fence; and
6. in a new short transaction, locks the Thread, Run and every async-result spawning Run in the affected prefix in stable ID order, current RunAttempt, inbox counter, and affected entries in canonical order; revalidates the same current Run, Attempt, fence, lease, exact FIFO prefix, origin outcomes, and published state; marks an entry whose origin is now failed or cancelled `suppressed`; and otherwise marks the incorporated contiguous prefix `consumed` with the exact state digest and checkpoint sequence and releases its pending counters.

The state receipt contains each inbox entry ID and kind. It is retained across replacement Attempts of the same Run and is not inherited merely because another Run copied parent state. `consumed` means that a relational transaction verified an exact durable Run-state checkpoint containing the receipt. It does not claim that a model understood the input, that a particular later model request included it, or that subsequent effects occurred exactly once.

If state publication succeeds but relational consumption does not, the current or replacement Worker reconciles the receipt without enqueueing the entry again while that Run remains an eligible running destination. A waiting or completed sealing transaction can perform the same reconciliation when its selected state contains the receipt. If failed or cancelled sealing wins first, every still-pending async result originating from that Run becomes `suppressed`, while ordinary steer and async results merely bound from another origin become `superseded`; an object-only receipt cannot overwrite either terminal disposition. If the Worker disappears before a complete state receipt, the entry remains pending and can be recognizably delivered again. A steer's `environment_path` source is then reacquired and deterministically replaced. Foundation prefers possible duplicate pre-checkpoint delivery to silently losing accepted input.

A planned handoff does not consume or supersede pending delivery. The successor Attempt for the same running Run preserves FIFO and can reconcile already-published receipts. The safe handoff boundary cannot occur during input materialization, native enqueue, state publication, or the consumption transaction. If the local Harness Run crosses its native terminal boundary after the last hook reconciliation while eligible pending delivery exists, that candidate cannot seal as completed. The owning Worker commits a retryable Attempt failure so an in-budget replacement resumes the same Run and drains the FIFO.

## Waiting Binding and First-Request Hook Delivery

Waiting Feedback and explicit waiting Continue accept exactly one direct successor under the [continuation contract](34-agent-control-input-and-continuation.md#deferred-interaction). Their final transaction locks the Thread, waiting parent, every relevant async-result spawning Run, inbox counter, and its pending waiting-source entries in canonical order. It suppresses entries whose origin is failed or cancelled, then sets `target_run_id` on the remaining eligible entries to the new successor in `delivery_sequence` order. The entries remain pending and keep `source_waiting_run_id` until they are consumed, superseded, suppressed, or rolled to a later waiting Run. If that successor later fails or is cancelled, results originating from it become suppressed and other entries bound to it become superseded. A later async result can remain sourced to the preserved waiting head and bind to a Retry only when its own spawning Run is not failed or cancelled. Continue From or another explicit branch that abandons the waiting head instead marks that waiting branch's remaining unconsumed ordinary steer and eligible async-result entries `superseded` in the same advancement transaction.

Foundation does not query or offer the waiting-derived FIFO before the successor's first model request. It withholds every later bound entry behind that prefix as well, including an async result or running steer accepted after successor creation. The first request receives only the successor's accepted Run input: explicit Feedback supplies `DeferredToolResume`, while waiting Continue supplies both `DeferredToolResume` and its `AgentInput`. No inbox payload enters that request.

The Worker installs the trusted Foundation-owned concrete [control Capability](16a-harness-runtime-integration.md#definition-construction) directly in the process-local `AgentDefinition`; it is not supplied through a Harness plugin and does not add a Harness input, queue, or durable receipt API. This mandatory Worker composition is neither persisted nor caller-selectable, grants no authority by itself, and does not change the accepted model, instructions, tools, Skill, Environment, or deferred surface. The Capability delegates PostgreSQL reads and payload materialization to the current fenced control dispatcher, uses the exact awaited hooks defined by the [Foundation Capability Hook Contract](16a-harness-runtime-integration.md#foundation-capability-hook-contract), and calls `RunContext.enqueue(..., priority="asap")`. It holds no database session while model or tool work is active and does not make its process-local enqueue ID authoritative.

The first-request gate follows these public Pydantic boundaries:

- after a first response with no tool calls, awaited `after_model_request()` reconciles the eligible FIFO and enqueues its adapted values before response handling can terminate;
- after a first response with tool calls, the Capability waits for awaited `after_node_run()` on the complete `CallToolsNode`. An ordinary next-request or terminal result reconciles and enqueues the FIFO, while a deferred/HITL terminal result enqueues nothing and allows Foundation to seal waiting and roll the entries forward; and
- Pydantic's native pending-message drain places `priority="asap"` values into the next model request or redirects an otherwise terminal ordinary result to another model request. Its `EnqueuedMessagesEvent` is only process-local incorporation evidence; the common checkpoint-and-receipt flow remains the durable consumption authority.

The gate is defined by the first model request and the complete tool batch, when one follows that response, not by `ModelAttempt`; one attempt can contain several requests. The first complete checkpoint after this gate proves the accepted feedback or waiting-Continue input was applied. A replacement Attempt that restores such an applied checkpoint can reconcile pending delivery before its own first model request; a replacement that restores `input_disposition=pending` must preserve the isolated first-request rule. A new inbox entry accepted after the gate participates in the ordinary FIFO. PostgreSQL binding, final outcome checks, and receipts remain authoritative.

## Interrupt Command

```http
POST /api/v1/runs/{run_id}/interrupt
Idempotency-Key: opaque-caller-key
```

Interrupt is an independent command and is never encoded as a steer mode or an `AgentInput`. It authenticates and authorizes `run.interrupt` from the same registry; locks the owning Thread, target Run, current RunAttempt when present, inbox counter, and affected entries in canonical order; requires the Run to remain current and active; and in one short transaction:

- seals the Run as `cancelled` through the ordinary Run lifecycle;
- terminalizes and deselects the current RunAttempt when one exists;
- marks every pending async-subagent result originating from that Run `suppressed`, marks every ordinary steer or different-origin async result targeted at that Run `superseded`, and releases their pending counters;
- preserves the prior continuation head and advances the Thread version; and
- commits lifecycle facts and idempotency evidence required by their owners.

Interrupt performs no object-store I/O and selects no new sealed state. The cancelled Run is not an eligible continuation parent, and a later retry rebuilds from the source Run's eligible parent, so cancellation does not need to capture an in-flight `state.json`. A state write prepared by the stale Worker can have an unknown storage outcome after cancellation, but it cannot change the relational outcome, become selected continuation state, or consume a superseded or suppressed delivery. Retry inherits neither disposition and never re-enables an old child result.

After commit, the control process best-effort appends the same reconcile-Thread signal to the control Stream. The current Worker re-reads the cancelled Run and its registered RunAttempt, then calls process-local `HarnessRunStream.cancel()` only when that registration identifies the Attempt terminalized by interrupt. A different or later generation never receives that process-local cancellation. The relational transition already prevents the old Worker from committing; the Redis signal only reduces wasted model, tool, Environment, or Worker time.

A later retry follows the [terminal-intent retry contract](34-agent-control-input-and-continuation.md#retry-of-terminal-intent) and creates a successor Run with a fresh RunAttempt and Harness Run. It never reopens the cancelled Run or resumes its process-local objects.

## Thread Control Signal Stream

Each Thread can have one tenant-scoped Redis Stream dedicated to active-control wakeups. It is distinct from the Run presentation Stream owned by [Lifecycle and Stream Persistence](17-lifecycle-and-stream-persistence.md). The key is derived from tenant and Thread identity and is never exposed as a bearer reference.

```python
class ReconcileThreadSignal:
    schema_version: Literal["1"]
    thread_id: ThreadId
```

Every entry means only “reconcile this Thread.” It carries no command kind, Run identity, inbox identity, or business payload. The current Worker or a control reconciler determines whether steer, interrupt, an async-subagent result, or no current action exists from PostgreSQL after receiving the signal.

One stable Redis consumer group per Thread stores the delivery cursor inside Redis. A Worker consumer is named by its Worker identity and generation. A new RunAttempt owner first reconciles PostgreSQL, joins the same group, and can claim abandoned group deliveries from a prior Worker generation. The group cursor, pending-entry list, acknowledgement, and Redis entry ID are transport state only; none is copied into the Thread row or used to decide whether an inbox entry was consumed.

The Stream uses bounded approximate trimming and an inactivity TTL. Publication and an active owning Worker refresh the TTL; once the Thread is cold and no owner refreshes it, the Stream and its consumer-group metadata expire together. The TTL and maximum length are bounded deployment configuration. Expiry, trimming, duplicate delivery, acknowledgement loss, consumer replacement, and complete Redis loss are safe because every signal causes a database reconciliation and every authoritative command is already represented in PostgreSQL. After expiry, the next publisher or active owner recreates the Stream and its stable group before appending or consuming signals; it still reconciles PostgreSQL first and never tries to reconstruct the expired cursor.

The Worker or control reconciler acknowledges a signal only after its bounded reconciliation attempt. Failure leaves the entry available for consumer-group reclaim. A signal can be stale, duplicated, or out of order; it never carries enough information to authorize or perform a domain transition without the database read. Independently, control replicas perform bounded PostgreSQL scans for pending asynchronous-result entries so an inactive Thread does not depend on a surviving Redis signal or active Worker for eventual reconciliation.

## Completion and Control Races

Inbox acceptance, origin suppression, binding, consumption, waiting rollover, automatic async-result successor acceptance, interrupt, planned yield, explicit branch advancement, and Run outcome selection use the canonical lock order: Thread, current, named, or async-result spawning Runs, current RunAttempt when applicable, inbox counter, then inbox entries in `delivery_sequence`; queue rows follow under their owning combined transaction. Runs of the same lock class use stable ID order. Attempt-scoped mutations additionally verify the shared fence and lease. No transaction spans Redis, object storage, Harness execution, model work, or tool work.

Outcome rules distinguish completion from waiting:

- `completed` can seal only after the selected state reconciles all of its receipts and the transaction proves no eligible `pending` delivery remains bound to the Run;
- `waiting` can seal with pending delivery. The transaction consumes receipts present in the selected waiting state, clears `target_run_id` on every remaining bound pending entry, sets `source_waiting_run_id` to the sealing Run, preserves `delivery_sequence`, and releases none of their pending budget;
- `failed` or `cancelled` atomically marks every still-pending async result originating from that Run `suppressed`, marks every ordinary steer or different-origin async result bound to that Run `superseded`, and releases their pending budget; and
- planned `yielded` seals no Run, changes no inbox binding or status, and leaves the successor Attempt to preserve the FIFO.

Waiting rollover does not put an inbox payload into `state.json`, add it to the pending-action summary, or treat it as deferred feedback. The selected waiting state's native deferred requests and the Thread inbox remain independent authorities. Feedback or waiting Continue later binds the rolled entries to the direct successor; another waiting outcome can roll the still-unconsumed suffix forward again.

Waiting sealing and concurrent acceptance serialize without stranding an old target:

- if inbox acceptance commits first against the running Run, the waiting transaction observes and rolls that entry;
- if waiting sealing commits first, a later steer or async-result publication observes the current/head waiting Run and records it directly as `source_waiting_run_id`; and
- no committed pending entry may retain an active target whose Run has already sealed waiting.

The remaining races follow from the same locks:

- if completed sealing commits first, a later public steer observes a non-running Run and is rejected; a later async result follows its inactive-Thread and queue-precedence rules;
- if delivery acceptance commits first, completed sealing observes it and cannot commit until it is consumed or finalized, while waiting sealing may roll it forward;
- if interrupt or another failed/cancelled seal commits first, stale consumption, waiting, completion, failure, or yield writes are fenced out and no object-only receipt can replace `suppressed` or `superseded`;
- if consumption commits first, later waiting, completion, or interrupt preserves that consumed fact and never claims rollback;
- if yield commits first, old-Attempt writes are fenced out, pending delivery stays bound to the same running Run, and later accepted delivery joins the same FIFO; and
- if an explicit Continue From or other branch abandons a waiting head first, its transaction supersedes that waiting source's unconsumed entries, so a stale Feedback or waiting Continue cannot bind them elsewhere.

Between a committed yield and successor claim, interrupt can seal the running Run even though `current_run_attempt_id` is null. It suppresses pending results originating from that Run, supersedes other bound pending delivery, and prevents a later claim. A successor claim that wins first creates a fresh Attempt; ordinary fencing then governs the later interrupt.

The Worker can perform additional database checks at execution boundaries for latency, but only the final locked receipt reconciliation and pending-delivery check make outcome selection authoritative. A check performed before acquiring the outcome transaction locks is insufficient.

## Failure Semantics

| Condition                                                            | Durable outcome                                                                                                                                                  | Reconciliation                                                                                            |
| -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Steer validation or authorization fails                              | No inbox entry exists                                                                                                                                            | Caller corrects input or authority                                                                        |
| Steer response is lost after commit                                  | The inbox entry may already be pending                                                                                                                           | Repeat the same idempotency key or read the exact steer status                                            |
| Redis publication fails or the Stream expires                        | PostgreSQL inbox and Run state remain authoritative                                                                                                              | Worker safe-point and takeover reconciliation discover pending work                                       |
| Worker receives a stale or duplicate signal                          | No duplicate domain transition follows from the signal                                                                                                           | Re-read current entry, Run, Attempt, and fence                                                            |
| Worker dies before state publication                                 | The FIFO prefix remains pending and may be delivered again                                                                                                       | Replacement Attempt resumes from the last durable state                                                   |
| State publishes but consumed-row transaction does not commit         | While the Run remains running, the receipt can prove incorporation while the row appears pending                                                                 | Current or replacement Worker reconciles it without enqueueing again                                      |
| A pending steer's binary source cannot be read or validated          | The current Attempt follows ordinary retryable or permanent failure classification                                                                               | Retry leaves the steer pending for reacquisition; terminal failure supersedes it                          |
| Failed/cancelled wins after state publication but before consumption | Own-origin async results become suppressed; steer and different-origin async results bound there become superseded; the object-only receipt is non-authoritative | No later Worker changes either entry to consumed                                                          |
| Run becomes completed before steer acceptance                        | No steer entry is accepted                                                                                                                                       | Caller starts or continues with a new Run                                                                 |
| Run becomes waiting before steer acceptance                          | The steer is accepted against that waiting source when current/head still match                                                                                  | Its direct successor receives it after the first-request barrier                                          |
| Interrupt commits while local work continues                         | Run is durably cancelled and the old Attempt is stale                                                                                                            | Redis or the next database boundary stops local work; no stale result can commit                          |
| Yield commits while delivery remains pending                         | Run remains running, the old Attempt is terminal, and the FIFO remains pending                                                                                   | Successor Attempt consumes it under the ordinary checkpoint contract                                      |
| State contains an inbox receipt but yield wins before consumption    | Object receipt is valid while the relational entry can still appear pending                                                                                      | Successor reconciles the same-Run receipt and does not enqueue it again                                   |
| Worker dies before publishing an asynchronous-result receipt         | Result remains pending and no Run is recorded as its durable destination                                                                                         | Current replacement or later eligible Run can receive the recognizable result                             |
| Waiting state is selected without a pending entry's receipt          | The Run seals waiting and the entry is rebound to it as waiting source                                                                                           | Feedback or waiting Continue later binds it to the direct successor                                       |
| Completed state is selected while eligible delivery is pending       | The completion transaction cannot commit                                                                                                                         | Worker drains FIFO or finalizes an ineligible entry before retrying completion                            |
| First request produces another deferred/HITL result                  | The Foundation delivery Capability enqueues nothing and the successor can seal waiting                                                                           | Waiting rollover preserves the pending FIFO for the next direct successor                                 |
| Redis is lost while the parent Thread is inactive                    | Pending result remains authoritative in PostgreSQL                                                                                                               | Bounded control scans discover it without a Redis delivery guarantee                                      |
| Outcome or interrupt races with yield                                | The shared locks and Attempt fence admit one authoritative transition                                                                                            | Loser observes committed state and cannot publish its stale candidate                                     |
| External model, tool, child, or Environment effect exists            | Interrupt and inbox delivery do not roll it back; an interrupted or repeated boundary can remain unknown                                                         | The owning provider or Capability's idempotency, receipt, or durable-task contract governs reconciliation |

## Compatibility and Trade-offs

Inbox entry kind, payload schema version, `delivery_sequence`, normalized async `origin_run_id`, cross-kind FIFO, active and waiting binding, per-kind status meaning including async-result suppression, waiting rollover, first-request invisibility, interrupt terminal meaning, and consumption evidence are durable compatibility facts. The Foundation Capability class name, hook decomposition, Redis key spelling, consumer name encoding, trim batch size, TTL duration, watcher organization, and dispatcher classes are internal when they preserve the defined loss, expiry, and reconciliation behavior.

The relational inbox adds one durable write and later state-coupled consumption write for steering and asynchronous results. Foundation accepts that cost so an accepted input remains queryable and cannot disappear with a Worker or Redis failure. Omitting an outbox for control wakeups accepts delayed reaction during Redis failure; mandatory Worker boundary checks and bounded control reconciliation preserve correctness and eventual observation while service capacity remains available.

## Invariants

01. PostgreSQL, not Redis or Worker memory, owns every inbox entry, consumption fact, and interrupted Run outcome.
02. Steer uses the canonical `AgentInput`, is accepted only against the exact current running Run or exact current/head waiting Run, and creates neither a Run nor a Thread advancement.
03. Ordinary steer and asynchronous-subagent results share one immutable Thread-level FIFO allocated by PostgreSQL acceptance order; neither kind can bypass an earlier eligible entry.
04. An inbox entry becomes consumed only with durable destination evidence; Redis acknowledgement never consumes it.
05. A consumed entry has committed relational evidence naming the exact Run-state checkpoint that contained its stable receipt.
06. Worker loss can duplicate an uncheckpointed delivery but cannot silently discard an accepted one.
07. Interrupt is an explicit command, not a steer mode, and seals the active Run as `cancelled`.
08. Interrupt never claims rollback of model, tool, child, Environment, provider, or client effects.
09. Eligible pending delivery blocks `completed` but does not block `waiting`; waiting sealing atomically rolls its unconsumed FIFO to that waiting Run's direct successor.
10. Failed and cancelled sealing atomically suppresses every pending async result originating from that Run and supersedes other pending delivery bound to it; Retry inherits neither disposition.
11. One Thread control Stream uses a Redis consumer-group cursor, bounded trimming, and inactivity expiry without adding a relational Redis cursor.
12. A Redis control signal identifies only the Thread to reconcile; its loss, duplication, trimming, or expiry cannot change authoritative behavior.
13. A replacement Worker reconciles the Thread inbox before relying on Redis delivery state.
14. Async-subagent results use the common FIFO while retaining their own authorization, origin-outcome gate, active-or-successor selection, retention, payload, and queue-precedence semantics; a failed or cancelled origin can neither inject a result nor trigger a new Run.
15. Planned yield neither consumes nor supersedes pending delivery; the successor reconciles it from PostgreSQL before relying on Redis.
16. A consumed entry is represented in complete same-Run state before yield, and receipt reconciliation remains valid across the yielded Attempt boundary.
17. Yield, interrupt, inbox consumption, successor acceptance, waiting rollover, and outcome serialize through the current RunAttempt fence and canonical Thread/Run locks, so only one authoritative destination or result can commit.
18. Waiting-derived delivery is invisible to the successor's first model request; the Foundation-owned awaited Capability hook offers it only at the following complete safe boundary, and another deferred result rolls it forward without injection.
