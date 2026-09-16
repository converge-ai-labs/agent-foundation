# Local Storage and Recovery

## Design Position

Harness UI keeps execution persistence continuation-oriented and stores published human comments separately:

1. editable YAML, MCP JSON, and local Markdown files own desired resources and global defaults;
2. data-root Content Plugin ID directories contain editable local files with optional Git provenance;
3. SQLite owns accepted-generation indexes, Project/resource lookup projections, terminal Project Model preferences, sticky Thread configurations, execution heads, selected references, and published output comments;
4. immutable content-addressed files own normalized configuration generations, resolved Run compositions, and complete continuation checkpoints;
5. shared browser drafts, live runtime objects, presence, and native terminal sessions remain in process memory.

The store supports local restart and inspection, not durable work scheduling. Harness UI does not persist a root input queue, Run-attempt ledger, renewable execution claim, worker assignment, effect journal, shell-process record, or delivery ledger.

## Persisted Values

| Value                                                                                             | Storage                       | Authority                                                                |
| ------------------------------------------------------------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------ |
| Desired resource definitions and global defaults                                                  | YAML and local Markdown files | Human-editable desired behavior                                          |
| Installed Content Plugin directories                                                              | Data-root files               | Current optional plugin availability and editable files                  |
| Thread scratch and submitted attachments                                                          | Data-root Thread directories  | Disposable working files and retained input files                        |
| Accepted configuration generation and resource indexes                                            | SQLite plus immutable object  | Current complete validated file and plugin generation                    |
| Thread metadata head, sticky configuration head, and initial-state reference                      | SQLite                        | Identity, mutable presentation, defaults, and first-Run bootstrap        |
| Empty initial `HarnessState`                                                                      | Immutable object              | Harness-generated Thread identity before any selected Run                |
| Resolved Run composition                                                                          | Immutable object              | Exact behavior and dependency provenance captured for one Run            |
| Root or child continuation bundle                                                                 | Immutable object              | Exact selected `HarnessState` resume authority                           |
| Child execution heads                                                                             | SQLite                        | Segment correlation, saved status, and selected checkpoint               |
| Compact child display                                                                             | Immutable child checkpoint    | Inspection history only                                                  |
| Environment-state references                                                                      | SQLite plus immutable files   | Current Host-authoritative state                                         |
| Published output comments and original saved target references                                    | SQLite                        | Durable human discussion; never model history or execution authority     |
| Shared browser drafts                                                                             | Process memory                | Synchronized editing state only; not execution or continuation authority |
| Participant presence and native Host terminal sessions                                            | Process memory                | Current shared instance only                                             |
| Root receipts, active tasks, Models, credentials, clients, adapters, streams, and shell processes | Process memory                | Current App only                                                         |
| Logs and OpenTelemetry                                                                            | Configured process outputs    | Diagnostics only                                                         |

## Child Inspection Results

The existing immutable child checkpoint retains bounded activity snapshots and the latest complete final answer. Activity budgets do not truncate that final answer; bounded HTTP windows paginate it for human inspection. A later failed or interrupted segment does not replace the previous complete result with an activity preview. This remains inspection state, not a separate event store or continuation authority. Older checkpoints whose answers were already truncated remain readable but cannot recover bytes that were never saved.

## Tool Presentation Evidence

Root Runs can retain observed filesystem edit evidence in the corresponding tool-return's application-only metadata, under `a13n.harness-ui.applied_edit`. It contains the observed `file_path`, `before`, `after`, and an explicit `omitted` flag. The existing selected `HarnessState` continuation serializes this metadata; it is not added to model-facing tool content, stored in a new event log, or published as a separate execution authority. Other tool metadata remains unchanged.

Association uses the exact Run and tool-call identity. The root collector retains at most 64 KiB of combined UTF-8 before/after text per edit and 512 KiB per Run. An edit beyond either bound retains its path and omission marker, not misleading truncated content. A later failed result can still carry an earlier observed edit. An event without a corresponding retained tool-return remains live-only. Existing opaque non-mapping tool metadata is preserved rather than replaced. The root display history retains captured result parts across model-context replacement. It remains checkpointed inspection content, not a permanent audit log of every event or unsaved effect.

Saved transcript projection exposes only the recognized bounded evidence through optional `applied_edit`, not arbitrary metadata. Older continuations remain readable without migration. The UI never reconstructs applied evidence from requested replacements or current Host files. Native provider tool calls and returns use the existing transcript call/result shapes with an optional provider identifier, separate from local function-call identity.

## Project Model Preferences

The App stores the last explicit terminal Model choice as one Model resource ID per Project ID in the data root's SQLite database. This is user interaction state, not a resource definition, Project YAML default, Thread configuration axis, or continuation authority. The preference can precede creation of a cwd-derived Project resource using the same deterministic identity. Resource definitions and credentials continue to resolve from the accepted configuration.

Each explicit selection atomically replaces one Project's preference; reset deletes only that Project's preference. Short SQLite write transactions serialize updates with last-write-wins semantics, including across independent Apps. Different Projects never replace each other's entries. Reads, Runs, and shutdown do not write preferences. Active terminals do not subscribe to preference changes. A removed Project or Model does not turn a preference into a resource definition, and lookup never resurrects missing resources. Downgrading past this additive table discards preferences only; it does not rewrite Threads, checkpoints, or YAML. [Interactive CLI](07-interactive-cli.md#agent-selection-and-reasoning) owns selection and fallback behavior.

## Shared Browser Drafts

Shared editing uses an in-memory CRDT document per participating root Thread, separate from sticky configuration, `HarnessState`, and root-operation receipts. Synchronization does not admit a Run, advance a continuation, or create an input queue. [Collaborative conversations](webui/01-collaborative-conversations.md) owns frontend editing and Send behavior.

A browser can reconnect to the document during the same App lifetime. Server restart restores selected conversation history, not shared drafts or presence. There is no durable draft store, save acknowledgment, CRDT update log, or draft-based submission registry. Editing state must not be submitted automatically on startup or reconnect.

Attachments reuse the existing Thread-scoped staging and retained-input lifecycle. The App protects a participating Thread's file area during its lifetime; submitting input retains referenced files independently of the document. Sharing a data root between independent App processes does not synchronize their live documents.

## Output Comment Storage

[Saved output comments](webui/05-output-comments.md) own comment semantics and source anchoring. The existing data-root `metadata.sqlite3` owns their complete durable records: comment identity, root and producing Thread association, exact saved source and text-block location, optional validated text range/quote, publication-time author attribution, body, creation time, mutation version, last-edit time, and deletion tombstone. Version-checked edits change only the publication body. Deletion removes the publication row and retains a separate identity-only tombstone to prevent retry resurrection; already captured message bytes remain independent. Older readers still see only valid publication rows. The database rejects re-insertion of tombstoned identities, including retries from older writers. Downgrade refuses to discard edited versions or deletion tombstones. Lookup indexes support Thread-scoped ordered listing and exact-target queries. Presence directories, browser tabs, and CRDT updates are not written into these records or new participant tables.

Publication reads and validates an existing saved source outside the database transaction. One short transaction checks the required selected source or existing retained comment target and inserts the complete record. The stable comment identity is unique; identical reconciliation returns the existing record and conflicting reuse fails without overwriting it. Comment publication does not increment Thread metadata/configuration versions or advance a continuation. Notification occurs only after commit and holds no database session across delivery.

Comment-held source references are retained even after the Thread selects a newer continuation. They point only to existing immutable saved output, not a new output accumulator, whole-transcript table, or a newly synthesized execution checkpoint. Referenced-object reads remain App-mediated and bounded. Scratch cleanup, archive, continuation compaction, draft clearing, and process shutdown do not delete comments or their referenced saved objects. A broken source reference fails explicitly without deleting or relocating the comment. This retention does not select the old checkpoint for execution.

The comment schema is an additive change to the package-owned local SQLite/Alembic history. Upgrade creates the comment storage and indexes with an empty collection for existing Threads; it neither rewrites checkpoints nor backfills comments from conversation text, usage, or live events. It uses the existing serialized migration transaction, single-head migration history, and short bounded lock waiting. There is no new database service, separate migration runner, or cross-database transaction.

Startup serves comment reads and writes only after a compatible schema is established. Migration failure is an explicit store-startup failure, not an in-memory-only fallback that acknowledges unsaved comments. Interrupted transactional upgrades retain the prior schema or the complete new schema. A newer revision alone does not exclude older Apps whose required storage surface remains available. Downgrade past comment storage refuses while comment records exist rather than silently deleting human discussion; forward repair or restoration of a compatible backup remains deliberate. An empty comment schema can be removed without altering Thread or checkpoint data.

## SQLite Contract

SQLite uses WAL, foreign keys, a bounded busy timeout, UTC-aware persistence values, and short transactions. A transaction never spans Agent execution, model or tool I/O, Environment operations, a wait, a sleep, configuration-file mutation, or live streaming.

The conceptual groups are:

| Group            | Representative facts                                                                                                                    |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Configuration    | Accepted generation digest, source/resource digests, and safe diagnostics                                                               |
| Threads          | Identity, parent, metadata version and values, initial state, sticky configuration version, exact selections, and selected continuation |
| Child executions | Execution ID, child Run ID, segment index, saved status, composition, checkpoint, and failure                                           |
| Environments     | Complete Thread/configuration/root binding key and current state reference                                                              |
| Output comments  | Stable comment identity, Thread family, saved target, author attribution, body, selection quote, and publication time                   |

Resource lookup rows are rebuildable projections of the accepted file generation. They accelerate queries but never authorize edits or survive as an alternate resource definition when the owning file is removed.

One App serializes root admission per Thread and state changes per child execution. Separate local App processes can open the same store, but they do not share root receipts, active tasks, or control and do not take over one another's executions. Thread metadata and Thread configuration updates compare their independent expected integer versions; continuation, child checkpoint, accepted generation, and Environment state selection compare expected references. A mismatch fails explicitly and never overwrites the newer head.

Harness UI does not use PID inspection, heartbeats, or time-based leases to infer whether another App is alive. Current execution ownership is process-local. Hard OS locks used for Thread file cleanup protect resource use only; they neither establish execution ownership nor authorize takeover.

## Compatible Upgrades Across App Versions

Multiple TUI or WebUI processes can remain open while a newer package migrates their shared data root. A database migration revision identifies applied schema changes; it is not a runtime package-version lock. An older App does not require the database revision to equal its bundled head, does not downgrade or stamp a newer revision, and can reconnect when its required tables and columns remain available. An active Run's save is not gated by a package-head comparison.

Startup upgrades revisions known to the package under the existing bounded serialized migration transaction. When the database has a single revision unknown to an older package, that package leaves the migration history unchanged and checks its required table/column surface. Additional tables and columns do not by themselves reject access. Missing required storage, ambiguous migration history, corrupt objects, and real write conflicts remain explicit errors; no in-memory acknowledgment substitutes for persistence.

Schema changes preserve the reads and writes of concurrently running supported older Apps. Compatible expansion retains existing columns, meanings, constraints, and immutable payload representations; added fields permit older writers to omit them. A migration's structural startup check is not proof of semantic compatibility. New writers must not publish payloads that supported old readers cannot interpret, and genuinely incompatible changes require an explicit compatibility transition rather than an ordinary automatic upgrade that breaks active Runs. Historical readers that predate this behavior retain their own startup limitations.

Reaccepting unchanged configuration sources reuses their existing immutable object when decoding that object produces the same complete normalized configuration. Compatible input aliases do not justify rewriting stored bytes, changing the source digest, or replacing indexes. Different normalized content under the same source digest remains an integrity error, and selecting an earlier accepted generation still requires the ordinary compare-and-select check. Harness owns legacy model-characteristic input aliases; the UI consumes them for configuration and Run composition snapshots.

An incompatible typed object emits a warning identifying its kind, object digest, schema/codec versions, and expected payload model. Diagnostics include a bounded list of schema-owned field locations and validation error types plus the total error count; arbitrary mapping keys are masked. Payload values, dynamic validation messages, and exception contexts are not logged. This evidence does not relax integrity validation or authorize deletion of the object.

Mixed-version access does not share live execution ownership or remove compare-and-select conflicts. Two Apps independently running the same Thread can still conflict on its selected continuation; schema compatibility never permits overwriting that newer continuation.

## Thread Files and Automatic Scratch Cleanup

The App owns a lazily created file area for each existing root or child Thread, keyed by its Harness-generated Thread ID. It introduces no pre-Run session identity. Under the data root, `threads/<thread-id>/tmp/` holds disposable working files and staged uploads; `threads/<thread-id>/attachments/` holds retained submitted inputs. These paths are not Project roots, continuation objects, or a separate resume authority. Runs and process restarts reuse the same Thread file area. Archive, terminal navigation, normal Run completion, and App shutdown do not delete it.

For every root and asynchronous child Run, the App supplies the current Thread mount's `tmp/tool-results/` as the Harness tool result directory. Oversized tool output never uses a Project's `.a13n/tmp/` in App-prepared Runs. These files retain the [Harness Run-private spill lifecycle](../a13n-harness/07-tool-execution.md#dispatch-retry-and-results): normal Run cleanup removes only owned leaves, while any residual files remain eligible for Thread scratch pruning. The surrounding Thread file area, ordinary scratch files, and retained inputs are unaffected.

An upload is staged under a unique Thread-scoped handle with its normalized original name, media type, and byte count. Different uploads with the same name do not overwrite one another. Submission promotes referenced files into retained storage before scheduling the Run. Promotion is idempotent for an already retained handle. Admission failure or a process interruption may leave an unreferenced retained file; cleanup favors retaining that file over deleting an input potentially referenced by execution. Submitted image bytes also enter native Harness input and selected checkpoints. Only a selected continuation restores conversation history; a retained upload does not prove that its submission completed.

Attachment metadata readers ignore additive display fields while retaining identifier, byte-count, file-type, and content-size checks. Promotion moves the unchanged metadata file rather than rewriting away unknown fields. Ordinary uploads omit absent source provenance instead of writing `source: null`; captured-source provenance retains its own validation. Invalid known metadata or changed content remains an attachment-local error, not a global configuration failure.

Automatically converted [long-text inputs](05-runtime-subagents-and-surfaces.md#long-text-input-files) use the same retained attachment storage. Conversion occurs during entered-Environment input preparation, or before steering enqueue, and retention completes before any model-visible reference is published. Selected checkpoints retain references rather than the original inline text; complete UTF-8 text remains available through the Thread attachment reader. Attachment storage alone never establishes that execution or steering delivery succeeded.

The startup janitor and an hourly App task remove only expired `tmp/` trees. The default inactivity threshold is three days, configurable through `StorageSettings.scratch_retention_seconds`. Age is measured from the App's last recorded use or release, not from archive status, PID inspection, or a Run-status guess. Each App conservatively protects every Thread file area it touches until App shutdown. Independent Apps hold independent hard OS file-use locks, allowing concurrent use without serializing their Runs. Cleanup tests those locks under a short registration gate and skips a Thread if any holder remains. Process death releases OS locks, making an old scratch tree eligible without a heartbeat timeout. Cleanup errors are diagnostic and do not prevent normal startup; there is no manual confirmation requirement.

Pruning never deletes retained attachments, SQLite rows, immutable objects, Project files, or external symlink targets. File-use lock bookkeeping is outside the deletable scratch tree. A discarded staged upload can expire; reading its old handle then fails explicitly. Surfaces do not treat a host path as a portable upload identifier or implement their own deletion policy. Important generated results belong in an explicitly chosen durable destination, not scratch storage.

## Observed Thread Usage

SQLite retains canonical Harness model and provider usage records separately from continuation history. Root and asynchronous child stream consumers persist each usage-report chunk before best-effort display publication; terminal local records reconcile idempotently. Inline descendant records retain their native attribution. A failed persistence write fails the consuming operation rather than silently dropping accounting facts; already committed records survive cancellation, failed Runs, compaction, and restart.

Records are scoped to the root Thread family. Model record identity deduplicates repeated reports and terminal reconciliation. Provider identity is `(provider, product, usage_id)`, independent of Run attribution: the same receipt is counted once per family and attributed to its first observation. A duplicate identity with changed usage is an explicit integrity error, not an overwrite. This observational ledger is not an invoice, execution journal, or authority to replay effects.

The root Thread usage projection distinguishes root-agent records, inline/asynchronous descendants, and their combined total. It sums individual committed records, never the already-inclusive root RunUsage plus child totals. Cached and audio token counters are subsets, not additions to input/output totals. Known model USD costs and provider costs in each reported currency remain separate; missing costs remain unknown. Bounded model breakdowns disclose omitted groups. The latest 32 observed Runs group unique contributions by native Run ID and agent instance; older Run contributions remain in totals. Run grouping is derived from the same immutable records, not a second execution or billing authority. Queries use a fixed ledger high-water mark and bounded detached batches with no database session held across rendering or external I/O.

Coverage starts with the first persisted observation. No record means unavailable coverage, not proven zero lifetime usage. Pre-ledger history and usage never observed after a crash are not backfilled from messages. While work is active, totals are recorded-so-far; context occupancy and subscription limits are separate projections.

## Thread Metadata Head

Each Thread has one mutable metadata head containing `version`, nullable `title`, and `archived`. A title/archive mutation compares its required expected version, changes both supplied fields in one short transaction, and increments the version once. A no-op can retain the current version. Metadata changes update Thread recency independently from configuration and continuation selection.

Metadata compare-and-select prevents a stale surface from silently overwriting a title or archive change. Process-local active status is not stored in this head. Root archive admission additionally checks current-process activity; another process remains an ordinary concurrent writer and is handled by the metadata version rather than a liveness protocol.

## Thread Configuration Head

Each root or child Thread has one mutable configuration head:

```python
class ThreadConfiguration(BaseModel):
    version: int
    project_id: str | None
    agent_source: AgentSource
    environment_profile_id: str
    harness_plugin_ids: tuple[str, ...]
    environment_run_extension_ids: tuple[str, ...]
    mcp_server_ids: tuple[str, ...]
```

`agent_source` is the discriminated Agent-resource or Markdown-subagent reference owned by [Projects, Threads, and Environments](04-projects-threads-and-environments.md#sticky-thread-configuration). A null `project_id` records a Thread without a Project and is preserved across restart and child creation. The SQLite upgrade rebuilds the Thread configuration table with a nullable Project column while preserving existing rows and references; no existing Thread is reassigned. It uses the normal serialized migration transaction. Downgrade refuses before altering the table if projectless Threads exist; readers that require a non-null Project cannot interpret projectless Threads; revision equality is not a substitute for that payload compatibility boundary. The lists are exact ordered enabled selections. Omission belongs only to create or patch input; the stored head contains no inheritance marker. Every non-empty update compares the caller's required expected version, commits all changed axes, and increments `version` atomically. A no-op can retain the version.

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

The composition reference explains which Agent, Project roots, Capability, Harness Plugin, Content Plugin snapshot, MCP, Provider, and Run Extension behavior produced the checkpoint. It does not constrain the next Run to use the same composition.

Root continuations also retain an independent display history. Complete root messages are captured before handoff or compaction replaces model context; context summaries retain their operation identities at their observed timeline positions. Retained input replay does not append duplicate user messages. Checkpoint and terminal publication save this inspection content in the UI-owned `a13n.harness-ui.display-history` Capability namespace under the same compare-and-select operation as native state. The existing extensible state envelope keeps the continuation payload readable by older Apps without changing model messages. The display history is never supplied as model history, used to resume execution, or treated as evidence that unsaved live output survived a crash. Nested helper histories are excluded.

Transcript pagination and saved-output locations address the selected display history, while context usage and execution continue to address `HarnessState`. A model-context replacement cannot remove previously saved display messages. Reloading or reopening a Thread preserves the same timeline. Older continuations without display history bootstrap from their available native history; already discarded content is not reconstructed. No SQLite migration or continuation-envelope change is required. Older Apps can still read and resume native state, but do not maintain the display snapshot. Its native-history digest prevents stale positional mappings from being reused after an older writer advances the Thread; such checkpoints fall back to their available native history. The existing immutable-object size bound still applies: oversize or failed publication leaves the prior selected head intact instead of silently truncating display history.

Deferred requests are stored only as part of the complete suspended continuation. Surface projections use the selected continuation digest as an opaque continuation ID and never expose the object reference or native request value. A deferred response compares that exact selected reference and reconstructs its complete native request/result pair in memory.

Before each normal root model request, Harness UI exports the complete canonical `HarnessState` after pending input and context transformations have been applied, publishes a continuation using that Run's captured composition, and compare-and-selects it against the current expected reference. This saves the initial authored input before model output and consumed steering before the following request. Nested compaction/helper model histories cannot select the root Thread head. These are complete Agent State checkpoints, not partial AG-UI event snapshots, an input journal, or a durable work queue. Admission alone, preparation failure before this boundary, and accepted but unconsumed steering do not establish saved input.

Every successful checkpoint advances the expected reference for subsequent request-boundary and terminal saves. Publication and selection complete under cancellation shielding; a failed selection does not advance the expected reference or emit a success marker. The native live stream emits a checkpoint marker only after selection succeeds, ordered after the input represented by that checkpoint and before the following model output. This marker is a process-local display cutover hint, not another persistence authority.

Harness UI also publishes every available valid terminal `HarnessState`, including failed and cancelled results, and compare-and-selects it against the latest successfully selected reference for that operation. After an unexpected exception or external cancellation, it attempts to export the Harness-retained shutdown checkpoint and publish it under cancellation shielding before propagating the original error. Saving a checkpoint does not turn failed or cancelled execution into success and never automatically replays input or effects. Deferred requests accompany only a suspended result. If export, publication, or selection fails, the prior or concurrently selected continuation remains current and the save failure is diagnosed independently. Root receipts, input, partial output, live AG-UI events, Environment files, and child display never synthesize a continuation.

## Saved Root Completions

Each root Thread retains its latest successful completion independently of current-process receipts, excerpts, and navigation recency. The relational marker contains a monotonically increasing completion version, Run ID, selected continuation identity, and completion time. A missing marker means version zero. Successful terminal continuation selection updates this marker in the same compare-and-select transaction; a failed selection cannot publish a completion. Repeated publication for the same Run does not advance its version. Unexpected exceptions and cancellation remain non-successful even when a completed model outcome is available. Environment cleanup diagnostics retain their existing root outcome semantics.

Request-boundary checkpoints, subsequent running work, failures, suspension, cancellation, metadata edits, and navigation never clear or advance a prior successful marker. The marker describes saved success, not a durable receipt or execution liveness. Browser acknowledgements are personal local state, not server Thread state. Transcript projection carries the completion version from the same detached Thread snapshot used to choose its history; reading a newer marker independently cannot acknowledge older rendered history.

The additive SQLite upgrade initializes existing Threads at version zero without inspecting or guessing historical outcomes. Supported older writers can omit the new columns and preserve existing markers, but cannot publish new completion evidence. Downgrade removes only completion metadata. Browser-close and server-restart recovery applies to successes recorded by a writer supporting this marker; it does not replay interrupted execution.

## Navigation Recency

A Thread has an independent nullable `touched_at` navigation time. New Threads initialize it to creation time. Explicit touch advances it monotonically in one short transaction without changing `updated_at`, conversation activity, metadata/configuration versions, excerpts, or continuation selection. It is navigation metadata, not evidence that input or work has been saved. Reads, checkpoint saves, progress, automatic continuation, completion, failure, cancellation, and metadata/configuration edits never touch navigation recency.

Explicit surface prompt/deferred-response admissions and explicit cross-Thread create/run operations touch before dispatch, after rejecting conflicting active admission. A touch failure prevents that admission. Accepted human steering touches after enqueue; a touch failure is diagnosed without reporting the already-enqueued input as rejected. Background cross-Thread messages and Agent steering do not touch the target. Opening a conversation alone does not touch it. The App and authenticated HTTP touch operation allow a caller to explicitly advance root navigation without starting work or restoring an archived Thread.

Navigation sorting uses `coalesce(touched_at, created_at)`, descending with the Thread-ID tie-breaker. The additive SQLite migration seeds existing rows from their prior `updated_at` once, preserving the initial order without reading historical objects. Nullable storage allows supported older writers to omit the new column; such newly created rows fall back to creation time. Older writers continue to update existing fields without changing a previously touched navigation time. Upgrade performs one metadata scan and creates the navigation index under the existing serialized migration transaction. Downgrade drops only navigation metadata and its index, retaining Thread identities, incoming references, and checkpoints.

## Saved Conversation Excerpts

A Thread retains bounded deterministic display excerpts alongside its selected continuation: the first authored input (512 characters), latest authored input (2048 characters), latest corresponding assistant text (2048 characters), and reply kind (`none`, `progress`, or `final`). These are literal whitespace-normalized excerpts with explicit truncation, not model-generated summaries. An explicit Thread title remains independently editable and is never overwritten by automatic extraction. Attachment-only input uses original names or media types, never binary payloads or internal attachment paths.

Request-boundary checkpoints derive excerpts from their canonical saved history while preserving the established first input. Terminal saving also uses excerpts collected from the root's native input, delivered steering, and closed assistant text before best-effort surface delivery. Hidden context overlays, system prompts, tools, thinking, descendant events, and background-process or subagent notifications do not become authored input. Each new authored input clears the preceding reply; a reply is final only when the root completes with that output. A suspended, failed, or cancelled checkpoint can retain progress or no reply. Excerpts survive model-history compaction without being reconstructed from a compact summary.

The complete continuation bundle carries its excerpt. Selecting that continuation updates the relational excerpts and their Unicode-casefolded search text in the same short compare-and-select transaction. Selection conflicts and publication failures retain the previous excerpts. Display data never reconstructs execution state, proves live work, or promises durable root-input acceptance. Hard process loss without a selected checkpoint retains the prior saved preview.

Conversation activity time changes only when new authored input or assistant text is saved. Metadata/configuration edits retain that time; empty Threads use creation time for activity sorting. Search text contains Thread ID, explicit title, and the saved excerpts. Title changes update search text with their metadata transaction, without changing the saved conversation. Session queries use these relational values and never hydrate continuation objects to build a list.

The additive local schema upgrade preserves existing Thread/checkpoint data and seeds name/ID search in bounded metadata batches. It does not scan historical object files during startup. Existing checkpoints without excerpts remain readable; new executions populate excerpts from observed conversation. The migration needs the ordinary local SQLite migration write transaction and one metadata scan plus activity-index creation. An interrupted transaction is retried through the existing migrator; downgrade removes only the added display columns and index, not continuation objects.

## MCP Resource Index Compatibility

Resource index identity is `(generation_digest, resource_kind, resource_id)`, not a physical source path. Multiple MCP resources can point to one source without duplicating source rows. The SQLite upgrade rebuilds only the derived resource index table, preserving existing rows and leaving Thread and continuation data unchanged. The rebuild takes a write lock and scans the local index; no external I/O or background backfill is required. Startup migrations retain their existing serialized transaction and rerun behavior. Older binaries do not support the new schema; use forward repair rather than mixed-version access. Downgrade refuses before altering the index if any generation contains multiple resources per source, rather than discarding entries.

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

Startup validates retained values lazily and reloads the file configuration together with current Content Plugin directories. Committed comments remain available through ordinary queries, without restoring page presence or shared drafts. It does not restore root receipts, replay root input, restart a child segment, reconnect shell processes, infer process liveness, or manufacture a checkpoint from display.

A Thread resumes from its selected continuation using its current sticky configuration unless the next admission applies a patch. A Thread with no selected continuation starts its first Run from the immutable empty `HarnessState` created with `HarnessState.new()` when the Thread was inserted. The generated Harness `thread_id` is the Harness UI Thread ID. If selected resources are missing from the current accepted generation or cannot reconstruct against installed dependencies, the Run fails before dispatch; recovery does not fall back to the composition that produced the prior continuation.

| Last stored fact                      | Recovery behavior                                                               |
| ------------------------------------- | ------------------------------------------------------------------------------- |
| No new continuation selected          | Resume the prior selected continuation                                          |
| Continuation object published only    | Resume the prior selected continuation                                          |
| Child nonterminal checkpoint selected | Retain for inspection; do not infer liveness, replay, or permit linked resume   |
| Child terminal checkpoint selected    | Serve the saved terminal view and permit policy-authorized linked resume        |
| Selected object missing or invalid    | Fail read or resume explicitly                                                  |
| Current Thread resource missing       | Preserve Thread and checkpoint; reject the next Run until configuration changes |

External model, tool, and Environment effects can be unknown and may repeat after explicit retry or linked resume.

## Private Failure Diagnostics

Recognized model failures and unexpected execution errors produce a best-effort private JSON report in the operating-system temporary directory. The report contains component versions, Python/platform metadata, Thread/Run correlation, exception chains, and frame locations, but no frame locals, source lines, configuration snapshot, transcript, or serialized checkpoint. Exception messages can themselves contain sensitive provider content; the report is user-reviewed diagnostic material, not a safe public payload. Files use owner-only access where supported. A report-write failure never replaces the execution error or blocks checkpoint publication.

The terminal failure presentation identifies the report path and the repository's new-Issue entry point, requests reproduction steps, and tells the user to review sensitive content before sharing. Nothing is uploaded automatically. Normal diagnostic logs retain safe exception types and stack locations with Thread/Run correlation, not raw exception messages or provider bodies. Reports and logs never reconstruct or select continuation state.

## Failure Semantics

| Failure                                         | Outcome                                                             |
| ----------------------------------------------- | ------------------------------------------------------------------- |
| Immutable serialization or publication fails    | No selected reference changes                                       |
| SQLite selection fails after object publication | Prior selected head remains current; object is unreferenced         |
| Thread configuration version conflicts          | Stale patch is rejected without partial selection changes           |
| Root process exits abruptly without cleanup     | Prior continuation remains current                                  |
| Child process exits abruptly during a segment   | Saved nonterminal head remains; no liveness or takeover is inferred |
| Child terminal checkpoint cannot be selected    | Execution is not reported as succeeded                              |
| Live delivery fails                             | Saved heads are unaffected                                          |

## Invariants

01. Files own desired resources; SQLite owns accepted projections, mutable runtime heads, and published human comments.
02. Thread configuration is sticky, exact, versioned, and replaceable between Runs.
03. Thread metadata and Thread configuration are independent versioned heads.
04. Every admitted Run has one immutable resolved composition.
05. A continuation records but is not permanently bound to its producing composition.
06. Root receipts, input, and active Runs are not durable work records.
07. Deferred response authority is the exact selected suspended continuation.
08. Compact display never becomes Harness continuation state.
09. Process loss never triggers implicit replay, takeover, PID inspection, heartbeat, lease, or lock-file recovery.
10. Transactions remain short and outside file or external execution I/O.
11. Published comments retain their original saved targets independently of the selected continuation; they never become continuation or execution authority.
12. Comment schema upgrades preserve existing conversation data, and no successful publication falls back to transient storage.
