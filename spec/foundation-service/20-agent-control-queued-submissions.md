# Agent Control: Queued Submissions

## Design Position

`POST /api/v1/threads/{thread_id}/runs` carries one complete existing-Thread Run submission with queue-if-busy semantics. When the Thread cannot accept that intent immediately, Foundation creates an editable `QueuedSubmission`; otherwise the same command directly accepts a Run. The explicit `waiting_resolution.mode="defaults"` branch is different: it resolves the selected waiting head and accepts one successor without consuming or reordering existing queued submissions. A queued submission is not a Run, RunAttempt, Thread inbox entry, execution lease, or lifecycle outcome. Enqueue, edit, delete, and reorder operations never create a Run. Consumption atomically changes one queued submission to `consumed` and accepts exactly one new Run; only that Run and its later RunAttempts own scheduling, execution, recovery, and outcome.

When a running Run produces a completed outcome while the queue is non-empty, Foundation uses a state-first combined handoff when preparation succeeds. It publishes both the completed source state and the successor's complete initial state before one short relational transaction seals the source Run, consumes the first queued submission, accepts the successor, and advances the Thread. If preparation or final validation cannot complete promptly, the source Run seals independently and the durable terminal-Run-plus-queue condition is handled by the recovery scan.

This contract keeps the queue intentionally small. A queued submission has only `queued` and `consumed` states. It has no lease, preparing, starting, blocked, failed, cancelled, or retry state. Validation failure leaves the submission queued and editable. A historical source is never stored in the queue; a caller that needs one uses [Continue From](18-agent-control-input-and-continuation.md#continue-from-an-explicit-run). Pending ordinary steer and asynchronous-subagent results are Thread-inbox delivery rather than queued intent. They drain before the current Run can complete and therefore before queue consumption; an eligible async result accepted after an inactive terminal outcome still cannot bypass an earlier queued submission, while a result from a failed or cancelled origin is suppressed instead.

## Boundaries

| Concern                                                                      | Owner                                                                                                                              | Relationship                                                                             |
| ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Ordinary input wire                                                          | [Agent Input](17-agent-input.md)                                                                                                   | Supplies the `AgentInput` inside the complete queued Run intent                          |
| Queue-if-busy submission, queue resource, order, edit, deletion, and consume | This contract                                                                                                                      | Chooses immediate Run acceptance or owns the two-state queue lifecycle                   |
| Thread advancement and queue revisions                                       | [Durable Thread Persistence](11-thread-persistence.md)                                                                             | Supplies `version`, `queue_version`, current Run, and selected head                      |
| Accepted Run input, state, and lineage                                       | [Agent Control: Input and Continuation](18-agent-control-input-and-continuation.md) and [Durable Run State](12-run-persistence.md) | Canonicalizes input and creates a continuation or root-like Run                          |
| Worker claim, lease, and recovery                                            | [Run Attempts, Scheduling, and Recovery](13-run-attempt-scheduling-and-recovery.md)                                                | Begins only after the consumed Run is durably accepted                                   |
| Steer, async-result delivery, and interrupt                                  | [Agent Control: Active Execution](19-agent-control-active-execution.md) and [Async Subagents](33-async-subagents.md)               | Thread inbox entries are not queued submissions; existing queue order retains precedence |
| API and mutation evidence                                                    | [Platform API Conventions](../api-conventions.md) and [Durable Operations and Outbox](06-durable-operations-and-outbox.md)         | Own common version, idempotency, retry, and unknown-commit behavior                      |

## Queued Submission Model

The public resource has this conceptual shape:

```python
type QueuedSubmissionState = Literal["queued", "consumed"]


class QueuedSubmission:
    queued_submission_id: str
    version: int
    thread_id: str
    authority_principal: PrincipalRef
    position: int | None
    submission: ThreadRunSubmissionIntent
    submission_digest_sha256: str
    state: QueuedSubmissionState
    consumed_run_id: str | None
    created_at: datetime
    updated_at: datetime
    consumed_at: datetime | None
```

`submission` is the bounded [`ThreadRunSubmissionIntent`](18-agent-control-input-and-continuation.md#input-bearing-operations) submitted to the existing-Thread Run route, not an accepted Run input. It contains the `AgentInput` plus every policy-permitted Preset selector, exact Revision selector, default-Revision precondition, typed config override, and inline Hook option that must survive delayed acceptance. `authority_principal` is the authenticated User or Service Account that submitted the intent and is immutable while the entry exists. The queue validates wire schemas, static limits, that Principal's current authority, and references at admission but does not resolve `delivery="auto"`, select an AgentPresetRevision or Runtime lock, merge `EffectiveAgentConfig`, create a HookSubscription, or initialize Harness state. Those decisions depend on the consumption-time head, current Run, and the stored Principal's current authority and occur only when Foundation accepts the resulting Run.

When the intent omits a stable Preset, consumption from a completed head inherits that parent's stable Preset and root-like consumption with a null head inherits the current failed or cancelled Run's stable Preset. An explicit stable Preset follows the ordinary continuation compatibility policy. An exact Revision selector remains exact while queued; `expected_default_revision_id` remains a separate consumption-time precondition. Consumption reauthorizes and resolves the complete typed override against the then-current eligible state. An inline HookSubscription is created in the same transaction as the accepted Run and receives that Run's exact scope. A stale or invalid option leaves the submission queued and editable rather than silently falling back to defaults.

For a queued submission, `position` is positive and determines its order among currently queued entries. For a consumed submission, `position` is null and `consumed_run_id` names the exact accepted successor Run. Public `state` is derived from `consumed_run_id`; persistence stores no separate status column. Deleting a queued submission removes it and is not a third state. Consumed submissions are immutable and retained under interaction and idempotency policy.

## Public API

The following routes are Foundation-owned `/api/v1` resources and commands:

```http
POST   /api/v1/threads/{thread_id}/runs
GET    /api/v1/threads/{thread_id}/queued-submissions
GET    /api/v1/queued-submissions/{queued_submission_id}
PATCH  /api/v1/queued-submissions/{queued_submission_id}
DELETE /api/v1/queued-submissions/{queued_submission_id}
POST   /api/v1/threads/{thread_id}/queued-submissions/reorder
POST   /api/v1/threads/{thread_id}/queued-submissions/consume
```

List and Get authorize `queued_submission.read`; queue admission authorizes
`queued_submission.create` plus the selected Preset invocation; PATCH authorizes
`queued_submission.update` and exact equality with the stored authority
Principal; DELETE authorizes `queued_submission.delete`; Reorder authorizes
`queued_submission.reorder`; and explicit Consume authorizes
`queued_submission.consume`. Consumption also reauthorizes the stored authority
Principal for the accepted Run. The IAM
[stable action registry](32-identity-and-access-management.md#stable-action-registry)
owns built-in grants; this contract owns queue state, ordering, identity, and
the additional stored-Principal checks.

Every mutating route requires an `Idempotency-Key`. Same-key replay of a committed canonical request returns its original response before evaluating entry, queue, or Thread versions.

Their mutation bodies are:

```python
class UpdateQueuedSubmissionRequest:
    expected_version: int
    submission: ThreadRunSubmissionIntent


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
    run: RunAcceptanceReceipt


class ThreadQueueMutationReceipt:
    thread_id: str
    queue_version: int


class ThreadRunSubmissionReceipt:
    outcome: Literal["run_accepted", "queued"]
    run: RunAcceptanceReceipt | None
    queued_submission: QueuedSubmission | None
    queue_version: int
```

The route accepts `expected_thread_version` plus the complete [`ThreadRunSubmissionIntent`](18-agent-control-input-and-continuation.md#input-bearing-operations) and optional top-level `waiting_resolution`. It returns `202 ThreadRunSubmissionReceipt`; exactly one of `run` and `queued_submission` is present according to `outcome`. Immediate acceptance uses the ordinary Continue, explicit waiting-Continue, or root-like existing-Thread rule. Queue admission appends after the current last queued entry and stores neither `expected_thread_version` nor `waiting_resolution`, because both are command-time admission preconditions rather than delayed Run intent.

The request requires `expected_thread_version` but no expected queue version. Foundation verifies the Thread version before selecting either result against authoritative commit-time state. Concurrent submissions serialize under the Thread and queue locks. Immediate Run acceptance advances `Thread.version`, so another request carrying the old version conflicts; queue-only admission leaves that version unchanged, so other same-version requests can append in the resulting deterministic queue order. Idempotent replay preserves the originally selected immediate or queued outcome.

The Thread collection defaults to queued entries in ascending `position, queued_submission_id` order. An explicit `state` filter can read retained consumed entries in ascending `consumed_at, queued_submission_id` order. Exact and collection reads reauthorize the Thread and never expose object-store or execution-private data.

PATCH replaces the complete submitted intent of a queued entry. It requires `expected_version`, revalidates the input and every command option, increments both the entry version and the Thread's `queue_version`, and returns `200 QueuedSubmissionMutationReceipt`. DELETE takes `expected_version` as a required query parameter, removes only a queued entry, compacts the remaining positions, increments `queue_version`, and returns `204`. A consumed entry conflicts with PATCH or DELETE. Mutation evidence makes a lost successful response reconcilable without introducing a cancelled tombstone.

Reorder carries `expected_queue_version` and the exact ordered IDs of every currently queued submission. The set must have no omission, duplicate, consumed entry, or entry from another Thread. One short transaction locks the Thread and queued rows, verifies the version and set, rewrites positions, and increments `queue_version`. Success returns `200 ThreadQueueMutationReceipt`. An unchanged order is a successful no-op and does not increment it.

Consume always selects the first queued entry. A caller chooses another entry by reordering first rather than bypassing queue order. Success returns `202 QueuedSubmissionConsumptionReceipt`.

## Admission and Mutation Rules

A retained Thread accepts `POST /api/v1/threads/{thread_id}/runs` in this order after verifying `expected_thread_version`:

1. if `waiting_resolution.mode="defaults"` is present, require current/head to name the same waiting Run and immediately accept the one composite waiting-Continue successor under its digest, authorization, no-override, input, and inbox-binding rules; existing queued submissions remain untouched;
2. otherwise, if any queued submission already exists, append the new intent so it cannot bypass earlier queue order;
3. otherwise, if the current Run is `accepted`, `running`, or `waiting`, create a queued submission;
4. otherwise, if `head_run_id` names a completed Run, immediately accept an ordinary continuation from that head;
5. otherwise, if `head_run_id=null` and the current Run is `failed` or `cancelled`, immediately accept a root-like Run with no parent in the same Thread;
6. otherwise the selected head is waiting after a failed or cancelled successor, so create a queued submission that remains pending until Retry or another explicit control operation establishes an eligible state.

Continue From, Feedback, waiting Continue, and Retry retain their independent precedence and can run while queued submissions exist because they establish the state from which later queue consumption proceeds. They do not append, reorder, or consume the queue.

Every mutation authenticates and authorizes the current principal against the Thread and revalidates the complete submitted intent's bounded schemas and references. Queue admission stores that exact Principal as `authority_principal`. PATCH additionally requires exact equality with the stored authority Principal, so editing cannot execute modified intent as another identity. Delete, reorder, and explicit consume authorize their command actor independently and do not replace the stored Principal. Input or resource identifiers grant no authority by possession. Queue admission does not promise that consumption is currently eligible.

The admission branch is decided against locked authoritative Thread and queue state. Preparing an immediate Run still performs object I/O outside the final transaction: if its detached Thread, head, or queue snapshot changes before commit, Foundation changes nothing and restarts bounded preflight against the new state. It never falls through to a different branch inside a database transaction or holds that transaction across object storage.

Queue-only mutations lock no Run and change neither `Thread.version` nor its current/head references. They increment `Thread.queue_version`; because this is an authoritative Thread-adjacent mutation, they also update `Thread.updated_at`. A stale entry or queue version conflicts without applying a partial edit or order.

The queue count, individual submission size, reorder body, and retained consumed history are bounded by deployment policy. Capacity rejection creates no row. Queued intent remains sensitive tenant data and follows the same disclosure and retention protection as accepted Run input and invocation configuration.

## Atomic Consumption

Consumption is Run acceptance delayed until the queue chooses an intent. Foundation first reads detached Thread, head, queue-entry, Agent, and policy state, canonicalizes the selected intent and accepted input, builds the new Run's complete initial state from the consumption-time state-selection rule, and publishes any required Run objects outside a database transaction. Inline HookSubscription creation is prepared under its owning contract but becomes authoritative only in the final Run-acceptance transaction.

For an already terminal Thread, the final short transaction:

1. locks the Thread, current or selected head Run and every referenced async-result spawning Run in stable ID order, inbox counter and pending entries in `delivery_sequence`, and selected queued submission;
2. resolves idempotent replay, then verifies `expected_thread_version`, `expected_queue_version`, command-actor authorization, the stored authority Principal's current status and complete Run authority, and that the entry remains queued in the same Thread;
3. requires no current `accepted` or `running` Run and rejects a current or selected waiting state;
4. repeats all ordinary Continue input, Preset, Revision, default-Revision, effective-config, Runtime, inline-Hook, parent-state, empty-state, and digest preconditions;
5. when `head_run_id` names a completed Run, inserts one `accepted` Run with `authority_principal` copied from the queue entry, `lineage_kind="continue"`, `input_kind="agent_input"`, and `parent_run_id=head_run_id`;
6. when `head_run_id=null` and the current Run is `failed` or `cancelled`, inserts one root-like `accepted` Run with `authority_principal` copied from the queue entry, `lineage_kind="root"`, `input_kind="agent_input"`, and `parent_run_id=null`; the trusted state adapter initializes empty state under the existing Thread ID;
7. marks every unbound async result whose spawning Run is failed or cancelled `suppressed`, then binds every remaining eligible result to the new Run in `delivery_sequence`; no pending entry is injected into initial Run input or consumed by this binding;
8. sets `current_run_id` to the new Run, preserves the completed head or the null head selected above, and increments `Thread.version`;
9. sets the queue row's `consumed_run_id`, clears its position, increments its version, increments `Thread.queue_version`, and commits lifecycle, idempotency, and ordinary publication facts with the Run; it creates no queue-drain-specific outbox intent.

These writes commit or roll back together. No observer can see a consumed queue entry without its Run or an accepted Run whose source entry is still queued. The first RunAttempt is created only by a later Worker claim. Queue workers, control replicas, and clients therefore need no queue lease; concurrent consume, edit, delete, reorder, Continue, Continue From, Feedback, Retry, and outcome sealing operations serialize through the same versions and row locks.

### Completion-Time Combined Handoff

When the current running Run has prepared a `completed` outcome candidate and the Thread has queued intent, the owning fenced Worker can combine source sealing with consumption of the first queued submission. This path never creates an accepted Run without its complete initial state and never holds a relational transaction across object storage or other external I/O.

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
    Worker->>DB: detached read Thread, source Run, first queued row, versions, and policy references
    DB-->>Worker: current/head, Run fence, queue order, and detached versions
    Worker->>Worker: allocate successor ID and freeze exact accepted selections
    Worker->>Worker: resolve queued intent and build successor state
    Worker->>Objects: create successor state.json and optional input payload
    Objects-->>Worker: successor object metadata and digests

    Worker->>DB: BEGIN short combined transaction
    activate DB
    Worker->>DB: TX1 lock Thread; verify version, current source, and selected head
    Worker->>DB: TX2 lock source Run and current RunAttempt; verify running, fence, and lease
    Worker->>DB: TX3 lock inbox counter, target inbox rows, and first queued row
    Worker->>DB: TX4 verify no pending delivery, queue order/version, stored Principal, selections, objects, and policy
    alt every precondition still holds
        Worker->>DB: TX5 seal source Run completed and select exact sealed state/output
        Worker->>DB: TX6 terminalize source RunAttempt, disable lease, and charge usage
        Worker->>DB: TX7 insert successor Run with queue authority Principal and parent_run_id=source Run
        Worker->>DB: TX8 mark queue row consumed with consumed_run_id=successor Run
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

Before the final transaction, Foundation has only detached relational facts and non-authoritative prepared objects. The transaction locks the Thread first, then the source Run and current RunAttempt, then the inbox counter and target entries in `delivery_sequence`, then the first queued row. It repeats every condition needed by both source completion and successor acceptance only after those canonical locks are held, including proof that no eligible pending ordinary steer or async result remains bound to the source. Its successful writes are:

1. select the exact completed candidate as the source Run's `sealed_state`, copy its output, set `status="completed"`, clear its current-attempt selection, and set completion timestamps;
2. terminalize the exact current RunAttempt as `succeeded`, disable its lease, and charge known usage;
3. append the source completion lifecycle facts and ordinary publication intents required by their owning contracts; no queue-drain intent is created;
4. insert one `accepted` successor Run whose complete initial state already exists, whose `authority_principal` is copied from the queued submission, whose `lineage_kind="continue"`, `input_kind="agent_input"`, and `parent_run_id` name the completed source, and whose exact Preset Revision, `EffectiveAgentConfig`, Runtime lock, recovery policy, and other accepted fields are frozen;
5. set the first queued row's `consumed_run_id` to that successor, clear its position, set `consumed_at`, and increment its version;
6. set `Thread.head_run_id` to the completed source and `Thread.current_run_id` to the accepted successor, increment `Thread.version` by two for the logically ordered seal and advancement, and increment `Thread.queue_version` by one; and
7. append the successor acceptance lifecycle and ordinary publication facts.

The old Run seal, old RunAttempt terminalization, queue consumption, successor Run insertion, Thread selection, version changes, and their required relational facts commit or roll back together. The queue row's unique `consumed_run_id` is the exact source correlation; the successor needs no duplicate queue-source column.

Completion-time preparation is bounded and never makes successful source completion depend on queued intent. If the queue is empty, the first entry is ineligible or changes, its stored authority Principal is no longer eligible, current dependency validation fails, successor state publication fails, or the combined transaction loses a race, Foundation follows the ordinary source-completion path and leaves the queue unchanged. A prepared but unselected successor object is not an accepted Run and is eligible for ownership-proven cleanup.

## Post-Terminal Drain and Recovery

A completed Run that did not use the combined handoff, and every eligible `failed` or `cancelled` outcome, can coexist with queued submissions. A bounded periodic recovery scan treats the relational combination of Thread selection, current terminal Run, and first queued row as the complete drain authority. It performs the same detached state-first preparation and atomic consumption boundary described above. No queue-drain outbox, queue lease, additional queue state, or resumable RunAttempt is required. An implementation can use a best-effort process-local wakeup for latency, but losing it does not affect correctness.

The consumer applies these rules to the first queued entry:

| Current outcome      | Selected head | Drain behavior                                                      |
| -------------------- | ------------- | ------------------------------------------------------------------- |
| `completed`          | completed     | Consume through ordinary Continue from that completed head          |
| `failed`/`cancelled` | completed     | Consume through ordinary Continue from the preserved head           |
| `failed`/`cancelled` | null          | Consume through root-like acceptance in the existing Thread         |
| `failed`/`cancelled` | waiting       | Do not consume; Retry or explicit branch selection must progress    |
| `waiting`            | waiting       | Do not consume; Feedback or explicit waiting Continue must progress |

When combined handoff is unavailable, terminal commit and later consumption are independent transactions, so a terminal Thread can temporarily retain queued entries. A new submission during that window appends behind them rather than accepting a Run out of order. Validation or dependency failure likewise leaves the entry queued and editable.

Continue From, Feedback, and waiting Continue do not alter the queue. Once any accepts an active successor, the queue waits for that successor's terminal outcome; the drain then uses the newly selected completed head, a preserved completed head, or the root-like null-head rule above.

Pending delivery and queued submissions use separate orders. Delivery already bound to the current Run must drain before that Run can complete and therefore before combined queue handoff. Once a Run has completed with an empty bound inbox, any eligible async result accepted for the inactive Thread does not bypass a queued submission: ordinary queue consumption proceeds first, then the result binds to the accepted successor and enters through the unified FIFO. Queue consumption first suppresses any result whose own spawning Run failed or was cancelled. A race between queue consumption and async-result reconciliation serializes on the Thread, origin-Run, inbox, and queue locks, and the async-result path rechecks that no queued row remains before accepting its own successor Run.

## Relational Persistence

`thread_queued_submissions` is the only new queue table. Its conceptual columns are:

| Column group       | Columns                                              | Contract                                                                                                                |
| ------------------ | ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Identity and scope | `id`, `version`, `tenant_id`, `thread_id`            | Same-tenant Thread ownership; positive entry version                                                                    |
| Run authority      | `authority_principal_type`, `authority_principal_id` | Immutable submitting User or Service Account copied to the accepted Run; never the consumer or worker                   |
| Order              | `position`                                           | Positive and unique among queued entries; null after consumption                                                        |
| Submitted intent   | `submission_json`, `submission_digest_sha256`        | Bounded canonical submission; no resolved delivery, selected Revision, effective config, Hook resource, or binary bytes |
| Consumption        | `consumed_run_id`, `consumed_at`                     | Both absent while queued and both present after atomic Run acceptance                                                   |
| Time               | `created_at`, `updated_at`                           | UTC resource timestamps                                                                                                 |

The `threads` table adds non-negative `queue_version`, initialized to zero. No queue state, lease, owner, attempt, retry, scheduling, or failure columns are added. The queue table preserves these constraints and access paths:

1. `(tenant_id, id)` is unique, and every entry references one same-tenant Thread.
2. `authority_principal_type` is `user` or `service_account`; admission validates the polymorphic Principal in the Thread tenant, and consumption repeats current domain referential and authorization validation.
3. A queued row has a non-null position and no consumption fields; a consumed row has null position and both consumption fields.
4. A present `consumed_run_id` is unique and references one Run in the same tenant and Thread whose authority Principal equals the queue row's Principal.
5. A partial unique index on `(tenant_id, thread_id, position)` for unconsumed rows enforces queued order.
6. `(tenant_id, thread_id, consumed_at, id)` supports retained consumed reads; `(tenant_id, thread_id, position, id)` supports the live queue.

The submitted intent remains relational because `AgentInput` contains no inline binary body and every invocation option is bounded configuration or a resource reference. The queue's limit is chosen for safe row mutation. At consumption, the accepted Run uses its existing inline-or-object-backed input representation and an inline HookSubscription becomes its own resource. Run state, payload objects, and Hook resources remain owned by their respective domains and never by the queue row.

## Failure Semantics

| Condition                                                                             | Durable outcome                                                                                                                         |
| ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Queue admission or edit validation fails                                              | No row or row mutation commits                                                                                                          |
| Queue capacity is exhausted                                                           | Admission is rejected without creating a Run                                                                                            |
| Submission state changes between detached preflight and commit                        | No mutation commits on the stale branch; bounded preflight restarts or returns a conflict                                               |
| Entry, queue, or Thread version is stale                                              | The command conflicts and changes neither queue nor Thread                                                                              |
| PATCH or DELETE targets a consumed entry                                              | The command conflicts; the entry and accepted Run remain immutable                                                                      |
| Idle consumption finds active work or a waiting selected head                         | The selected entry remains queued and editable                                                                                          |
| Consumption-time input, option, Hook, or dependency validation fails                  | The selected entry remains queued; no Run or HookSubscription is accepted                                                               |
| Stored authority Principal is absent, disabled, cross-tenant, or no longer authorized | The selected entry remains queued; no consumer or administrator is substituted                                                          |
| Successor preparation fails during completion-time handoff                            | The source follows ordinary completion; the entry remains queued and editable                                                           |
| Prepared successor objects exist but the combined transaction rolls back              | The source is not sealed by that transaction, the entry remains queued, no successor is accepted, and unowned objects can be cleaned up |
| Worker disappears before the combined transaction commits                             | Existing RunAttempt recovery owns the still-active source Run; the queue remains queued                                                 |
| Worker disappears after the combined transaction commits                              | The source is completed, the entry is consumed, and ordinary Worker scanning claims the accepted successor                              |
| Commit acknowledgement is lost after consumption                                      | Reconcile the queue's `consumed_run_id` and Thread current/head selection before retrying                                               |
| The consumed Run later fails or is cancelled                                          | Entry remains consumed; Run retry or another queue entry expresses later work                                                           |
| A process-local drain wakeup is lost                                                  | Terminal Run plus queued row remains authoritative; periodic recovery scanning retries the same state-first boundary                    |

## Compatibility and Trade-offs

Queued-submission identity, the two-state lifecycle, submitted-intent meaning, ordering, editability, consumption correlation, and the atomic Run-creation boundary are compatibility facts. Adding a durable intermediate state or moving Run creation before consumption would change lifecycle meaning and requires an incompatible contract.

Keeping submitted intent separate from a Run makes queue edits and deletion honest and keeps the Run DAG free of work that has not been selected. The state-first combined path removes the ordinary completed-to-next-Run gap when all preparation succeeds, while its fallback allows terminal state and a non-empty queue to coexist until recovery drain succeeds. Those conditions remain ordinary queue facts and diagnostics rather than expanding the queue state machine.

## Invariants

01. A queued submission is editable Run intent, not accepted Agent work; enqueue, edit, delete, and reorder create no Run or RunAttempt.
02. Public queue state is exactly `queued` or `consumed` and is derived from `consumed_run_id`; persistence has no status or lease column.
03. Consumption and Run acceptance are one atomic relational commit, and the consumed queue row records the exact resulting Run through its unique correlation; a completion-time combined handoff can include source sealing in that same commit.
04. Queue consumption creates an ordinary continuation from the consumption-time completed head or, when the head is null after a failed or cancelled current Run, a root-like Run in the same Thread; a historical source requires Continue From.
05. Execution ownership, scheduling, leases, recovery, and outcome begin with the accepted Run and its later RunAttempts, never with the queue row.
06. Queue-only mutation increments `Thread.queue_version` without changing Thread advancement version or Run references; ordinary consumption increments both versions once, while completion-time combined consumption applies the additional source-seal advancement defined below.
07. An input, invocation-option, Hook, or dependency validation failure leaves the entry queued and editable rather than creating a blocked or failed queue state.
08. Continue From, waiting Feedback or Continue, and active Agent control do not mutate queued submissions; later consumption uses the then-selected completed head or null-head rule.
09. An existing-Thread Run submission never bypasses an existing queued submission. Successful state-first handoff can combine completed sealing with first-entry consumption; otherwise terminal state with queued entries is a valid transient or validation-blocked condition recovered by relational scanning.
10. Waiting state blocks queue drain. A failed or cancelled feedback successor whose selected head remains waiting must be retried or explicitly redirected before queued intent can run.
11. No accepted successor exists before its complete initial state and any object-backed input are durable; missing state is never an implicit queue-preparation status.
12. A completion-time combined handoff advances `Thread.version` once for source sealing and once for successor acceptance, and advances `Thread.queue_version` once for consumption.
13. Eligible inbox delivery bound to the current Run drains before that Run can complete and before queue consumption. An unbound asynchronous result for an already inactive Thread never bypasses a queued submission; it remains pending until queue consumption creates an active Run or the queue becomes empty, unless its spawning Run fails or is cancelled and terminally suppresses it first.
14. Supplying `waiting_resolution.mode="defaults"` is an explicit waiting-head advancement, not queue consumption: it accepts one composite successor while preserving every queued row and its order.
15. Every queued submission persists one immutable authority Principal. Consumption reauthorizes and copies that Principal to the accepted Run; the command actor, automatic drain process, and Worker never replace it.
