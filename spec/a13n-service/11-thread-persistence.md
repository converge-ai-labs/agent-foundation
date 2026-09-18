# Durable Thread Persistence

## Design Position

Service persists every hosted `Thread` as an independent versioned relational resource. The Thread row is the authority for Session membership, Thread origin, the most recently accepted Run, the selected continuation head, compare-and-swap serialization of accepted advancement, an independent queue revision, and internal inbox sequence and admission counters. A Thread is not a grouping inferred from Run timestamps or a `thread_id` copied into otherwise unrelated rows.

The shared [Platform Interaction Model](../interaction-model.md) owns the cross-platform meaning of Thread. The Harness owns creation and preservation of the matching `HarnessState.thread_id`. Service stores that exact ID rather than generating a parallel Host Thread identity. [Durable Run State](12-run-persistence.md) owns Run fields, state objects, parent edges, and outcomes; this contract owns which Run is current for a Service Thread and which sealed Run is selected as its continuation head. Whether the current Run is active derives from its own status rather than another stored Thread pointer.

A root Thread can be created before its first Run. Creation records its Session, optional default Environment and inbox authority without provisioning a target. Combined root Run start, Fork and child acceptance create their Thread and first Run atomically. [Environment Management](29-environment-management.md#thread-defaults-and-run-selection) owns automatic allocation from a template and the mutable default selection.

[Configuration conversations](43-agent-configuration-assistant.md#configuration-session-and-thread-scope) reuse these Session/Thread identities. Each configuration Session owns one stable draft, created atomically with the Session. Every Thread in that Session discusses the same candidate; creating or forking a Thread allocates no draft. Independent candidates require separate configuration Sessions. Draft edits and applications compare its shared version without changing any accepted Run's stable draft identity or execution snapshot. Configuration ownership adds read/control predicates without creating another Thread lifecycle or history store.

## Boundaries

| Concern                                                                            | Owner                                                                               | Contract                                                                                                                                    |
| ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Session, Thread, Run, and Item meaning                                             | [Platform Interaction Model](../interaction-model.md)                               | Defines identity and cross-platform relationships                                                                                           |
| `thread_id` creation, preservation, and fork transformation                        | [Harness State](../a13n-harness/10-snapshot-and-resume.md)                          | Supplies one stable ID inside complete continuation state                                                                                   |
| Durable Thread resource, versions, origin, current Run, and selected head          | This contract                                                                       | Serializes Service Thread advancement and queued-submission mutation and supplies read authority                                            |
| Persisted Session membership and root selection                                    | This contract                                                                       | Requires one existing Session container and exactly one retained root Thread; other Session product metadata remains outside the Thread row |
| Run row, state object, parent edge, scheduling, and outcome                        | [Durable Run State](12-run-persistence.md)                                          | Owns one accepted advancement and its resumable state                                                                                       |
| RunAttempt lease, generation, and stale-writer fence                               | [Run Attempts, Scheduling, and Recovery](13-run-attempt-scheduling-and-recovery.md) | Authorizes worker mutation of the current Run while it is active                                                                            |
| Agent invocation and advancement commands                                          | [Agent Control: Input and Continuation](18-agent-control-input-and-continuation.md) | Accepts start, the existing-Thread Run request and immediate branches, waiting Feedback or Continue, fork, and retry                        |
| Asynchronous-result advancement                                                    | [Async Subagents](34-async-subagents.md)                                            | Delivers to the current active Run or accepts an eligible successor without resolving a waiting state                                       |
| Queue-if-busy Run intent                                                           | [Agent Control: Queued Submissions](20-agent-control-queued-submissions.md)         | Owns the existing-Thread route's queued branch, queue rows, ordering, editing, and atomic consumption into a Run                            |
| Thread inbox entries, FIFO counter, binding, steer, interrupt, and control wakeups | [Agent Control: Active Execution](19-agent-control-active-execution.md)             | Owns FIFO and capacity transitions on the Thread row; inbox entries and Redis cursors keep their own stores                                 |
| Public resource catalog and wire read models                                       | [Management API](16-management-api.md)                                              | Exposes authorized Thread reads and common API behavior                                                                                     |
| Agent-facing history retrieval                                                     | [Agent Interaction Retrieval](35-agent-interaction-retrieval.md)                    | Projects authorized Thread and Run data without becoming authority                                                                          |

A Thread row contains no message history, Thread inbox payload, Harness state, Item payload, provider state, credential, worker lease, queue entry, Redis consumer-group cursor, replay cursor, or live process object. Those values retain their owning stores and lifecycles.

## Durable Thread Model

The following schema is conceptual. It defines durable field meaning rather than a public wire representation or concrete ORM class.

```python
type ThreadRole = Literal["root", "child"]
type ThreadOriginKind = Literal["new", "fork", "child"]


class Thread:
    labels: dict[str, str]
    id: str
    version: int
    queue_version: int

    # Internal inbox accounting; omitted from public Thread reads.
    next_delivery_sequence: int
    pending_count: int
    pending_bytes: int

    organization_id: str
    session_id: str

    role: ThreadRole
    origin_kind: ThreadOriginKind
    origin_thread_id: str | None
    origin_run_id: str | None

    head_run_id: str | None
    current_run_id: str | None
    default_environment_id: EnvironmentId | None
    default_environment_working_directory: str | None
    default_environment_access: EnvironmentAccess | None

    created_at: datetime
    updated_at: datetime
```

`id` equals the `thread_id` in the Thread's first complete `HarnessState` and in every later Run state for that Thread. Service allocates the Thread's Service object ID first and passes it to `HarnessState.new(thread_id=id)` for a new or empty-history Thread, or to `source_state.fork(thread_id=id)` for a fork. The Harness stores and projects that same value without re-encoding it. The ID grants no authority.

A previously committed or imported Thread whose valid Harness state uses the legacy Harness-generated `thread-<32 lowercase hex>` form retains that exact value in both the Thread row and every later state; Service never rewrites an existing identity. New Service-owned Threads use the shared Service object-ID generator rather than the legacy generated form.

`version` is the positive Thread domain-object version. It starts at `1` when the Thread is created and increases by one for every accepted Thread advancement and every transition that seals the current Run. When an owning command defines idempotent replay, that replay resolves before comparing an expected version. Claim, execution, and worker recovery inside the same current Run use the Run's own version and do not change the Thread version. Source completion and later queued successor acceptance each increment `version` once in independent transactions.

`queue_version` is a non-negative version of the Thread's queued-submission collection. It starts at `0` and increases for every add, edit, delete, reorder, consume, or terminal-failure mutation. Queue-only mutation, including failure after the current Run is terminal, does not change `version`, `current_run_id`, or `head_run_id`. Consuming an entry increments both `queue_version` and `version` because the same transaction changes the queue and accepts another Run.

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
| `current_run_id` | Most recently accepted Run, regardless of status; null until the first Run is accepted                                                                        |

Service never derives either value from timestamps, event order, object listings, or replay data. When present, both references name Runs with the same `organization_id`, `session_id`, and `thread_id` as the Thread row.

An empty Thread has no current outcome or active Run. Once present, the current Run's status supplies the Thread's current execution and latest outcome projection. A current Run in `accepted` or `running` is the Thread's sole active Run. A current Run in `waiting`, `completed`, `failed`, or `cancelled` is sealed and the Thread has no active Run. The Thread stores no separate status or active-Run pointer. Intermediate claim and recovery transitions remain Run lifecycle facts and do not advance the Thread state version.

Thread mutations apply these resource-local effects. The linked operation contract owns eligibility, authorization, lineage, and the complete transaction flow.

| Durable mutation                                                                                         | Operation owner                                                                                                                                                                                | Thread effect                                                                                                                                    |
| -------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Accept a Run in an existing Thread                                                                       | [Agent Control: Input and Continuation](18-agent-control-input-and-continuation.md), [Queued Submissions](20-agent-control-queued-submissions.md), or [Async Subagents](34-async-subagents.md) | Select the new Run as current and increment `version`; preserve the prior head except when Continue From explicitly selects its completed source |
| Seal the current Run as `waiting` or `completed`                                                         | [Durable Run State](12-run-persistence.md#run-acceptance-checkpoint-and-outcome-commit)                                                                                                        | Retain the Run as current, select it as head, and increment `version`                                                                            |
| Seal the current Run as `failed` or `cancelled`                                                          | [Durable Run State](12-run-persistence.md#run-acceptance-checkpoint-and-outcome-commit) and [Active Execution](19-agent-control-active-execution.md)                                           | Retain the Run as current, preserve the prior head, and increment `version`                                                                      |
| Consume the first queued submission after the current Run is terminal                                    | [Queued Submissions](20-agent-control-queued-submissions.md#post-completion-consumption)                                                                                                       | Select the accepted successor as current, preserve the completed or null head, and increment `version` and `queue_version` once each             |
| Add, edit, delete, or reorder queued intent, or fail it after the current Run is terminal                | [Queued Submissions](20-agent-control-queued-submissions.md)                                                                                                                                   | Increment `queue_version` without changing `version`, current, or head                                                                           |
| Claim or recover a RunAttempt, publish a checkpoint, mutate the Thread inbox, or advance a stream cursor | The corresponding RunAttempt, Run state, active-control, or stream contract                                                                                                                    | Do not change Thread current, head, or `version`                                                                                                 |

`head_run_id` selects a frozen state base; it does not itself authorize an operation. The [input and continuation contract](18-agent-control-input-and-continuation.md#acceptance-and-lineage) owns completed-head, waiting-head, null-head, Continue From, Feedback, waiting Continue, and terminal-intent Retry eligibility. The [Async Subagents contract](34-async-subagents.md) independently owns whether a retained result may advance an inactive Thread.

## Relational Thread Table

The conceptual model materializes as one row in `threads`. Supported relational backends preserve the same validation and query semantics.

| Column group       | Columns                                                                                         | Relational contract                                                                                                      |
| ------------------ | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Identity and scope | `id`, `version`, `queue_version`, `organization_id`, `session_id`                               | `id` is the primary key; advancement version is positive; queue version is non-negative; Session membership is immutable |
| Origin             | `role`, `origin_kind`, `origin_thread_id`, `origin_run_id`                                      | Immutable validated provenance; origin references can cross Session only for an authorized Session fork                  |
| Advancement        | `head_run_id`, `current_run_id`                                                                 | Same-Thread Run references updated only by accepted advancement or outcome commit                                        |
| Environment        | `default_environment_id`, `default_environment_working_directory`, `default_environment_access` | Mutable complete binding; Run acceptance freezes its own selection and updates these fields atomically                   |
| Inbox accounting   | `next_delivery_sequence`, `pending_count`, `pending_bytes`                                      | Internal FIFO allocation and pending-capacity state, serialized by the owning Thread row lock                            |
| Time               | `created_at`, `updated_at`                                                                      | UTC instants; `updated_at` follows authoritative Thread mutation, not stream activity                                    |

The Thread row stores `next_delivery_sequence`, `pending_count`, and `pending_bytes` as non-null internal columns. Every new Thread initializes them to `1`, `0`, and `0`, respectively, including root, fork, and child creation. `next_delivery_sequence` remains positive and pending counters remain non-negative. These columns are omitted from public Thread representations and have no independent identity or lifecycle. [Active Execution](19-agent-control-active-execution.md#thread-inbox) owns sequence allocation, capacity reservation, and release semantics.

Every counter mutation holds the owning Thread row lock until its short transaction commits or rolls back and commits atomically with the corresponding inbox changes. Counter-only updates do not change `version`, `queue_version`, `updated_at`, `current_run_id`, or `head_run_id`, and require no optimistic Thread-version increment or precondition of their own. Commands that also advance the Thread or mutate queued submissions retain their existing version rules. Other Thread mutations preserve these counters unless the same transaction performs an inbox transition; stale detached Thread values must not overwrite current counters.

The relational contract preserves these constraints:

1. `(organization_id, id)` is unique, and every Run has a same-organization foreign key to its Thread.
2. `(organization_id, session_id, id)` is unique, and every Thread references one persisted Session in the same organization.
3. A partial unique constraint on `(organization_id, session_id)` for `role="root"` enforces one root Thread per Session.
4. `current_run_id` is null exactly before the first Run, and otherwise names the most recently accepted same-Thread Run. `head_run_id` is null until a same-Thread Run seals as `waiting` or `completed`, and otherwise names only such a selected Run.
5. At most one Run per Thread is `accepted` or `running`; when such a Run exists it is `current_run_id`. The Run table's partial uniqueness constraint enforces the single-active rule.
6. Origin reference combinations match `origin_kind`; malformed or cross-organization origins are rejected.
7. A root Thread may exist before its first Run. Combined root start, Fork and child acceptance publish the Thread and first Run atomically.
8. `queue_version` starts at zero, is non-negative, and changes only under the queued-submission mutation contract; consumption updates it in the same transaction that advances the Thread, while terminal failure can update it without accepting a Run.
9. Every Thread row contains a positive `next_delivery_sequence` and non-negative `pending_count` and `pending_bytes`; their initialization and lifecycle belong to that row.

The accepted access paths are:

| Access path                        | Index or uniqueness contract                                                         |
| ---------------------------------- | ------------------------------------------------------------------------------------ |
| Exact Thread read and state lock   | Unique `(organization_id, id)`                                                       |
| Session Thread listing             | `(organization_id, session_id, created_at, id)`                                      |
| Updated Thread listing             | `(organization_id, session_id, updated_at, id)`                                      |
| Inbox allocation and capacity lock | Unique `(organization_id, id)`; the same row lock used for Thread advancement        |
| Queue mutation lock                | Unique `(organization_id, id)` plus `queue_version`                                  |
| Current or head Run join           | Same-organization unique Run references stored on the Thread                         |
| Origin traversal                   | `(organization_id, origin_run_id, id)` and `(organization_id, origin_thread_id, id)` |
| One root Thread per Session        | Partial unique `(organization_id, session_id)` for root role                         |

Run-table indexes for Worker claims, Run listing, search, and DAG traversal remain owned by the Run contract. They do not replace the Thread row or its state version.

## Thread Creation

`POST /workspaces/{workspace}/threads` creates an empty root Thread and selects or creates its Session under Session uniqueness and authorization. The request can select an Agent for its default template and an explicit environment choice. In one short transaction it allocates the Thread and optional Environment record, initializes the internal inbox columns as defined above, sets `version=1`, `queue_version=0`, and leaves both Run references null. No Harness execution or Provider preparation occurs. First Run acceptance creates `HarnessState.new(thread_id=thread.id)` and advances the existing Thread.

The combined root Run command uses the same allocation rules and commits the new Thread, selected Environment and first Run together. [Agent Control](18-agent-control-input-and-continuation.md) owns root/Fork authorization and [Async Subagents](34-async-subagents.md) owns child creation. An existing Thread's default changes only under authorized Run acceptance; accepted Runs retain their own fixed selection. Physical Thread deletion releases its default reference but never implicitly deletes a shared target.

## Reads and History Selection

An exact Thread read comes from `threads` under current authorization. It returns safe identity, advancement and queue versions, Session, role, currently authorized origin references, the optional head/current Run references, authorized default Environment binding, including working directory and access, and timestamps. A concealed origin reference is omitted rather than exposing another Session or Run by possession. A Session Thread listing pages authorized Thread rows directly and can join the exact current Run for bounded current-status and latest-outcome summaries. It never groups Run rows to invent a Thread resource. Continuation eligibility comes from the selected head, its status, and the owning operation contract; it is not inferred from the current Run summary alone.

Thread Run listing filters authorized Run rows by the selected durable Thread identity. Exact lineage still starts from an explicitly selected Run and follows `parent_run_id`; `head_run_id` is a continuation selector, not a replacement for an explicit lineage head. Item and event replay remain separate projections and cannot repair or advance Thread state.

## Retention and Deletion

Service exposes no independent hard-delete mutation for a Thread. Session and interaction-retention policy can remove a Thread only after no retained Run, state object, queued submission, Thread inbox entry, Item, event, usage record, child relationship, fork origin, or idempotency evidence requires it. Expiry of the Thread's Redis control Stream and consumer group does not remove the Thread or its inbox. Removing presentation detail never removes the Thread row or changes its head.

A retained origin reference keeps the minimum safe source identity required by lineage policy. Retention can redact inaccessible content without rewriting Thread, Session, or Run identities or making an incomplete lineage appear complete.

## Security and Authorization

Every create, read, advance, queue mutation, fork, retry, feedback, and retention operation authorizes the Thread through its current organization, Session, Workspace policy, and action. Origin and Run references grant no access by possession. A concealed Thread returns the same bounded not-found behavior as another concealed resource.

The Thread row contains correlation and control state only. It never exposes raw prompts, outputs, Harness state, pending payloads, credentials, provider state, private child content, or storage locators. Cross-Session fork validates both source read authority and destination mutation authority before publishing the new state.

## Failure Semantics

| Failure                                                             | Durable outcome                                                                     | Retry or reconciliation                                                              |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Authorization, origin, parent, or state-version validation fails    | No Thread or Run mutation                                                           | Caller refreshes authority or resource state                                         |
| Initial state publication fails                                     | No Thread or Run is accepted                                                        | Root and fork can retry with the same key; child admission starts a fresh invocation |
| State publishes but relational creation or advancement rolls back   | Existing Thread is unchanged; new objects are non-authoritative                     | Follow the owning operation's retry or orphan-cleanup contract                       |
| Response is lost after commit                                       | Thread and Run may already exist                                                    | Root and fork replay by key; child admission reports an unknown outcome              |
| Concurrent advancement wins                                         | Losing request changes nothing                                                      | Read the current Thread and decide against its new state version and head            |
| Current Run seals while another command uses older state            | Sealing wins and increments Thread state version                                    | Stale command conflicts and rereads the Thread                                       |
| Queue acceptance loses a final precondition after source completion | No part of queue acceptance mutates the Thread; source completion remains committed | Reread committed Thread state and retry consumption                                  |
| Referenced head or current Run is missing or mismatched             | Thread fails closed as relational corruption                                        | Readiness or repair restores a verified consistent relational state                  |
| Stale RunAttempt tries to seal a non-current Run or select a head   | Mutation is fenced and rejected                                                     | Current RunAttempt or transactional Worker takeover owns the transition              |

## Compatibility

Thread identity, Session membership, role, origin meaning, state-version and queue-generation semantics, and the meanings of the head and current Run references are durable API compatibility facts. Changing any of those meanings requires an incompatible API contract and a reviewed relational migration.

Adding safe read-only fields or indexes is compatible when authorization, ordering, state checks, and mutation semantics remain unchanged. Storage query shape, ORM organization, and transaction helper boundaries remain private implementation details.

## Trade-offs

The independent Thread row duplicates relationships that are also present on Run rows and requires atomic cross-row updates at acceptance and outcome commit. Service accepts that cost to provide one explicit owner for Thread existence, Session membership, optimistic concurrency, current-Run selection, and continuation-head selection. Reads join the current Run to derive execution and latest-outcome state instead of maintaining another mutable active pointer. Run rows remain the immutable work and state DAG; the Thread row is a compact mutable selector with internal inbox accounting rather than another transcript or checkpoint store. Inbox accounting shares the Thread row lock already required by delivery and outcome selection. This avoids a separately created and locked counter record, while counter changes write the Thread row and share its contention with other mutations of the same Thread.

## Invariants

01. Every Service Thread is one independently versioned relational resource and belongs to exactly one persisted Session.

02. A root Thread can precede its first Run; combined root start, Fork and child acceptance are atomic.

03. Thread ID equals the stable `HarnessState.thread_id` for every Run state in that Thread.

04. Exactly one retained root Thread belongs to a Session; child and fork histories use distinct Thread IDs.

05. `current_run_id` is null before first acceptance and otherwise names the most recently accepted Run and is never inferred from time or event order; it is the sole active Run exactly while its status is `accepted` or `running`.

06. `head_run_id` is null until the Thread selects a sealed waiting or completed Run and never names a failed or cancelled Run.

07. Thread `version` changes once on accepted advancement and once whenever the current Run seals. Source sealing and later queued acceptance commit separately. Claim, execution, worker recovery, and queue-only mutation do not change it.

08. Accepted advancement and Run sealing update current, head, and Thread versions exactly as defined by this contract; the owning input, queue, or asynchronous-subagent contract determines operation eligibility and Run lineage.

09. When an owning command defines idempotency, replay resolves before `expected_thread_version`; a losing concurrency check changes neither Thread nor Run state.

10. No Thread mutation transaction spans Harness execution, object I/O, provider calls, Redis, streaming, sleeps, or other external work.

11. Thread identity, origin, Run references, cursors, and object locators grant no authority by possession.

12. Run state, Items, events, provider state, and presentation history never substitute for the durable Thread row.

13. Internal inbox sequence and capacity counters live on the Thread row and are updated atomically with inbox transitions under its row lock. They are absent from public Thread reads; counter-only updates preserve Thread versions, timestamps, and Run selection. Inbox entries and Redis control-group cursors retain their separate stores.

14. `queue_version` changes on every queued-submission mutation. Consuming an entry atomically advances the queue version and creates one accepted Run; terminally failing permanently invalid queued intent advances the queue version and creates no Run. Both transitions commit independently of the already-terminal source outcome.

15. Source completion selects the completed Run as head and retains it as current before any queued successor is prepared. Terminal Thread state and a non-empty queue can coexist until the Worker or periodic scanner consumes an entry, or while consumption is recoverably blocked. An ordinary existing-Thread submission appends behind that queue when the current Run is completed; a failed or cancelled current Run rejects it.

16. Thread labels are mutable classification metadata outside execution and queue versions. A new ordinary or child Thread copies its Session's current labels at acceptance; a forked Thread instead copies its source Thread's current labels. Explicit creation overrides apply after the copy and no later parent mutation propagates.
