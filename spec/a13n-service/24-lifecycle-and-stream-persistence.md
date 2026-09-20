# Lifecycle and Stream Persistence

## Design Position

a13n Service separates durable lifecycle authority from presentation persistence. One append-oriented `lifecycle_events` table stores ordered lifecycle facts. A Run-scoped Redis Stream carries live Agent messages and public observations with bounded replay, while one conditionally replaced display snapshot preserves merged presentation and its durable consumption cursor beyond the Redis horizon.

Redis and display snapshots are projections, never Run or `RunAttempt` authority. Service creates no relational table for Items, live or retained stream replay, Native notifications, pending calls or approvals, or provider receipts.

The Thread-scoped Redis control signal Stream is a separate business-payload-free reconciliation wakeup transport owned by [Agent Control: Active Execution](19-agent-control-active-execution.md#thread-control-signal-stream). It never shares the Run presentation cursor, display object, or lifecycle projection state.

## Boundaries and Table Inventory

| Concern                              | Persistence shape                       | Authority                                                                 |
| ------------------------------------ | --------------------------------------- | ------------------------------------------------------------------------- |
| Current Run and attempt state        | `runs`, `run_attempts`                  | Owning domain row                                                         |
| Thread control wakeups               | Thread-scoped Redis Stream              | Expiring notification only; PostgreSQL inbox and Run remain authoritative |
| Ordered lifecycle history            | `lifecycle_events`                      | Fact log committed with the owning state mutation                         |
| Live Agent messages and observations | Redis Stream                            | Bounded transport and replay projection only                              |
| Retained Items and consumer progress | Replaceable `RunDisplaySnapshot` object | Presentation projection, never Run-state or lifecycle authority           |

Only `lifecycle_events` is introduced here, under the service-wide [Relational Schema Lifecycle](04-relational-schema.md). Run waiting state belongs to [Run Persistence](12-run-persistence.md). Tool observations remain presentation or telemetry unless an owning Capability defines its own durable task protocol; this document introduces no generic tool lifecycle authority.

Each accepted lifecycle mutation commits its lifecycle fact with pending Hook dispatch in the same short transaction. [Hook dispatch](26-hook-notifications.md#asynchronous-hook-dispatch) matches Webhook subscriptions and creates Outbox rows asynchronously. Enabled A2A delivery retains its separately composed transactional delivery writer; its failure rolls back the source bundle, and disabled A2A delivery performs no subscription matching. Delivery publication remains outside the transaction.

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
    organization_id: str

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

    hook_dispatch_state: Literal["pending", "done", "failed"]
    hook_dispatch_attempts: int
    hook_dispatch_next_attempt_at: datetime | None
    hook_dispatched_at: datetime | None
    hook_dispatch_error: SafeFailure | None
```

`seq` is a database-assigned positive monotonic Workspace cursor. It orders committed facts in one database history but is not contiguous after filtering to one resource and does not invent causal or Run-parent order. It therefore cannot detect a resource-local delivery gap.

`resource_seq` is a positive, contiguous lifecycle sequence beginning at `1` within one `(organization_id, entity_type, entity_id)` resource. The owning Run or RunAttempt row retains its lifecycle sequence independently from the business version. The state mutation increments this counter and appends the fact atomically under the resource mutation fence, so committed events for that resource have no internal sequence gap before retention. A rolled-back mutation consumes no resource sequence. Retention never resets the counter or permits an earlier sequence to be reused, including when no events remain retained. It is the ordering and gap-detection value shared by lifecycle Webhooks and the resource-scoped lifecycle API. `entity_version` is the resource version after the event's owning mutation; it is monotonic but need not be contiguous and is not a lifecycle cursor.

`id` is the stable public event identity. `mutation_id` identifies the owning state mutation; `(organization_id, mutation_id, event_type, entity_type, entity_id)` is unique so transaction retry cannot append the same fact twice.

Entity and correlation fields are typed, organization-scoped references. A Run event requires `run_id`; an attempt event requires `run_id` and `run_attempt_id`. Optional Session and Thread fields are query correlations only. `payload` is bounded, versioned, redacted JSON and never contains credentials, arbitrary provider bodies, complete message history, or a Run state object.

Public lifecycle reads omit `projection_lease_owner`. Projection state, retry counts, timing, and safe failure observations remain available for diagnostics. Worker actors retain `actor_type="worker"` with a null `actor_id`; their process-instance identity and payload `worker_id` stay internal. Public Attempt IDs, Harness Run correlation, and Worker build identity remain available for diagnostics.

Event queries, Native SSE, retained Item reads, and lifecycle Webhook delivery apply the same disclosure boundary to both new and previously retained data. An object-backed Run output exposes only `digest_sha256`, `size_bytes`, `content_type`, and `schema_version` metadata in its output reference; its `object_key` remains internal. These projections do not recursively redact user-authored output fields with matching names. Durable lifecycle facts and display objects keep their original internal references and integrity metadata; public reads do not rewrite them.

Fact columns through `created_at` are immutable. Redis projection and Hook dispatch bookkeeping change independently; neither changes the fact or authorizes a state transition. Hook dispatch starts `pending` with zero attempts and an immediately due retry time; `done` records `hook_dispatched_at`, while `failed` requires explicit retry. [Hook Notifications](26-hook-notifications.md#asynchronous-hook-dispatch) owns dispatch matching, completion, and recovery.

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
3. `(organization_id, entity_type, entity_id, resource_seq)` is unique and supports ordered resource-local recovery.
4. Indexes on `(organization_id, seq)`, `(organization_id, entity_type, entity_id, seq)`, `(organization_id, run_id, seq)`, and `(organization_id, run_attempt_id, seq)` support organization polling, stable forward pagination, and entity or Run correlation.
5. A partial index on `(projection_state, projection_next_attempt_at, seq)` for `pending`, `projecting`, and `retry_wait` supports bounded projectors.
6. Projection lease fields exist only for `projecting`; `projection_next_attempt_at` exists for `pending` and `retry_wait`; and `projected_at` exists only for `projected`.
7. A state mutation and its required lifecycle events commit in the same short relational transaction. A missing required event aborts that mutation.
8. A partial index on `(hook_dispatch_next_attempt_at, seq)` for pending Hook dispatch supports bounded claims; dispatch attempts are non-negative, and dispatch bookkeeping is independent of projection leases.

Retention deletes bounded event ranges older than the configured horizon. The API rejects a cursor below the lowest retained organization sequence and returns that boundary explicitly. A resource-scoped lifecycle read likewise reports its lowest retained `resource_seq`; a caller can claim gap-free event recovery only while its requested predecessor remains within that boundary. An event with pending or failed Hook dispatch remains pinned. A lifecycle event referenced by a retained Outbox record remains pinned until that delivery is no longer deliverable or redriveable under the bounded [Outbox retention contract](06-durable-operations-and-outbox.md#outbox-contract). Lifecycle events have no cold archive.

[Control Background Tasks](07-control-background-tasks.md#evidence-and-lifecycle-retention) owns periodic execution of this retention policy. Its scans preserve a contiguous retained boundary and wait for unfinished Hook dispatch, projection, or retained delivery dependencies rather than deleting around them.

## Run Redis Stream

Every accepted Run has one stable organization-scoped Redis Stream shared by all its `RunAttempt` values. The internal stream locator is not a bearer reference, and each event preserves its source Attempt and Harness Run identities when present. Checkpoint resume neither allocates another presentation stream nor uses a stream cursor as state input.

A `run_attempt.yielded` fact closes only that Attempt generation. It does not close the Run Stream, emit another `run.running`, reset its Redis replay cursor, or allocate a replacement stream. A planned-handoff successor appends observations under its fresh Attempt and Harness Run identities to the same open Run Stream. Display projection runs throughout execution and across handoffs. Only the eventual Run terminal outcome closes the stream and makes the final display snapshot eligible.

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

The Redis Stream entry ID is the live replay cursor. `event_id` is the stable event identity used for publication deduplication and display projection; `item_id` is present only when the event creates, changes, or closes one semantic Item. Redis entry IDs, lifecycle `seq`, lifecycle event IDs, Item IDs, and Harness Run IDs remain distinct identifier domains.

Tool observations carrying `toolCallId` also preserve the native call reference as `payload.source_tool_call_id`, scoped by the envelope's RunAttempt and Harness Run. The presentation `toolCallId` remains namespaced for inline children. Consumers use the native reference and source scope to join delegation and CodeAct observations; the reference grants no authority and does not replace Item identity.

Writers bound payloads, use deterministic event identities for retryable publication, and preserve every accepted event not yet covered by a confirmed display snapshot. Stream length targets may trim only a prefix at or below the durable display cursor; they never authorize unconditional `MAXLEN` eviction of unpersisted events. Consumers resume within the live horizon from the last Redis Stream entry ID. A cursor equal to the safely trimmed boundary remains a valid predecessor for reading the surviving suffix.

An active Run's stream does not expire. After stream closure, retention cannot expire its unpersisted suffix merely because the Run is terminal: final snapshot publication or explicit incomplete-presentation retirement must settle first. Optional retention of already persisted raw events supports bounded exact replay and is independent of display-history availability.

### Projection Capacity and Retention

The Worker role, including `all`, owns a supervised display projector independently of any selected Attempt's lifetime. It discovers accepted Runs with unfinished display projection through bounded fair reconciliation, including terminal Runs after a process restart; neither a browser attachment nor a one-shot terminal notification is required. Wakeups only accelerate discovery. PostgreSQL Run and lifecycle rows identify candidates, while the display object owns durable consumption progress. No relational Item or replay-progress table is introduced.

The projector consumes ordered batches after the last committed display cursor and merges them in bounded process-local memory. It flushes when any configured event-count, accumulated-byte, or elapsed-time threshold is reached; an elapsed-time flush also covers a quiet stream. Terminal closure forces a final flush. Shutdown attempts a bounded flush and otherwise leaves uncommitted progress in Redis for a replacement consumer.

Runtime configuration declares finite batch, memory, snapshot-size, unpersisted-backlog, flush, and retry bounds. Event-count thresholds trigger flushing rather than limit the total event count of a Run. When persistence stalls, writers apply bounded backpressure before accepting events beyond the unpersisted-backlog bound; they neither silently drop events nor acknowledge an append that did not occur. Client slowness is not this persistence backpressure. A limit or exhausted publication deadline follows the existing safe publication-failure contract, preserves the last good snapshot and unpersisted suffix until explicit retirement, and reports presentation incompleteness without rewriting the Run outcome. Logs and metrics expose cursor lag, backlog bytes/events, flush age/failures, conditional conflicts, and incomplete retirement without message content.

The public Native SSE framing, `Last-Event-ID` behavior, and replay-to-live cutover are owned by [Native Streaming and Notifications](21-native-streaming-and-notifications.md#run-sse). Hosted AG-UI and A2A can project this source under their own protocol identities, but they do not reinterpret the Redis entry ID as an AG-UI or A2A cursor.

### Publication Activation and Fencing

PostgreSQL owns Attempt selection, leases, and the monotonic `attempt_number` fence defined by [Attempt authority](13-run-attempt-scheduling-and-recovery.md#current-attempt-authority). Redis reuses that fence only to gate presentation publication; it allocates no independent ownership counter and grants no execution or outcome authority.

After the claim commits and before publishing any Attempt-owned observation, including Environment preparation, the executor requests one Redis Lua activation with the claim-derived organization, Run, Attempt, fence, and exact committed `run_attempt.leased` fact. No database session or transaction spans this request or its retries. The operation:

1. validates Run identity, open-stream state, and expected publication state; rejects a lower fence or a different Attempt at the same fence;
2. atomically advances the publication fence, appends the committed `run_attempt.leased` projection, and appends `run.recovery` immediately after it for a non-initial Attempt; and
3. retains stable opening-event identities, canonical payloads, and activation-result evidence for idempotent retry. The same request returns its existing result; conflicting content fails explicitly. The result also reports whether that generation remains active.

The successful Redis activation is the presentation switch. Old observations accepted after the PostgreSQL claim but before activation remain valid history before the opening events. Already accepted or buffered events cannot be recalled; ordering concerns stream position, not client receipt time. Lease expiry alone does not atomically revoke Redis publication before another activation. Execution still obeys PostgreSQL authority throughout.

Only confirmed activation admits observations. Every Attempt-owned append atomically checks the exact activated Attempt/fence and open-stream state together with `XADD` in Lua. A separate check followed by append is insufficient; ordinary append cannot initialize or advance authority. An old activation receipt never permits publication after a newer activation. Stable event deduplication remains required independently of fencing.

The same fence protects Attempt-owned completion metadata and any Worker-accessible reset, cleanup, or close operation. A stale Worker cannot delete replacement events, close its stream, or certify its projection complete. Stale-publication rejection closes local publishing admission and invokes existing authority-loss handling; it never authorizes a stale Run failure. Trusted projection of a committed Run outcome retains its separate stream-closure rules.

### Lifecycle Projection at Activation

`run_attempt.leased` has one Run Stream publication path: activation. The generic asynchronous lifecycle projector never appends it independently. Worker retries and service-owned repair use the same atomic operation and identities. Confirmation settles its lifecycle projection bookkeeping without repeating the source mutation. Once initialization has settled, activation retries do not repeat initialization. If the original Worker disappears while its Attempt is still current and its lease valid, lifecycle repair resumes that exact activation after revalidating durable authority. When an unfinished current activation blocks an earlier historical fact, repair completes that activation before continuing ordered lifecycle projection.

If an Attempt is superseded before activation, its leased fact remains in PostgreSQL history but is not later appended as a false switch or used to reactivate that Attempt. After establishing that no activation occurred, reconciliation marks the fact `projected`, meaning no projection work remains. An unknown activation outcome follows bounded reconciliation; it is not silently skipped or certified complete. Exhaustion without establishing completeness marks the projection `abandoned` and retained replay unavailable. Projection settlement never blocks the authoritative Run outcome. This distinction settles superseded claims without waiting forever for an obsolete activation.

Other committed lifecycle facts, including a delayed failure for an older Attempt, remain eligible for trusted lifecycle projection. Their older provenance alone does not reject them, and their publication never changes the active fence. This exception grants no live-writing permission to the old Worker. Stream completeness accounts for these facts separately from activation and Attempt-owned observations.

### Recovery Event

`run.recovery` is a Run Stream presentation event, not a new Run status, PostgreSQL lifecycle-event type, or durable Hook-delivery source. It records a switch to a replacement publisher, not successful preparation, Harness restoration, or agent execution. Activation of the first Attempt emits only `run_attempt.leased`. Every successor Attempt's successful activation emits one recovery event, even if its predecessors never activated or produced agent output, or the new Worker stops before Harness entry.

It uses the existing `RunStreamEvent` envelope with the same Run and Thread, the new `run_attempt_id`, the source leased fact's `lifecycle_event_id`, and no `item_id`. `harness_run_id` may be null before Harness entry. Its stable `event_id` is derived from the source leased event identity and the recovery event type; its `occurred_at` uses the source fact's timestamp. Both remain unchanged on retry. The ordered stream position defines the effective switch, independently of that timestamp.

Version `1` has the serialized payload `{"reason": "lease_expired"}`, with this finite reason registry:

| Reason                | Owning claim classification                                                |
| --------------------- | -------------------------------------------------------------------------- |
| `lease_expired`       | Replacement of an expired selected Attempt; does not prove a Worker crash  |
| `retry_after_failure` | Retry after a known retryable Attempt failure                              |
| `planned_handoff`     | Successor to a yielded Attempt                                             |
| `pending_input`       | Successor after an Attempt succeeded while accepted input remained pending |

Native live delivery, reconnect, and retained replay preserve the event's identity and source position before replacement observations. Replay includes it only when it follows the supplied cursor within available history; a cursor past the boundary never causes another recovery event. Missing history follows the existing explicit gap rules. [Hosted AG-UI recovery](22-hosted-ag-ui.md#recovery-projection) owns the safe custom projection and informative client guidance. The source event carries no public fence or lease proof.

### Publication Failure and Continuity

Activation timeout, unknown outcome, or script failure permits only bounded reconciliation and retry, never unfenced publication. Lua does not roll back writes made before a runtime error: input, key types, payload bounds, and ordinary rejection conditions are validated before mutation. Partial activation must remain closed to observations until its complete opening sequence is established; a fence-only write is not successful activation. Transient failures permit an identical retry, while stale authority is rejected and known continuity loss stops activation retries. A receipt for an already committed historical lifecycle fact may be confirmed without mutation while another operation is unfinished, allowing a partial terminal close to resume.

Active publication metadata and deduplication evidence cannot expire before their required active-Run lifetime. Missing, lost, or rolled-back metadata is not permission to restamp authority. Service-owned recovery revalidates durable authority before reinitialization and stops publication when admission or continuity cannot be established. Incomplete history retains explicit replay-gap/unavailable semantics and cannot produce a complete snapshot. A prefix safely trimmed after confirmed display persistence is not a continuity loss for display projection; publication receipts and activation evidence still survive for their required deduplication lifetime.

Lua atomicity alone provides no continuity guarantee across asynchronous-replication rollback. A supported Redis topology defines how discontinuity invalidates publication admission and how authority is revalidated before reopening it; unsupported rollback conditions fail closed. Redis Cluster support requires every key touched by one script to share a hash slot. No normal writer bypasses these rules when metadata or scripts are unavailable.

A committed terminal Run fact owns retirement of unavailable presentation state. Retirement marks surviving metadata incomplete, removes publication admission, and applies the closed-stream retention deadline without repairing history or requiring a valid primary incarnation. Missing event or metadata keys and partial publication cannot prevent cleanup. A surviving stream without metadata receives an incomplete terminal marker. Retirement validates Run identity and never grants an expired or superseded Attempt authority.

If terminal publication exhausts its retry budget, the same durable lifecycle projection remains retryable until retirement succeeds; subsequent claims perform cleanup only. Retirement fences the source without expiring unpersisted content. After the consumer confirms an explicitly incomplete finalized display snapshot, cleanup starts the retention deadline; retries preserve that deadline, including after partially applied expiration or lost acknowledgement. Nonterminal abandonment remains recorded in SQL so the terminal fact can account for missing history and retire it. Cleanup failure never changes the authoritative Run outcome or certifies retained replay complete.

## Workspace Events and Best-Effort Notifications

Authorized Workspace lifecycle reads page forward over `lifecycle_events.seq` under the Native Workspace event collection. The lifecycle cursor is distinct from every Run Stream entry ID. Retention below a Workspace cursor produces an explicit lifecycle replay gap and never falls through to a surviving row as if history were complete.

Authorized Run and RunAttempt lifecycle reads page forward over `resource_seq` under the Native resource lifecycle collections. Their sequence domain is independent for each resource and supports Webhook gap recovery; it is never inferred from the Workspace cursor. The complete API behavior is owned by [Native Streaming and Notifications](21-native-streaming-and-notifications.md#resource-lifecycle-event-collections).

Native WebSocket notifications are an ephemeral wake-up projection of current resource and lifecycle changes. They have no relational row, Redis replay stream, retained object, delivery acknowledgement, or cursor. Their loss cannot remove a lifecycle event, Run Stream entry, Item, or resource mutation. Disconnected clients reconcile through the Workspace event collection and current resource reads.

## Items and Display Snapshot

Agent Stream Protocol projection assigns stable Item IDs and emits their changes in `RunStreamEvent`. The display projector incrementally merges these observations and conditionally replaces one object at this deterministic internal key:

```text
organizations/{organization_id}/runs/{run_id}/display_messages.json
```

Each successful publication atomically stores the complete merged representation through one cursor, including the continuation state needed to merge the next event. It uses the [compressed JSON codec](03-storage.md#compressed-json-objects), with `schema-version=1` and `run-id` added to the shared encoding metadata. The snapshot is a projection checkpoint, never a Harness execution checkpoint.

The following conceptual schema defines the required stored information; the projection schema owns the exact typed Item content and merge-state encoding:

```python
class RetainedItem:
    id: str
    kind: str
    state: Literal["in_progress", "completed", "interrupted", "failed"]
    parent_item_id: str | None
    first_stream_id: str
    last_stream_id: str
    content: JsonValue


class RunDisplaySnapshot:
    schema_version: Literal["1"]
    projection_schema_version: str
    version: int
    run_id: str
    thread_id: str
    stream_key_digest_sha256: str
    cursor: str | None
    complete: bool
    incomplete_reason: str | None
    finalized: bool
    closed_at: datetime | None
    source_run_attempt_ids: tuple[str, ...]
    items: tuple[RetainedItem, ...]
    merge_state: JsonObject
```

`version` is a positive, monotonically increasing publication version. `cursor` is the last fully incorporated Redis entry, or null before any event. `complete` means every accepted source event from the Run's start through that cursor was incorporated; it does not mean that the Run ended. `finalized` means display projection has settled through confirmed stream closure or explicit incomplete retirement. `closed_at` is null until finalization and then identifies that presentation closure, never proof of Run success. Complete snapshots have no incomplete reason; finalized complete snapshots cover the final stream boundary and contain no in-progress Item. Incomplete snapshots carry a bounded safe reason and cannot later claim complete coverage across the same missing interval.

`merge_state` is versioned, serializable projection data needed for deterministic continuation: open text and reasoning accumulators, partial tool arguments, Item and parent correlation, source Attempt/Harness identities, and any deduplication or recovery-boundary state needed across batches. It contains no live object, lease, credential, Harness state, or execution authority. Flush boundaries do not close open Items. Terminal settlement marks unresolved Items interrupted without fabricating completion; publisher recovery preserves earlier Items and source attribution rather than resetting the projection. Readers reject an unsupported merge-state version instead of restarting from an arbitrary surviving event.

### Conditional Publication and Recovery

One consumer normally projects a Run at a time. Correctness relies on expected-object-version publication, not a process-local mutex or a Redis lease alone. The first write is create-only; every replacement uses the opaque object version read with its predecessor. Every successor incorporates the predecessor's full committed state and advances `version`; its cursor never regresses and a finalized object cannot be reopened. Competing writers with the same predecessor cannot both commit. A losing or superseded consumer discards its speculative merge state and reloads the winning snapshot before doing more work.

Content, open-item merge state, coverage flags, and cursor are one atomic object publication. A separate cursor write or consumer acknowledgement cannot precede it. Only a confirmed object publication allows the safe Redis trim watermark to advance, and that watermark is monotonic and bound to the same Run and stream identity. It is a reconstructible hint; the object remains the progress authority. A stale consumer cannot publish an older object or advance trimming beyond verified durable coverage.

If a PUT acknowledgement is lost, read and validate the object and reconcile the exact submitted bytes/metadata or a verified successor before acknowledging consumption or trimming. Never retry an unconditional overwrite. If publication succeeded but the process died before updating Redis, a replacement restores the watermark from the verified object. If the process died before publication, it reloads the previous snapshot and reconsumes the still-retained suffix. Reprocessing cannot append duplicate text or create duplicate Items.

Missing or corrupt snapshots never authorize skipping to the Redis floor. A missing snapshot can start from the beginning only when the source prefix is still complete. A discontinuity beyond the committed cursor preserves already persisted Items but is explicitly incomplete; the cursor and normal trim watermark do not advance across that missing interval, and only explicit incomplete retirement can release the remaining suffix; merged display content cannot manufacture missing raw events. Final publication checks terminal lifecycle projection settlement as well as the source closure boundary, including abandoned facts. Stream closure and display finalization are independent of Run sealing.

### Read and Size Boundaries

Authorized Item reads use the latest verified display snapshot during execution and after completion. They expose Items, snapshot version, projection cursor, coverage, and finalization, but not internal merge state or storage versions. Collection pagination binds to one snapshot version; if a subsequent page cannot read that version, it returns an explicit snapshot-change conflict and the caller restarts the collection. Pages from different snapshots are never silently combined. [Management API](16-management-api.md#read-models) owns the public read behavior.

Snapshot size is bounded by merged Item count and decoded bytes, not lifetime raw-event count. Canonical JSON is limited to 16 MiB by default, with the existing authorized object-backed large-content rules. An oversized projection preserves the last valid snapshot and reports a bounded limit failure; it never advances the cursor over omitted content. Repeated whole-object replacement is the accepted initial write-cost trade-off; this contract introduces no segmented archive.

The service derives the object key only after an organization-authorized Run lookup. Object identity, encoding, digest, size, schema, cursor order, and Item source positions are verified before use. The retained Run pins its display object and referenced payloads under ordinary collection rules. Display content cannot prove tool execution, side effects, Run completion, or continuation state.

### Display History and Exact Replay

The display snapshot preserves merged semantic Items, not every raw delta, event boundary, or timestamp. It cannot replay Native SSE from an arbitrary original cursor or supply a complete execution-event count. Exact replay uses only a source that actually retains the original ordered events and identities. This contract adds no continuous raw-event archive.

A client recovering display history loads one consistent Item snapshot, replaces its covered local projection, and attaches to live SSE after that snapshot's cursor. If that cursor has since fallen outside the raw horizon, it reloads a newer snapshot rather than reconnecting from the beginning. Bounded unsuccessful recovery reports current display availability and an explicit live gap; it does not loop forever or claim unavailable Items were reconciled. A finalized snapshot requires no live attachment. Pending actions and Run status are read separately from their authoritative resources.

## Failure Semantics

| Failure                                                         | Durable effect                                                                    | Recovery                                                                                                    |
| --------------------------------------------------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Owning state mutation cannot append its required lifecycle fact | Neither change commits                                                            | Retry the complete relational transaction                                                                   |
| Lifecycle live projection fails                                 | Fact remains pending or retryable                                                 | Projector reclaims it without repeating the source mutation                                                 |
| Yielded lifecycle projection is delayed or duplicated           | The Attempt remains durably yielded and the Run remains running                   | Successor scheduling reads relational state; projection retry preserves the same event identity             |
| Redis presentation stream is lost while Run is active           | Live observation and continuity are unavailable                                   | Revalidate publication admission under the continuity contract; execution authority remains relational      |
| Activation acknowledgement is lost                              | Opening events may already exist                                                  | Retry with identical identities and payloads; confirm completeness and current activation before publishing |
| Old append races activation                                     | Accepted before the switch or rejected after it                                   | Close stale local publishing admission; never fail the Run under stale authority                            |
| Redis Thread control Stream is trimmed, expires, or is lost     | Wakeup delivery and its group cursor are unavailable                              | Attempt executor reconciles durable Thread inbox and Run state at mandatory boundaries                      |
| Display snapshot publication fails                              | Last confirmed snapshot and cursor remain authoritative; Run outcome is unchanged | Retry conditionally; retain the unpersisted suffix and apply bounded backlog backpressure                   |
| Consumer stops before or after an uncertain PUT                 | In-memory progress is not authority                                               | Reload the verified snapshot and reconcile the write before replay or trim                                  |
| Raw events already covered by a snapshot are trimmed            | Exact replay may have a gap; display history remains available                    | Read the display snapshot and attach after its cursor                                                       |
| Native notification is dropped or duplicated                    | Wake-up observation is incomplete                                                 | Client reconciles durable Workspace events and current resources                                            |

## Compatibility and Trade-offs

Lifecycle payload versions, Redis presentation-event versions, Thread control signal versions, display snapshot versions, Item projection versions, and compressed storage encoding versions are independent. Unknown required versions fail explicitly in their own reader; they do not change Run or attempt interpretation.

The distinction between the Workspace `seq` cursor and resource-local `resource_seq`, including the latter's per-resource contiguity, is a wire compatibility contract. A deployment cannot renumber retained resource events, reuse a resource sequence, or reinterpret `entity_version` as the recovery cursor.

Using Redis Streams and one replaceable display object keeps high-volume presentation writes out of the relational database. The costs are repeated whole-object writes, bounded consumer backpressure, and an exact replay horizon independent of display-history retention. Callers never mistake either projection for lifecycle or Run-state authority.

Deployment compatibility covers every publication entry point: mixed versions cannot leave an unfenced writer able to bypass activation. Rollout or drain removes that access before relying on the new guarantee. Recovery is an additive presentation event; Native decoding, Hosted visibility, and replay serialization preserve its identity and ordering together.

## Invariants

01. Every required lifecycle fact commits atomically with its owning relational state mutation in the one lifecycle fact table.

02. Lifecycle facts are immutable; projection and Hook dispatch bookkeeping cannot change entity state.

03. One Run uses one stable Redis Stream across all `RunAttempt` values; its entry IDs are transport cursors, not state, lifecycle, Item, or idempotency identities.

04. Service creates no relational Item, stream replay, pending-call, or provider-receipt table.

05. Display content, resumable merge state, coverage, and cursor commit atomically in one conditionally replaced snapshot; partial coverage is explicit.

06. Events, Items, Redis, and replay objects never select Run state or authorize another `RunAttempt`.

07. Workspace lifecycle cursors, Run Stream cursors, Hosted delivery cursors, and best-effort notification identities remain separate domains.

08. Native notifications have no durable replay source and never replace lifecycle or resource reads.

09. `seq` orders the Workspace lifecycle feed; `resource_seq` is contiguous only within one lifecycle resource and is the sole numeric resource-gap signal.

10. Thread control signal Streams, consumer-group cursors, and TTL expiry remain separate from Run presentation replay and every durable domain cursor.

11. Planned handoff appends `run_attempt.yielded` without closing or replacing the Run Stream and without repeating `run.running`.

12. Activation atomically fences publication and appends `leased`, then recovery for a replacement, before admitting that Attempt's observations.

13. Every Attempt-owned stream mutation checks the active generation atomically; trusted historical lifecycle projection never promotes publication authority.

14. Unknown or incomplete activation never admits observations; missing continuity never becomes complete-looking replay.

15. No event beyond confirmed durable display coverage is trimmed or expired solely to satisfy a length or terminal TTL target.

16. A stale consumer cannot overwrite a newer snapshot, regress the cursor, reopen a finalized projection, or certify missing history complete.

17. A crash before publication replays the retained suffix; a crash after publication restores progress from the object without duplicating Items or text.

18. More raw events than the live replay target do not alone make display history unavailable.

19. Display snapshots do not claim exact raw-event replay, and Run sealing does not prove display finalization.
