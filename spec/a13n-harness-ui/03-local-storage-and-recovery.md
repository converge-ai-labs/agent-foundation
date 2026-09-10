# Local Storage and Recovery

## Design Position

Harness UI keeps persistence continuation-oriented:

1. editable YAML, MCP JSON, and local Markdown files own desired resources and global defaults;
2. data-root Content Plugin ID directories contain editable local files with optional Git provenance;
3. SQLite owns accepted-generation indexes, Project/resource lookup projections, sticky Thread configurations, execution heads, and selected references;
4. immutable content-addressed files own normalized configuration generations, resolved Run compositions, and complete continuation checkpoints;
5. saved shared browser drafts retain editable input separately from continuation;
6. live runtime objects, presence, and native terminal sessions remain in process memory.

The store supports local restart and inspection, not durable work scheduling. Harness UI does not persist a root input queue, Run-attempt ledger, renewable execution claim, worker assignment, effect journal, shell-process record, or delivery ledger.

## Persisted Values

| Value                                                                                             | Storage                       | Authority                                                         |
| ------------------------------------------------------------------------------------------------- | ----------------------------- | ----------------------------------------------------------------- |
| Desired resource definitions and global defaults                                                  | YAML and local Markdown files | Human-editable desired behavior                                   |
| Installed Content Plugin directories                                                              | Data-root files               | Current optional plugin availability and editable files           |
| Thread scratch and submitted attachments                                                          | Data-root Thread directories  | Disposable working files and retained input files                 |
| Accepted configuration generation and resource indexes                                            | SQLite plus immutable object  | Current complete validated file and plugin generation             |
| Thread metadata head, sticky configuration head, and initial-state reference                      | SQLite                        | Identity, mutable presentation, defaults, and first-Run bootstrap |
| Empty initial `HarnessState`                                                                      | Immutable object              | Harness-generated Thread identity before any selected Run         |
| Resolved Run composition                                                                          | Immutable object              | Exact behavior and dependency provenance captured for one Run     |
| Root or child continuation bundle                                                                 | Immutable object              | Exact selected `HarnessState` resume authority                    |
| Child execution heads                                                                             | SQLite                        | Segment correlation, saved status, and selected checkpoint        |
| Compact child display                                                                             | Immutable child checkpoint    | Inspection history only                                           |
| Environment-state references                                                                      | SQLite plus immutable files   | Current Host-authoritative state                                  |
| Shared browser drafts and referenced input content                                                | App data-root storage         | Saved editing state only; not execution or continuation authority |
| Participant presence and native Host terminal sessions                                            | Process memory                | Current shared instance only                                      |
| Root receipts, active tasks, Models, credentials, clients, adapters, streams, and shell processes | Process memory                | Current App only                                                  |
| Logs and OpenTelemetry                                                                            | Configured process outputs    | Diagnostics only                                                  |

## Shared Browser Drafts

The App persists the shared editing content of each participating root Thread under the selected data root. This is a separate value from Thread sticky configuration, `HarnessState`, and root-operation receipts. Saving a draft does not admit a Run, advance a continuation, or create an input queue. [Collaborative conversations](webui/01-collaborative-conversations.md) owns editing and submission behavior.

A server save acknowledgment follows successful persistence. Process restart can restore saved editing content but not transient presence, unsynchronized browser edits, old control authority, or a live Run. Persistence failure is visible and is not reported as a successful save. A saved draft must not be submitted automatically on startup or reconnect; selected continuation and current-process receipt evidence remain the authorities for execution history and control.

Draft content and content references needed to restore a saved draft are not disposable scratch. Cleanup must retain referenced input content while the saved draft uses it, without treating it as evidence of a submitted or completed Run. Unreferenced scratch retains the ordinary cleanup policy. Browser draft persistence does not store terminal output, native process handles, or reusable credentials.

One WebUI instance coordinates its participants. Sharing the data root between independent App processes does not provide cross-process live document synchronization or distributed same-submission coordination.

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

One App serializes root admission per Thread and state changes per child execution. Separate local App processes can open the same store, but they do not share root receipts, active tasks, or control and do not take over one another's executions. Thread metadata and Thread configuration updates compare their independent expected integer versions; continuation, child checkpoint, accepted generation, and Environment state selection compare expected references. A mismatch fails explicitly and never overwrites the newer head.

Harness UI does not use PID inspection, heartbeats, or time-based leases to infer whether another App is alive. Current execution ownership is process-local. Hard OS locks used for Thread file cleanup protect resource use only; they neither establish execution ownership nor authorize takeover.

## Thread Files and Automatic Scratch Cleanup

The App owns a lazily created file area for each existing root or child Thread, keyed by its Harness-generated Thread ID. It introduces no pre-Run session identity. Under the data root, `threads/<thread-id>/tmp/` holds disposable working files and staged uploads; `threads/<thread-id>/attachments/` holds retained submitted inputs. These paths are not Project roots, continuation objects, or a separate resume authority. Runs and process restarts reuse the same Thread file area. Archive, terminal navigation, normal Run completion, and App shutdown do not delete it.

An upload is staged under a unique Thread-scoped handle with its normalized original name, media type, and byte count. Different uploads with the same name do not overwrite one another. Submission promotes referenced files into retained storage before scheduling the Run. Promotion is idempotent for an already retained handle. Admission failure or a process interruption may leave an unreferenced retained file; cleanup favors retaining that file over deleting an input potentially referenced by execution. Submitted image bytes also enter native Harness input and selected checkpoints. Only a selected continuation restores conversation history; a retained upload does not prove that its submission completed.

Automatically converted [long-text inputs](05-runtime-subagents-and-surfaces.md#long-text-input-files) use the same retained attachment storage. Conversion occurs during entered-Environment input preparation, or before steering enqueue, and retention completes before any model-visible reference is published. Selected checkpoints retain references rather than the original inline text; complete UTF-8 text remains available through the Thread attachment reader. Attachment storage alone never establishes that execution or steering delivery succeeded.

The startup janitor and an hourly App task remove only expired `tmp/` trees. The default inactivity threshold is three days, configurable through `StorageSettings.scratch_retention_seconds`. Age is measured from the App's last recorded use or release, not from archive status, PID inspection, or a Run-status guess. Each App conservatively protects every Thread file area it touches until App shutdown. Independent Apps hold independent hard OS file-use locks, allowing concurrent use without serializing their Runs. Cleanup tests those locks under a short registration gate and skips a Thread if any holder remains. Process death releases OS locks, making an old scratch tree eligible without a heartbeat timeout. Cleanup errors are diagnostic and do not prevent normal startup; there is no manual confirmation requirement.

Pruning never deletes retained attachments, saved shared drafts or their referenced input content, SQLite rows, immutable objects, Project files, or external symlink targets. File-use lock bookkeeping is outside the deletable scratch tree. A discarded staged upload can expire; reading its old handle then fails explicitly. Surfaces do not treat a host path as a portable upload identifier or implement their own deletion policy. Important generated results belong in an explicitly chosen durable destination, not scratch storage.

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

`agent_source` is the discriminated Agent-resource or Markdown-subagent reference owned by [Projects, Threads, and Environments](04-projects-threads-and-environments.md#sticky-thread-configuration). A null `project_id` records a Thread without a Project and is preserved across restart and child creation. The SQLite upgrade rebuilds the Thread configuration table with a nullable Project column while preserving existing rows and references; no existing Thread is reassigned. It uses the normal serialized migration transaction. Downgrade refuses before altering the table if projectless Threads exist; older binaries must not open this schema. The lists are exact ordered enabled selections. Omission belongs only to create or patch input; the stored head contains no inheritance marker. Every non-empty update compares the caller's required expected version, commits all changed axes, and increments `version` atomically. A no-op can retain the version.

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

Deferred requests are stored only as part of the complete suspended continuation. Surface projections use the selected continuation digest as an opaque continuation ID and never expose the object reference or native request value. A deferred response compares that exact selected reference and reconstructs its complete native request/result pair in memory.

Harness UI publishes every available valid terminal `HarnessState`, including failed and cancelled results, and compare-and-selects it against the reference loaded at admission. After an unexpected exception or external cancellation, it attempts to export the Harness-retained shutdown checkpoint and publish it under cancellation shielding before propagating the original error. Saving a checkpoint does not turn failed or cancelled execution into success and never automatically replays input or effects. Deferred requests accompany only a suspended result. If export, publication, or selection fails, the prior or concurrently selected continuation remains current and the save failure is diagnosed independently. Root receipts, input, partial output, live AG-UI events, Environment files, and child display never synthesize a continuation.

## Saved Conversation Excerpts

A Thread retains bounded deterministic display excerpts alongside its selected continuation: the first authored input (512 characters), latest authored input (2048 characters), latest corresponding assistant text (2048 characters), and reply kind (`none`, `progress`, or `final`). These are literal whitespace-normalized excerpts with explicit truncation, not model-generated summaries. An explicit Thread title remains independently editable and is never overwritten by automatic extraction. Attachment-only input uses original names or media types, never binary payloads or internal attachment paths.

The root executor collects excerpts from the root's native input, delivered steering, and closed assistant text before best-effort surface delivery. Hidden context overlays, system prompts, tools, thinking, descendant events, and background-process or subagent notifications do not become authored input. Each new authored input clears the preceding reply; a reply is final only when the root completes with that output. A suspended, failed, or cancelled checkpoint can retain progress or no reply. Excerpts survive model-history compaction without being reconstructed from a compact summary.

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

Startup validates retained values lazily and reloads the file configuration together with current Content Plugin directories. It does not restore root receipts, replay root input, restart a child segment, reconnect shell processes, infer process liveness, or manufacture a checkpoint from display.

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

01. Files own desired resources; SQLite owns accepted projections and mutable runtime heads.
02. Thread configuration is sticky, exact, versioned, and replaceable between Runs.
03. Thread metadata and Thread configuration are independent versioned heads.
04. Every admitted Run has one immutable resolved composition.
05. A continuation records but is not permanently bound to its producing composition.
06. Root receipts, input, and active Runs are not durable work records.
07. Deferred response authority is the exact selected suspended continuation.
08. Compact display never becomes Harness continuation state.
09. Process loss never triggers implicit replay, takeover, PID inspection, heartbeat, lease, or lock-file recovery.
10. Transactions remain short and outside file or external execution I/O.
