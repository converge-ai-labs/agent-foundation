# Lifecycle and Stream Persistence

## Design Position

Foundation Service uses one append-oriented `lifecycle_events` table for
ordered durable lifecycle facts. It does not create separate relational tables
for user-visible Items, live stream entries, retained stream replay, pending
calls or approvals, or provider receipts.

Agent-run messages and public observations flow through a Turn-scoped Redis
Stream. Redis is the live transport and bounded replay buffer, not Turn or
`TurnAttempt` authority. An immutable replay snapshot in object storage
preserves retained presentation after the Redis horizon. Item identity lives
in the stream and snapshot envelopes rather than an `items` table.

## Boundaries and Table Inventory

| Concern                              | Persistence shape                                                      | Authority                                                         |
| ------------------------------------ | ---------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Current Turn and attempt state       | `turns`, `turn_attempts`                                               | Owning domain row                                                 |
| Ordered lifecycle history            | `lifecycle_events`                                                     | Append-oriented fact log committed with the owning state mutation |
| Live Agent messages and observations | Redis Stream                                                           | Bounded transport and replay projection only                      |
| Retained Items and stream replay     | Immutable `TurnReplaySnapshot` object                                  | Presentation projection, never Turn-state or lifecycle authority  |
| Waiting call or approval             | Waiting Turn `pending_json` plus its frozen state                      | Exact parent Turn and child-acceptance transaction                |
| Tool/provider effect evidence        | Bounded `turn_attempts.effect_evidence_json` plus provider-owned state | Provider truth and Turn recovery policy                           |

Only `lifecycle_events` is introduced by this contract. Its model participates
in the service-wide
[Relational Schema Lifecycle](03-relational-schema.md).

## Lifecycle Event Model

The following conceptual schema defines one durable fact:

```python
type LifecycleEntityType = Literal[
    "turn",
    "turn_attempt",
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
    entity_version: int
    event_type: str
    schema_version: str
    mutation_id: str

    session_id: str | None
    thread_id: str | None
    turn_id: str | None
    turn_attempt_id: str | None

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

`seq` is a database-assigned positive monotonic cursor. It orders committed
facts in one database history but does not invent causal or Turn-parent order.
`id` is the stable public event identity. `mutation_id` is the stable identity
of the owning state mutation; `(tenant_id, mutation_id, event_type, entity_type, entity_id)` is unique so transaction retry cannot append the same
fact twice.

Entity and correlation fields are typed, tenant-scoped references. A Turn event
requires `turn_id`; an attempt event requires `turn_id` and
`turn_attempt_id`. Optional Session and Thread fields are query correlations
only. `payload` is bounded,
versioned, redacted JSON and never contains credentials, arbitrary provider
bodies, complete message history, or a Turn state object.

Fact columns through `created_at` are immutable. Projection columns may change
as the event is mirrored to realtime Redis. Projection bookkeeping never changes
the fact or grants another state transition.

An event that requires realtime projection starts `pending`. An event with no
configured realtime projection starts `projected` with
`projected_at=created_at`; this means “no projection work remains.”

The initial event-type registry is finite and additive:

| Entity      | Event types                                                                                                                                   |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Turn        | `turn.accepted`, `turn.running`, `turn.recovering`, `turn.waiting`, `turn.completed`, `turn.failed`, `turn.cancelled`                         |
| TurnAttempt | `turn_attempt.leased`, `turn_attempt.running`, `turn_attempt.succeeded`, `turn_attempt.failed`, `turn_attempt.lost`, `turn_attempt.cancelled` |

Adding an event type requires a schema-versioned payload and an owning state or
observation rule. Consumers preserve unknown additive event types but never use
them to infer an unsupported state transition.

### Relational Shape and Access Paths

The `lifecycle_events` table contains exactly the conceptual fields above and
preserves these indexes and constraints:

1. `seq` is the primary key and `id` is globally unique.
2. Entity and schema versions are positive; projection attempts are
   non-negative.
3. `(tenant_id, seq)` supports tenant polling and stable forward pagination.
4. `(tenant_id, entity_type, entity_id, seq)` supports one entity's history.
5. `(tenant_id, turn_id, seq)` and `(tenant_id, turn_attempt_id, seq)` support
   correlated history when the corresponding reference exists.
6. A partial index on
   `(projection_state, projection_next_attempt_at, seq)` for `pending`,
   `projecting`, and `retry_wait` supports bounded projectors.
7. Projection lease fields exist only for `projecting`;
   `projection_next_attempt_at` exists for `pending` and `retry_wait`; and
   `projected_at` exists only for `projected`.
8. A state mutation and its required lifecycle events commit in the same short
   relational transaction. A missing required event aborts that mutation.

Retention deletes only bounded event ranges older than the configured horizon.
A cursor never silently jumps over deleted history: the API compares it with
the lowest retained tenant sequence and reports an expired cursor plus that
boundary explicitly. This contract defines no lifecycle cold-archive object.

## Turn Redis Stream

Every accepted Turn has one stable internal Redis Stream key derived from its
tenant and Turn identity:

```text
foundation:tenant:{tenant_id}:turn:{turn_id}:events
```

The key is an internal locator, not a public bearer reference. All
`TurnAttempt` values for the same Turn publish to this same stream and identify
their own attempt and Harness Run in each event. Checkpoint resume therefore
does not allocate another presentation stream or use a stream cursor as state
input.

Each Redis entry contains one versioned JSON envelope:

```python
class TurnStreamEvent:
    schema_version: Literal["1"]
    event_id: str
    event_type: str
    turn_id: str
    thread_id: str
    turn_attempt_id: str | None
    run_id: str | None
    lifecycle_event_id: str | None
    item_id: str | None
    occurred_at: datetime
    payload: JsonObject
```

The Redis Stream entry ID is the live replay cursor. `event_id` is the stable
event identity carried into retained snapshots; `item_id` is present only when
the event creates, changes, or closes one semantic Item. Redis entry IDs,
lifecycle `seq`, lifecycle event IDs, Item IDs, and Harness Run IDs remain
distinct identifier domains.

Writers use bounded payloads, deterministic event identities for retryable
publication, capped stream length, and a retention TTL that does not expire an
active Turn's stream. Consumers resume within the live horizon by supplying the
last Redis Stream entry ID. Pub/Sub notifications may wake consumers but never
replace Stream reads.

Redis loss or trimming can remove live observations without changing Turn,
`TurnAttempt`, or lifecycle facts. The service reports replay unavailability
instead of reconstructing missing model deltas from relational state.

## Items and Retained Replay Object

Foundation creates no `items` or `stream_replay` table. Agent Stream Protocol
projection assigns stable Item IDs and emits their changes in
`TurnStreamEvent`. Once a Turn's live stream is closed, a projection worker
compacts the complete retained presentation into one immutable object at the
deterministic internal key:

```text
tenants/{tenant_id}/turns/{turn_id}/replay/1.json
```

The stored content type is
`application/vnd.converge.turn-replay+json`. Object metadata records
`schema-version=1`, `turn-id`, and the lowercase SHA-256 digest of the canonical
stored bytes; object stat supplies the exact byte size. These values are
validated before decoding.

Replay JSON objects use UTF-8 RFC 8785 canonical JSON. Their digests and sizes
cover the exact stored bytes; non-finite numbers and duplicate object keys are
invalid.

The object body is this serialized schema:

```python
class RetainedTurnStreamEvent:
    stream_id: str
    event: TurnStreamEvent


class RetainedItem:
    id: str
    kind: str
    state: Literal["completed", "interrupted", "failed"]
    parent_item_id: str | None
    first_stream_id: str
    last_stream_id: str
    content: JsonValue


class TurnReplaySnapshot:
    schema_version: Literal["1"]
    turn_id: str
    thread_id: str
    stream_key_digest_sha256: str
    first_stream_id: str
    last_stream_id: str
    closed_at: datetime
    source_turn_attempt_ids: tuple[str, ...]
    events: tuple[RetainedTurnStreamEvent, ...]
    items: tuple[RetainedItem, ...]
```

The snapshot is written create-only. Before treating an existing object as the
same projection, the writer validates its SHA-256 metadata and complete body.
The snapshot is emitted only from a closed, completely readable stream; an
incomplete or already-trimmed source produces no snapshot rather than a value
that appears complete. Publication failure is independently retryable and does
not roll back an already committed Turn outcome. A closed stream with no
entries also produces no snapshot, so `first_stream_id` and `last_stream_id`
are always meaningful when the object exists.

Event count, Item count, individual payload size, and total encoded bytes are
bounded. A Turn whose retained presentation exceeds the configured contract
keeps its durable output and state but reports retained replay as
unavailable; version `1` defines no partial snapshot or chunk manifest.

The object key is derived only after tenant-authorized Turn lookup. APIs never
accept it as direct authorization. A missing snapshot after Redis expiry means
retained presentation is unavailable; the exact Turn input, output, and
state remain governed by their own records. Item content is a
presentation projection and cannot prove tool execution, provider side
effects, or Turn completion.

## Failure Semantics

| Failure                                                         | Durable effect                    | Recovery                                                                    |
| --------------------------------------------------------------- | --------------------------------- | --------------------------------------------------------------------------- |
| Owning state mutation cannot append its required lifecycle fact | Neither change commits            | Retry the complete relational transaction                                   |
| Lifecycle realtime projection fails                             | Fact remains pending or retryable | Projector reclaims it without repeating the source mutation                 |
| Redis stream is lost while Turn is active                       | Live observation is unavailable   | Work continues from durable Turn and attempt state; no cursor becomes state |
| Replay snapshot publication fails                               | Turn outcome remains committed    | Retry deterministic create-only publication while source stream is complete |

## Compatibility and Trade-offs

Lifecycle payload versions, Redis event versions, replay snapshot versions, and
Item projection versions are independent. Unknown required versions fail
explicitly in their own reader; they do not change Turn or attempt
interpretation.

Using Redis Streams and one retained object instead of Item and replay tables
keeps high-volume presentation writes out of the relational database. The cost
is an explicit replay horizon and independent projection availability; callers
must not mistake retained presentation for durable lifecycle or Turn-state
authority.

## Invariants

1. Foundation has one lifecycle fact table.
2. Every required lifecycle fact commits atomically with its owning relational
   state mutation.
3. Lifecycle fact columns are immutable; projection bookkeeping cannot change
   entity state.
4. One Turn uses one stable Redis Stream across all its `TurnAttempt` values.
5. Redis Stream entry IDs are transport cursors and never Turn-state,
   lifecycle, Item, or idempotency identities.
6. Foundation creates no relational Item, stream replay, pending-call, or
   provider-receipt table.
7. Retained Item and stream presentation is one immutable, complete
   `TurnReplaySnapshot`; a partial source never produces a complete-looking
   snapshot.
8. Events, Items, Redis, and replay objects never select Turn state or
   authorize another `TurnAttempt`.
