# Durable Thread Persistence

## Design Position

Foundation persists every hosted `Thread` as an independent versioned relational resource. The Thread row is the authority for Session membership, Thread origin, the most recently accepted Run, the selected continuation head, compare-and-swap serialization of accepted advancement, and an independent queue revision. A Thread is not a grouping inferred from Run timestamps or a `thread_id` copied into otherwise unrelated rows.

The shared [Platform Interaction Model](../interaction-model.md) owns the cross-platform meaning of Thread. The Harness owns creation and preservation of the matching `HarnessState.thread_id`. Foundation stores that exact ID rather than generating a parallel Host Thread identity. [Durable Run State](12-run-persistence.md) owns Run fields, state objects, parent edges, and outcomes; this contract owns which Run is current for a Foundation Thread and which sealed Run is selected as its continuation head. Whether the current Run is active derives from its own status rather than another stored Thread pointer.

Foundation never creates an empty Thread. Root, fork, and child acceptance each create one Thread together with its first Run, initial complete Run state, inbox counter authority, lifecycle facts, and outbox intents. Root and fork acceptance also retain their owning idempotency evidence; child acceptance instead retains its asynchronous-child relationship and assigns no spawn idempotency identity. Later immediate submission, queued-submission consumption, authenticated feedback, explicit waiting Continue, eligible asynchronous-result acceptance, and retry acceptance create another Run and advance the existing Thread under its current state version; queue-only admission, Thread-inbox acceptance, suppression, or active delivery do not.

## Boundaries

| Concern                                                                            | Owner                                                                               | Contract                                                                                                                                    |
| ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Session, Thread, Run, and Item meaning                                             | [Platform Interaction Model](../interaction-model.md)                               | Defines identity and cross-platform relationships                                                                                           |
| `thread_id` creation, preservation, and fork transformation                        | [Harness State](../agent-harness/10-snapshot-and-resume.md)                         | Supplies one stable ID inside complete continuation state                                                                                   |
| Durable Thread resource, versions, origin, current Run, and selected head          | This contract                                                                       | Serializes Foundation Thread advancement and queued-submission mutation and supplies read authority                                         |
| Persisted Session membership and root selection                                    | This contract                                                                       | Requires one existing Session container and exactly one retained root Thread; other Session product metadata remains outside the Thread row |
| Run row, state object, parent edge, scheduling, and outcome                        | [Durable Run State](12-run-persistence.md)                                          | Owns one accepted advancement and its resumable state                                                                                       |
| RunAttempt lease, generation, and stale-writer fence                               | [Run Attempts, Scheduling, and Recovery](13-run-attempt-scheduling-and-recovery.md) | Authorizes worker mutation of the current Run while it is active                                                                            |
| Agent invocation and advancement commands                                          | [Agent Control: Input and Continuation](18-agent-control-input-and-continuation.md) | Accepts start, the existing-Thread Run request and immediate branches, waiting Feedback or Continue, fork, and retry                        |
| Asynchronous-result advancement                                                    | [Async Subagents](34-async-subagents.md)                                            | Delivers to the current active Run or accepts an eligible successor without resolving a waiting state                                       |
| Queue-if-busy Run intent                                                           | [Agent Control: Queued Submissions](20-agent-control-queued-submissions.md)         | Owns the existing-Thread route's queued branch, queue rows, ordering, editing, and atomic consumption into a Run                            |
| Thread inbox entries, FIFO counter, binding, steer, interrupt, and control wakeups | [Agent Control: Active Execution](19-agent-control-active-execution.md)             | Persists ordered inbound work and its independent counter separately from the Thread row and keeps Redis cursors outside relational state   |
| Public resource catalog and wire read models                                       | [Management API](16-management-api.md)                                              | Exposes authorized Thread reads and common API behavior                                                                                     |
| Agent-facing history retrieval                                                     | [Agent Interaction Retrieval](35-agent-interaction-retrieval.md)                    | Projects authorized Thread and Run data without becoming authority                                                                          |

A Thread row contains no message history, Thread inbox payload or sequence counter, Harness state, Item payload, provider state, credential, worker lease, queue entry, Redis consumer-group cursor, replay cursor, or live process object. Those values retain their owning stores and lifecycles.

## Durable Thread Model

The following schema is conceptual. It defines durable field meaning rather than a public wire representation or concrete ORM class.

```python
type ThreadRole = Literal["root", "child"]
type ThreadOriginKind = Literal["new", "fork", "child"]


class Thread:
    id: str
    version: int
    queue_version: int
    tenant_id: str
    session_id: str

    role: ThreadRole
    origin_kind: ThreadOriginKind
    origin_thread_id: str | None
    origin_run_id: str | None

    head_run_id: str | None
    current_run_id: str

    created_at: datetime
    updated_at: datetime
```

`id` equals the `thread_id` in the Thread's first complete `HarnessState` and in every later Run state for that Thread. Foundation allocates the Thread's Foundation object ID first and passes it to `HarnessState.new(thread_id=id)` for a new or empty-history Thread, or to `source_state.fork(thread_id=id)` for a fork. The Harness stores and projects that same value without re-encoding it. The ID grants no authority.

A previously committed or imported Thread whose valid Harness state uses the legacy Harness-generated `thread-<32 lowercase hex>` form retains that exact value in both the Thread row and every later state; Foundation never rewrites an existing identity. New Foundation-owned Threads use the shared Foundation object-ID generator rather than the legacy generated form.

`version` is the positive Thread domain-object version. It starts at `1` when the Thread and first Run commit and increases by one for every accepted Thread advancement and every transition that seals the current Run. When an owning command defines idempotent replay, that replay resolves before comparing an expected version. Claim, execution, and worker recovery inside the same current Run use the Run's own version and do not change the Thread version. A state-first combined completion and queued successor acceptance applies both logically ordered changes in one transaction and therefore increments `version` by two.

`queue_version` is a non-negative version of the Thread's queued-submission collection. It starts at `0` and increases for every add, edit, delete, reorder, consume, or terminal-failure mutation. Queue-only mutation, including failure after the current Run is terminal, does not change `version`, `current_run_id`, or `head_run_id`. Consuming an entry increments both `queue_version` and `version` because the same transaction changes the queue and accepts another Run. When consumption is combined with completion of the prior current Run, `queue_version` increments once while `version` increments twice: once for the seal and once for advancement. When permanent queue invalidity is instead combined with completion, `queue_version` increments once and `version` increments once for the seal; no successor is accepted.

`session_id`, `role`, origin fields, and `created_at` are immutable. Exactly one Thread with `role="root"` belongs to a retained Session. A child Thread remains inside its parent's Session. A Session fork creates a new Session whose root Thread has `origin_kind="fork"`; an in-Session fork creates a child Thread with the same origin kind.

Origin fields preserve structural provenance without granting authority or replacing Run state lineage:

| Origin  | Required fields                                                        | Meaning                                                                                  |
| ------- | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `new`   | Both origin references absent; role is `root`                          | New Session root initialized with `HarnessState.new(thread_id=id)`                       |
| `fork`  | `origin_thread_id` and completed `origin_run_id` present               | New history initialized with `source_state.fork(thread_id=id)` from the exact source Run |
| `child` | Parent `origin_thread_id` and `origin_run_id` present; role is `child` | Host-managed child history caused by the selected parent Run                             |

`origin_run_id` is the source Run for a fork and structural cause for a child. Only a fork uses that source as its first Run's state parent. A child that starts with empty history has a root Run with `parent_run_id=null`; its structural relationship remains in the Thread origin and child relationship records.

### Head and Current Runs

The two Run references have distinct meanings:

| Field            | Meaning                                                                                                                                                       |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `head_run_id`    | Exact sealed `waiting` or `completed` Run whose frozen state is the currently selected continuation head; null until the Thread first selects such an outcome |
| `current_run_id` | Most recently accepted Run, regardless of whether it is active, waiting, completed, failed, or cancelled; always present                                      |

Foundation never derives either value from timestamps, event order, object listings, or replay data. Both references name Runs with the same `tenant_id`, `session_id`, and `thread_id` as the Thread row.

The current Run's status supplies the Thread's current execution and latest outcome projection. A current Run in `accepted` or `running` is the Thread's sole active Run. A current Run in `waiting`, `completed`, `failed`, or `cancelled` is sealed and the Thread has no active Run. The Thread stores no separate status or active-Run pointer. Intermediate claim and recovery transitions remain Run lifecycle facts and do not advance the Thread state version.

Thread mutations apply these resource-local effects. The linked operation contract owns eligibility, authorization, lineage, and the complete transaction flow.

| Durable mutation                                                                                         | Operation owner                                                                                                                                                                                | Thread effect                                                                                                                                    |
| -------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Accept a Run in an existing Thread                                                                       | [Agent Control: Input and Continuation](18-agent-control-input-and-continuation.md), [Queued Submissions](20-agent-control-queued-submissions.md), or [Async Subagents](34-async-subagents.md) | Select the new Run as current and increment `version`; preserve the prior head except when Continue From explicitly selects its completed source |
| Seal the current Run as `waiting` or `completed`                                                         | [Durable Run State](12-run-persistence.md#run-acceptance-checkpoint-and-outcome-commit)                                                                                                        | Retain the Run as current, select it as head, and increment `version`                                                                            |
| Seal the current Run as `failed` or `cancelled`                                                          | [Durable Run State](12-run-persistence.md#run-acceptance-checkpoint-and-outcome-commit) and [Active Execution](19-agent-control-active-execution.md)                                           | Retain the Run as current, preserve the prior head, and increment `version`                                                                      |
| Complete the current Run and consume the first queued submission atomically                              | [Queued Submissions](20-agent-control-queued-submissions.md#completion-time-combined-handoff)                                                                                                  | Select the completed source as head and its accepted successor as current; increment `version` twice and `queue_version` once                    |
| Complete the current Run and fail permanently invalid queued intent atomically                           | [Queued Submissions](20-agent-control-queued-submissions.md#completion-time-combined-handoff)                                                                                                  | Select the completed source as head, retain it as current, increment `version` once and `queue_version` once, and accept no successor            |
| Add, edit, delete, or reorder queued intent, or fail it after the current Run is terminal                | [Queued Submissions](20-agent-control-queued-submissions.md)                                                                                                                                   | Increment `queue_version` without changing `version`, current, or head                                                                           |
| Claim or recover a RunAttempt, publish a checkpoint, mutate the Thread inbox, or advance a stream cursor | The corresponding RunAttempt, Run state, active-control, or stream contract                                                                                                                    | Do not change Thread current, head, or `version`                                                                                                 |

`head_run_id` selects a frozen state base; it does not itself authorize an operation. The [input and continuation contract](18-agent-control-input-and-continuation.md#acceptance-and-lineage) owns completed-head, waiting-head, null-head, Continue From, Feedback, waiting Continue, and terminal-intent Retry eligibility. The [Async Subagents contract](34-async-subagents.md) independently owns whether a retained result may advance an inactive Thread.

## Relational Thread Table

The conceptual model materializes as one row in `threads`. Supported relational backends preserve the same validation and query semantics.

| Column group       | Columns                                                     | Relational contract                                                                                                      |
| ------------------ | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Identity and scope | `id`, `version`, `queue_version`, `tenant_id`, `session_id` | `id` is the primary key; advancement version is positive; queue version is non-negative; Session membership is immutable |
| Origin             | `role`, `origin_kind`, `origin_thread_id`, `origin_run_id`  | Immutable validated provenance; origin references can cross Session only for an authorized Session fork                  |
| Advancement        | `head_run_id`, `current_run_id`                             | Same-Thread Run references updated only by accepted advancement or outcome commit                                        |
| Time               | `created_at`, `updated_at`                                  | UTC instants; `updated_at` follows authoritative Thread mutation, not stream activity                                    |

The independent `thread_inbox_counters` row owned by [Active Execution](19-agent-control-active-execution.md#thread-inbox) is keyed by the same tenant and Thread. Its allocation and pending-budget updates do not change `version`, `queue_version`, `updated_at`, `current_run_id`, or `head_run_id`; it is delivery-order authority rather than another Thread resource field.

The relational contract preserves these constraints:

1. `(tenant_id, id)` is unique, and every Run has a same-tenant foreign key to its Thread.
2. `(tenant_id, session_id, id)` is unique, and every Thread references one persisted Session in the same tenant.
3. A partial unique constraint on `(tenant_id, session_id)` for `role="root"` enforces one root Thread per Session.
4. `current_run_id` is always present and names the most recently accepted same-Thread Run. `head_run_id` is null until a same-Thread Run seals as `waiting` or `completed`, and otherwise names only such a selected Run.
5. At most one Run per Thread is `accepted` or `running`; when such a Run exists it is `current_run_id`. The Run table's partial uniqueness constraint enforces the single-active rule.
6. Origin reference combinations match `origin_kind`; malformed or cross-tenant origins are rejected.
7. A committed Thread row and its first Run always exist together. Neither becomes independently visible without the other.
8. `queue_version` starts at zero, is non-negative, and changes only under the queued-submission mutation contract; consumption updates it in the same transaction that advances the Thread, while terminal failure can update it without accepting a Run.

The accepted access paths are:

| Access path                      | Index or uniqueness contract                                             |
| -------------------------------- | ------------------------------------------------------------------------ |
| Exact Thread read and state lock | Unique `(tenant_id, id)`                                                 |
| Session Thread listing           | `(tenant_id, session_id, created_at, id)`                                |
| Updated Thread listing           | `(tenant_id, session_id, updated_at, id)`                                |
| Queue mutation lock              | Unique `(tenant_id, id)` plus `queue_version`                            |
| Current or head Run join         | Same-tenant unique Run references stored on the Thread                   |
| Origin traversal                 | `(tenant_id, origin_run_id, id)` and `(tenant_id, origin_thread_id, id)` |
| One root Thread per Session      | Partial unique `(tenant_id, session_id)` for root role                   |

Run-table indexes for Worker claims, Run listing, search, and DAG traversal remain owned by the Run contract. They do not replace the Thread row or its state version.

## Thread Creation

Thread creation is part of Run acceptance and has no standalone empty-resource operation. The owning root, fork, or child acceptance contract creates the complete initial Run state before one short relational transaction inserts the Thread and first Run together with the required facts, evidence, relationships, and outbox intents. The transaction sets `version=1`, `queue_version=0`, selects the first Run as current, and leaves the head null.

The [input and continuation contract](18-agent-control-input-and-continuation.md) owns root and fork authorization, state transformation, and acceptance flow. [Async Subagents](34-async-subagents.md) owns child creation and its parent fence. This contract requires only that the committed Thread and first Run become visible together with valid Session membership, origin, and matching `thread_id`; a failed relational commit leaves any prepared objects non-authoritative.

## Reads and History Selection

An exact Thread read comes from `threads` under current authorization. It returns safe identity, advancement and queue versions, Session, role, currently authorized origin references, the head and current Run references, and timestamps. A concealed origin reference is omitted rather than exposing another Session or Run by possession. A Session Thread listing pages authorized Thread rows directly and can join the exact current Run for bounded current-status and latest-outcome summaries. It never groups Run rows to invent a Thread resource. Continuation eligibility comes from the selected head, its status, and the owning operation contract; it is not inferred from the current Run summary alone.

Thread Run listing filters authorized Run rows by the selected durable Thread identity. Exact lineage still starts from an explicitly selected Run and follows `parent_run_id`; `head_run_id` is a continuation selector, not a replacement for an explicit lineage head. Item and event replay remain separate projections and cannot repair or advance Thread state.

## Retention and Deletion

Foundation exposes no independent hard-delete mutation for a Thread. Session and interaction-retention policy can remove a Thread only after no retained Run, state object, queued submission, Thread inbox entry, Item, event, usage record, child relationship, fork origin, or idempotency evidence requires it. Expiry of the Thread's Redis control Stream and consumer group does not remove the Thread or its inbox. Removing presentation detail never removes the Thread row or changes its head.

A retained origin reference keeps the minimum safe source identity required by lineage policy. Retention can redact inaccessible content without rewriting Thread, Session, or Run identities or making an incomplete lineage appear complete.

## Security and Authorization

Every create, read, advance, queue mutation, fork, retry, feedback, and retention operation authorizes the Thread through its current tenant, Session, Workspace policy, and action. Origin and Run references grant no access by possession. A concealed Thread returns the same bounded not-found behavior as another concealed resource.

The Thread row contains correlation and control state only. It never exposes raw prompts, outputs, Harness state, pending payloads, credentials, provider state, private child content, or storage locators. Cross-Session fork validates both source read authority and destination mutation authority before publishing the new state.

## Failure Semantics

| Failure                                                                | Durable outcome                                                 | Retry or reconciliation                                                              |
| ---------------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Authorization, origin, parent, or state-version validation fails       | No Thread or Run mutation                                       | Caller refreshes authority or resource state                                         |
| Initial state publication fails                                        | No Thread or Run is accepted                                    | Root, fork, and async delegate can retry with the same owning key                    |
| State publishes but relational creation or advancement rolls back      | Existing Thread is unchanged; new objects are non-authoritative | Follow the owning operation's retry or orphan-cleanup contract                       |
| Response is lost after commit                                          | Thread and Run may already exist                                | Root, fork, and async delegate replay by their owning keys; linked resume is unknown |
| Concurrent advancement wins                                            | Losing request changes nothing                                  | Read the current Thread and decide against its new state version and head            |
| Current Run seals while another command uses older state               | Sealing wins and increments Thread state version                | Stale command conflicts and rereads the Thread                                       |
| Combined completion and queued acceptance loses any final precondition | No part of that combined transaction mutates the Thread         | Re-evaluate ordinary completion; later terminal recovery can consume the queue       |
| Referenced head or current Run is missing or mismatched                | Thread fails closed as relational corruption                    | Readiness or repair restores a verified consistent relational state                  |
| Stale RunAttempt tries to seal a non-current Run or select a head      | Mutation is fenced and rejected                                 | Current RunAttempt or transactional Worker takeover owns the transition              |

## Compatibility

Thread identity, Session membership, role, origin meaning, state-version and queue-generation semantics, and the meanings of the head and current Run references are durable API compatibility facts. Changing any of those meanings requires an incompatible API contract and a reviewed relational migration.

Adding safe read-only fields or indexes is compatible when authorization, ordering, state checks, and mutation semantics remain unchanged. Storage query shape, ORM organization, and transaction helper boundaries remain private implementation details.

## Trade-offs

The independent Thread row duplicates relationships that are also present on Run rows and requires atomic cross-row updates at acceptance and outcome commit. Foundation accepts that cost to provide one explicit owner for Thread existence, Session membership, optimistic concurrency, current-Run selection, and continuation-head selection. Reads join the current Run to derive execution and latest-outcome state instead of maintaining another mutable active pointer. Run rows remain the immutable work and state DAG; the Thread row is a compact mutable selector rather than another transcript or checkpoint store.

## Invariants

01. Every Foundation Thread is one independently versioned relational resource and belongs to exactly one persisted Session.
02. A Thread is created atomically with its first Run and never exists as an empty resource.
03. Thread ID equals the stable `HarnessState.thread_id` for every Run state in that Thread.
04. Exactly one retained root Thread belongs to a Session; child and fork histories use distinct Thread IDs.
05. `current_run_id` always names the most recently accepted Run and is never inferred from time or event order; it is the sole active Run exactly while its status is `accepted` or `running`.
06. `head_run_id` is null until the Thread selects a sealed waiting or completed Run and never names a failed or cancelled Run.
07. Thread `version` changes once on accepted advancement and once whenever the current Run seals; a combined completed seal and queued acceptance applies both increments in one transaction, while a combined completed seal and queue failure applies only the seal increment. Claim, execution, worker recovery, and queue-only mutation do not change it.
08. Accepted advancement and Run sealing update current, head, and Thread versions exactly as defined by this contract; the owning input, queue, or asynchronous-subagent contract determines operation eligibility and Run lineage.
09. When an owning command defines idempotency, replay resolves before `expected_thread_version`; a losing concurrency check changes neither Thread nor Run state.
10. No Thread mutation transaction spans Harness execution, object I/O, provider calls, Redis, streaming, sleeps, or other external work.
11. Thread identity, origin, Run references, cursors, and object locators grant no authority by possession.
12. Run state, Items, events, provider state, and presentation history never substitute for the durable Thread row.
13. Thread inbox rows, their independent sequence counter, and Redis control-group cursors remain separate from the Thread resource; none changes current-Run or continuation-head meaning or increments the Thread state version or queue generation.
14. `queue_version` changes on every queued-submission mutation. Consuming an entry atomically advances the queue version and creates one accepted Run; terminally failing permanently invalid queued intent advances the queue version and creates no Run. Either transition can commit with source completion under the owning queue contract.
15. A state-first combined handoff can atomically select a completed source as head and either select an accepted queued successor as current or terminally fail permanently invalid queued intent while retaining the source as current. If that path is unavailable, terminal Thread state and a non-empty queue can coexist until recovery drain or while consumption is recoverably blocked; an existing-Thread Run submission appends behind that queue.
