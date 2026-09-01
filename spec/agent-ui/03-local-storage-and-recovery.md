# Local Storage and Recovery

## Design Position

Agent UI keeps local persistence small and continuation-oriented:

1. editable files own desired configuration;
2. SQLite owns compact mutable indexes and current selections;
3. content-addressed files own immutable snapshots, managed Skill packages, Session continuation bundles, and Environment state;
4. live Runs, pending input, AG-UI events, subscriptions, async children, and native runtime objects stay in memory.

The store provides best-effort continuation persistence. It preserves the latest successfully selected continuation under ordinary local SQLite and filesystem behavior but does not promise that active work, partial output, or the most recent OS-buffered write survives process or machine failure. Agent UI does not expose durability profiles, run an effect journal, or emulate a distributed workflow store.

## Persisted Values

| Value                                                                                           | Storage                           | Reason                                                       |
| ----------------------------------------------------------------------------------------------- | --------------------------------- | ------------------------------------------------------------ |
| Process and product configuration                                                               | YAML/JSON/Markdown source files   | Inspectable desired state                                    |
| Accepted generation and resource index                                                          | SQLite                            | Fast local lookup of the current valid file-backed catalog   |
| Resolved Agent and Environment snapshots                                                        | Immutable object files            | Existing Sessions pin exact composition after source changes |
| Managed Skill packages                                                                          | Immutable object files            | Exact Session reconstruction after source changes            |
| Session identity, metadata, snapshot references, and latest continuation reference              | SQLite                            | Resume, list, fork, archive, and multi-Session browsing      |
| Complete continuation bundle                                                                    | Immutable object file             | Canonical restart boundary for one Session                   |
| Environment assignment and Environment state                                                    | SQLite plus immutable object file | Resume or clean up backing targets                           |
| envd executable cache                                                                           | Replaceable runtime cache         | Avoid repeated verified download                             |
| Active Runs, input, live events, async children, Environment adapters, credentials, and clients | Process memory                    | No cross-process continuation promise                        |
| Logs and OpenTelemetry                                                                          | Configured process outputs        | Diagnostics only                                             |

SQLite is not a durable input queue, Run ledger, event journal, child-job database, or exactly-once delivery store.

## Storage Topology

```text
data-root/
├── metadata.sqlite3
├── objects/
│   ├── agent-snapshots/
│   ├── environment-snapshots/
│   ├── skill-packages/
│   ├── continuations/
│   └── Environment-states/
├── runtimes/
│   └── agent-envd/<version>/<target>/agent-envd[.exe]
└── staging/
```

Configured definition roots can live elsewhere. Object paths are derived only from storage-owned kind, schema, and digest values. A Session, model, provider payload, or frontend value cannot provide an arbitrary storage path.

## SQLite Contract

SQLite runs in WAL mode with foreign keys, a bounded busy timeout, and short transactions. A transaction never spans model execution, tool execution, provider I/O, file compression, a network request, sleep, or streaming delivery.

The conceptual tables are:

| Group         | Representative facts                                                                       |
| ------------- | ------------------------------------------------------------------------------------------ |
| Configuration | accepted generation, resource revisions, source diagnostics                                |
| Composition   | immutable snapshot references and managed Skill references                                 |
| Sessions      | identity, display metadata, pinned snapshots, fork lineage, current continuation reference |
| Environments  | Session assignments, Host lifecycle status, and Environment-state reference                |

There are no tables for pending submissions, active Runs or attempts, AG-UI segments, replay cursors, Item projection watermarks, async-child jobs, steering, or parent delivery.

Session metadata, continuation references, and Environment-state references use ordinary last-write-wins updates. One Host serializes its own operations with process-local locks. If separate local processes update the same Session or resource concurrently, the last committed update becomes current; Agent UI does not add cross-process fencing, conflict detection, or merge semantics.

## Immutable Object Publication

Immutable values are canonical, typed, bounded, and content-addressed. Publication follows this sequence:

1. serialize and validate the canonical payload;
2. write a uniquely named file under the same storage root;
3. atomically replace the digest-derived destination with the staged file;
4. commit any SQLite reference in a later short transaction.

The implementation does not claim a cross-store transaction. A crash can leave an unreferenced file; it cannot intentionally commit a reference before publication. Content-addressing and decode-time validation are sufficient; publication does not need a no-replace protocol or directory synchronization.

Atomic replacement prevents partial files. It does not imply device-level durability. Agent UI need not use SQLite `synchronous=FULL`; normal local SQLite and filesystem guarantees are sufficient for best-effort continuation persistence.

## Continuation Bundle

One selected `StoredSessionContinuation` contains every Harness value needed to resume one Session boundary:

```python
class StoredSessionContinuation(BaseModel):
    schema_version: str
    harness_release: str
    harness_state: HarnessState
    deferred_requests: DeferredToolRequests | None
    created_at: datetime
```

`HarnessState` remains the canonical continuation value. `deferred_requests` contains the exact complete `DeferredToolRequests` only when the corresponding root Run suspended. `harness_release` identifies the required Harness release, and `created_at` records when Agent UI created the bundle. Keeping these values together gives the Session one self-contained continuation without a separate pending-request lifecycle.

The bundle never contains Model clients, credentials, Provider runtime collaborators, Environment adapters, entered mount facades, plugin instances, tasks, locks, AG-UI cursors, or async-child jobs.

At a complete or suspended Harness boundary:

1. the Host publishes one continuation object;
2. one SQLite transaction replaces the Session's latest continuation reference and updates Session activity time.

If publication or the database update fails, the prior continuation remains current. A published but unselected object is harmless. If another local process writes a later continuation, that later write becomes current. Startup does not scan or reclaim unreferenced objects.

## Session History and Presentation

The latest continuation's `HarnessState.message_history` is the retained conversation source for CLI and WebUI. Session metadata does not duplicate a second transcript or terminal-result record.

AG-UI values are live presentation. The Host can retain a bounded in-memory ring per Session to smooth a browser reconnect within the same process. That ring is not written to disk, is not gap-free across processes, and is discarded at shutdown.

A reconnecting frontend:

1. subscribes to the current Host's live stream;
2. queries the latest Session continuation projection;
3. renders queued live events after the snapshot;
4. refreshes when continuation selection or another Host changes the Session.

Duplicate or missed transient events can be resolved by rerendering the latest continuation. No compressed segment chain, digest linkage, retention generation, replay watermark, or rebuildable Item projection is required.

## Startup and Recovery

Startup is deliberately small:

1. open SQLite and apply package-owned migrations under a short write transaction;
2. load or accept one valid configuration generation;
3. start the selected runtime Runner;
4. validate Session snapshots, continuations, Environment state, and executable artifacts only when a command selects them.

Startup does not:

- scan every retained Session;
- mark another process's work interrupted;
- infer liveness from process generation;
- claim or quarantine another process's staging files;
- rebuild an event projection;
- replay accepted input;
- restart Runs or async children;
- persist recovery diagnostics as product state.

When a selected continuation is missing or invalid, opening or running that Session returns an explicit error. The user can delete, fork from another known continuation when available, restore a backup, or inspect the local files. Agent UI does not fabricate continuation from rendered messages or partial events.

Process interruption has four simple cases:

| Last observable storage state            | Resume behavior                                            |
| ---------------------------------------- | ---------------------------------------------------------- |
| No new continuation object               | Use the prior selected continuation                        |
| New object published but not selected    | Use the prior selected continuation                        |
| New continuation selected                | Use the new continuation                                   |
| Selected object later missing or corrupt | Session cannot resume until explicitly repaired or removed |

External model, tool, and Environment effects before continuation selection can be unknown and can repeat after retry. Agent UI reports this best-effort limitation; it does not maintain a general reconciliation journal.

## Concurrency

Multiple local Host processes can open the same data root. This does not turn Agent UI into a distributed system.

- SQLite provides ordinary transaction serialization and a bounded busy timeout.
- Immutable files use unique staging names and atomic replacement.
- Session, configuration, and Environment-state updates use last-write-wins.
- One Host uses process-local locks to avoid overlapping its own operations.
- Agent UI provides no cross-process leases, fences, conflict protocol, retry coordinator, or merge semantics.

A process generation is diagnostic correlation only. It does not grant storage authority or prove another process is dead.

## Cleanup, Export, and Delete

Cleanup remains straightforward:

- deleting a Session removes its metadata and continuation reference after process-local work stops and required Environment cleanup is attempted; cleanup failure is reported even though local deletion still completes;
- unreferenced content-addressed objects are removed only by an explicit offline cleanup while no Host is using the data root, because online deletion would race ordinary file-first publication;
- Agent UI does not perform online garbage collection, complex startup retention, lossy event compaction, archive moves, or recovery quarantine;
- export copies Session metadata, pinned snapshots, managed Skills, and the selected continuation bundle;
- import validates those values and creates a new local Session identity;
- Environment state and credentials are excluded unless an explicit provider-aware export contract is added later.

Because there is no durable event or child-job ledger, export does not need retention generations, delivery records, projection databases, or event compaction policy.

## Failure Semantics

| Failure                                     | Outcome                                                                        |
| ------------------------------------------- | ------------------------------------------------------------------------------ |
| Object serialization or validation fails    | No continuation reference changes                                              |
| Object publishes but SQLite selection fails | Prior continuation remains current; object is unreferenced                     |
| Another local process writes the Session    | The last committed continuation reference becomes current                      |
| Live AG-UI delivery fails                   | Current viewer can miss transient output; continuation selection is unaffected |
| Process exits during a Run                  | Active input and partial work disappear; prior selected continuation remains   |
| SQLite commit fails after external effects  | Prior continuation remains; effects can be unknown and may repeat              |
| Process-local cleanup fails after selection | The error reports the selected continuation; selection remains current         |
| Selected continuation is missing or corrupt | Session resume fails explicitly                                                |
| OTel or logging fails                       | Diagnostic loss only                                                           |

## Compatibility

SQLite schema, immutable object schemas, Harness continuation schema, Environment-state codec, and resolved snapshot schemas evolve independently. A package migration changes SQLite structure. Object readers either support an older schema explicitly or reject it. Agent UI never silently converts partial presentation history into a newer Harness continuation.
