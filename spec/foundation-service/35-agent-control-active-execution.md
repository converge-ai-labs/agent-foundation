# Agent Control: Active Execution

## Design Position

Foundation controls already accepted Agent work through durable commands against
the owning Turn. Cancellation is durable intent followed by cooperative
enforcement; it does not freeze a process, restore a prior process-local Run, or
claim rollback of effects.

This contract owns the public cancellation command and its Agent-work semantics.
[Durable Turn State](14-turn-persistence.md) owns the resulting lifecycle and
sealed state, [Durable Turn Attempt Persistence](15-turn-attempt-persistence.md)
owns the current worker fence, and [Scheduling, Workers, and Recovery](16-scheduling-workers-and-recovery.md)
owns worker observation and enforcement. Automatic replacement of a failed or
expired TurnAttempt remains recovery inside the same Turn rather than a public
Agent-control command.

## Boundaries

| Concern                                             | Owner                                                                                                                                           | Relationship                                                                       |
| --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Public Turn cancellation command                    | This contract                                                                                                                                   | Records durable cancellation intent against the selected active Turn               |
| Turn lifecycle, state freezing, and terminal record | [Durable Turn State](14-turn-persistence.md)                                                                                                    | Commits or rejects the `cancelled` outcome                                         |
| Worker lease, fence, and Attempt terminalization    | [Durable Turn Attempt Persistence](15-turn-attempt-persistence.md)                                                                              | Prevents stale workers from committing after control state advances                |
| Worker polling, takeover, and automatic recovery    | [Scheduling, Workers, and Recovery](16-scheduling-workers-and-recovery.md)                                                                      | Cooperatively observes the durable command and never treats it as process rollback |
| Authentication and command idempotency              | [Identity and Access Management](10-identity-and-access-management.md) and [Durable Operations and Outbox](06-durable-operations-and-outbox.md) | Own authorization, evidence, and unknown-commit reconciliation                     |

## Cancellation Command

```http
POST /api/v1/turns/{turn_id}/cancel
Idempotency-Key: opaque-caller-key
```

The command requires current authorization and an active target Turn. It records
durable cancellation intent and seals the active Turn through the owning Turn
lifecycle. A command returns the mutated resource or a durable receipt. `202`
means accepted, not that every process-local or external effect has stopped.

Unknown outcome after possible command dispatch is reconciled by repeating the
same idempotency key or reading the returned resource. A client never generates
a new key merely because acknowledgement was lost. A client disconnect or
transport-delivery failure never cancels a Turn unless the client separately
submits this command.

## Enforcement and Outcome

A worker checks cancellation before expensive or effectful boundaries and
attempts a fenced outcome commit. Cancellation never claims rollback of model,
tool, child, external Environment, keep-alive, provider, or client effects.

Cancellation before durable Turn acceptance creates no Turn. Cancellation after
acceptance follows the Turn lifecycle and freezes the latest valid state but
does not make the Turn an eligible parent. The sealed cancelled Turn remains the
Thread's current Turn, preserves the prior continuation head, and cannot return
to `accepted` or `running`. A later retry follows the
[terminal-intent retry contract](34-agent-control-input-and-continuation.md#retry-of-terminal-intent)
and creates a successor Turn rather than reopening it.

Exactly one legal generation-fenced transition wins a cancellation, waiting, or
completion race. A losing local result remains diagnostic only. A stale
TurnAttempt cannot publish Turn state, mutate Thread current/head selection,
publish pending work, retain Items, commit lifecycle transitions, or select a
terminal outcome. Late immutable usage evidence can retain its original attempt
attribution under the usage contract, but it cannot mutate Turn lifecycle.

## Invariants

1. Cancellation is durable intent followed by cooperative enforcement.
2. Cancellation never implies rollback of model, tool, child, Environment, provider, or client effects.
3. Transport disconnect and delivery failure do not cancel Agent work.
4. Exactly one fenced cancellation, waiting, or completion transition selects the Turn outcome.
5. A cancelled Turn is sealed and immutable; later retry creates a successor Turn.
6. Automatic TurnAttempt recovery is not a public Agent-control command.
