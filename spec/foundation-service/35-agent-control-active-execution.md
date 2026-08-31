# Agent Control: Active Execution

## Design Position

Foundation controls an already running Turn through a durable Thread inbox and an explicit interrupt command. Steering appends one accepted semantic input to the current running Turn without creating another Turn. Interrupt seals the active Turn as `cancelled`; later work creates another Turn and Harness Run rather than restoring the interrupted process-local Run.

PostgreSQL is authoritative for inbox acceptance, consumption, Turn lifecycle, and steer ordering. A Thread-scoped Redis control Stream only wakes the Worker early. Its consumer-group cursor remains in Redis, its entries are bounded and expire after inactivity, and loss of the Stream never loses an accepted inbox entry or changes Turn state. The control API therefore remains stateless and does not route directly to a process-local Agent instance.

[Agent Input](33-agent-input.md) owns the `AgentInput` carried by steering. [Durable Turn State](14-turn-persistence.md) owns the resulting checkpoint and sealed outcome, [Durable Turn Attempt Persistence](15-turn-attempt-persistence.md) owns the current Worker fence, and [Scheduling, Workers, and Recovery](16-scheduling-workers-and-recovery.md) owns Worker scheduling and the process-local dispatcher invocation points.

## Boundaries

| Concern                                                  | Owner                                                                                                                                           | Relationship                                                                                                                  |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Thread inbox identity, per-kind status, and common store | This contract                                                                                                                                   | Persists inputs and internal results awaiting one Thread without making Redis authoritative                                   |
| Public steer, steer status, and interrupt commands       | This contract                                                                                                                                   | Accepts running-Turn input, exposes its exact receipt status, or seals the active Turn                                        |
| Steering `AgentInput` and binary normalization           | [Agent Input](33-agent-input.md)                                                                                                                | Supplies the same accepted input protocol used by start, continue, and fork                                                   |
| Async-subagent result payload and selection              | [Async Subagents](18-async-subagents.md)                                                                                                        | Uses the common inbox row while retaining child-result authorization, expiry, and selection semantics                         |
| State envelope, checkpoint write, and sealed-state shape | [Durable Turn State](14-turn-persistence.md)                                                                                                    | Supplies complete state and the inbox receipt embedded by this contract's steer-consumption flow                              |
| Worker lease, fence, and Attempt terminalization         | [Durable Turn Attempt Persistence](15-turn-attempt-persistence.md)                                                                              | Supplies the current Worker authority required by steer consumption and rejected after interrupt                              |
| Worker scheduling, dispatcher, takeover, and recovery    | [Scheduling, Workers, and Recovery](16-scheduling-workers-and-recovery.md)                                                                      | Runs this contract's reconciliation flow at the required execution boundaries                                                 |
| Redis commands and backend behavior                      | [Foundation Storage Capabilities](03-storage.md#redis-compatible-data-structures)                                                               | Supplies Streams, consumer groups, bounded trimming, and expiry without assigning domain authority                            |
| Authentication, authorization, and idempotency evidence  | [Identity and Access Management](10-identity-and-access-management.md) and [Durable Operations and Outbox](06-durable-operations-and-outbox.md) | Own current permission, replay evidence, and unknown-commit reconciliation; no outbox is required solely for a control signal |
| Common API behavior                                      | [Management API](21-management-api.md) and [Platform API Conventions](../api-conventions.md)                                                    | Catalogs the exact public commands and receipt read without exposing the internal inbox as a generic resource                 |

## Thread Inbox

A Thread inbox entry is one durable input or internal result offered to an existing Thread independently of Turn creation. The entry has its own identity, kind-owned payload, status, retention, and consumption evidence. It does not replace Turn input, Thread advancement, waiting feedback, lifecycle events, or Harness state. Only entries whose owning kind requires order define an ordering field.

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
    target_turn_id: TurnId | None
    steer_sequence: int | None
    payload_schema_version: str
    payload: JsonValue | None
    payload_object: InboxPayloadObjectRef | None

    status: ThreadInboxStatus
    consumed_by_turn_id: TurnId | None
    consumed_state_digest_sha256: str | None
    consumed_checkpoint_seq: int | None

    expires_at: datetime | None
    created_at: datetime
    finalized_at: datetime | None
```

`steer_sequence` is required only for `steer`, is positive and monotonic within its exact target Turn, and is allocated while the steer acceptance transaction holds that Turn's mutation lock. Workers incorporate pending steer entries for one Turn in this order. Async-subagent results have no order relative to one another or to steer entries; their exact identity, relationship, and selection transaction are sufficient.

Exactly one of `payload` and `payload_object` is present. The entry kind selects the exact versioned payload contract: `steer` stores one canonical accepted [`AgentInput`](33-agent-input.md#accepted-input-and-canonicalization), while `async_subagent_result` stores the payload owned by [Async Subagents](18-async-subagents.md#result-delivery). A kind can be added only with an owning payload, authorization, targeting, consumption, expiry, and compatibility contract.

`target_turn_id` is required for `steer` and names the exact running Turn for which the input was accepted. Another Turn never consumes that steer. An async-subagent result has neither a target Turn nor `steer_sequence`; its relationship is carried by its kind-owned payload and its later-Turn selection rules do not make it active steering merely because both kinds use this table.

`pending` is the only consumable state. `consumed` means the kind-specific consumer committed its authoritative destination together with the listed consumption evidence. A steer can become only `consumed` or `superseded`; it does not expire or support explicit discard. An async-subagent result can become `consumed`, `expired`, or `discarded`; it never becomes `superseded`. Every terminal status is immutable except for retention redaction allowed by the payload owner.

The table preserves these constraints and access paths:

1. `(tenant_id, id)` is unique, and the owning Thread belongs to that tenant.
2. A present target Turn belongs to the same tenant and Thread.
3. Payload columns form an exactly-one representation group.
4. Consumption fields are absent unless `status="consumed"` and are all required when it is consumed. `finalized_at` is present exactly for a terminal status.
5. Steer rows require a target Turn and positive `steer_sequence`, forbid `expires_at`, and are unique on `(tenant_id, target_turn_id, steer_sequence)`.
6. Async-subagent result rows forbid a target Turn and `steer_sequence`; their payload owner defines relationship uniqueness and expiry.
7. `(tenant_id, target_turn_id, status, steer_sequence)` supports ordered steer reconciliation and the mandatory pending check before outcome commit.
8. `(tenant_id, thread_id, status, created_at, id)` supports bounded internal reconciliation without defining a cross-kind business order.

## Steer Command

```http
POST /api/v1/turns/{turn_id}/steer
Idempotency-Key: opaque-caller-key
```

The request carries one submitted `AgentInput`. Foundation acquires and canonicalizes binary content before the final short transaction. That transaction authenticates and authorizes `turn.steer`, locks the owning Thread and target Turn in the canonical order, requires the Turn to remain the Thread's current Turn with `status="running"`, allocates the next target-Turn `steer_sequence`, inserts one `pending` `steer` entry, and commits the idempotency evidence. It neither creates another Turn nor changes Thread head, current-Turn selection, or version. The exact Turn identity and locked running precondition make a caller-supplied Turn version unnecessary.

A successful command returns `202` with this conceptual receipt:

```python
class SteerReceipt:
    schema_version: Literal["1"]
    session_id: SessionId
    thread_id: ThreadId
    turn_id: TurnId
    steer_id: ThreadInboxEntryId
    accepted_at: datetime
```

The same idempotency key and canonical request return the same entry and receipt. Reuse with different input conflicts. `202` proves durable acceptance, not that a Worker, Harness, model, or external tool has incorporated the input. The following authorized read exposes the exact steer status without making the internal Thread inbox a generic public resource:

```http
GET /api/v1/turns/{turn_id}/steers/{steer_id}
```

The read returns safe identity, target, `pending`, `consumed`, or `superseded` status, consumption correlation when present, and timestamps. It does not return the accepted payload or an object-store locator. Foundation exposes no generic Thread-inbox collection or cross-kind inbox-entry endpoint.

After the relational commit, the control process best-effort appends one business-payload-free reconcile signal to the Thread control Stream. Failure or unknown outcome of that Redis write neither rolls back the accepted inbox entry nor creates an outbox record solely for retrying the signal. The process records bounded diagnostics and readiness follows the shared Redis dependency contract.

## Steer Consumption and State Commitment

The current Worker reconciles eligible `pending` steer entries in `steer_sequence` order. A Redis signal can cause immediate reconciliation, but the Worker also queries PostgreSQL when it starts or takes over an Attempt, before each model boundary, after complete tool batches, before checkpoints, and before every outcome decision.

For each eligible steer, the Worker:

1. reads the accepted payload and revalidates its source descriptions, the current Turn, Attempt, fence, lease, and input adapter outside a long transaction;
2. applies the accepted binary delivery: direct `model_url` passes the original URL unchanged, `model_content` reads the source into bounded process-local content, and `environment_path` reads the source through Worker staging and replaces the deterministic path owned by this inbox entry;
3. enters the process-local run-control barrier and calls `HarnessRunStream.steer()` while the native run remains steerable;
4. exports complete state containing the resulting Harness messages plus the Host-owned inbox receipt;
5. conditionally replaces the Turn's deterministic `state.json` under the current object version and Attempt fence; and
6. in a new short transaction that locks the Thread and Turn in the canonical order, revalidates the same running Turn, current Attempt, fence, and lease, then marks the contiguous delivered steer entries `consumed` with the exact state digest and checkpoint sequence.

The state receipt contains each consumed inbox entry ID and kind. It is retained across replacement Attempts of the same Turn and is not inherited by another Turn. If state publication succeeds but the relational consumption transaction does not, the current or replacement Worker can reconcile the receipt and mark the matching entry consumed without enqueueing it again only while that same Turn remains current and running. If a failed or cancelled terminal transaction wins first, it marks the entry `superseded`; the object-only receipt cannot change that terminal status or become consumption evidence later. If the Worker disappears after local enqueue but before a state containing the receipt is durably published, the entry remains pending and can be delivered again. Any `environment_path` file is then reacquired and deterministically replaced before redelivery. Once the receipt commits consumption, neither replacement Workers nor Redis redelivery materialize that steer again; Turn-wide model request usage is not used for this per-steer decision.

`consumed` therefore means that the relational transaction verified an exact durable Turn-state checkpoint containing the receipt. It does not claim that a model understood the input, that a model request included it, or that subsequent effects occurred exactly once. Foundation prefers possible duplicate delivery at the pre-checkpoint crash boundary to silently losing accepted user intent.

A planned handoff does not supersede pending steer. An entry not yet delivered remains `pending` for the successor Attempt. An entry already represented by a complete state receipt remains eligible for the same receipt reconciliation after `yielded`, because the same Turn is still current and `running`; once relationally `consumed`, it is never delivered again. The safe handoff boundary cannot occur during `HarnessRunStream.steer()`, input materialization, state publication, or the consumption transaction.

If a local Harness Run crosses its native terminal steering boundary before an accepted pending steer is incorporated, its candidate cannot seal the Turn. The owning Worker commits a retryable Attempt failure because that generation can no longer produce an authoritative outcome, and ordinary in-budget TurnAttempt recovery resumes the same Turn from the latest complete state and consumes the pending entry. Recovery never commits the stale terminal candidate ahead of accepted steering.

## Interrupt Command

```http
POST /api/v1/turns/{turn_id}/interrupt
Idempotency-Key: opaque-caller-key
```

Interrupt is an independent command and is never encoded as a steer mode or an `AgentInput`. It authenticates and authorizes `turn.interrupt`, locks the owning Thread and target Turn, requires the Turn to remain current and active, and in one short transaction:

- seals the Turn as `cancelled` through the ordinary Turn lifecycle;
- terminalizes and deselects the current TurnAttempt when one exists;
- marks every pending steer targeted at that Turn `superseded`;
- preserves the prior continuation head and advances the Thread version; and
- commits lifecycle facts and idempotency evidence required by their owners.

Interrupt performs no object-store I/O and selects no new sealed state. The cancelled Turn is not an eligible continuation parent, and a later retry rebuilds from the source Turn's eligible parent, so cancellation does not need to capture an in-flight `state.json`. A state write prepared by the stale Worker can have an unknown storage outcome after cancellation, but it cannot change the relational outcome, become selected continuation state, or consume a superseded steer.

After commit, the control process best-effort appends the same reconcile-Thread signal to the control Stream. The current Worker re-reads the cancelled Turn and its registered TurnAttempt, then calls process-local `HarnessRunStream.cancel()` only when that registration identifies the Attempt terminalized by interrupt. A different or later generation never receives that process-local cancellation. The relational transition already prevents the old Worker from committing; the Redis signal only reduces wasted model, tool, Environment, or Worker time.

A later retry follows the [terminal-intent retry contract](34-agent-control-input-and-continuation.md#retry-of-terminal-intent) and creates a successor Turn with a fresh TurnAttempt and Harness Run. It never reopens the cancelled Turn or resumes its process-local objects.

## Thread Control Signal Stream

Each Thread can have one tenant-scoped Redis Stream dedicated to active-control wakeups. It is distinct from the Turn presentation Stream owned by [Lifecycle and Stream Persistence](17-lifecycle-and-stream-persistence.md). The key is derived from tenant and Thread identity and is never exposed as a bearer reference.

```python
class ReconcileThreadSignal:
    schema_version: Literal["1"]
    thread_id: ThreadId
```

Every entry means only “reconcile this Thread.” It carries no command kind, Turn identity, inbox identity, or business payload. The Worker determines whether steer, interrupt, an async-subagent result, or no current action exists from PostgreSQL after receiving the signal.

One stable Redis consumer group per Thread stores the delivery cursor inside Redis. A Worker consumer is named by its Worker identity and generation. A new TurnAttempt owner first reconciles PostgreSQL, joins the same group, and can claim abandoned group deliveries from a prior Worker generation. The group cursor, pending-entry list, acknowledgement, and Redis entry ID are transport state only; none is copied into the Thread row or used to decide whether an inbox entry was consumed.

The Stream uses bounded approximate trimming and an inactivity TTL. Publication and an active owning Worker refresh the TTL; once the Thread is cold and no owner refreshes it, the Stream and its consumer-group metadata expire together. The TTL and maximum length are bounded deployment configuration. Expiry, trimming, duplicate delivery, acknowledgement loss, consumer replacement, and complete Redis loss are safe because every signal causes a database reconciliation and every authoritative command is already represented in PostgreSQL. After expiry, the next publisher or active owner recreates the Stream and its stable group before appending or consuming signals; it still reconciles PostgreSQL first and never tries to reconstruct the expired cursor.

The Worker acknowledges a signal only after its bounded reconciliation attempt. Failure leaves the entry available for consumer-group reclaim. A signal can be stale, duplicated, or out of order; it never carries enough information to authorize or perform a domain transition without the database read.

## Completion and Control Races

Steer acceptance, interrupt, planned yield, and Turn outcome selection serialize through the same canonical Thread and Turn mutation locks; attempt-scoped mutations also lock or CAS the current TurnAttempt through its shared fence and lease authority. No transaction spans Redis, object storage, Harness execution, model work, or tool work.

A Worker can seal a waiting or completed outcome only in a transaction that revalidates its current Attempt and fence and proves that no eligible `pending` steer targets that Turn. A failed or interrupt-driven `cancelled` transition instead marks remaining target steers `superseded` in its sealing transaction. Steer consumption uses the same locks when it changes pending entries to `consumed`. Planned yield does not seal the Turn and therefore does not require pending steer to be absent or change its status. The checks and mutations occur in their owning transaction:

- if outcome sealing commits first, a later steer observes a non-running Turn and is rejected without creating an inbox entry;
- if steer acceptance commits first, outcome sealing observes the pending entry and cannot complete until it is consumed or becomes terminal under an owning rule; planned yield may still commit without changing the pending entry, and its successor must reconcile that entry;
- if interrupt commits first, stale consumption, waiting, completion, and failure or yield commits are fenced out, while any object-only state preparation is non-authoritative;
- if steer consumption commits first, a later interrupt can still cancel the Turn but cannot rewrite the consumed entry or claim rollback;
- if yield commits first, later state, consumption, outcome, or control writes from the old Attempt are fenced out, pending steer stays pending, and a new steer can still be accepted against the same running Turn; and
- if outcome or interrupt commits first, yield fails its Turn/Attempt CAS and cannot replace the authoritative result.

Between a committed yield and successor claim, interrupt can seal the running Turn even though `current_turn_attempt_id` is null. That transaction prevents all later claim, supersedes pending steer, and does not need to recreate or cancel the old process-local Run. A successor claim that wins first creates a fresh Attempt; ordinary fencing then governs any later interrupt.

If `state.json` containing a steer receipt was written before interrupt but the consumption transaction had not committed, interrupt wins the durable race: the pending entry becomes `superseded`, and later receipt reconciliation is forbidden because the Turn is no longer running. The object write remains non-authoritative preparation rather than a consumed inbox fact.

The Worker can perform additional database checks at execution boundaries for latency, but only the final in-transaction pending check makes outcome selection authoritative. A check performed before acquiring the outcome transaction lock is insufficient.

## Failure Semantics

| Condition                                                            | Durable outcome                                                                                    | Reconciliation                                                                   |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Steer validation or authorization fails                              | No inbox entry exists                                                                              | Caller corrects input or authority                                               |
| Steer response is lost after commit                                  | The inbox entry may already be pending                                                             | Repeat the same idempotency key or read the exact steer status                   |
| Redis publication fails or the Stream expires                        | PostgreSQL inbox and Turn state remain authoritative                                               | Worker safe-point and takeover reconciliation discover pending work              |
| Worker receives a stale or duplicate signal                          | No duplicate domain transition follows from the signal                                             | Re-read current entry, Turn, Attempt, and fence                                  |
| Worker dies before state publication                                 | Steer remains pending and may be delivered again                                                   | Replacement Attempt resumes from the last durable state                          |
| State publishes but consumed-row transaction does not commit         | While the Turn remains running, the receipt can prove incorporation while the row appears pending  | Current or replacement Worker reconciles it without enqueueing again             |
| A pending steer's binary source cannot be read or validated          | The current Attempt follows ordinary retryable or permanent failure classification                 | Retry leaves the steer pending for reacquisition; terminal failure supersedes it |
| Interrupt wins after state publication but before consumption commit | The steer becomes superseded; the object-only receipt is non-authoritative                         | No later Worker changes it to consumed                                           |
| Turn becomes terminal before steer acceptance                        | No steer entry is accepted                                                                         | Caller starts or continues with a new Turn                                       |
| Interrupt commits while local work continues                         | Turn is durably cancelled and the old Attempt is stale                                             | Redis or the next database boundary stops local work; no stale result can commit |
| Yield commits while steer remains pending                            | Turn remains running, the old Attempt is terminal, and the steer remains pending                   | Successor Attempt consumes it under the ordinary checkpoint contract             |
| State contains a steer receipt but yield wins before consumption     | Object receipt is valid while the relational entry can still appear pending                        | Successor reconciles the same-Turn receipt and does not enqueue the steer again  |
| Outcome or interrupt races with yield                                | The shared locks and Attempt fence admit one authoritative transition                              | Loser observes committed state and cannot publish its stale candidate            |
| External model, tool, child, or Environment effect exists            | Interrupt and steering do not roll it back; an interrupted or repeated boundary can remain unknown | Owning idempotency, receipt, or unknown-outcome contract governs reconciliation  |

## Compatibility and Trade-offs

Inbox entry kind, payload schema version, per-kind status meaning, target-Turn steer ordering, steer targeting, interrupt terminal meaning, and consumption evidence are durable compatibility facts. Redis key spelling, consumer name encoding, trim batch size, TTL duration, watcher organization, and dispatcher classes are internal when they preserve the defined loss, expiry, and reconciliation behavior.

The relational inbox adds one durable write and later state-coupled consumption write for steering and asynchronous results. Foundation accepts that cost so an accepted input remains queryable and cannot disappear with a Worker or Redis failure. Omitting an outbox for control wakeups accepts delayed reaction during Redis failure; mandatory database checks preserve correctness.

## Invariants

01. PostgreSQL, not Redis or Worker memory, owns every inbox entry, consumption fact, and interrupted Turn outcome.
02. Steer uses the canonical `AgentInput`, targets one exact running Turn, is ordered only against steer for that Turn, and creates neither a Turn nor a Thread advancement.
03. An inbox entry becomes consumed only with durable destination evidence; Redis acknowledgement never consumes it.
04. A consumed steer has committed relational evidence naming the exact Turn-state checkpoint that contained its stable receipt.
05. Worker loss can duplicate an uncheckpointed steer but cannot silently discard an accepted one.
06. Interrupt is an explicit command, not a steer mode, and seals the active Turn as `cancelled`.
07. Interrupt never claims rollback of model, tool, child, Environment, provider, or client effects.
08. Steer acceptance, consumption, and outcome sealing serialize under the same Thread and Turn locks, and a pending steer prevents waiting or completed outcome selection.
09. One Thread control Stream uses a Redis consumer-group cursor, bounded trimming, and inactivity expiry without adding a relational Redis cursor.
10. A Redis control signal identifies only the Thread to reconcile; its loss, duplication, trimming, or expiry cannot change authoritative behavior.
11. A replacement Worker reconciles the Thread inbox before relying on Redis delivery state.
12. Async-subagent results can use the common inbox while retaining their own authorization, selection, retention, and payload semantics.
13. Planned yield neither consumes nor supersedes pending steer; the successor reconciles it from PostgreSQL before relying on Redis.
14. A consumed steer is represented in complete same-Turn state before yield, and receipt reconciliation remains valid across the yielded Attempt boundary.
15. Yield, interrupt, and outcome serialize through the current TurnAttempt fence and canonical Thread/Turn locks, so only one authoritative result can commit.
