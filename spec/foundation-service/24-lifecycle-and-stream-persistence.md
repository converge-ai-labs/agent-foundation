# Lifecycle and Stream Persistence

## Design Position

Foundation Service separates durable lifecycle authority from presentation persistence. One append-oriented `lifecycle_events` table stores ordered lifecycle facts. A Run-scoped Redis Stream carries live Agent messages and public observations with bounded replay, while one immutable object preserves retained presentation after the Redis horizon.

Redis and replay snapshots are projections, never Run or `RunAttempt` authority. Foundation creates no relational table for Items, live or retained stream replay, Native notifications, pending calls or approvals, or provider receipts.

The Thread-scoped Redis control signal Stream is a separate business-payload-free reconciliation wakeup transport owned by [Agent Control: Active Execution](19-agent-control-active-execution.md#thread-control-signal-stream). It never shares the Run presentation cursor, retained replay object, or lifecycle projection state.

## Boundaries and Table Inventory

| Concern                              | Persistence shape                    | Authority                                                                 |
| ------------------------------------ | ------------------------------------ | ------------------------------------------------------------------------- |
| Current Run and attempt state        | `runs`, `run_attempts`               | Owning domain row                                                         |
| Thread control wakeups               | Thread-scoped Redis Stream           | Expiring notification only; PostgreSQL inbox and Run remain authoritative |
| Ordered lifecycle history            | `lifecycle_events`                   | Fact log committed with the owning state mutation                         |
| Live Agent messages and observations | Redis Stream                         | Bounded transport and replay projection only                              |
| Retained Items and stream replay     | Immutable `RunReplaySnapshot` object | Presentation projection, never Run-state or lifecycle authority           |

Only `lifecycle_events` is introduced here, under the service-wide [Relational Schema Lifecycle](04-relational-schema.md). Run waiting state belongs to [Run Persistence](12-run-persistence.md). Tool observations remain presentation or telemetry unless an owning Capability defines its own durable task protocol; this document introduces no generic tool lifecycle authority.

The process composes the required transactional delivery writers explicitly. Each accepted lifecycle mutation, its lifecycle fact, and enabled Webhook/A2A delivery intents commit in the same short transaction; any writer failure rolls back that bundle. Disabled A2A delivery performs no subscription matching. Delivery publication remains outside the transaction.

## Lifecycle Event Model

The following conceptual schema defines one durable fact:

```python
type LifecycleEntityType = Literal[
    "run",
    "run_attempt",
]
type LifecycleProjectionState = Literal[
    "pending",
    "projecting",
    "retry_wait",
    "projected",
    "abandoned",
]


class LifecycleEvent:
    seq: int
    id: str
    tenant_id: str

    entity_type: LifecycleEntityType
    entity_id: str
    resource_seq: int
    entity_version: int
    event_type: str
    schema_version: str
    mutation_id: str

    session_id: str | None
    thread_id: str | None
    run_id: str | None
    run_attempt_id: str | None

    payload: JsonObject
    actor_type: str
    actor_id: str | None
    occurred_at: datetime
    created_at: datetime

    projection_state: LifecycleProjectionState
    projection_attempts: int
    projection_next_attempt_at: datetime | None
    projection_lease_owner: str | None
    projection_lease_expires_at: datetime | None
    projected_at: datetime | None
    projection_error: SafeFailure | None
```

`seq` is a database-assigned positive monotonic Workspace cursor. It orders committed facts in one database history but is not contiguous after filtering to one resource and does not invent causal or Run-parent order. It therefore cannot detect a resource-local delivery gap.

`resource_seq` is a positive, contiguous lifecycle sequence beginning at `1` within one `(tenant_id, entity_type, entity_id)` resource. The owning state transaction allocates it while holding the resource mutation lock, so committed events for that resource have no internal sequence gap before retention. It is the ordering and gap-detection value shared by lifecycle Webhooks and the resource-scoped lifecycle API. `entity_version` is the resource version after the event's owning mutation; it is monotonic but need not be contiguous and is not a lifecycle cursor.

`id` is the stable public event identity. `mutation_id` identifies the owning state mutation; `(tenant_id, mutation_id, event_type, entity_type, entity_id)` is unique so transaction retry cannot append the same fact twice.

Entity and correlation fields are typed, tenant-scoped references. A Run event requires `run_id`; an attempt event requires `run_id` and `run_attempt_id`. Optional Session and Thread fields are query correlations only. `payload` is bounded, versioned, redacted JSON and never contains credentials, arbitrary provider bodies, complete message history, or a Run state object.

Fact columns through `created_at` are immutable. Projection columns may change as the event is mirrored to Redis but cannot change the fact or authorize a state transition.

An event that requires live projection starts `pending`. An event with no configured live projection starts `projected` with `projected_at=created_at`; this means “no projection work remains.”

The supported event-type registry is finite and additive:

| Entity     | Event types                                                                                                                                |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Run        | `run.accepted`, `run.running`, `run.waiting`, `run.completed`, `run.failed`, `run.cancelled`                                               |
| RunAttempt | `run_attempt.leased`, `run_attempt.running`, `run_attempt.succeeded`, `run_attempt.yielded`, `run_attempt.failed`, `run_attempt.cancelled` |

Adding an event type requires a schema-versioned payload and an owning state or observation rule. Consumers preserve unknown additive event types but never use them to infer an unsupported state transition.

### Relational Shape and Access Paths

The `lifecycle_events` table contains the conceptual fields above and preserves these constraints and access paths:

1. `seq` is the primary key and `id` is globally unique.
2. Resource sequences, entity versions, and schema versions are positive; projection attempts are non-negative.
3. `(tenant_id, entity_type, entity_id, resource_seq)` is unique and supports ordered resource-local recovery.
4. Indexes on `(tenant_id, seq)`, `(tenant_id, entity_type, entity_id, seq)`, `(tenant_id, run_id, seq)`, and `(tenant_id, run_attempt_id, seq)` support tenant polling, stable forward pagination, and entity or Run correlation.
5. A partial index on `(projection_state, projection_next_attempt_at, seq)` for `pending`, `projecting`, and `retry_wait` supports bounded projectors.
6. Projection lease fields exist only for `projecting`; `projection_next_attempt_at` exists for `pending` and `retry_wait`; and `projected_at` exists only for `projected`.
7. A state mutation and its required lifecycle events commit in the same short relational transaction. A missing required event aborts that mutation.

Retention deletes bounded event ranges older than the configured horizon. The API rejects a cursor below the lowest retained tenant sequence and returns that boundary explicitly. A resource-scoped lifecycle read likewise reports its lowest retained `resource_seq`; a caller can claim gap-free event recovery only while its requested predecessor remains within that boundary. A lifecycle event referenced by a retained Outbox record remains pinned until that delivery is no longer deliverable or redriveable under the bounded [Outbox retention contract](06-durable-operations-and-outbox.md#outbox-contract). Lifecycle events have no cold archive.

## Run Redis Stream

Every accepted Run has one stable tenant-scoped Redis Stream shared by all its `RunAttempt` values. The internal stream locator is not a bearer reference, and each event identifies its own attempt and Harness Run. Checkpoint resume neither allocates another presentation stream nor uses a stream cursor as state input.

A `run_attempt.yielded` fact closes only that Attempt generation. It does not close the Run Stream, emit another `run.running`, reset its Redis replay cursor, or allocate a replacement stream. A planned-handoff successor appends observations under its fresh Attempt and Harness Run identities to the same open Run Stream. Only the eventual Run terminal outcome closes the stream and makes retained replay publication eligible.

Each Redis entry contains one versioned JSON envelope:

```python
class RunStreamEvent:
    schema_version: Literal["1"]
    event_id: str
    event_type: str
    run_id: str
    thread_id: str
    run_attempt_id: str | None
    harness_run_id: str | None
    lifecycle_event_id: str | None
    item_id: str | None
    occurred_at: datetime
    payload: JsonObject
```

The Redis Stream entry ID is the live replay cursor. `event_id` is the stable event identity carried into retained snapshots; `item_id` is present only when the event creates, changes, or closes one semantic Item. Redis entry IDs, lifecycle `seq`, lifecycle event IDs, Item IDs, and Harness Run IDs remain distinct identifier domains.

Writers bound payloads and stream length, use deterministic event identities for retryable publication, and set a retention TTL that never expires an active Run's stream. Consumers resume within the live horizon from the last Redis Stream entry ID.

The public Native SSE framing, `Last-Event-ID` behavior, and replay-to-live cutover are owned by [Native Streaming and Notifications](21-native-streaming-and-notifications.md#run-sse). Hosted AG-UI and A2A can project this source under their own protocol identities, but they do not reinterpret the Redis entry ID as an AG-UI or A2A cursor.

## Workspace Events and Best-Effort Notifications

Authorized Workspace lifecycle reads page forward over `lifecycle_events.seq` under the Native Workspace event collection. The lifecycle cursor is distinct from every Run Stream entry ID. Retention below a Workspace cursor produces an explicit lifecycle replay gap and never falls through to a surviving row as if history were complete.

Authorized Run and RunAttempt lifecycle reads page forward over `resource_seq` under the Native resource lifecycle collections. Their sequence domain is independent for each resource and supports Webhook gap recovery; it is never inferred from the Workspace cursor. The complete API behavior is owned by [Native Streaming and Notifications](21-native-streaming-and-notifications.md#resource-lifecycle-event-collections).

Native WebSocket notifications are an ephemeral wake-up projection of current resource and lifecycle changes. They have no relational row, Redis replay stream, retained object, delivery acknowledgement, or cursor. Their loss cannot remove a lifecycle event, Run Stream entry, Item, or resource mutation. Disconnected clients reconcile through the Workspace event collection and current resource reads.

## Items and Retained Replay Object

Agent Stream Protocol projection assigns stable Item IDs and emits their changes in `RunStreamEvent`. Once a Run's live stream closes, a projection worker compacts the complete retained presentation into one immutable object at this deterministic internal key:

```text
tenants/{tenant_id}/runs/{run_id}/replay/version-{schema_version}.json
```

For version `1`, this resolves to `tenants/{tenant_id}/runs/{run_id}/replay/version-1.json`; the version segment names the snapshot schema, not a replay sequence or Run state version.

`RunReplaySnapshot` follows the common [Run object serialization rules](12-run-persistence.md#other-object-storage-schemas). Its content type is `application/vnd.converge.run-replay+json`. Object metadata records `schema-version=1`, `run-id`, and the lowercase SHA-256 digest of the canonical stored bytes; object stat supplies the exact byte size. These values are validated before decoding.

The object body is this serialized schema:

```python
class RetainedRunStreamEvent:
    stream_id: str
    event: RunStreamEvent


class RetainedItem:
    id: str
    kind: str
    state: Literal["completed", "interrupted", "failed"]
    parent_item_id: str | None
    first_stream_id: str
    last_stream_id: str
    content: JsonValue


class RunReplaySnapshot:
    schema_version: Literal["1"]
    run_id: str
    thread_id: str
    stream_key_digest_sha256: str
    first_stream_id: str
    last_stream_id: str
    closed_at: datetime
    source_run_attempt_ids: tuple[str, ...]
    events: tuple[RetainedRunStreamEvent, ...]
    items: tuple[RetainedItem, ...]
```

Snapshot publication is create-only. An existing object is accepted only after its digest metadata and complete body validate. A snapshot exists only for a closed, complete, nonempty stream within the configured event-count, Item-count, payload-size, and encoded-size bounds. An incomplete, trimmed, empty, or oversized source reports retained replay as unavailable; version `1` has no partial snapshot or chunk manifest.

The service derives the object key only after a tenant-authorized Run lookup and never treats it as direct authorization. A missing snapshot after Redis expiry means retained presentation is unavailable; Run input, output, and state remain governed by their own records. Item content cannot prove tool execution, provider side effects, or Run completion.

## Failure Semantics

| Failure                                                         | Durable effect                                                  | Recovery                                                                                        |
| --------------------------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Owning state mutation cannot append its required lifecycle fact | Neither change commits                                          | Retry the complete relational transaction                                                       |
| Lifecycle live projection fails                                 | Fact remains pending or retryable                               | Projector reclaims it without repeating the source mutation                                     |
| Yielded lifecycle projection is delayed or duplicated           | The Attempt remains durably yielded and the Run remains running | Successor scheduling reads relational state; projection retry preserves the same event identity |
| Redis presentation stream is lost while Run is active           | Live observation is unavailable                                 | Work continues from durable Run and attempt state; no cursor becomes state                      |
| Redis Thread control Stream is trimmed, expires, or is lost     | Wakeup delivery and its group cursor are unavailable            | Attempt executor reconciles durable Thread inbox and Run state at mandatory boundaries          |
| Replay snapshot publication fails                               | Run outcome remains committed                                   | Retry deterministic create-only publication while source stream is complete                     |
| Native notification is dropped or duplicated                    | Wake-up observation is incomplete                               | Client reconciles durable Workspace events and current resources                                |

## Compatibility and Trade-offs

Lifecycle payload versions, Redis presentation-event versions, Thread control signal versions, replay snapshot versions, and Item projection versions are independent. Unknown required versions fail explicitly in their own reader; they do not change Run or attempt interpretation.

The distinction between the Workspace `seq` cursor and resource-local `resource_seq`, including the latter's per-resource contiguity, is a wire compatibility contract. A deployment cannot renumber retained resource events, reuse a resource sequence, or reinterpret `entity_version` as the recovery cursor.

Using Redis Streams and one retained object instead of Item and replay tables keeps high-volume presentation writes out of the relational database. The cost is an explicit replay horizon and independent projection availability; callers must not mistake retained presentation for durable lifecycle or Run-state authority.

## Invariants

01. Every required lifecycle fact commits atomically with its owning relational state mutation in the one lifecycle fact table.
02. Lifecycle facts are immutable; projection bookkeeping cannot change entity state.
03. One Run uses one stable Redis Stream across all `RunAttempt` values; its entry IDs are transport cursors, not state, lifecycle, Item, or idempotency identities.
04. Foundation creates no relational Item, stream replay, pending-call, or provider-receipt table.
05. Retained presentation is one immutable, complete `RunReplaySnapshot`; a partial source never produces a complete-looking snapshot.
06. Events, Items, Redis, and replay objects never select Run state or authorize another `RunAttempt`.
07. Workspace lifecycle cursors, Run Stream cursors, Hosted delivery cursors, and best-effort notification identities remain separate domains.
08. Native notifications have no durable replay source and never replace lifecycle or resource reads.
09. `seq` orders the Workspace lifecycle feed; `resource_seq` is contiguous only within one lifecycle resource and is the sole numeric resource-gap signal.
10. Thread control signal Streams, consumer-group cursors, and TTL expiry remain separate from Run presentation replay and every durable domain cursor.
11. Planned handoff appends `run_attempt.yielded` without closing or replacing the Run Stream and without repeating `run.running`.
