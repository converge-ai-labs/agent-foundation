# Running state

> Discussion proposal, not the current specification. See [status and scope](README.md).

## What the state holds

A Run's continuation state remains today's `RunState` around a `HarnessState`: message history, Capability namespaces and environment states, plus the Service's sequence, attempt, the deferred requests of a waiting outcome, and resume-input progress ([`checkpoints.py`](../../../packages/a13n-service/a13n_service/runs/checkpoints.py)). Two things leave it:

- **`a13n.usage`.** The Service removes the namespace from every state it persists. It never restores accounting from a checkpoint (`resume_usage=False`), and `usage_records` already holds every contribution. Harness UI still restores usage from state, so the Harness default does not change.
- **Inline subagent states.** See [inline subagent state](#inline-subagent-state).

## Start object and staged changes

Every executing Run has a start object: its own latest state object if it has written one, otherwise its parent's final state object (continue, resume, child-result successor, fork origin) or the Thread's initial seed. This is today's rule for where an attempt starts.

While the Run executes, PostgreSQL stages only what differs from the start object:

- `run_messages`: one row per message position whose bytes differ from the start object. A later write to the same position replaces the row.
- `run_namespaces`: one row per Capability namespace whose value differs from the start object. A removed namespace is a row without a value.
- A staged header on the Run: sequence, attempt, message count, resume-input progress, and environment states.

The rows hold the current version, not a history of changes. Their size is bounded by the current context, which compaction already bounds, and does not grow with the number of boundaries. Recovery reads the start object and overlays the staged rows by position and name. Nothing is replayed.

## Each boundary

The worker keeps the canonical bytes of each committed message. At a boundary it encodes each message and compares them from the first position:

| Comparison result                                                           | Action                                           |
| --------------------------------------------------------------------------- | ------------------------------------------------ |
| Only appended messages, optionally with the last committed message replaced | Stage those positions and the changed namespaces |
| Any earlier message changed, or the history became shorter                  | [Merge](#merges): write a new start object       |

The last committed message may change because the model-context coordinator commits request overlays after the boundary's export ([`model_context.py`](../../../packages/a13n-harness/a13n_harness/model_context.py)).

The rule inspects bytes, not causes. Compaction, idle-time trimming by the default cold-start filter, system-prompt reconciliation, adjacent-request merging, and any future rewrite are caught without being enumerated. The comparison uses persisted bytes rather than Python object identity because some code edits history objects in place ([`self_healing.py`](../../../packages/a13n-harness/a13n_harness/models/self_healing.py)). A spurious difference only causes an extra merge. The worst case is a complete state write at every boundary, which is today's behavior.

Staged rows commit in the existing fenced boundary transaction together with input consumption, steer assignment, and the Run's progress columns.

## Merges

A merge writes one complete state object outside any database session. One fenced transaction then moves `runs.checkpoint` to it, clears the staged header, deletes the Run's staged rows, and stages reclamation of the replaced object when this Run wrote it. A merge happens when:

1. **The Run ends.** A completed or waiting seal commits the final object in its seal transaction, as today. A worker sealing failed or cancelled writes one when its lease leaves time. Otherwise the [background job](03-storage-and-apis.md#background-job) merges after the seal.
1. **Earlier history is rewritten**, as detected by the comparison above.
1. **The Run's staged bytes exceed a cap.**

History rewrites happen while a model request is prepared. Today's model boundary does not hold the request for its commit, so the extra upload does not delay the request.

## Inline subagent state

Inline subagents keep executing inside the parent's tool call. Only their storage changes:

- When a child run ends, the Harness saves its state through a Host-provided save/load binding and records a registry entry in the parent's `a13n.subagents` namespace: child instance ID, subagent name, definition ID, child Thread ID, and the stored state reference. The saved state omits `a13n.usage`; continuing a child never restores it.
- `resume_subagent` loads the referenced state and validates its definition at that point. An incompatible child fails that resume, not every later Run of the parent Thread.
- Fork rewrites the Thread IDs in registry entries. A child's stored state is forked when it is next loaded.
- Registry entries always hold references. Without a Host store, the Harness default binding keeps saved states inside Harness state.

The Service stores each saved child state as an object written once and never deleted individually, so registries in earlier Runs' final states keep resolving.

Asynchronous subagents are already separate Threads and Runs. Their own states and visible items follow this proposal as ordinary Runs. The parent learns about them through the existing `subagent_info`, `wait_subagent`, and `child_result` delivery.

## Change-detection cost

The first version encodes every message once per boundary, replacing the whole-history encoding that `export_state` performs today. On synthetic histories, encoding takes about 9 ms per MB and comparison less than 0.5 ms; today's boundary already encodes the history more than once ([measurements](04-validation-and-rollout.md#measurements)).

Encoding only changed messages requires a Harness invariant: history message objects are never edited in place, so an unchanged object identity implies unchanged bytes. That needs the self-healing transformation to copy before editing, a guard test, and verification against complete encoding at every merge. Whether the first version adopts this is an [open question](04-validation-and-rollout.md#open-questions).
