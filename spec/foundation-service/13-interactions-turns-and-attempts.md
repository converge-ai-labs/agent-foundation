# Interactions, Turns, and Attempts

## Design Position

Foundation implements the shared [`Session`, `Thread`, `Turn`, and `Item`](../interaction-model.md) interaction model directly as its durable Agent-work model. A `Turn` is one accepted scheduling, recovery, state, and terminal-outcome boundary. A `TurnAttempt` is one replaceable fenced worker generation for that Turn. Foundation defines no separate durable `Execution` or `ExecutionAttempt` resource.

Every Foundation-managed Agent invocation, including an interactive request, schedule, webhook, service request, or asynchronous child, accepts a Turn before work is claimable. Worker loss and recoverable infrastructure failure create later TurnAttempts under the same non-terminal Turn.

[Durable Thread Persistence](24-thread-persistence.md) owns the independent Thread row, Session membership, origin, version, current Turn, and selected continuation head. [Durable Turn State](14-turn-persistence.md) owns Turn fields, lifecycle, state-object publication, sealing, lineage, and recovery budget. [Durable Turn Attempt Persistence](15-turn-attempt-persistence.md) owns TurnAttempt fields, leases, fences, dispatch evidence, and attempt outcomes. This document owns only the interaction-to-runtime mapping and the boundaries that those detailed contracts must preserve.

[Agent Control: Input and Continuation](28b-agent-control-input-and-continuation.md)
owns root invocation, ordinary continuation, waiting feedback, fork, and retry.
[Agent Control: Active Execution](28c-agent-control-active-execution.md) owns cancellation commands and
their Agent-work semantics.

## Relationships

```mermaid
flowchart TB
    Session[Session]
    RootThread[Root Thread]
    ChildThread[Child Thread]
    ParentTurn[Parent Turn]
    WaitingTurn[Waiting Turn]
    FeedbackTurn[New feedback Turn]
    Attempt1[TurnAttempt generation 1]
    Attempt2[TurnAttempt generation 2]
    Run1[Harness Run]
    Run2[Harness Run]
    Model[ModelAttempt]
    Item[Item]

    Session --> RootThread --> ParentTurn --> Attempt1 --> Run1 --> Model
    ParentTurn --> Attempt2 --> Run2
    ParentTurn --> Item
    RootThread --> WaitingTurn --> FeedbackTurn
    Session --> ChildThread
```

The diagram shows identity and lineage, not live object containment. Every Turn belongs to exactly one Session and Thread. A Turn can own zero or more immutable TurnAttempts over its lifetime, but at most one leased generation can authorize worker mutation at a time. One TurnAttempt starts at most one Harness Run; one Harness Run can contain several process-local `ModelAttempt` values.

Items and replay data are projections of a Turn and never become continuation state. A Harness Run, model request, Redis entry, Item, or delivery cursor never replaces Turn or TurnAttempt identity.

## Turn and TurnAttempt Lifecycle Boundary

A Turn begins as `accepted`. A Worker's first successful claim creates a new TurnAttempt, selects it as the current generation, and moves the Turn to `running`. A replacement Worker's short transaction marks an expired prior Attempt `failed`, creates and selects the next generation within budget, and leaves the same Turn `running`. A retryable Attempt failure can temporarily leave that running Turn without a current Attempt until `available_at`. A waiting or completed Harness outcome first publishes a complete matching state candidate and then atomically verifies that the Turn remains the Thread's current Turn, seals it, selects it as the Thread head, and increments the Thread version. Failed or cancelled sealing leaves that Turn current and preserves the prior head.

`waiting`, `completed`, `failed`, and `cancelled` are sealed Turn outcomes. They are never returned to `accepted` or `running`. Pending feedback does not reopen a waiting Turn: once the full feedback set is authenticated and accepted, Foundation accepts a new Turn whose `parent_turn_id` names that waiting Turn and leaves the parent unchanged.

A TurnAttempt is an immutable audit record after it reaches `succeeded`, `failed`, or `cancelled`. `failed` is generation-terminal and does not by itself mean the Turn failed; Foundation defines no separate Attempt `lost` state. Replacing an Attempt creates a new generation with a fresh lease, Harness Run, Environment connector scope, attachment, clients, credentials, and bindings. It reconnects the target frozen in Turn state and never restores another process's task, socket, database session, live connector handle, attachment, or Harness Run.

## State and Checkpoint Boundary

Each Turn owns one deterministic object-storage state key. A checkpoint operation conditionally replaces the complete object at that same key under the current TurnAttempt fence. Foundation exposes no separate checkpoint resource, checkpoint ID, base-state object, result-state object, or selectable checkpoint history.

The relational Turn record stores the exact digest, size, schema versions, checkpoint sequence, and committing TurnAttempt of a sealed state. State publication alone does not seal the Turn; the generation-fenced relational transition selects the exact published candidate. Partial messages, raw stream deltas, provider history, Items, process memory, and tool fragments are never continuation state.

## Durable Agent Tool Dispatch Boundary

Before dispatching an Agent tool call, Foundation records the invocation identity
and bounded request summary under the current TurnAttempt fence. A complete
checkpoint records matching tool results through Harness state. After a
replacement Attempt owns the lease, its Worker compares those two durable facts
outside a database transaction: every dispatched invocation absent from the
latest complete checkpoint becomes bounded `unknown_outcome` context committed
on that new Attempt before Harness entry.

The next Agent may inspect that context and decide whether to issue another
ordinary invocation. Foundation never automatically replays the prior call and
never treats missing receipts, logs, heartbeats, or telemetry as proof that the
call failed or had no effect. Foundation persists no TurnAttempt-wide generic
dispatch phase; model requests, run-local Environment setup, managed Skill
materialization, and other Host preparation do not need a phase surrogate.

## TurnAttempt and Harness Mapping

One TurnAttempt starts at most one logical Harness Run. Before Harness entry,
the worker resolves exact immutable revisions, reuses the Turn-owned model
execution snapshot, creates fresh run Capabilities, resolves fresh credentials,
opens fresh Environment connector attachments from the exact configuration in
Turn state, and builds fresh `RunBindings` through the Harness advanced
Environment binding API. Each connector keeps the already-running resource alive
only while that binding is active and closes its process-local clients afterward.

As soon as the Harness supplies its Run identity and before the worker publishes the first live observation, the worker binds `harness_run_id` immutably to the current TurnAttempt under the attempt fence. That durable binding provides provenance and authorization correlation; it does not define the Turn-scoped Redis Stream, whose stable identity and replay contract are owned by [Lifecycle and Stream Persistence](17-lifecycle-and-stream-persistence.md).

Bounded connector transport retries and internal Harness recovery remain within the Harness Run and do not allocate another TurnAttempt. Conversely, durable worker replacement always allocates another TurnAttempt and another Harness Run.

## Invariants

01. Session, Thread, Turn, and Item retain the shared platform meanings.
02. Every hosted Thread is an independent versioned relational resource whose current Turn and continuation head are never inferred from Turn timestamps; current-Turn status determines whether the Thread has active work.
03. Turn owns accepted schedulable Agent work, lineage, state, recovery budget, and durable outcome.
04. TurnAttempt owns one replaceable fenced worker generation and starts at most one Harness Run.
05. Foundation defines no separate durable Execution or ExecutionAttempt resource.
06. Only the current TurnAttempt can conditionally publish state or commit a Turn transition.
07. The current TurnAttempt durably binds its immutable Harness Run identity before the first live observation is published.
08. Every Agent tool invocation is recorded durably under the current fence before dispatch.
09. Absence of a receipt, event, or telemetry signal never proves Agent tool-call failure.
10. Foundation resumes from complete Turn state, projects unmatched Agent tool calls as `unknown_outcome`, and never automatically replays them.
11. A new Turn freezes current model configuration once; replacement
    TurnAttempts reuse that snapshot and resolve only fresh credential values.
12. A replacement TurnAttempt reuses the exact Environment execution
    configuration in Turn state, opens fresh connector attachments, and creates
    no Environment connection lease.
