# Document Memory and the Filesystem Backend

## Design Position

Document memory stores reusable knowledge, procedures, and events as addressable documents with provenance. Agents receive bounded navigation and retrieve the evidence needed for a task. The built-in filesystem backend uses the current Environment's `FileOperator`; it does not require a second Environment, a local Worker directory, or shell execution.

[Context and Memory](09-context-and-memory.md#memory-integration) owns the single `MemoryCapability`, native record backends, and context integration. This document owns the shared document model, document tools, extraction and organization behavior, and the filesystem backend. [Environment Integration](08-environment-integration.md) owns operation scopes, routing, permission ceilings, and adapter lifetime. [Service Memory](../a13n-service/42-memory.md) owns managed configuration, storage bindings, directory publication, authorization, and sharing. [Bot Memory](../frontend/bot-memory.md) owns the conversation-facing experience.

| Concern                                                                             | Owner                                   |
| ----------------------------------------------------------------------------------- | --------------------------------------- |
| Model context, memory tools, explicit remember/forget behavior                      | `MemoryCapability`                      |
| Candidate extraction, classification, conflict assessment                           | Authorized memory organization workflow |
| Document validation, revisions, change records, commit/recovery, derived indexes    | Document backend                        |
| Filesystem reads and mutations                                                      | Bound Environment `FileOperator`        |
| Storage selection, tenant/subject authority, retention, durable workflow scheduling | Host                                    |

The filesystem implementation is registered as `a13n.filesystem`. Mem0 and other native backends remain explicit alternatives; installation never changes a selected backend. Native record CRUD does not imply support for revisioned documents. Providers advertise document, revision, and change-record support separately and reject unsupported operations before mutation.

## Memory Types and Time

`kind` has three values. It describes content, not ownership, retention duration, or sharing authority.

| Kind         | Unit and organization                                                          | Extraction and evolution                                                                                         | Retrieval purpose                                              |
| ------------ | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `semantic`   | One stable subject, entity, or coherent topic                                  | Store explicit facts or evidence-backed conclusions; revise with sources, scope, uncertainty, and effective time | What is true now or was true at a specified time?              |
| `procedural` | One reusable task with prerequisites, steps, verification, and failure signals | Extract from supported experience; retain applicability and counterexamples; revise under a version precondition | How should this task be performed in this environment/version? |
| `episodic`   | One bounded event with time, participants, context, and outcome                | Append an account of the event; corrections are new documents linked to the original                             | What happened, when, and how was it handled?                   |

`daily` and `long_term` are not document kinds. Date grouping and daily summaries are views or explicitly created derived documents. Retention is an independent Host policy: an event may be retained for years, while a procedure may become obsolete quickly. A daily summary does not replace its source events.

The model distinguishes event occurrence time and time zone, knowledge applicability/effective time, trusted document creation time, and revision save time. Unknown time remains unknown. A new revision does not reset document creation time or move a historical event into a newer day. Recency alone establishes neither correctness nor supersession.

One input need not produce every kind. A deployment failure can yield an event, a separately supported dependency fact, and a verified deployment procedure; each derived conclusion links to the evidence that supports it. The backend never infers these conclusions merely because text is being stored.

## Documents, Revisions, and Sources

A document has one stable opaque ID, an immutable owning scope and kind, a logical relative path, and a positive integer `version`. A revision is the complete immutable content at that version: Markdown body, title, bounded navigation description, applicability, supported time fields, sources, and explicit correction/supersession relationships. The document and its current revision expose the same version, following [Platform Data Conventions](../data-conventions.md). A content digest detects changed bytes; it is not another revision counter or an authority token.

Paths and headings support navigation, while IDs support durable references. Moving a document preserves its ID. References returned by read/search include the document ID and exact version; section locators also bind the content digest. A path that now denotes another document cannot satisfy a stale ID/version reference. Changing ownership or kind creates a new document with an explicit relationship rather than silently reclassifying the original.

Semantic and procedural documents can advance to a new revision after an expected-version check. Prior revisions remain readable under current authorization until explicit retention or deletion removes them. Episodic bodies remain append-only: a correction has its own identity and event attribution. Shared read access never authorizes revising another scope's documents.

Sources identify exact authorized evidence, such as a Thread/Run/message, an external document version, or another memory revision. Evidence references grant no access by themselves. Memory does not automatically copy all conversations, tool results, or external documents into a second raw archive. Optional snapshots require the Host's retention authority. A missing or deleted source remains unavailable; the system does not invent replacement evidence.

Provider-managed metadata sufficient to reconstruct document identity, versions, paths, and provenance is stored with the document corpus. Files use a versioned, validated metadata envelope; the Agent supplies content fields, while trusted code supplies identity, versions, ownership bindings, and save times. Host authorization, sharing visibility, and operation eligibility never derive from editable file metadata. Body text is stored exactly as accepted, without provider-side inference.

## Filesystem Layout and Navigation

The selected root contains Host-derived isolated subject namespaces. Within one subject, the logical layout is:

```text
<subject-root>/
├── _index.md
├── semantic/
│   ├── _index.md
│   ├── projects/
│   └── preferences/
├── procedural/
│   ├── _index.md
│   └── engineering/
├── episodic/
│   ├── _index.md
│   └── 2026/09/
├── inbox/
└── .internal/
    ├── revisions/
    ├── commits/
    ├── indexes/
    └── staging/
```

Topic directories and descriptive filenames are meaningful human navigation, not a fixed exhaustive taxonomy. A document covers one coherent retrieval intent. Independent tasks, incompatible applicability, or different audiences do not merge merely because their words are similar. Unknown event dates use an explicit undated location. Changing a topic path does not change a document's scope.

`_index.md` is the sole index name at both the root and child directories; there is no second `MEMORY.md` knowledge body. Indexes contain directory scope, concise child descriptions, links, and retrieval hints. Generated entries are derived from committed metadata, without an LLM call during browsing. Substantive facts live in documents. Optional authored directory descriptions are stored separately from generated entries and survive reindexing.

The Host renders a permission-filtered, bounded root index as untrusted input context. Shared documents are composed into that projection without exposing other tenants' paths, titles, or existence. Physical index files are not authority and are not injected without access filtering. Whole entries and continuation fit the existing 32 KiB encoded-context budget. Large collections use subindexes and pagination, not silent truncation or an unbounded tree dump. Index-first document mode does not also inject native top-k bodies by default.

Current documents, retained revisions, and commit/identity records, including retained change diffs, are authoritative corpus data. Search indexes, TOC caches, and generated navigation are rebuildable; deleting these derived files loses no committed memory. Deleting revisions or commit records is not reindexing. `.internal/` and pending candidates do not appear in ordinary model navigation. `inbox/` holds unaccepted or disputed candidates for authorized organization/review, not ordinary factual recall.

## Storage Binding and Environment Lifetime

With no explicit storage override, the filesystem backend uses the current default Environment and a dedicated `/memory` root in its provider-local namespace. This is a sandbox path in Service, not `/memory` on the Worker. Hosts may configure another root, including a pre-mounted persistent volume, or explicitly supply another Environment's file access. A multi-mount Host must identify the selected mount or have one explicit default; iteration order never selects storage.

The memory backend receives a scoped, root-confined `FileOperator`. It reuses the Environment's readiness, current permission and execution guards, backing identity, and lifetime without opening or closing the same adapter twice. It has no shell, process, port, or arbitrary execution interface. Memory does not issue shell commands to perform search, parsing, indexing, or writes. An Environment provider's own internal file-transport implementation remains provider-owned; exposing files does not expose its internal machinery as Agent command authority.

Disabling generic file or shell tools does not disable a permitted memory tool, but memory cannot bypass underlying Environment file permissions. Conversely, a file-only memory facade does not restrict another tool's authority over the same sandbox. A task shell or generic file tool with access to the directory can change it; forbidding such access requires actual Host/Environment isolation, not a memory flag or a `noexec` mount.

No configuration, unavailable configuration, missing Environment, or missing mount ever selects a Worker-local directory implicitly. Embedded callers may explicitly supply Direct Local under their own authority. With no current Environment and no explicit storage, optional memory reports unavailable and required memory fails before use.

| Situation                               | Observable behavior                                                                                                |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| No explicit storage configuration       | Select current Environment files; no durability beyond that target is implied                                      |
| Same sandbox, another Run or Worker     | Reconnect to the same storage identity through Environment; reauthorize the subject                                |
| Another sandbox selected for a new Run  | Select its separate memory store; do not copy or union the old store implicitly                                    |
| Explicit persistent path is unavailable | Report unavailable; never initialize an empty store beneath a missing mount or redirect to local storage           |
| Sandbox disappears or its data is lost  | Report storage loss for retained references; a rebuilt target is not restoration                                   |
| Explicit storage override is unhealthy  | Preserve the binding and fail the memory operation; normal optional execution may continue without recalled memory |

An initial unconfigured selection using the sandbox is the default, not failure recovery. Automatic fallback from a failed explicit target into a different empty store is not supported. Changing targets is an explicit Host selection; old references keep their original identity.

The Host binds the Environment selector, normalized root, and observed store identity. A committed store marker distinguishes a newly initialized corpus from a missing previous corpus. Initial creation is explicit and race-safe; reconnecting to an existing binding never recreates its missing marker silently. Environment generation changes invalidate cached operations and require revalidation. The same persistent corpus can survive an Environment reconnect only when its identity and authority are verified again. Marker contents alone grant no authority and cannot prove that an external mount is healthy; deployments using a required mount supply and verify its identity before writes.

NFS or another persistent volume is mounted by the deployment or sandbox provider. Memory selects an existing path and does not mount filesystems, obtain mount credentials, or promise durability by configuration alone. The backing filesystem and write coordinator must support the commit guarantees below. Process-local serialization is insufficient when several Workers can write the same corpus.

## Extraction and Organization

Explicit remember requests can create a document or revise authorized semantic/procedural knowledge. Extraction returns a structured candidate with kind, proposed content, retrieval intent, applicability, source references, uncertainty, and a proposed operation. The Host supplies the audience and retention authority. File creation does not depend on successful global clustering. Accepted explicit and automatic writes take effect after the normal commit checks; recording a change for later inspection does not introduce a human-approval gate. Deferred candidates remain separate from committed changes.

Automatic organization is opt-in and off by default. It consumes only Host-confirmed, durably completed work. The Host owns scheduling, the extraction cursor, duplicate admission, model selection, bounded budgets, cancellation, and recovery; a process-local Harness result does not establish eligibility. Embedded callers can invoke the same organization operation after their own commit without a background scheduler. No unowned task survives a logical Harness Run.

The organization workflow:

1. Select authorized evidence since the confirmed extraction cursor.
2. Extract candidates and reject transient noise, unsupported conclusions, and unauthorized retention.
3. Search existing documents within the same authorized audience; compare applicability and sources as well as similarity.
4. Choose create, revise, relate, defer as a candidate, or ignore. Known exact duplicates are ignored. Episodic corrections create another document.
5. Submit bounded changes to the deterministic writer with exact source references and expected versions.
6. Advance completion only after the Host confirms admitted results, including reconciliation of uncertain commits.

Semantic facts preserve temporal changes separately from corrections. Procedures include prerequisites, verification, failure signals, and relevant counterexamples. Recalled procedures remain untrusted reference material; organization never installs a Skill, changes instructions, or grants tool authority automatically. Low-confidence conclusions and unresolved conflicts remain candidates and do not overwrite accepted knowledge.

Organization groups by retrieval intent and compatible scope. It prefers headings for stages of one task, separate documents for independently answerable tasks, and directories for stable topic groups. One authoritative document may have several navigation links. Structural relocation preserves complete content blocks, qualifications, sources, and stable document IDs; semantic rewriting is an explicit revision. The initial organization workflow performs candidate classification, duplicate/conflict assessment, targeted revision, and cross-linking. Autonomous corpus-wide splitting/merging and automatic Skill promotion are outside this contract.

## Writes, Concurrency, and Recovery

Only deterministic code commits memory operations. It confines paths beneath the selected root, rejects traversal and escaping links, validates metadata and Markdown structure, verifies internal/source reference syntax and allowed targets, and binds sources under current authority. Markdown parsing uses an AST; headings inside code fences are not sections. Structural validation does not prove semantic truth.

Each create, revision, move, or delete has a Host-owned request key, bound to its exact payload, scope, and storage identity. Reusing a key with another payload is a conflict. A successful key resolves to its already committed result. Expected document versions are checked at publication, not just when reading or staging. A stale writer cannot overwrite a newer revision. Deletion also fences pending revisions and organization work so a retry cannot resurrect removed content.

The writer stages complete candidates and their change records, persists sufficient recovery evidence before publishing effects, and publishes one recoverable commit. A filesystem mutation cannot become a readable committed version without its matching change record. Staged records do not prove success. Readers see one committed revision or a typed unavailable/conflict result, never half-written content or a mixed multi-file reorganization. Backend support must establish conditional publication and crash recovery across all writers. The general `FileOperator` alone is not a transaction, distributed lock, compare-and-swap API, or proof of durable acknowledgement. The Host supplies write coordination or the selected provider supplies equivalent verified primitives; unsupported write configurations fail before mutation. A persistent-volume deployment cannot claim multi-Worker safety from a Python lock or an assumed NFS lock. An expired or superseded writer cannot publish using an earlier coordination grant.

After publication, readback verifies exact content, version, subject, required metadata, and the matching change record when supported. The Host then admits the result into its directory. Index maintenance is a separate completion fact. A committed write with a failed index update returns its confirmed identity/version and explicit indexing status; it is not reported as a failed creation to be retried. Dirty or stale indexes cannot silently omit committed content while claiming a complete search. They are repaired before use, or the operation reports bounded/degraded or unavailable retrieval. Exact document reads remain possible when current authority and committed content are verified.

Timeout or disconnection after possible publication is `memory_write_unconfirmed`. Cancellation does not prove rollback. Recovery inspects the same request key and commit evidence rather than issuing a new write blindly. A precondition failure is `memory_conflict` and requires a fresh read. No memory text, native path, secret, or provider diagnostic is included in an error.

External file changes invalidate digest-bound TOC/search caches and are reported explicitly. A changed document cannot be served as a previously committed revision. Valid external edits can be admitted through the same validation and revision workflow; malformed or identity/authority-changing edits remain unavailable until reconciled. Reindexing does not silently bless an out-of-band rewrite as trusted history. Backup/restore includes the corpus and commit metadata; authorization and sharing state are backed up by their Host owner.

Deletion removes the document, its retained revisions, and content-bearing change details from subsequent authorized retrieval and follows explicit physical cleanup/retention rules. A tombstone or audit record must not become another body archive. Revocation of shared access and already-delivered context follow the Host's separate completion rules.

## Change Records and Diffs

A change records one committed document mutation for inspection after saving. The filesystem backend stores one immutable, versioned JSON record per affected document under `.internal/commits/`, within the existing commit/recovery model. A multi-document operation links its records through the same Host-owned operation key and publishes them together. Independent operations do not append to one shared JSONL file. Git, a separate audit database, and shell execution are not required by the embedded backend.

The record has the following durable fields; this table defines their meaning, not a public serialization layout:

| Field                  | Meaning                                                                                                                                                                                                   |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Identity and binding   | Stable kind-prefixed change ID, operation key, exact storage identity, owning scope, and document ID supplied by trusted code                                                                             |
| Operation              | Create, revise, move, or delete; event corrections create a new document with their protected relationship                                                                                                |
| Before and after       | Exact document versions and content digests; the absent side of creation/deletion is null; a path-only move can retain the content version                                                                |
| Origin and attribution | Host-confirmed principal, save time, and trigger: explicit request, automatic organization, management edit, or admitted external edit; Host work/Run references when available                           |
| Evidence and reason    | Exact authorized source references and an optional bounded, attributed explanation; an unavailable reason remains absent                                                                                  |
| Diff                   | Format version, Markdown unified diff, and structured before/after changes to title, navigation description, logical path, applicability, time fields, sources, and correction/supersession relationships |

Deterministic code computes the diff from the exact accepted predecessor and successor, preserving whitespace and line endings. It records metadata-only changes even when the body diff is empty. Creation compares against an absent document. Deletion records the predecessor identity and deletion outcome without retaining a new removed-body patch. A model-supplied explanation is reference content, not proof of correctness or a replacement for the computed diff. External-edit admission attributes the admitting principal and records any external author as unknown unless independently established.

Complete revisions remain the historical content authority; reads and recovery do not replay a patch chain. Record and diff formats carry independent schema/format versions rather than another document version counter. Size and computation budgets are bounded before mutation; an oversized change is rejected rather than committed with a silently truncated diff. Detail reads use the document read-byte ceiling with explicit continuation bound to the exact change and record digest. A backend advertises `supports_changes` only when it preserves the complete change, commit, and retention contract; native revision support alone does not establish it.

Change records belong to the memory corpus and share its storage lifetime, backup, and restore requirements. The Host supplies bounded change-list and detail access for management; change payloads do not enter ordinary indexes, search, recall, or a new model tool. Reading a diff requires current authority over both represented versions and its protected source metadata. Shared-content grants do not automatically grant access to the source's revision history or change records. File metadata never authenticates an actor or restores authority, and file-backed history is not a tamper-proof audit store.

Content-bearing records, including explanations and removed lines, follow the corresponding memory retention and erasure policy. Logical deletion immediately blocks their content reads; physical cleanup removes the document's revisions and content-bearing change records, including pending copies and derived diff caches. Remaining operation evidence contains only permitted non-content identifiers, times, action, and outcome. Multi-document cleanup removes only the affected document's payloads and preserves other documents' records and required recovery evidence. Retention deletion is distinct from rewriting a historical change. Missing or erased details are reported as unavailable or removed, never as an empty diff or reconstructed from another retained audit copy.

## Retrieval and Model Tools

The host-authorized `MemoryDocumentStore` exposes index, search, read, TOC, create, revise, history, and delete behavior. A backend advertises unsupported revision/history operations explicitly; ordinary native records are not fabricated into a document history. The same tools serve filesystem and other document adapters when their capabilities are available.

| Tool                                                             | Contract                                                                                               |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `memory_index(path, cursor)`                                     | Bounded root or subdirectory navigation; only logical scope-relative paths and authorized entries      |
| `memory_search(query, filters, limit)`                           | Bounded document/section hits with ID, version, title, description, and locator; no whole-body preload |
| `memory_toc(reference, version)`                                 | Markdown heading tree and section locators bound to one revision/digest                                |
| `memory_read(reference, version, section, start, length)`        | Read an exact revision section or bounded range, with explicit continuation and source location        |
| `memory_add(kind, title, description, text, sources, ...)`       | Create validated memory in the Host-fixed scope; identity and request key are not model arguments      |
| `memory_revise(reference, expected_version, text, sources, ...)` | Create the next semantic/procedural revision; reject episodic rewrites                                 |
| `memory_history(reference, cursor)`                              | Bounded authorized revision metadata; read a selected version separately                               |
| `memory_forget(reference)`                                       | Confirmed authorized deletion with the Host's visibility and retention rules                           |

`memory_index` provides tree navigation without a second `memory_tree` tool. `memory_read` includes section reads without a separate `memory_read_section`. Validation, commit, and reindexing are backend operations, not model-visible shell or administrative tools. There is no separate model-facing frontmatter editor, generic `memory_write(plan)`, or requirement to call `memory_propose` before every explicit save. Pending candidates and corpus maintenance are managed by the organization workflow and Host interfaces.

Tool arguments, document size, number of headings, search candidates, index entries, and output are bounded. Document bodies are at most 256 KiB UTF-8 including their rendered content metadata; reads are at most 32 KiB per response and preserve valid UTF-8 and explicit continuation. Index context retains the 32 KiB encoded ceiling. A section larger than the read budget returns a bounded part with continuation rather than dropping its tail. TOC locators bind an exact version; a stale locator never reads the same offset from newer text. Unsupported sizes fail before a provider write, never truncate the stored body.

Search starts from an authorized scope and allowed document set, then applies kind, topic/entity, event-time, or applicability filters. Models can narrow filters but cannot supply tenant IDs, native filters, storage selectors, or audiences. The first filesystem implementation provides lexical full-text and section retrieval, including Chinese and mixed-language text. Vector retrieval is optional, not a prerequisite or the only discovery path. A keyword query can search directly; a known reference can read directly; broad discovery can start with indexes. Tree traversal is not mandatory on every query.

Current revisions are the default for semantic/procedural search; historical queries explicitly select history and time. Event corrections and contradictions remain visible with their evidence relationships. Reranking considers applicability, evidence and time rather than blindly preferring the newest text. Evidence expansion reauthorizes each source and does not automatically fetch arbitrary links.

Search/TOC caches bind store identity, document ID/version/digest, and access context. Persistent caches use supported storage semantics; an implementation does not open a remote SQLite file on the Worker by treating an Environment path as a local path. Index representation is internal and never required as a model input.

## Compatibility and Verification

New documents use the three-kind contract. Historical `daily`/`long_term` labels are not automatically interpreted as semantic types. An explicit import/classification operation retains original labels and provenance; unclassified legacy content is not silently discarded or given a new audience by classification. Existing references continue to identify their original data. The `MEMORY.md` presentation name is replaced by `_index.md`; any accepted legacy entry link resolves only to the same authorized derived index, never to an editable body.

Verification covers:

- sandbox-default selection, no Environment, explicit persistent path, missing mount, target change, and cross-Worker reconnection;
- file-only dispatch, root confinement, same-sandbox external edits, and no Worker-local fallback;
- semantic/procedural revision conflicts, immutable episodes, exact historical reads, and deletion fencing;
- duplicate requests, concurrent writers, crash boundaries, cancellation, uncertain publication, and successful writes with dirty indexes;
- exact body and metadata diffs, one change per committed mutation despite retries, rejected writes without successful change records, and no visible version without its required record;
- bounded change reads, denied source-history access through sharing, external-edit attribution, and erasure of diffs without removing another document's history;
- AST headings, repeated headings, code fences, Chinese search, oversized sections, exact continuation, and stale locators;
- authorized navigation/search/source expansion, revoked access, current-version shared reads without source-history access, and legacy classification;
- index removal/rebuild without losing corpus data, plus answer evidence coverage, retrieved bytes/tokens, tool calls, latency, and organization fidelity on representative tasks.

Formatting, schema validation, and lower retrieval cost do not establish memory quality. Evaluation compares a raw-history baseline, lexical document retrieval, and navigation plus lexical/section retrieval using the same tasks and authorization boundary. Optional vectors must justify their effect on evidence completeness and answer correctness.
