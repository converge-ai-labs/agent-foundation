# Agent Control: Queued Submissions

## Design Position

A Thread submission carries ordinary Agent input with queue-if-busy semantics. When the Thread cannot accept that input immediately, Foundation creates an editable `QueuedSubmission`; otherwise the same command directly accepts a Turn. A queued submission is not a Turn, TurnAttempt, Thread inbox entry, execution lease, or lifecycle outcome. Enqueue, edit, delete, and reorder operations never create a Turn. Consumption atomically changes one queued submission to `consumed` and accepts exactly one new Turn; only that Turn and its later TurnAttempts own scheduling, execution, recovery, and outcome.

When a running Turn produces a completed outcome while the queue is non-empty, Foundation uses a state-first combined handoff when preparation succeeds. It publishes both the completed source state and the successor's complete initial state before one short relational transaction seals the source Turn, consumes the first queued submission, accepts the successor, and advances the Thread. If preparation or final validation cannot complete promptly, the source Turn seals independently and the durable terminal-Turn-plus-queue condition is handled by the recovery scan.

This contract keeps the queue intentionally small. A queued submission has only `queued` and `consumed` states. It has no lease, preparing, starting, blocked, failed, cancelled, or retry state. Validation failure leaves the submission queued and editable. A caller that wants explicit invocation options or to continue from a historical Turn uses direct Continue or [Continue From](34-agent-control-input-and-continuation.md#continue-from-an-explicit-turn) rather than storing those selectors in the queue.

## Boundaries

| Concern                                                                      | Owner                                                                                                                                | Relationship                                                            |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------- |
| Ordinary input wire                                                          | [Agent Input](33-agent-input.md)                                                                                                     | Supplies the submitted `AgentInput` stored by a queued submission       |
| Queue-if-busy submission, queue resource, order, edit, deletion, and consume | This contract                                                                                                                        | Chooses immediate Turn acceptance or owns the two-state queue lifecycle |
| Thread advancement and queue revisions                                       | [Durable Thread Persistence](24-thread-persistence.md)                                                                               | Supplies `version`, `queue_version`, current Turn, and selected head    |
| Accepted Turn input, state, and lineage                                      | [Agent Control: Input and Continuation](34-agent-control-input-and-continuation.md) and [Durable Turn State](14-turn-persistence.md) | Canonicalizes input and creates a continuation or root-like Turn        |
| Worker claim, lease, and recovery                                            | [Durable Turn Attempt Persistence](15-turn-attempt-persistence.md)                                                                   | Begins only after the consumed Turn is durably accepted                 |
| Active steer and interrupt                                                   | [Agent Control: Active Execution](35-agent-control-active-execution.md)                                                              | Unchanged; Thread inbox entries are not queued submissions              |
| API and mutation evidence                                                    | [Platform API Conventions](../api-conventions.md) and [Durable Operations and Outbox](06-durable-operations-and-outbox.md)           | Own common version, idempotency, retry, and unknown-commit behavior     |

## Queued Submission Model

The public resource has this conceptual shape:

```python
type QueuedSubmissionState = Literal["queued", "consumed"]


class QueuedSubmission:
    queued_submission_id: str
    version: int
    thread_id: str
    position: int | None
    input: AgentInput
    input_digest_sha256: str
    state: QueuedSubmissionState
    consumed_turn_id: str | None
    created_at: datetime
    updated_at: datetime
    consumed_at: datetime | None
```

`input` is the bounded submitted `AgentInput`, not an accepted Turn input. The queue validates its wire schema, static limits, and caller authority but does not resolve `delivery="auto"`, select an AgentPresetVersion or Runtime lock, or initialize Harness state. Those decisions depend on the consumption-time head and current Turn and occur only when Foundation accepts the resulting Turn.

The queue stores no invocation options. Consumption from a completed head inherits that parent's stable Preset. Root-like consumption with a null head inherits the current failed or cancelled Turn's stable Preset. Both resolve the current active Version and apply their owning default managed Skill, Environment, and input-adapter rules. A caller that needs explicit invocation options, an inline HookSubscription, or a historical source submits Continue or Continue From directly.

For a queued submission, `position` is positive and determines its order among currently queued entries. For a consumed submission, `position` is null and `consumed_turn_id` names the exact accepted successor Turn. Public `state` is derived from `consumed_turn_id`; persistence stores no separate status column. Deleting a queued submission removes it and is not a third state. Consumed submissions are immutable and retained under interaction and idempotency policy.

## Public API

The following routes are Foundation-owned `/api/v1` resources and commands:

```http
POST   /api/v1/threads/{thread_id}/submissions
GET    /api/v1/threads/{thread_id}/queued-submissions
GET    /api/v1/queued-submissions/{queued_submission_id}
PATCH  /api/v1/queued-submissions/{queued_submission_id}
DELETE /api/v1/queued-submissions/{queued_submission_id}
POST   /api/v1/threads/{thread_id}/queued-submissions/reorder
POST   /api/v1/threads/{thread_id}/queued-submissions/consume
```

Every mutating route requires an `Idempotency-Key`. Same-key replay of a committed canonical request returns its original response before evaluating entry, queue, or Thread versions.

Their mutation bodies are:

```python
class SubmitThreadInputRequest:
    input: AgentInput


class UpdateQueuedSubmissionRequest:
    expected_version: int
    input: AgentInput


class ReorderQueuedSubmissionsRequest:
    expected_queue_version: int
    queued_submission_ids: tuple[str, ...]


class ConsumeQueuedSubmissionRequest:
    expected_thread_version: int
    expected_queue_version: int


class QueuedSubmissionMutationReceipt:
    queued_submission: QueuedSubmission
    queue_version: int


class QueuedSubmissionConsumptionReceipt:
    queued_submission_id: str
    queue_version: int
    turn: TurnAcceptanceReceipt


class ThreadQueueMutationReceipt:
    thread_id: str
    queue_version: int


class ThreadSubmissionReceipt:
    outcome: Literal["turn_accepted", "queued"]
    turn: TurnAcceptanceReceipt | None
    queued_submission: QueuedSubmission | None
    queue_version: int
```

Submit accepts only one `AgentInput` and no Preset, Version, Skill, Environment, historical-source, or Hook option. It returns `202 ThreadSubmissionReceipt`; exactly one of `turn` and `queued_submission` is present according to `outcome`. Immediate acceptance uses the ordinary Continue defaults or the root-like existing-Thread rule owned by the continuation contract. Queue acceptance appends after the current last queued entry.

The request requires no expected Thread or queue version because it explicitly accepts either result according to authoritative commit-time state. Concurrent submissions serialize into one immediate acceptance followed by queued entries, or into a deterministic queue order. A caller requiring an exact Thread version or invocation selections uses direct Continue instead.

The Thread collection defaults to queued entries in ascending `position, queued_submission_id` order. An explicit `state` filter can read retained consumed entries in ascending `consumed_at, queued_submission_id` order. Exact and collection reads reauthorize the Thread and never expose object-store or execution-private data.

PATCH replaces the complete submitted `input` of a queued entry. It requires `expected_version`, increments both the entry version and the Thread's `queue_version`, and returns `200 QueuedSubmissionMutationReceipt`. DELETE takes `expected_version` as a required query parameter, removes only a queued entry, compacts the remaining positions, increments `queue_version`, and returns `204`. A consumed entry conflicts with PATCH or DELETE. Mutation evidence makes a lost successful response reconcilable without introducing a cancelled tombstone.

Reorder carries `expected_queue_version` and the exact ordered IDs of every currently queued submission. The set must have no omission, duplicate, consumed entry, or entry from another Thread. One short transaction locks the Thread and queued rows, verifies the version and set, rewrites positions, and increments `queue_version`. Success returns `200 ThreadQueueMutationReceipt`. An unchanged order is a successful no-op and does not increment it.

Consume always selects the first queued entry. A caller chooses another entry by reordering first rather than bypassing queue order. Success returns `202 QueuedSubmissionConsumptionReceipt`.

## Admission and Mutation Rules

A retained Thread accepts the submission command in this order:

1. if any queued submission already exists, append the new input so it cannot bypass earlier queue order;
2. otherwise, if the current Turn is `accepted`, `running`, or `waiting`, create a queued submission;
3. otherwise, if `head_turn_id` names a completed Turn, immediately accept an ordinary continuation from that head;
4. otherwise, if `head_turn_id=null` and the current Turn is `failed` or `cancelled`, immediately accept a root-like Turn with no parent in the same Thread;
5. otherwise the selected head is waiting after a failed or cancelled feedback successor, so create a queued submission that remains pending until Retry or another explicit control operation establishes an eligible state.

The direct Continue route never queues. It requires an empty queue and applies only rules 3 and 4; a current or selected waiting state conflicts. Continue From, Feedback, and Retry retain their independent precedence and can run while queued submissions exist because they establish the state from which later queue consumption proceeds.

Every mutation authenticates and authorizes the current principal against the Thread and revalidates the submitted input's bounded schema. Input or resource identifiers grant no authority by possession. Queue admission does not promise that consumption is currently eligible.

The admission branch is decided against locked authoritative Thread and queue state. Preparing an immediate Turn still performs object I/O outside the final transaction: if its detached Thread, head, or queue snapshot changes before commit, Foundation changes nothing and restarts bounded preflight against the new state. It never falls through to a different branch inside a database transaction or holds that transaction across object storage.

Queue-only mutations lock no Turn and change neither `Thread.version` nor its current/head references. They increment `Thread.queue_version`; because this is an authoritative Thread-adjacent mutation, they also update `Thread.updated_at`. A stale entry or queue version conflicts without applying a partial edit or order.

The queue count, individual input size, reorder body, and retained consumed history are bounded by deployment policy. Capacity rejection creates no row. Queued input remains sensitive tenant data and follows the same disclosure and retention protection as accepted Turn input.

## Atomic Consumption

Consumption is Turn acceptance delayed until the queue chooses an input. Foundation first reads detached Thread, head, queue-entry, Agent, and policy state, canonicalizes the selected input, builds the new Turn's complete initial state from the consumption-time state-selection rule, and publishes any required Turn objects outside a database transaction.

For an already terminal Thread, the final short transaction:

1. locks the Thread and selected queued submission;
2. resolves idempotent replay, then verifies `expected_thread_version`, `expected_queue_version`, current authorization, and that the entry remains queued in the same Thread;
3. requires no current `accepted` or `running` Turn and rejects a current or selected waiting state;
4. repeats all ordinary Continue input, Preset, Runtime, Skill, Environment, parent-state, empty-state, and digest preconditions;
5. when `head_turn_id` names a completed Turn, inserts one `accepted` Turn with `lineage_kind="continue"`, `input_kind="agent_input"`, and `parent_turn_id=head_turn_id`;
6. when `head_turn_id=null` and the current Turn is `failed` or `cancelled`, inserts one root-like `accepted` Turn with `lineage_kind="root"`, `input_kind="agent_input"`, and `parent_turn_id=null`; the trusted state adapter initializes empty state under the existing Thread ID;
7. sets `current_turn_id` to the new Turn, preserves the completed head or the null head selected above, and increments `Thread.version`;
8. sets the queue row's `consumed_turn_id`, clears its position, increments its version, increments `Thread.queue_version`, and commits lifecycle, idempotency, and ordinary publication facts with the Turn; it creates no queue-drain-specific outbox intent.

These writes commit or roll back together. No observer can see a consumed queue entry without its Turn or an accepted Turn whose source entry is still queued. The first TurnAttempt is created only by a later Worker claim. Queue workers, control replicas, and clients therefore need no queue lease; concurrent consume, edit, delete, reorder, Continue, Continue From, Feedback, Retry, and outcome sealing operations serialize through the same versions and row locks.

### Completion-Time Combined Handoff

When the current running Turn has prepared a `completed` outcome candidate and the Thread has queued input, the owning fenced Worker can combine source sealing with consumption of the first queued submission. This path never creates an accepted Turn without its complete initial state and never holds a relational transaction across object storage or other external I/O.

```mermaid
sequenceDiagram
    participant Harness
    participant Worker as Current fenced Worker
    participant Objects as Object storage
    participant DB as PostgreSQL
    participant NextWorker as Worker scheduler

    Harness-->>Worker: completed output and complete state
    Worker->>Objects: CAS publish source completed state candidate
    Objects-->>Worker: source object version and digest
    Worker->>DB: detached read Thread, source Turn, first queued row, versions, and policy references
    DB-->>Worker: current/head, Turn fence, queue order, and detached versions
    Worker->>Worker: allocate successor ID and freeze exact accepted selections
    Worker->>Worker: build successor state from source candidate plus queued AgentInput
    Worker->>Objects: create successor state.json and optional input payload
    Objects-->>Worker: successor object metadata and digests

    Worker->>DB: BEGIN short combined transaction
    activate DB
    Worker->>DB: TX1 lock Thread; verify version, current source, and selected head
    Worker->>DB: TX2 lock source Turn and current TurnAttempt; verify running, fence, and lease
    Worker->>DB: TX3 lock target inbox rows and first queued row
    Worker->>DB: TX4 verify no pending steer, queue order/version, object metadata, selections, authority, and policy
    alt every precondition still holds
        Worker->>DB: TX5 seal source Turn completed and select exact sealed state/output
        Worker->>DB: TX6 terminalize source TurnAttempt, disable lease, and charge usage
        Worker->>DB: TX7 insert successor Turn accepted with parent_turn_id=source Turn
        Worker->>DB: TX8 mark queue row consumed with consumed_turn_id=successor Turn
        Worker->>DB: TX9 set Thread head/current; increment version by 2 and queue_version by 1
        Worker->>DB: TX10 append completion, acceptance, idempotency, and ordinary publication facts
        Worker->>DB: COMMIT transaction
        DB-->>Worker: source completed, queue consumed, successor accepted
    else queue, control, fence, policy, or version changed
        Worker->>DB: ROLLBACK all combined writes
        DB-->>Worker: no source seal, queue consumption, or successor acceptance
    end
    deactivate DB

    opt combined transaction rolled back while source completion remains eligible
        Worker->>DB: retry ordinary source completion under current control-race rules
        Note over Worker,Objects: Prepared successor objects remain non-authoritative cleanup candidates
    end
    opt combined transaction committed
        NextWorker->>DB: later scan and claim accepted successor
    end
```

Before the final transaction, Foundation has only detached relational facts and non-authoritative prepared objects. The transaction locks the Thread first, then the source Turn and current TurnAttempt, then target inbox rows and the first queued row. It repeats every condition needed by both source completion and successor acceptance only after those canonical locks are held. Its successful writes are:

1. select the exact completed candidate as the source Turn's `sealed_state`, copy its output, set `status="completed"`, clear its active model snapshot and current-attempt selection, and set completion timestamps;
2. terminalize the exact current TurnAttempt as `succeeded`, disable its lease, and charge known usage;
3. append the source completion lifecycle facts and ordinary publication intents required by their owning contracts; no queue-drain intent is created;
4. insert one `accepted` successor Turn whose complete initial state already exists, whose `lineage_kind="continue"`, `input_kind="agent_input"`, and `parent_turn_id` name the completed source, and whose exact Preset Version, Runtime lock, model snapshot, recovery policy, and other accepted fields are frozen;
5. set the first queued row's `consumed_turn_id` to that successor, clear its position, set `consumed_at`, and increment its version;
6. set `Thread.head_turn_id` to the completed source and `Thread.current_turn_id` to the accepted successor, increment `Thread.version` by two for the logically ordered seal and advancement, and increment `Thread.queue_version` by one; and
7. append the successor acceptance lifecycle and ordinary publication facts.

The old Turn seal, old TurnAttempt terminalization, queue consumption, successor Turn insertion, Thread selection, version changes, and their required relational facts commit or roll back together. The queue row's unique `consumed_turn_id` is the exact source correlation; the successor needs no duplicate queue-source column.

Completion-time preparation is bounded and never makes successful source completion depend on queued input. If the queue is empty, the first entry is ineligible or changes, current authorization or dependency validation fails, successor state publication fails, or the combined transaction loses a race, Foundation follows the ordinary source-completion path and leaves the queue unchanged. A prepared but unselected successor object is not an accepted Turn and is eligible for ownership-proven cleanup.

## Post-Terminal Drain and Recovery

A completed Turn that did not use the combined handoff, and every eligible `failed` or `cancelled` outcome, can coexist with queued submissions. A bounded periodic recovery scan treats the relational combination of Thread selection, current terminal Turn, and first queued row as the complete drain authority. It performs the same detached state-first preparation and atomic consumption boundary described above. No queue-drain outbox, queue lease, additional queue state, or resumable TurnAttempt is required. An implementation can use a best-effort process-local wakeup for latency, but losing it does not affect correctness.

The consumer applies these rules to the first queued entry:

| Current outcome      | Selected head | Drain behavior                                                   |
| -------------------- | ------------- | ---------------------------------------------------------------- |
| `completed`          | completed     | Consume through ordinary Continue from that completed head       |
| `failed`/`cancelled` | completed     | Consume through ordinary Continue from the preserved head        |
| `failed`/`cancelled` | null          | Consume through root-like acceptance in the existing Thread      |
| `failed`/`cancelled` | waiting       | Do not consume; Retry or explicit branch selection must progress |
| `waiting`            | waiting       | Do not consume; authenticated Feedback must progress             |

When combined handoff is unavailable, terminal commit and later consumption are independent transactions, so a terminal Thread can temporarily retain queued entries. A new submission during that window appends behind them rather than accepting a Turn out of order. Validation or dependency failure likewise leaves the entry queued and editable.

Continue From and Feedback do not alter the queue. Once either accepts an active successor, the queue waits for that successor's terminal outcome; the drain then uses the newly selected completed head, a preserved completed head, or the root-like null-head rule above.

## Relational Persistence

`thread_queued_submissions` is the only new queue table. Its conceptual columns are:

| Column group       | Columns                                   | Contract                                                               |
| ------------------ | ----------------------------------------- | ---------------------------------------------------------------------- |
| Identity and scope | `id`, `version`, `tenant_id`, `thread_id` | Same-tenant Thread ownership; positive entry version                   |
| Order              | `position`                                | Positive and unique among queued entries; null after consumption       |
| Submitted input    | `input_json`, `input_digest_sha256`       | Bounded canonical submitted JSON; no resolved delivery or binary bytes |
| Consumption        | `consumed_turn_id`, `consumed_at`         | Both absent while queued and both present after atomic Turn acceptance |
| Time               | `created_at`, `updated_at`                | UTC resource timestamps                                                |

The `threads` table adds non-negative `queue_version`, initialized to zero. No queue state, lease, owner, attempt, retry, scheduling, or failure columns are added. The queue table preserves these constraints and access paths:

1. `(tenant_id, id)` is unique, and every entry references one same-tenant Thread.
2. A queued row has a non-null position and no consumption fields; a consumed row has null position and both consumption fields.
3. A present `consumed_turn_id` is unique and references one Turn in the same tenant and Thread.
4. A partial unique index on `(tenant_id, thread_id, position)` for unconsumed rows enforces queued order.
5. `(tenant_id, thread_id, consumed_at, id)` supports retained consumed reads; `(tenant_id, thread_id, position, id)` supports the live queue.

The submitted input remains relational because `AgentInput` contains no inline binary body and the queue's bounded limit is chosen for safe row mutation. At consumption, the accepted Turn uses its existing inline-or-object-backed input representation. Turn state and payload objects remain owned by the Turn and never by the queue row.

## Failure Semantics

| Condition                                                                | Durable outcome                                                                                                                         |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| Queue admission or edit validation fails                                 | No row or row mutation commits                                                                                                          |
| Queue capacity is exhausted                                              | Admission is rejected without creating a Turn                                                                                           |
| Submission state changes between detached preflight and commit           | No mutation commits on the stale branch; bounded preflight restarts or returns a conflict                                               |
| Entry, queue, or Thread version is stale                                 | The command conflicts and changes neither queue nor Thread                                                                              |
| PATCH or DELETE targets a consumed entry                                 | The command conflicts; the entry and accepted Turn remain immutable                                                                     |
| Idle consumption finds active work or a waiting selected head            | The selected entry remains queued and editable                                                                                          |
| Consumption-time input or dependency validation fails                    | The selected entry remains queued; no Turn is accepted                                                                                  |
| Successor preparation fails during completion-time handoff               | The source follows ordinary completion; the entry remains queued and editable                                                           |
| Prepared successor objects exist but the combined transaction rolls back | The source is not sealed by that transaction, the entry remains queued, no successor is accepted, and unowned objects can be cleaned up |
| Worker disappears before the combined transaction commits                | Existing TurnAttempt recovery owns the still-active source Turn; the queue remains queued                                               |
| Worker disappears after the combined transaction commits                 | The source is completed, the entry is consumed, and ordinary Worker scanning claims the accepted successor                              |
| Commit acknowledgement is lost after consumption                         | Reconcile the queue's `consumed_turn_id` and Thread current/head selection before retrying                                              |
| The consumed Turn later fails or is cancelled                            | Entry remains consumed; Turn retry or another queue entry expresses later work                                                          |
| A process-local drain wakeup is lost                                     | Terminal Turn plus queued row remains authoritative; periodic recovery scanning retries the same state-first boundary                   |

## Compatibility and Trade-offs

Queued-submission identity, the two-state lifecycle, submitted-input meaning, ordering, editability, consumption correlation, and the atomic Turn-creation boundary are compatibility facts. Adding a durable intermediate state or moving Turn creation before consumption would change lifecycle meaning and requires an incompatible contract.

Keeping submitted input separate from a Turn makes queue edits and deletion honest and keeps the Turn DAG free of work that has not been selected. The state-first combined path removes the ordinary completed-to-next-Turn gap when all preparation succeeds, while its fallback allows terminal state and a non-empty queue to coexist until recovery drain succeeds. Those conditions remain ordinary queue facts and diagnostics rather than expanding the queue state machine.

## Invariants

01. A queued submission is editable input, not accepted Agent work; enqueue, edit, delete, and reorder create no Turn or TurnAttempt.
02. Public queue state is exactly `queued` or `consumed` and is derived from `consumed_turn_id`; persistence has no status or lease column.
03. Consumption and Turn acceptance are one atomic relational commit, and the consumed queue row records the exact resulting Turn through its unique correlation; a completion-time combined handoff can include source sealing in that same commit.
04. Queue consumption creates an ordinary continuation from the consumption-time completed head or, when the head is null after a failed or cancelled current Turn, a root-like Turn in the same Thread; a historical source requires Continue From.
05. Execution ownership, scheduling, leases, recovery, and outcome begin with the accepted Turn and its later TurnAttempts, never with the queue row.
06. Queue-only mutation increments `Thread.queue_version` without changing Thread advancement version or Turn references; ordinary consumption increments both versions once, while completion-time combined consumption applies the additional source-seal advancement defined below.
07. A validation or dependency failure leaves the entry queued and editable rather than creating a blocked or failed queue state.
08. Continue From and active Agent control do not mutate queued submissions; later consumption uses the then-selected completed head or null-head rule.
09. A Thread submission never bypasses an existing queued submission. Successful state-first handoff can combine completed sealing with first-entry consumption; otherwise terminal state with queued entries is a valid transient or validation-blocked condition recovered by relational scanning.
10. Waiting state blocks queue drain. A failed or cancelled feedback successor whose selected head remains waiting must be retried or explicitly redirected before queued input can run.
11. No accepted successor exists before its complete initial state and any object-backed input are durable; missing state is never an implicit queue-preparation status.
12. A completion-time combined handoff advances `Thread.version` once for source sealing and once for successor acceptance, and advances `Thread.queue_version` once for consumption.
