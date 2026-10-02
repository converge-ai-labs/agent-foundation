# State, checkpoints, and recovery

> Discussion proposal, not the current specification. See [status and scope](README.md).

## What a checkpoint contains

A checkpoint is a complete, bounded serialized continuation value at one committed event position. It includes the active model context and the state needed to continue execution; it is not a full Session history.

```text
checkpoint
  run_id, through_seq
  harness
    thread_id
    message_history                  # current bounded model context
    agent_context_state              # bounded continuation namespaces
    environment_states               # bounded recovery descriptors, not files
  service
    binding_reference                # the Run's immutable accepted selection
    pending_calls                    # bounded call/child references and outcomes
    pending_approval NULL
    consumed_input_progress
    memory_cursors
    execution_phase
```

This sketches the proposed serialized contents rather than a second definition of native Pydantic AI messages. Message values retain a validated canonical representation. Control metadata, pending tool outcomes, and Capability state also count toward the byte budget, even when they are not sent to the model.

Exclude full Usage records or accumulator state, full child Harness state, Display history, trace payloads, and historical task or message collections that no longer affect continuation. A completed child's state remains in its own Thread. A parent retains the result it actually incorporates into its active model context and any bounded references needed to consume that result.

Model context limits alone do not establish a checkpoint byte limit. Enforce a serialized byte cap across the entire checkpoint, with per-namespace and pending-payload bounds. Compact the active context when appropriate, externalize supported large immutable payloads, and fail clearly if required continuation state still cannot fit. Do not silently discard model-required state or use an unlimited attachment as a hidden state extension.

## Selecting a state base

At acceptance, each Run records an immutable `base_state` reference. Normally it selects the terminal state position of the previous completed Run in its Thread; an initial Run selects an explicit empty or imported seed. A fork selects a specific supported source position. No recovery operation looks up “whatever the Thread head is now” to decide a previously accepted Run's base.

Resolve that source position from its published checkpoint and remaining source events. If the previous final checkpoint is still pending, its journal remains sufficient to reconstruct the source state. If necessary, follow the fixed predecessor references back to an available checkpoint or the initial seed, then replay forward. Count the whole required replay against the recovery budget; a chain of short Runs must not evade checkpoint thresholds. Materialize an additional checkpoint before further execution when that budget would otherwise be exceeded.

The new Run's `run.accepted` event records how its bounded initial state is selected: carry compatible conversation and Capability continuation data, reset Run-local pending work, and use the newly frozen bindings. Carrying incompatible private state across an agent change is rejected unless the Capability explicitly supports that transition. The proposal does not introduce an automatic state-conversion framework.

A published checkpoint in the current Run supersedes the need to traverse predecessors for recovery at or after its position. Earlier snapshots and base references stay reachable for historical inspection. A Run that fails or is cancelled does not become the next Run's base automatically.

## When to make a checkpoint

Use three triggers:

| Trigger                                                       | Why                                                                          |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Enough state-changing events or replay bytes have accumulated | Bound recovery work without uploading a snapshot after every event           |
| Active-context compaction has committed                       | Establish a convenient new base for the smaller context                      |
| The Run has completed, failed, or been cancelled              | Materialize its terminal state and a convenient continuation/inspection base |

Threshold checks apply at a validated continuation boundary. For discussion, start evaluation with 32 state-changing events and an independent replay-byte budget. These are tuning inputs, not benchmarked defaults. Bound the work between boundaries too; reaching a threshold cannot permit an unlimited tool result or an arbitrarily growing in-memory state.

Ordinary worker handoff or durable waiting commits its necessary journal and continuation descriptor. Neither forces a full snapshot every time. A Run's age alone does not require another copy of unchanged state. Usage-only or presentation-only events do not count as state mutations.

Checkpoint requests record a target position. A writer claims a bounded materialization lease and uses a detached state capture known to cover exactly that committed target, or reconstructs it from the journal. Object upload occurs outside PostgreSQL; publication validates that lease, inserts the checkpoint catalog entry, and advances the latest pointer monotonically in a short transaction. A snapshot must never claim to cover events that have not committed, and it must not accidentally include later in-memory mutations. Competing identical targets arbitrate through the unique catalog key and must agree on the canonical state digest. An expired writer cannot later publish an orphan selected for cleanup.

Snapshot creation is materialization of committed state, not a new execution action. It can be completed by a control-owned sweep after the originating worker has gone. That sweep cannot invent state changes or resume tools. Worker-originated captures require the valid execution fence at their commit point; background reconstruction uses only already committed records.

The terminal event and outcome can commit before the terminal snapshot upload finishes. Store `terminal_state_seq` and request materialization in the sealing transaction; a control sweep can finish it after a worker crash. Until then, the last checkpoint plus the journal remains authoritative. Late accounting can extend the stream head but never advances `terminal_state_seq`.

Checkpoint generation does not trigger archival, and archival does not trigger checkpoint generation. The jobs may run concurrently and cover different positions.

## Compaction changes context, not history

Compaction records the actual replacement model context in `messages.compacted`, including any recorded summary needed for replay. Replaying that event installs the recorded result; it does not ask a model to summarize again. Its payload may reference an immutable object within the state-size limit.

A subsequent checkpoint includes that compacted context. Older journal events and older published checkpoints remain retained. As a result, Display still shows earlier messages, and a request for state before compaction can still reconstruct the earlier active context.

## Reconstructing a selected event

For Run event position `T`:

1. Select the latest retained checkpoint in that Run with `through_seq <= T`, or resolve its fixed base if there is no such checkpoint.
2. Read subsequent events through `T` with the unified journal reader.
3. Apply each registered deterministic reducer in order. Events without state effects leave the execution state unchanged.
4. Return the reconstructed persisted state with the exact covered position and whether it is a safe continuation boundary.

For a concrete example, assume a checkpoint at event 64, archived logs through event 80, and recent PG events through 100:

```text
S3 checkpoint       S3 journal                     PG journal
state through 64    events 65 ... 80               events 81 ... 100
        |                   |                              |
        +-------------------+------------------------------+
                            |
                  state through event 100
```

Suppose the relevant records are:

```json
[
  {"seq": 65, "kind": "capability.patched", "data": {"namespace": "example.counter", "changes": [{"op": "set", "path": "/value", "value": 11}]}},
  {"seq": 80, "kind": "capability.patched", "data": {"namespace": "example.counter", "changes": [{"op": "set", "path": "/value", "value": 12}]}},
  {"seq": 90, "kind": "usage.recorded", "data": {"record_id": "usage_example", "cost": "0.002"}},
  {"seq": 100, "kind": "capability.patched", "data": {"namespace": "example.counter", "changes": [{"op": "set", "path": "/value", "value": 13}]}}
]
```

The example omits the other events and common envelope fields. If the checkpoint's counter was 10, the counter at event 80 is 12 and at event 100 is 13. Event 90 records a fee but does not modify restored Harness accounting. Every omitted event must still be present and processed; the reader does not accept unexplained sequence holes.

After archival moves events 81–100 into S3 and removes their PG copies, the answer is unchanged. After checkpoint 100 is published, it can answer a state-100 request directly. Neither action permits deletion of the journal history.

## Recovery is different from inspection

The replayed value describes persisted execution state, not arbitrary Python memory or the contents of external files. It cannot undo an email, shell command, database write, or remote model request.

On Attempt recovery, fence the previous writer, read a fixed durable head, reconstruct the state, and inspect its continuation descriptors before executing anything. A `recovery.boundary` records a validated place to resume. If later durable events contain an unfinished external call, do not drop that suffix and blindly run from the earlier boundary.

| Recovered condition                                                      | Action                                                                        |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| Safe boundary with no unresolved call                                    | Resume under the new Attempt and the same frozen Run bindings                 |
| Waiting for approval or a known child Run                                | Restore the wait/reference; do not submit another child or hold a worker slot |
| Tool result committed but model-message insertion pending                | Finish the deterministic local incorporation using the recorded result        |
| External call has a supported idempotency/recovery handle                | Follow that provider's explicit recovery contract                             |
| External effect may have happened and safe recovery is unavailable       | Fail or stop for explicit resolution; do not automatically repeat it          |
| Missing checkpoint bytes, corrupt chunk, or incompatible state operation | Surface failure/unavailability; do not fabricate a partial successful restore |

An incomplete ordinary model request may be restarted from its committed input under the existing retry policy when no unresolved tool effect prevents it. Record the interruption and a fresh request identity; possible duplicate provider cost is part of the accepted limitation on unreported charges, not an exactly-once claim. Provider-native suspended generation that requires an old accounting scope is not transparently continued after scope reset. Keep the existing refusal unless a specific supported adapter supplies a correct continuation contract.

At a historical Session position, child state can be inspected separately by selecting each child's last event at or before that `session_seq`. Reconstructing a parent does not reconstruct the whole child tree. Usage history can likewise be inspected through accounting records/events without injecting it into the execution checkpoint.
