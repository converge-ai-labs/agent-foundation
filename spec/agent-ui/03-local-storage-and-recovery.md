# Local Storage and Recovery

## Design Position

Agent UI keeps persistence continuation-oriented:

1. editable configuration files own desired behavior;
2. SQLite owns compact mutable indexes and selected heads;
3. immutable content-addressed files own resolved snapshots and complete root or child checkpoints;
4. live runtime objects remain in process memory.

The store supports local restart and inspection, not durable work scheduling. Agent UI does not persist a root input queue, Run-attempt ledger, renewable execution claim, worker assignment, effect journal, shell-process record, or delivery ledger.

## Persisted Values

| Value                                                                              | Storage                     | Authority                                                   |
| ---------------------------------------------------------------------------------- | --------------------------- | ----------------------------------------------------------- |
| Desired configuration                                                              | YAML and canonical Markdown | Latest editable behavior source                             |
| Accepted configuration and snapshot references                                     | SQLite                      | Current selectable configuration index                      |
| Resolved Agent and Environment-profile snapshots                                   | Immutable objects           | Exact Session composition                                   |
| Session metadata and selected root continuation                                    | SQLite                      | Current root Thread head                                    |
| Root continuation bundle                                                           | Immutable object            | Exact root `HarnessState` resume authority                  |
| Child Thread and execution heads                                                   | SQLite                      | Segment correlation, last saved status, and checkpoint head |
| Child progress and terminal checkpoints                                            | Immutable objects           | Exact child `HarnessState` and compact inspection display   |
| Environment-state references                                                       | SQLite plus immutable files | Current Host-authoritative state                            |
| Active tasks, Models, credentials, clients, adapters, streams, and shell processes | Process memory              | Current App only                                            |
| Logs and OpenTelemetry                                                             | Configured process outputs  | Diagnostics only                                            |

## SQLite Contract

SQLite uses WAL, foreign keys, a bounded busy timeout, UTC timestamps, and short transactions. SQLite stores UTC values without an offset and restores them as UTC-aware `datetime` values at the persistence boundary. A transaction never spans Agent execution, model or tool I/O, Environment operations, a wait, a sleep, or live streaming.

The conceptual groups are:

| Group            | Representative facts                                                                         |
| ---------------- | -------------------------------------------------------------------------------------------- |
| Configuration    | Accepted source digest and immutable snapshot references                                     |
| Sessions         | Identity, metadata, pinned snapshots, and selected root continuation                         |
| Child Threads    | Parent root Thread, child Thread, definition digest, and ordered execution segments          |
| Child executions | Public execution ID, child Run ID, segment index, last saved status, checkpoint, and failure |
| Environments     | Complete binding key and current state reference                                             |

One App serializes root admission per Session and state changes per child execution. Separate local App processes can open the same store through ordinary SQLite behavior, but they do not share active tasks or take over one another's executions. Selection of a root continuation, child checkpoint, or current Environment state compares the reference loaded at admission with the still-current reference in the same short transaction. A mismatch records an explicit concurrency failure and never overwrites the newer head. This optimistic check protects retained truth without claiming that external effects were serialized or rolled back.

Agent UI does not use process lock files, PID inspection, heartbeats, or time-based leases to infer whether another App is alive. Current execution ownership is known only from process-local runtime state. A different App can read saved child projections but cannot steer, cancel, wait on, or resume a segment merely because it can open the same data root.

## Immutable Publication

Immutable values are canonical, bounded, typed, and content-addressed. Publication follows:

1. serialize and validate the complete payload;
2. write a uniquely named file under the same storage root;
3. atomically publish the digest-derived destination;
4. select its digest in a later short SQLite transaction.

There is no cross-store transaction. A crash can leave an unreferenced object, but no selected reference intentionally points to an unpublished object. Atomic publication prevents partial files and does not claim device-level durability.

## Root Continuation

One root bundle contains every Harness value required to continue a Session boundary:

```python
class StoredSessionContinuation(BaseModel):
    schema_version: str
    harness_release: str
    harness_state: HarnessState
    deferred_requests: DeferredToolRequests | None
    created_at: datetime
```

At an acceptable complete or suspended root result, Agent UI publishes the object and then compare-and-selects the Session reference against the continuation loaded for that Run. Publication or selection failure leaves the prior or concurrently selected continuation current. A published but unselected object is harmless.

Root input, partial output, live AG-UI events, Environment files, and child display never synthesize a continuation. A suspended root continuation retains its exact deferred requests in the same bundle.

## Child Threads and Execution Segments

One logical async child is one child Thread. `delegate` creates the Thread and segment zero. `resume_subagent` keeps `child_thread_id`, creates a new `child_run_id`, increments `segment_index`, and starts from the exact selected child `HarnessState`.

```python
class ChildExecutionHead(BaseModel):
    execution_id: str
    session_id: str
    parent_thread_id: str
    child_thread_id: str
    child_run_id: str
    segment_index: int
    child_definition_digest: str
    status: Literal["running", "succeeded", "failed", "cancelled", "lost"]
    selected_checkpoint: ObjectRef | None
    resumed_from: str | None
    failure: SafeFailure | None
```

`execution_id` is the bounded public selector returned to the model and surfaces. It is not a credential, storage path, or Provider identity. Segment indexes are contiguous within one child Thread. A resumed segment names the immediately preceding retained execution and never changes the child definition identity. `child_run_id` identifies the latest Harness Run inside the segment; an internally denied deferred request can advance it while preserving the execution ID and segment index. Every checkpoint records the exact Run that produced its state.

A saved `running` status means only that no terminal checkpoint or explicit local loss was selected. It is not evidence that an operating-system process, task, or connection is still alive. Live presentation comes from the current App's process-local execution registry and live hub. An orderly App shutdown requests cancellation: a durably completed cancellation becomes `cancelled`, while a segment that cannot reach a terminal checkpoint becomes `lost`. An abrupt process exit can leave a saved nonterminal head whose liveness is unknown. Agent UI does not silently convert that ambiguity into takeover or replay.

### Child Checkpoint

A child checkpoint contains:

```python
class StoredChildCheckpoint(BaseModel):
    schema_version: str
    harness_release: str
    execution_id: str
    child_thread_id: str
    child_run_id: str
    segment_index: int
    harness_state: HarnessState
    display: CompactChildDisplay
    terminal: bool
    created_at: datetime
```

`HarnessState` is the only resume authority. `display` is the bounded inspection and rendering authority. It cannot reconstruct execution state.

The operator may publish a progress checkpoint only at a natural complete Harness boundary where the supplied `HarnessState` is internally resumable. Terminal publication follows this order:

1. finish the child Run and Environment cleanup;
2. construct and validate the complete terminal checkpoint;
3. publish the immutable checkpoint;
4. select the checkpoint and terminal execution status in one short transaction;
5. acknowledge terminal completion to waiters and live surfaces.

An execution is not `succeeded` until step 4 commits. Checkpoint publication or selection failure produces explicit `failed` status when that failure can be committed and makes that execution non-resumable. A previously selected progress checkpoint remains readable for inspection only; it is not promoted to terminal success and cannot authorize `resume_subagent` after the failed terminal attempt.

### Compact Child Display

The display is a compact sequence of closed AG-UI activity derived from one observer per child segment. It includes only:

- closed assistant text;
- closed non-encrypted thinking text;
- completed Tool summaries with aggressively truncated and redacted arguments/results;
- bounded failure and completion metadata.

It omits open text or thinking, running Tool calls, encrypted reasoning, unrelated custom events, and raw `RUN_FINISHED.result`. The final answer is the final closed text activity, so clearing the terminal result avoids duplicate output.

Per-item and total bounds are fixed Agent UI policy. Model-facing child queries expose no output paging. CLI and WebUI can group segments by `child_thread_id` and combine saved display with current-process closed live activity.

## Recovery

Startup validates retained values lazily. It does not replay root input, restart a child segment, reconnect shell processes, infer process liveness, or manufacture a checkpoint from display.

A root Session resumes from its selected root continuation. A child execution resumes only through explicit `resume_subagent` from a successful terminal execution with a selected compatible terminal child checkpoint whose head remains resumable. A saved nonterminal or `lost` segment is inspectable but is not resumable or controllable after its process-local runtime disappears. Failed terminal persistence makes the execution non-resumable even when an earlier progress checkpoint remains selected for inspection.

| Last stored fact                       | Recovery behavior                                                                |
| -------------------------------------- | -------------------------------------------------------------------------------- |
| No new root continuation selected      | Resume the prior root continuation                                               |
| Root object published but not selected | Resume the prior root continuation                                               |
| Child nonterminal checkpoint selected  | Retain it for inspection; do not infer liveness, replay, or permit linked resume |
| Child terminal checkpoint selected     | Serve the saved terminal view and permit compatible linked resume when allowed   |
| Selected object missing or invalid     | Fail opening or resume explicitly; never reconstruct from display                |

External model, tool, and Environment effects can be unknown and may repeat after explicit retry or linked resume.

## History and Live Presentation

Root retained history comes from the selected root `HarnessState.message_history`. Child retained history comes from selected compact display checkpoints. Agent UI does not persist a second root transcript or treat its live hub as recovery authority.

The live hub can keep a bounded process-memory ring. A reconnect loads saved root or child projections and then follows current live events. Missed or duplicate transient delivery does not change selected continuation or checkpoint truth.

## Failure Semantics

| Failure                                         | Outcome                                                                      |
| ----------------------------------------------- | ---------------------------------------------------------------------------- |
| Immutable serialization or publication fails    | No selected reference changes                                                |
| SQLite selection fails after object publication | Prior selected head remains current; object is unreferenced                  |
| Root process exits during a Run                 | Prior root continuation remains current                                      |
| Child process exits abruptly during a segment   | Saved nonterminal head remains; no liveness, takeover, or replay is inferred |
| Child terminal checkpoint cannot be selected    | Execution is not reported as succeeded                                       |
| Live delivery fails                             | Saved heads are unaffected                                                   |
| Selected object is missing or corrupt           | Read or resume fails explicitly                                              |

## Invariants

1. SQLite stores compact mutable heads; immutable files store complete checkpoints and snapshots.
2. Root input and active root Runs are not durable work records.
3. Each child segment has one execution ID, one index, a latest child Run ID, and one selected checkpoint head.
4. A saved `running` child status is a nonterminal persistence fact, not proof of process liveness.
5. Terminal child success follows terminal checkpoint acknowledgement.
6. Compact display never becomes Harness continuation state.
7. Process loss never triggers implicit replay, takeover, PID inspection, heartbeat, lease, or lock-file recovery.
8. Transactions remain short and outside external or Agent execution.
9. Presentation delivery never changes persistence truth.
