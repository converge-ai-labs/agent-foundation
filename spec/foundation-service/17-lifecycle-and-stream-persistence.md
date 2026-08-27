# Lifecycle and Stream Persistence

## Design Position

Foundation Service separates durable lifecycle authority from presentation
persistence. One append-oriented `lifecycle_events` table stores ordered
lifecycle facts. A Turn-scoped Redis Stream carries live Agent messages and
public observations with bounded replay, while one immutable object preserves
retained presentation after the Redis horizon.

Redis and replay snapshots are projections, never Turn or `TurnAttempt`
authority. Foundation creates no relational table for Items, live or retained
stream replay, pending calls or approvals, or provider receipts.

## Boundaries and Table Inventory

| Concern                              | Persistence shape                     | Authority                                                        |
| ------------------------------------ | ------------------------------------- | ---------------------------------------------------------------- |
| Current Turn and attempt state       | `turns`, `turn_attempts`              | Owning domain row                                                |
| Ordered lifecycle history            | `lifecycle_events`                    | Fact log committed with the owning state mutation                |
| Live Agent messages and observations | Redis Stream                          | Bounded transport and replay projection only                     |
| Retained Items and stream replay     | Immutable `TurnReplaySnapshot` object | Presentation projection, never Turn-state or lifecycle authority |

Only `lifecycle_events` is introduced here, under the service-wide
[Relational Schema Lifecycle](04-relational-schema.md). Turn waiting state and
Agent tool-dispatch evidence belong to [Turn Persistence](14-turn-persistence.md)
and [TurnAttempt Persistence](15-turn-attempt-persistence.md), respectively.

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
`id` is the stable public event identity. `mutation_id` identifies the owning
state mutation; `(tenant_id, mutation_id, event_type, entity_type, entity_id)`
is unique so transaction retry cannot append the same fact twice.

Entity and correlation fields are typed, tenant-scoped references. A Turn event
requires `turn_id`; an attempt event requires `turn_id` and
`turn_attempt_id`. Optional Session and Thread fields are query correlations
only. `payload` is bounded, versioned, redacted JSON and never contains
credentials, arbitrary provider bodies, complete message history, or a Turn
state object.

Fact columns through `created_at` are immutable. Projection columns may change
as the event is mirrored to Redis but cannot change the fact or authorize a
state transition.

An event that requires live projection starts `pending`. An event with no
configured live projection starts `projected` with
`projected_at=created_at`; this means “no projection work remains.”

The supported event-type registry is finite and additive:

| Entity      | Event types                                                                                                                                   |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Turn        | `turn.accepted`, `turn.running`, `turn.waiting`, `turn.completed`, `turn.failed`, `turn.cancelled`                                            |
| TurnAttempt | `turn_attempt.leased`, `turn_attempt.running`, `turn_attempt.succeeded`, `turn_attempt.failed`, `turn_attempt.lost`, `turn_attempt.cancelled` |

Adding an event type requires a schema-versioned payload and an owning state or
observation rule. Consumers preserve unknown additive event types but never use
them to infer an unsupported state transition.

### Relational Shape and Access Paths

The `lifecycle_events` table contains the conceptual fields above and preserves
these constraints and access paths:

1. `seq` is the primary key and `id` is globally unique.
2. Entity and schema versions are positive; projection attempts are
   non-negative.
3. Indexes on `(tenant_id, seq)`,
   `(tenant_id, entity_type, entity_id, seq)`, `(tenant_id, turn_id, seq)`, and
   `(tenant_id, turn_attempt_id, seq)` support tenant polling, stable forward
   pagination, and entity or Turn correlation.
4. A partial index on
   `(projection_state, projection_next_attempt_at, seq)` for `pending`,
   `projecting`, and `retry_wait` supports bounded projectors.
5. Projection lease fields exist only for `projecting`;
   `projection_next_attempt_at` exists for `pending` and `retry_wait`; and
   `projected_at` exists only for `projected`.
6. A state mutation and its required lifecycle events commit in the same short
   relational transaction. A missing required event aborts that mutation.

Retention deletes bounded event ranges older than the configured horizon. The
API rejects a cursor below the lowest retained tenant sequence and returns that
boundary explicitly. Lifecycle events have no cold archive.

## Turn Redis Stream

Every accepted Turn has one stable tenant-scoped Redis Stream shared by all its
`TurnAttempt` values. The internal stream locator is not a bearer reference,
and each event identifies its own attempt and Harness Run. Checkpoint resume
neither allocates another presentation stream nor uses a stream cursor as state
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

Writers bound payloads and stream length, use deterministic event identities
for retryable publication, and set a retention TTL that never expires an active
Turn's stream. Consumers resume within the live horizon from the last Redis
Stream entry ID.

## Items and Retained Replay Object

Agent Stream Protocol projection assigns stable Item IDs and emits their changes
in `TurnStreamEvent`. Once a Turn's live stream closes, a projection worker
compacts the complete retained presentation into one immutable object at this
deterministic internal key:

```text
tenants/{tenant_id}/turns/{turn_id}/replay/version-{schema_version}.json
```

For version `1`, this resolves to
`tenants/{tenant_id}/turns/{turn_id}/replay/version-1.json`; the version segment
names the snapshot schema, not a replay sequence or Turn version.

`TurnReplaySnapshot` follows the common
[Turn object serialization rules](14-turn-persistence.md#other-object-storage-schemas).
Its content type is `application/vnd.converge.turn-replay+json`. Object metadata records
`schema-version=1`, `turn-id`, and the lowercase SHA-256 digest of the canonical
stored bytes; object stat supplies the exact byte size. These values are
validated before decoding.

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

Snapshot publication is create-only. An existing object is accepted only after
its digest metadata and complete body validate. A snapshot exists only for a
closed, complete, nonempty stream within the configured event-count,
Item-count, payload-size, and encoded-size bounds. An incomplete, trimmed,
empty, or oversized source reports retained replay as unavailable; version `1`
has no partial snapshot or chunk manifest.

The service derives the object key only after a tenant-authorized Turn lookup
and never treats it as direct authorization. A missing snapshot after Redis
expiry means retained presentation is unavailable; Turn input, output, and
state remain governed by their own records. Item content cannot prove tool
execution, provider side effects, or Turn completion.

## Failure Semantics

| Failure                                                         | Durable effect                    | Recovery                                                                    |
| --------------------------------------------------------------- | --------------------------------- | --------------------------------------------------------------------------- |
| Owning state mutation cannot append its required lifecycle fact | Neither change commits            | Retry the complete relational transaction                                   |
| Lifecycle live projection fails                                 | Fact remains pending or retryable | Projector reclaims it without repeating the source mutation                 |
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

1. Every required lifecycle fact commits atomically with its owning relational
   state mutation in the one lifecycle fact table.
2. Lifecycle facts are immutable; projection bookkeeping cannot change entity
   state.
3. One Turn uses one stable Redis Stream across all `TurnAttempt` values; its
   entry IDs are transport cursors, not state, lifecycle, Item, or idempotency
   identities.
4. Foundation creates no relational Item, stream replay, pending-call, or
   provider-receipt table.
5. Retained presentation is one immutable, complete `TurnReplaySnapshot`; a
   partial source never produces a complete-looking snapshot.
6. Events, Items, Redis, and replay objects never select Turn state or authorize
   another `TurnAttempt`.
