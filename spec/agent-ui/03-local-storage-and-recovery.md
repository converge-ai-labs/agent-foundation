# Local Storage and Recovery

## Design Position

Agent UI uses a hybrid local store optimized for durable continuation, indexed product queries, transparent inspection, and bounded write amplification:

1. human- and agent-editable files own desired configuration;
2. one SQLite database owns mutable metadata, control state, references, and query projections;
3. immutable Zstandard-compressed files own resolved snapshots, managed Skill packages, `HarnessState`, pending root deferred requests, provider resource state, and retained AG-UI event data;
4. OpenTelemetry leaves the store through configured exporters and is never persisted in SQLite or Session object files.

The design does not claim a cross-file transaction between SQLite and the filesystem. It establishes publication ordering: an immutable file is completely written, synchronized under the selected durability profile, verified, and atomically published before a SQLite transaction can reference it. SQLite can therefore lag an already published unreferenced object, but a committed metadata row must not intentionally lead a missing object.

## Boundaries

| Concern                                                                      | Primary authority                                  | Relationship                                                          |
| ---------------------------------------------------------------------------- | -------------------------------------------------- | --------------------------------------------------------------------- |
| Desired process and product configuration                                    | Reloadable configuration files                     | SQLite stores accepted-generation indexes and diagnostics only        |
| Mutable Session, Thread, Turn, Environment, job, queue, and display metadata | SQLite                                             | Authoritative local control state and selected object references      |
| Resolved Agent and Environment snapshots                                     | Compressed immutable object files                  | SQLite and Sessions reference exact content digests                   |
| Managed Skill package revisions                                              | Compressed immutable Skill-package object files    | Agent snapshots reference exact manifests and payload digests         |
| Complete root or async-child continuation                                    | Compressed immutable `HarnessState` object files   | SQLite checkpoint/job row selects one existing verified object        |
| Pending root deferred resume authority                                       | Compressed immutable deferred-request object files | SQLite waiting root Turn selects one exact unconsumed request object  |
| Provider resource state                                                      | Compressed immutable provider-state object files   | SQLite owns lifecycle/fencing metadata and selected state reference   |
| Retained processed AG-UI sequence                                            | Compressed immutable event segments                | SQLite indexes segment ranges, cursors, Items, and search projections |
| Managed Local Sandbox envd executable cache                                  | Package manifest plus runtime cache                | Replaceable runtime material; no Session or Environment authority     |
| Live execution, tasks, streams, clients, attachments, credentials            | Process memory and owning runtime                  | Never reconstructed by reading local storage alone                    |
| OpenTelemetry                                                                | Configured OTel SDK/exporter                       | Independent diagnostic delivery; no local lifecycle authority         |
| Ordinary application logs                                                    | `a13n-logging` process boundary                    | Separate from SQLite and Session history                              |

SQLite is not a disposable cache as a whole. Some tables are authoritative control state, while resource and event indexes can be rebuilt from their owning files. Each table documents which category it belongs to; recovery never guesses SQLite-owned facts from display history.

## Storage Topology

The logical data-root layout is stable enough for backup, inspection, and recovery tooling, while exact sharding depth and temporary names remain implementation details:

```text
agent-ui-home/
├── config.toml
├── definitions/
├── metadata.sqlite3
├── objects/
│   ├── agent-snapshots/
│   ├── environment-snapshots/
│   ├── skill-packages/
│   ├── harness-states/
│   ├── deferred-requests/
│   └── provider-states/
├── sessions/
│   └── YYYY/MM/DD/<session-id>/
│       └── events/
├── runtimes/
│   └── agent-envd/<version>/<target>/agent-envd[.exe]
├── staging/
└── quarantine/
```

Configured project definition roots can live outside `agent-ui-home`; the configuration loader preserves their authority and source boundary. SQLite `-wal` and `-shm` files are ordinary sidecars, not separate logical stores.

Object paths are derived from validated kind, schema version, digest, and storage-owned sharding. A Session value, model value, API value, event field, or provider payload cannot provide an arbitrary filesystem path. Paths are local locators and grant no authority.

The `runtimes/agent-envd` tree is a separate replaceable executable cache. It is not an immutable object kind, SQLite-selected checkpoint, provider-state payload, Session export input, or source configuration root. The package-owned runtime manifest selects its exact version/target path and expected hashes; download and extraction stage under the same data root and atomically publish only verified executable bytes. Deleting a cached executable can require a later verified re-download but cannot change a Session, prove provider cleanup, or authorize execution. Ordinary orphan-object retention does not scan or delete this runtime tree.

## SQLite Metadata Store

SQLite runs in WAL mode with foreign keys enabled, bounded busy timeout, and short transactions. One transaction never spans model execution, tool execution, provider I/O, filesystem compression, sleeps, background work, or streaming delivery.

Conceptual table groups are:

| Group                         | Representative facts                                                                                | Authority                                                                         |
| ----------------------------- | --------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Configuration index           | accepted generation, resource IDs/digests, source provenance, diagnostics                           | Generation selection and diagnostics; resource content remains file-backed        |
| Sessions and Threads          | identity, title, archive/pin/order, lineage, Agent/Environment snapshot refs, control versions      | SQLite-owned                                                                      |
| Turns and checkpoints         | acceptance, state, base/selected checkpoint refs, Run correlation, terminal outcome                 | SQLite-owned lifecycle and selection; state payload file-owned                    |
| Environment resources         | provider spec ref, lifecycle state, operation fence, provider-state ref, cleanup status             | SQLite-owned lifecycle; provider payload file-owned                               |
| Async-subagent jobs and input | accepted work, exact child node, process generation, steering, terminal outcome, and delivery state | SQLite-owned lifecycle and selection; terminal child state payload file-owned     |
| Event segment index           | Session sequence ranges, segment digest/path, previous segment, projection watermark                | Rebuildable from verified event headers except mutable cursor/retention selection |
| Item and search projection    | messages, tools, child observations, previews, bounded searchable text                              | Rebuildable from AG-UI event files                                                |
| Store maintenance             | schema version, leases, and recovery/quarantine records                                             | SQLite-owned control state                                                        |

A single integer does not pretend to serialize every concern. The store uses distinct versions:

- `configuration_generation` for accepted resource catalogs;
- `session_control_version` for title, archive, pin, Agent/Environment selection, and lineage operations;
- `thread_commit_version` for accepted Turn and selected checkpoint advancement;
- `job_version` for async-subagent job outcome/delivery changes;
- `presentation_sequence` and `retention_generation` for AG-UI replay;
- `projection_watermark` for rebuildable indexing.

A command declares the exact expected version it protects. Renaming a Session does not conflict with an unrelated live event projection, while two stale Turn submissions cannot both advance one Thread checkpoint.

## Immutable Object Contract

Every immutable object is self-describing after decompression and contains:

- object kind and object-schema version;
- logical content digest;
- creation time;
- owning Session/Thread/Turn identities where applicable;
- producer release and payload codec versions needed for compatibility;
- finite typed payload.

The logical digest covers canonical uncompressed content with the envelope's own `logical_digest` field omitted from the digest input. The object is stored as a Zstandard frame with checksum enabled and a bounded decompressed size. Readers verify object kind, schema, digest, size, internal identities, and expected SQLite reference before returning a value.

Publication follows one contract:

1. serialize and validate canonical content;
2. compress into a staging file under the same storage root;
3. flush and synchronize according to the configured durability profile;
4. read and verify the staged object;
5. publish to its digest-derived final path by no-replace atomic publication;
6. synchronize the containing directory when required by the durability profile;
7. only then commit a SQLite reference.

If the final object already exists, the store verifies exact logical identity and reuses it. A collision or incompatible object at the same digest fails closed. A failure before SQLite selection leaves an unreferenced object eligible for automatic retention cleanup after the configured orphan-retention period; it does not become a selected checkpoint or provider state.

## Skill Package Objects

Each imported Skill revision publishes one compressed immutable package object before an Agent snapshot can reference it. The object contains the normalized package manifest, model-facing Skill name and description, safe import provenance, ordered relative regular-file entries, per-file digests and media metadata, and bounded file payloads. Paths are normalized relative paths; absolute paths, traversal, links, devices, sockets, and other special files are invalid.

The logical digest covers the complete canonical manifest and payload. A package object is source content, not a runnable extension: it contains no Python object, executable plugin grant, credential, Environment selector, or native Host path. Runtime reconstruction reads the verified object and a trusted Agent UI `SkillMaterializer` writes its exact files through the selected Environment's public `FileOperator`. The destination and current write authority remain run-scoped.

Changing a managed package publishes another immutable object and Skill resource revision. Agent snapshots retain every referenced package object after the editable source changes or disappears. Unreferenced package objects follow ordinary orphan retention.

## Harness State Objects

Each complete root or async-child checkpoint uses one compressed immutable state object. Conceptually, the decompressed envelope is:

```python
class RootTurnStateOwner(BaseModel):
    kind: Literal["root_turn"]
    turn_id: str


class AsyncSubagentStateOwner(BaseModel):
    kind: Literal["async_subagent"]
    subagent_job_id: str


type HarnessStateOwner = RootTurnStateOwner | AsyncSubagentStateOwner


class StoredHarnessState(BaseModel):
    object_schema_version: str
    session_id: str
    thread_id: str
    owner: HarnessStateOwner
    checkpoint_id: str
    harness_release: str
    harness_state_schema: str
    logical_digest: str
    harness_state: HarnessState
    exported_at: datetime
```

The payload contains the complete exported public `HarnessState`, including its Thread identity and Capability namespaces. It never contains model clients, provider credentials, provider attachments, an `EnvironmentRuntime`, plugin objects, tasks, locks, or presentation cursors.

Writing a state object does not select it. For a root Turn, the authoritative selected checkpoint is the SQLite checkpoint/Thread transition that references the verified object. Root terminal commit writes the object first, then uses one short SQLite transaction to validate the expected Thread commit version, register and select the checkpoint, complete the Turn, and advance the Thread.

For an async child, the authoritative selected checkpoint is the SQLite terminal job transition under `job_version`. A terminal child boundary writes the object first, then registers and selects it together with the terminal job outcome. Selecting child state never advances or replaces the root Thread checkpoint. Active child jobs retain no resumable intermediate checkpoint or waiting state.

A database failure after file publication leaves the prior root or child checkpoint selected. External model, tool, and Environment effects remain unknown where applicable; the Host never infers rollback from the unselected object.

## Deferred Request Objects

A suspended root Harness result carries complete public `DeferredToolRequests` outside `HarnessState`. Agent UI stores that exact value in a separate compressed immutable object before a root Turn can enter `waiting`. Harness child invocations never suspend, so async-subagent jobs never own this object kind:

```python
class RootTurnDeferredOwner(BaseModel):
    kind: Literal["root_turn"]
    turn_id: str


class StoredDeferredRequests(BaseModel):
    object_schema_version: str
    session_id: str
    thread_id: str
    owner: RootTurnDeferredOwner
    source_run_id: str
    agent_snapshot_digest: str
    harness_release: str
    request_codec_version: str
    request_digest: str
    tool_surface_lock: DeferredToolSurfaceLock
    requests: DeferredToolRequests
    exported_at: datetime
```

`request_digest` covers the canonical complete request value, including the exact distinction and identities of deferred calls and approvals. `tool_surface_lock` identifies the resolved Agent/tool/plugin/Capability surface required to interpret those requests; it contains no live tool or authority. The object is continuation input but is not itself `HarnessState`, an AG-UI projection, or proof that a response has been authorized.

The SQLite root waiting transition atomically selects the verified checkpoint and deferred-request objects, records their digests, preserves the request as unconsumed, and advances the Thread commit version. A response command verifies both objects, exact root Turn owner and pending identities, pinned root Agent node and snapshots, codec compatibility, and unconsumed status. Before dispatch it records one consuming `run_id` and transitions the Turn to `running`; process loss after possible dispatch becomes interrupted and never reuses the request automatically. A later suspended root result publishes and selects a new complete request object.

## Provider State and Resolved Snapshots

Resolved Agent and Environment snapshots use the same compressed immutable object contract. Their content is authority-neutral and content-addressed. Removing or changing current source configuration does not remove an object referenced by a retained Session.

Provider resource state is stored separately from Environment snapshots and `HarnessState`. Its envelope records provider key, provider state version, resource identity correlation, operation fence, and opaque provider-owned payload. SQLite owns which provider-state object is selected for one canonical Host Environment resource and which Session assignments reference it. A fresh credential and provider runtime are still required to resume, pause, inspect, or destroy it.

Provider-state objects receive owner-only local file permissions and are excluded from ordinary Session export, model-visible tools, AG-UI events, and telemetry. They contain no credential according to the Provider contract, but remain sensitive operational state.

## AG-UI Event Segments

Agent UI retains the complete post-processor AG-UI event sequence selected by its Host persistence policy as immutable `.jsonl.zst` segments. It does not write AG-UI events into SQLite payload columns.

The first decompressed JSON line is a segment header:

```python
class AguiSegmentHeader(BaseModel):
    record_type: Literal["segment_header"]
    schema_version: str
    session_id: str
    first_sequence: int
    last_sequence: int
    event_count: int
    previous_segment_digest: str | None
    logical_digest: str
    created_at: datetime
```

Subsequent lines are Host-owned records:

```python
class StoredAguiEvent(BaseModel):
    record_type: Literal["agui_event"]
    event_id: str
    presentation_sequence: int
    session_id: str
    thread_id: str
    turn_id: str | None
    run_id: str | None
    observed_at: datetime
    stream: PresentationStreamRef
    event: AguiEvent
```

`presentation_sequence` is monotonic within one Session across root and exposed child streams. `stream` preserves root/child correlation without deriving authority from presentation identity. Segments cover contiguous non-overlapping ranges and link to the prior retained segment digest. The chain detects gaps and wrong ordering; it is an integrity structure, not a tamper-proof audit log.

The Host accumulates bounded event batches, serializes each batch into an immutable compressed segment, publishes the file, and then registers its range in a short SQLite transaction. Only after registration succeeds does it fan those stored events out to process-local live subscribers. The delivery record identifies durable replay versus live-after-registration origin; either origin carries the same `event_id` and `presentation_sequence`, and neither strengthens Turn or checkpoint completion.

A subscription installs its bounded live queue and captures the current durable Session watermark under the same per-Session append lock. The caller first performs a finite replay through that watermark and then consumes live deliveries, so an append cannot fall between replay selection and live registration. Queue overflow or subscription closure terminates that live path rather than silently dropping an interior event; the client resumes finite replay after its last received sequence.

At a terminal Harness result, the coordinator observes the terminal public item and attempts to publish and register its terminal AG-UI batch before publishing the durable terminal Session projection. Event-segment registration and checkpoint selection use independent short SQLite transactions; neither waits inside the other, and no cross-store transaction is claimed. If event publication or registration fails while checkpoint selection succeeds, execution continuation remains valid, the presentation failure remains explicit, and startup recovery indexes a verified directly appendable segment or reports the missing history. It never rolls back or invents the terminal checkpoint from presentation state.

AG-UI files are presentation history, not `HarnessState`, a provider operation journal, OpenTelemetry, or an authorization log. They can reconstruct WebUI/CLI Items and protocol inspection, but cannot resume a pending tool call, recreate an async-subagent task, restore Environment authority, or prove absence of an external side effect.

## Projection and Query

SQLite projects compressed AG-UI segments into bounded query tables for:

- Session previews and last activity;
- paginated Turns and Items;
- message/tool/child summaries;
- current Item values;
- text search;
- replay cursor resolution.

Projection reads only verified complete segments. It advances segment digest, last sequence, and projection watermark in one SQLite transaction with the derived rows. Projection failure leaves the watermark behind durable event files. Startup or on-demand read repair resumes from the last verified segment; rebuild can discard only projection-owned tables and recreate them from retained segments.

The projector never writes a new checkpoint, changes a Turn outcome, delivers an async-child result, or repairs SQLite-owned Session control state. Search ranking and Item summaries are observations derived from presentation history.

## Configuration and Event File Visibility

Configuration files are readable desired state. Immutable object files are machine-oriented but intentionally use canonical JSON or JSON Lines before standard Zstandard compression, stable envelopes, compact identifiers, and documented codecs. Local tools and Agents with separately authorized filesystem access can inspect them using ordinary decompression and JSON tooling; no SQLite-internal binary encoding is required for large state or event payloads.

This inspectability grants no Host command authority. Directly editing an immutable object or SQLite file is corruption, not a supported mutation API. Product mutations flow through configuration reload or `AgentUiHost`.

## Recovery

Startup recovery proceeds before command acceptance:

01. acquire the configured application/store ownership lease;
02. open SQLite, validate schema, apply owned migrations, and verify integrity needed for authoritative tables;
03. validate the latest accepted configuration generation or accept a newer complete file generation;
04. reconcile staging files and quarantine malformed objects;
05. remove immutable objects whose file age exceeds the configured orphan-retention period and which have no durable reference;
06. verify every selected Agent snapshot, managed Skill package, Environment snapshot, provider-state reference needed for lifecycle, selected root/terminal-child checkpoint, and pending root deferred-request object;
07. verify registered AG-UI segment headers, indexes, digests, contiguous ranges, and digest linkage, then repair rebuildable projection lag;
08. discover complete unregistered event files left by file-first publication and register one only when its first sequence and previous digest directly extend the Session's current durable presentation head; process additional candidates only when each then directly extends the newly selected head;
09. retain a conflicting, forked, malformed, gapped, or otherwise non-authoritative candidate as unselected evidence and record a bounded path-free recovery diagnostic rather than guessing its authority;
10. mark prior-process `accepted` or `running` Turns and every prior-process `accepted`, `queued`, or `running` async job interrupted, while preserving a root Turn in `waiting` only when its complete checkpoint and exact deferred correlations validate;
11. publish the recovered application view.

Registered corruption and an unselected event candidate do not invalidate an otherwise verified selected `HarnessState`. They remain explicit presentation diagnostics, and finite replay fails at an affected retained range rather than skipping it. Startup records bounded path-free diagnostics for each invalid selected authority and marks the affected Session `blocked` or provider resource `unknown` before command acceptance; it does not report either as silently usable. Startup retention cleanup removes an immutable object only when no durable reference retains it and its file age exceeds the configured orphan-retention period. Recent unreferenced publications remain available across interruption, while referenced objects never expire merely because of age. Referenced missing or corrupt objects have type-specific outcomes:

| Missing or corrupt value                     | Recovery outcome                                                                                                                   |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Current source configuration                 | Candidate reload fails; last retained accepted generation can remain queryable, but new composition requiring the source is denied |
| Pinned Agent or Environment snapshot         | Affected Session fails closed for execution                                                                                        |
| Referenced managed Skill package             | Affected Agent node fails closed for reconstruction; no current source or similarly named Skill is substituted                     |
| Selected root or child `HarnessState`        | Affected Thread/job fails closed for continuation; AG-UI history is not promoted                                                   |
| Selected deferred-request object             | Waiting root Turn fails closed; identifiers or AG-UI cannot reconstruct the request                                                |
| Unselected checkpoint/deferred object        | Quarantine or remove after reference analysis; selected state is unchanged                                                         |
| Selected provider resource state             | Provider lifecycle operation fails closed; no replacement resource is silently created                                             |
| AG-UI segment with valid selected checkpoint | Continuation can remain available; replay exposes an explicit sequence gap                                                         |
| Rebuildable projection rows/database pages   | Rebuild from verified event files                                                                                                  |
| SQLite-owned control state                   | Fail closed or restore from a verified SQLite backup; event/state files do not invent titles, pins, lifecycle, or selections       |

The database and its WAL/SHM sidecars are quarantined together when corruption requires replacement. A recovery tool can scan self-describing files and produce an importable evidence report, but partial reconstruction is never silently installed as authoritative control state.

## Concurrency and Leases

One stable `AgentUiHost` process owns write coordination for a selected data root. A store lease records an unguessable process generation and heartbeat in SQLite, with platform process-liveness evidence where available. A second CLI or WebUI process either attaches through an explicitly supported local client path or reports the active owner; it does not start another writer silently. Replaceable runtime Runners do not acquire this lease and can overlap during restart.

Within the application process, one Thread has at most one advancing foreground Turn. Expected Thread commit versions are verified in SQLite before dispatch and at terminal selection. Independent Sessions can run concurrently subject to Host limits. Filesystem publication can occur concurrently for distinct digests, while SQLite transactions remain short and retry bounded busy conflicts.

Reclaiming an abandoned lease authorizes recovery inspection. It does not prove prior model, tool, Environment, or provider work stopped without side effects.

## Archive, Retention, Export, and Delete

Archive is SQLite-owned display/control metadata and does not weaken state or event references. Implementations can move compressed event trees for storage management only through a staged move plus SQLite path update and compensation/reconciliation on failure; logical identity uses digest rather than location.

Retention can delete only objects and event segments with no retained Session, Agent-snapshot/Skill-package reference, fork, selected root or terminal-child checkpoint, unconsumed root pending-deferred reference, provider lifecycle, async-child result/delivery, or export reference. Startup automatically deletes unreferenced immutable objects after the configured orphan-retention period. Business retention first removes the owning reference; the same orphan cleanup later reclaims the file. Because AG-UI segments are compressed and supply complete presentation replay, lossy compaction is explicit: it creates a new retention generation and a complete semantic snapshot plus gap metadata before removing detailed prior segments. It never changes `HarnessState` or claims byte-for-byte replay after compaction.

Export copies a consistent SQLite projection plus referenced safe Agent/Environment snapshots, every referenced managed Skill package, selected root and terminal-child `HarnessState` objects, every selected unconsumed root deferred-request object, retained async-child outcomes/delivery ledgers, and AG-UI segments according to export policy. Deferred objects receive the same integrity, codec, Agent/tool-surface lock, and content-protection checks as local resume; omitting one makes a waiting Session export invalid rather than history-only. Provider resource state, credentials, browser capabilities, and live authority are excluded by default. Import validates all envelopes and references and creates new local control records rather than trusting source paths.

Hard delete conflicts with active work, marks deletion intent, performs provider-resource cleanup according to the [Session Environment lifecycle policy](04-sessions-environments-and-state.md), and removes SQLite references. The ordinary orphan-retention cleanup later removes the unreferenced files. Local deletion does not roll back external effects.

## OpenTelemetry Separation

Agent UI configures repository-standard OpenTelemetry at the process boundary. Pydantic AI and the Harness retain ownership of their model, tool, run, and Environment spans. Agent UI adds Host spans and metrics for application commands, configuration reload, Session locking, Environment management, immutable-object publication, SQLite transactions, projection lag, replay gaps, async-subagent routing, and surface transport.

OTel records are sent to configured exporters through bounded asynchronous buffering. They are never inserted into `metadata.sqlite3`, AG-UI event segments, state objects, provider-state files, or configuration snapshots. Exporter delay, rejection, or outage cannot change command acceptance, Harness outcome, checkpoint selection, Environment lifecycle, job delivery, or shutdown correctness.

Session, Thread, Turn, Run, and safe provider correlation can appear as bounded attributes. Prompt content, model/tool payloads, AG-UI values, credentials, provider-state payloads, filesystem content, browser capabilities, and private exception text are absent by default. A required durable product or audit fact belongs in the owning metadata or event store; OTel is not an audit authority or recovery input.

## Durability Profiles

The Host declares one local durability profile governing file flush, file synchronization, directory synchronization, and SQLite synchronous behavior. All profiles preserve logical ordering and integrity; weaker profiles can lose recently acknowledged local data after device or OS failure and must expose that trade-off explicitly. Atomic rename alone never claims device-level durability.

Application acknowledgement distinguishes:

- command accepted in SQLite;
- event segment durably registered;
- the registered event observed live;
- Harness result observed;
- checkpoint selected;
- Environment state selected;
- OTel export attempted or completed.

None substitutes for another.

## Failure Semantics

| Failure                                                    | Outcome                                                                                                                           |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Compression or object validation fails                     | No SQLite reference is committed                                                                                                  |
| Object publishes but SQLite commit fails                   | Object remains unreferenced and is removed after orphan retention; prior selected metadata remains                                |
| SQLite selects a reference but later file loss is detected | Type-specific corruption outcome; no fallback fabrication                                                                         |
| AG-UI projection update fails                              | Event file remains durable; projection catches up later                                                                           |
| AG-UI event publication or registration fails              | That batch receives no live fan-out; directly appendable published evidence can recover, otherwise replay reports missing history |
| Checkpoint commit succeeds but AG-UI registration fails    | Thread can continue; presentation repair indexes verified files or reports missing history                                        |
| SQLite busy timeout expires before dispatch                | Command conflicts/fails before Harness or provider side effects                                                                   |
| SQLite commit fails after external work                    | Prior selected state remains; effect outcome is unknown and requires reconciliation                                               |
| Store lease owner disappears                               | Recovery interrupts prior active root/child execution, preserves validated waiting root boundaries, and never auto-runs           |
| OTel exporter or ordinary logging fails                    | Diagnostic loss only; product lifecycle facts are unchanged                                                                       |

## Compatibility

SQLite schema, each immutable object schema, Harness state schema, Environment provider-state version, Agent/Environment snapshot schema, AG-UI event version, segment envelope, compression codec, projection schema, and OTel semantic conventions evolve independently.

A migration never rewrites content under an existing logical digest. It writes and verifies new immutable objects before switching SQLite references. Projection schema can rebuild from retained event files. Authoritative SQLite migrations preserve control semantics or fail before command acceptance. Readers reject unknown codecs and excessive decompressed sizes before allocation.

## Trade-offs

### SQLite metadata and compressed payload files

SQLite provides efficient mutation, conflict detection, pagination, and search without storing large evolving state or event payloads in database pages. File publication introduces ordered multi-store commits and automatic orphan-retention cleanup, but state and history remain inspectable with standard Zstandard and JSON tooling.

### Immutable segments instead of one append file

Immutable compressed segments avoid in-place compressed-tail corruption and support atomic publication, digest verification, concurrent readers, and retention generations. They create more filesystem objects and require a SQLite range index.

### OTel outside local persistence

Separating telemetry prevents diagnostic volume or exporter failure from corrupting Session storage and avoids building another tracing database. Local offline telemetry search depends on the configured collector or log destination rather than the Agent UI metadata store.

## Invariants

01. SQLite stores metadata, control state, object references, and projections; it never stores complete managed Skill packages, `HarnessState`, `DeferredToolRequests`, provider resource-state payloads, or AG-UI event payloads.
02. Every referenced Skill package, selected state, deferred request, snapshot, provider-state payload, and retained AG-UI segment is an immutable verified Zstandard-compressed file.
03. A file is completely published before any SQLite transaction can select or index it.
04. There is no claimed cross-file ACID transaction; orphan files are safe and SQLite references fail closed when payloads are missing.
05. `HarnessState` remains the only Agent state authority; a waiting root Turn additionally requires its exact unconsumed `DeferredToolRequests`, async-child jobs never own deferred requests, and event/projection data can replace neither.
06. AG-UI segments own presentation replay; SQLite Item/search tables are rebuildable projections and cannot strengthen execution facts.
07. SQLite-owned mutable control facts are never guessed from AG-UI or state files after corruption.
08. OpenTelemetry and ordinary logs remain outside SQLite, state objects, and AG-UI files and never determine product completion.
09. No transaction or database session remains open across Harness execution, provider I/O, async-subagent waits, or a streaming response.
10. Retention deletes an immutable object only when no durable reference retains it and its file age exceeds the configured orphan-retention period.
11. Managed envd executables live in a separate manifest-selected runtime cache and never become immutable object, Session, provider-state, export, or retention authority.
