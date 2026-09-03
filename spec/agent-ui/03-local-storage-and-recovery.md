# Local Storage and Recovery

## Design Position

Agent UI keeps persistence continuation-oriented:

1. editable YAML and Markdown files own desired resources and global defaults;
2. SQLite owns accepted-generation indexes, Project/resource lookup projections, sticky Thread configurations, execution heads, and selected references;
3. immutable content-addressed files own normalized configuration generations, resolved Run compositions, and complete continuation checkpoints;
4. live runtime objects remain in process memory.

The store supports local restart and inspection, not durable work scheduling. Agent UI does not persist a root input queue, Run-attempt ledger, renewable execution claim, worker assignment, effect journal, shell-process record, or delivery ledger.

## Persisted Values

| Value                                                                                             | Storage                      | Authority                                                         |
| ------------------------------------------------------------------------------------------------- | ---------------------------- | ----------------------------------------------------------------- |
| Desired resource definitions and global defaults                                                  | YAML and Markdown files      | Human-editable desired behavior                                   |
| Accepted configuration generation and resource indexes                                            | SQLite plus immutable object | Current complete validated file generation                        |
| Thread metadata head, sticky configuration head, and initial-state reference                      | SQLite                       | Identity, mutable presentation, defaults, and first-Run bootstrap |
| Empty initial `HarnessState`                                                                      | Immutable object             | Harness-generated Thread identity before any selected Run         |
| Resolved Run composition                                                                          | Immutable object             | Exact behavior and dependency provenance captured for one Run     |
| Root or child continuation bundle                                                                 | Immutable object             | Exact selected `HarnessState` resume authority                    |
| Child execution heads                                                                             | SQLite                       | Segment correlation, saved status, and selected checkpoint        |
| Compact child display                                                                             | Immutable child checkpoint   | Inspection history only                                           |
| Environment-state references                                                                      | SQLite plus immutable files  | Current Host-authoritative state                                  |
| Root receipts, active tasks, Models, credentials, clients, adapters, streams, and shell processes | Process memory               | Current App only                                                  |
| Logs and OpenTelemetry                                                                            | Configured process outputs   | Diagnostics only                                                  |

## SQLite Contract

SQLite uses WAL, foreign keys, a bounded busy timeout, UTC-aware persistence values, and short transactions. A transaction never spans Agent execution, model or tool I/O, Environment operations, a wait, a sleep, configuration-file mutation, or live streaming.

The conceptual groups are:

| Group            | Representative facts                                                                                                                    |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Configuration    | Accepted generation digest, source/resource digests, and safe diagnostics                                                               |
| Threads          | Identity, parent, metadata version and values, initial state, sticky configuration version, exact selections, and selected continuation |
| Child executions | Execution ID, child Run ID, segment index, saved status, composition, checkpoint, and failure                                           |
| Environments     | Complete Thread/configuration/root binding key and current state reference                                                              |

Resource lookup rows are rebuildable projections of the accepted file generation. They accelerate queries but never authorize edits or survive as an alternate resource definition when the owning file is removed.

One App serializes root admission per Thread and state changes per child execution. Separate local App processes can open the same store, but they do not share root receipts, active tasks, or control and do not take over one another's executions. Thread metadata and configuration updates compare their independent expected integer versions; continuation, child checkpoint, accepted generation, and Environment state selection compare expected references. A mismatch fails explicitly and never overwrites the newer head.

Agent UI does not use process lock files, PID inspection, heartbeats, or time-based leases to infer whether another App is alive. Current execution ownership is process-local.

## Thread Metadata Head

Each Thread has one mutable metadata head containing `version`, nullable `title`, and `archived`. A title/archive mutation compares its required expected version, changes both supplied fields in one short transaction, and increments the version once. A no-op can retain the current version. Metadata changes update Thread recency independently from configuration and continuation selection.

Metadata compare-and-select prevents a stale surface from silently overwriting a title or archive change. Process-local active status is not stored in this head. Root archive admission additionally checks current-process activity; another process remains an ordinary concurrent writer and is handled by the metadata version rather than a liveness protocol.

## Thread Configuration Head

Each root or child Thread has one mutable configuration head:

```python
class ThreadConfiguration(BaseModel):
    version: int
    project_id: str
    agent_source: AgentSource
    environment_profile_id: str
    harness_plugin_ids: tuple[str, ...]
    environment_run_extension_ids: tuple[str, ...]
    mcp_server_ids: tuple[str, ...]
```

`agent_source` is the discriminated Agent-resource or Markdown-subagent reference owned by [Projects, Threads, and Environments](04-projects-threads-and-environments.md#sticky-thread-configuration). The lists are exact ordered enabled selections. Omission belongs only to create or patch input; the stored head contains no inheritance marker. Every non-empty update compares the caller's required expected version, commits all changed axes, and increments `version` atomically. A no-op can retain the version.

An admitted Run records the Thread configuration version and accepted generation it captured. A later update is valid and affects only later admissions.

## Immutable Publication

Immutable values are canonical, bounded, typed, and content-addressed. Publication follows:

1. serialize and validate the complete payload;
2. write a uniquely named file under the same storage root;
3. atomically publish the digest-derived destination;
4. select its digest in a later short SQLite transaction.

There is no cross-store transaction. A crash can leave an unreferenced object, but no selected reference intentionally points to an unpublished object.

## Run Composition and Continuation

A resolved Run composition captures one complete effective configuration without live collaborators. The root or child continuation produced by that Run records its composition reference:

```python
class StoredContinuation(BaseModel):
    schema_version: str
    harness_release: str
    run_composition: ObjectRef
    harness_state: HarnessState
    deferred_requests: DeferredToolRequests | None
    created_at: datetime
```

The composition reference explains which Agent, Project roots, Capability, Plugin, MCP, Provider, and Run Extension behavior produced the checkpoint. It does not constrain the next Run to use the same composition.

Deferred requests are stored only as part of the complete suspended continuation. Surface projections use the selected continuation digest as an opaque continuation ID and never expose the object reference or native request value. A deferred response compares that exact selected reference and reconstructs its complete native request/result pair in memory.

At an acceptable complete or suspended result, Agent UI publishes the continuation and compare-and-selects it against the reference loaded at admission. Publication or selection failure leaves the prior or concurrently selected continuation current. Root receipts, input, partial output, live AG-UI events, Environment files, and child display never synthesize a continuation.

## Child Threads and Execution Segments

A logical async child is an ordinary Thread with a `parent_thread_id`. `delegate` creates the child and segment zero. `resume_subagent` keeps `thread_id`, applies an optional sticky configuration patch, creates a new `child_run_id`, increments `segment_index`, and starts from the selected child `HarnessState`.

```python
class ChildExecutionHead(BaseModel):
    execution_id: str
    parent_thread_id: str
    child_thread_id: str
    child_run_id: str
    segment_index: int
    run_composition: ObjectRef
    status: Literal["running", "succeeded", "failed", "cancelled", "lost"]
    selected_checkpoint: ObjectRef | None
    resumed_from: str | None
    failure: SafeFailure | None
```

The composition identifies the Agent definition used by that segment. It is historical provenance, not a permanent child-definition compatibility gate. A later child segment can use a different Agent, Capability, Plugin, MCP, Project, or Environment profile selection while continuing the same child Harness history.

A saved `running` status means only that no terminal checkpoint or explicit local loss was selected. It is not evidence that a process, task, or connection remains alive. A surface view therefore reports persisted status separately from current-process activity and available actions. Another App can inspect saved projections but cannot steer, cancel, wait on, or take over a vanished local execution.

### Child Checkpoint

A child checkpoint contains the exact execution correlation, Run composition reference, `HarnessState`, bounded compact display, terminal fact, and creation time. Terminal publication follows:

1. finish the child Run and Environment cleanup;
2. construct and validate the complete terminal checkpoint;
3. publish the immutable checkpoint;
4. select the checkpoint and terminal execution status in one short transaction;
5. acknowledge terminal completion.

An execution is not `succeeded` until step 4 commits. A checkpoint failure never promotes an earlier progress checkpoint to terminal success.

### Compact Child Display

The display includes bounded closed assistant text, non-encrypted thinking, completed Tool summaries, failure, and completion metadata. It excludes open content, running Tool calls, encrypted reasoning, unrelated custom events, and duplicate terminal output. It is inspection authority only and cannot reconstruct `HarnessState`.

## Environment State

Current Environment state is selected independently from continuation. Its complete private key contains:

```text
Thread ID
+ Environment profile identity and normalized profile digest
+ Host adapter key
+ normalized Project root path
```

The profile digest reuses the accepted generation's canonical normalized content for Provider and Host-adapter behavior and excludes source formatting and display-only fields. Switching Project, Provider, or behavior configuration cannot retarget another state entry. Removing a root or changing the selected Environment profile leaves its prior state dormant; selecting the same compatible profile identity, behavior, and root later can reuse it. Changed state uses expected-reference selection and never silently overwrites a concurrent newer value.

## Recovery

Startup validates retained values lazily and reloads the file configuration independently. It does not restore root receipts, replay root input, restart a child segment, reconnect shell processes, infer process liveness, or manufacture a checkpoint from display.

A Thread resumes from its selected continuation using its current sticky configuration unless the next admission applies a patch. A Thread with no selected continuation starts its first Run from the immutable empty `HarnessState` created with `HarnessState.new()` when the Thread was inserted. The generated Harness `thread_id` is the Agent UI Thread ID. If selected resources are missing from the current accepted generation or cannot reconstruct against installed dependencies, the Run fails before dispatch; recovery does not fall back to the composition that produced the prior continuation.

| Last stored fact                      | Recovery behavior                                                               |
| ------------------------------------- | ------------------------------------------------------------------------------- |
| No new continuation selected          | Resume the prior selected continuation                                          |
| Continuation object published only    | Resume the prior selected continuation                                          |
| Child nonterminal checkpoint selected | Retain for inspection; do not infer liveness, replay, or permit linked resume   |
| Child terminal checkpoint selected    | Serve the saved terminal view and permit policy-authorized linked resume        |
| Selected object missing or invalid    | Fail read or resume explicitly                                                  |
| Current Thread resource missing       | Preserve Thread and checkpoint; reject the next Run until configuration changes |

External model, tool, and Environment effects can be unknown and may repeat after explicit retry or linked resume.

## Failure Semantics

| Failure                                         | Outcome                                                             |
| ----------------------------------------------- | ------------------------------------------------------------------- |
| Immutable serialization or publication fails    | No selected reference changes                                       |
| SQLite selection fails after object publication | Prior selected head remains current; object is unreferenced         |
| Thread configuration version conflicts          | Stale patch is rejected without partial selection changes           |
| Root process exits during a Run                 | Prior continuation remains current                                  |
| Child process exits abruptly during a segment   | Saved nonterminal head remains; no liveness or takeover is inferred |
| Child terminal checkpoint cannot be selected    | Execution is not reported as succeeded                              |
| Live delivery fails                             | Saved heads are unaffected                                          |

## Invariants

01. Files own desired resources; SQLite owns accepted projections and mutable runtime heads.
02. Thread configuration is sticky, exact, versioned, and replaceable between Runs.
03. Thread metadata and configuration are independent versioned heads.
04. Every admitted Run has one immutable resolved composition.
05. A continuation records but is not permanently bound to its producing composition.
06. Root receipts, input, and active Runs are not durable work records.
07. Deferred response authority is the exact selected suspended continuation.
08. Compact display never becomes Harness continuation state.
09. Process loss never triggers implicit replay, takeover, PID inspection, heartbeat, lease, or lock-file recovery.
10. Transactions remain short and outside file or external execution I/O.
