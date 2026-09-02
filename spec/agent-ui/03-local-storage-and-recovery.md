# Local Storage and Recovery

## Design Position

Agent UI keeps persistence continuation-oriented:

1. editable configuration files own desired behavior;
2. SQLite owns compact mutable indexes and selected heads;
3. immutable content-addressed files own resolved snapshots and complete root or child checkpoints;
4. live runtime objects remain in process memory.

The store supports local restart and inspection, not durable work scheduling. Agent UI does not persist a root input queue, Run-attempt ledger, renewable execution claim, worker assignment, effect journal, shell-process record, or delivery ledger.

## Persisted Values

| Value                                                                              | Storage                       | Authority                                                     |
| ---------------------------------------------------------------------------------- | ----------------------------- | ------------------------------------------------------------- |
| Desired configuration                                                              | YAML and canonical Markdown   | Latest editable behavior source                               |
| Accepted configuration and snapshot references                                     | SQLite                        | Current selectable configuration index                        |
| Resolved Agent and Environment-profile snapshots                                   | Immutable objects             | Exact Session composition                                     |
| Session metadata and selected root continuation                                    | SQLite                        | Current root Thread head                                      |
| Root continuation bundle                                                           | Immutable object              | Exact root `HarnessState` resume authority                    |
| Child Thread and execution heads                                                   | SQLite                        | Current segment, status, correlation, and selected checkpoint |
| Child progress and terminal checkpoints                                            | Immutable objects             | Exact child `HarnessState` and compact inspection display     |
| Environment-state references and cleanup bookkeeping                               | SQLite plus immutable objects | Current Host-authoritative state                              |
| Active tasks, Models, credentials, clients, adapters, streams, and shell processes | Process memory                | Current App only                                              |
| Logs and OpenTelemetry                                                             | Configured process outputs    | Diagnostics only                                              |

## SQLite Contract

SQLite uses WAL, foreign keys, a bounded busy timeout, and short transactions. A transaction never spans Agent execution, model or tool I/O, Environment operations, a wait, a sleep, or live streaming.

The conceptual groups are:

| Group            | Representative facts                                                                                                  |
| ---------------- | --------------------------------------------------------------------------------------------------------------------- |
| Configuration    | Accepted source digest and immutable snapshot references                                                              |
| Sessions         | Identity, metadata, pinned snapshots, fork lineage, and selected root continuation                                    |
| Child Threads    | Parent root Thread, child Thread, definition digest, and ordered execution segments                                   |
| Child executions | Public execution ID, child Run ID, segment index, status, selected checkpoint, failure, and process owner correlation |
| Environments     | Complete binding key, current state reference, and cleanup/prune facts                                                |

Session deletion cascades its child Thread and execution indexes. Immutable objects become unreferenced and are removed only by explicit offline cleanup.

One App serializes root admission per Session and state changes per child execution. Separate local App processes can open the same store through ordinary SQLite behavior, but they do not share active tasks or take over one another's executions. Selection of a root continuation or current Environment state compares the selected reference loaded at admission with the still-current reference in the same short transaction. A mismatch records an explicit concurrency failure and never overwrites the newer head. This optimistic check protects retained truth without claiming that external effects were serialized or rolled back. A process that cannot establish current owner authority serves only the saved child projection and does not steer, cancel, or resume a running foreign execution.

## Immutable Publication

Immutable values are canonical, bounded, typed, and content-addressed. Publication follows:

1. serialize and validate the complete payload;
2. write a uniquely named file under the same storage root;
3. atomically replace the digest-derived destination;
4. select its digest in a later short SQLite transaction.

There is no cross-store transaction. A crash can leave an unreferenced object, but no selected reference intentionally points to an unpublished object. Atomic replacement prevents partial files and does not claim device-level durability.

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

Startup validates retained values lazily. It does not replay root input, restart a child segment, reconnect shell processes, or manufacture a checkpoint from display.

A root Session resumes from its selected root continuation. A child execution resumes only through explicit `resume_subagent` from a selected compatible child checkpoint whose execution head is still marked resumable. Failed terminal persistence makes the execution non-resumable even when an earlier progress checkpoint remains selected for inspection.

An execution persisted as `running` whose process owner is confirmed gone becomes `lost` before it is reported as resumable or terminal. Agent UI does not take over or replay that segment. If another local process can still own it, the observing process keeps the saved running projection read-only until owner loss is established.

Each App instance creates a concise `app_instance_id`, acquires the corresponding OS-held exclusive lock under the data root before child admission, and holds it until the local store closes. On startup, an App attempts the corresponding lock for each foreign instance that still owns a `running` head. It marks that instance's still-running heads `lost` only while it holds the foreign lock; a lock held by another App proves only that owner may still be live and leaves its heads unchanged. Instance-ID-derived filenames are hashed rather than treated as paths. These files establish same-host process liveness only: they are locks, not renewable execution leases, worker claims, or a distributed failover protocol.

| Last stored fact                                       | Recovery behavior                                                                           |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| No new root continuation selected                      | Resume the prior root continuation                                                          |
| Root object published but not selected                 | Resume the prior root continuation                                                          |
| Child progress checkpoint selected, segment owner lost | Mark segment `lost`; retain checkpoint for inspection and explicit compatible resume policy |
| Child terminal checkpoint selected                     | Serve the saved terminal view and permit compatible linked resume when allowed              |
| Selected object missing or invalid                     | Fail opening or resume explicitly; never reconstruct from display                           |

External model, tool, and Environment effects can be unknown and may repeat after explicit retry or linked resume.

## History and Live Presentation

Root retained history comes from the selected root `HarnessState.message_history`. Child retained history comes from selected compact display checkpoints. Agent UI does not persist a second root transcript or treat its live hub as recovery authority.

The live hub can keep a bounded process-memory ring. A reconnect loads saved root or child projections and then follows current live events. Missed or duplicate transient delivery does not change selected continuation or checkpoint truth.

## Cleanup and Export

- deleting a Session first prevents new root admissions, cancels process-local root and child work it owns, attempts required Environment cleanup, then deletes Session and child indexes;
- cleanup failure is reported and retains enough Environment state for explicit retry or prune;
- unreferenced immutable objects are removed only by an explicit offline cleanup;
- export includes Session metadata, pinned snapshots, selected root continuation, and selected child checkpoints;
- credentials, live Environment authority, process objects, and transient events are excluded.

## Failure Semantics

| Failure                                         | Outcome                                                                        |
| ----------------------------------------------- | ------------------------------------------------------------------------------ |
| Immutable serialization or publication fails    | No selected reference changes                                                  |
| SQLite selection fails after object publication | Prior selected head remains current; object is unreferenced                    |
| Root process exits during a Run                 | Prior root continuation remains current                                        |
| Child process exits during a segment            | Persisted segment becomes `lost` when owner loss is established                |
| Child terminal checkpoint cannot be selected    | Execution is not reported as succeeded                                         |
| Live delivery fails                             | Saved heads are unaffected                                                     |
| Cleanup fails after a selected checkpoint       | Selected checkpoint remains current; cleanup failure is reported independently |
| Selected object is missing or corrupt           | Read or resume fails explicitly                                                |

## Invariants

1. SQLite stores compact mutable heads; immutable files store complete checkpoints and snapshots.
2. Root input and active root Runs are not durable work records.
3. Each child segment has one execution ID, one index, a latest child Run ID, and one selected checkpoint head.
4. Terminal child success follows terminal checkpoint acknowledgement.
5. Compact display never becomes Harness continuation state.
6. Active child loss never triggers implicit replay or takeover.
7. Transactions remain short and outside external or Agent execution.
8. Presentation delivery never changes persistence truth.
