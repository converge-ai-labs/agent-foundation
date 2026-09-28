# Memory: file and record memories and their mounts

## Design position

A memory is a workspace resource that agents read and write across conversations. It has one of two kinds:

- A **file memory** (type `postgres`) is a small tree of text files whose content, history and change feed live in the Service's PostgreSQL. Every write is compare-and-swap at the moment of the call, so two conversations never overwrite each other silently: a stale write fails and returns the current content. Every change keeps the content it replaced as a revision, so people can see, diff and restore what agents wrote.
- A **record memory** keeps short text records that later conversations recall by similarity. Its records live in the backend of a [Memory Provider](#memory-providers) under a namespace the memory owns, and its type is the provider's type, such as `mem0_platform` or `mem0_oss`. Records have no versions: the last writer wins, and a write the backend does not confirm fails as unconfirmed instead of being retried. The Service keeps no copy and no history of them.

A thread mounts memories under names; each run freezes the thread's mount set at acceptance and reaches its memories only through the Harness memory tools, and a record memory also through recall at the run's first input. A memory is live, not revisioned: its content is data that runs change, and its configuration (guide, always-loaded paths) is read when an attempt starts. Nothing a run writes is rolled back when the run fails.

## Boundaries

| Concern                                                                                                                                                | Owner                                                          |
| ------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| The file store contract, file format, mount instructions, file tools, context projection and cursor semantics                                          | [Harness: file memory](../a13n-harness/21-file-memory.md)      |
| The record store contract, Memory Provider definitions, the mem0 stores, record tools and recall                                                       | [Harness: record memory](../a13n-harness/21a-record-memory.md) |
| Verbs and execution authority                                                                                                                          | [03](03-tenancy.md#authorization)                              |
| Agent configuration, its default mounts' validation and the `memory` toolset                                                                           | [04](04-resources.md#agents)                                   |
| Memory Provider resources, their scope, credential and test                                                                                            | [04](04-resources.md#provider-resources)                       |
| The registry and the outbound endpoint policy                                                                                                          | [08](08-providers.md)                                          |
| When acceptance freezes mounts, the checkpoint commit and child threads                                                                                | [05](05-runs.md)                                               |
| The outbox's staging, claims, retries and dead rows                                                                                                    | [07](07-facts-and-delivery.md#outbox)                          |
| Memory settings and their bounds                                                                                                                       | [09](09-runtime.md#settings)                                   |
| Routes, preconditions and cursors                                                                                                                      | [10](10-api.md)                                                |
| Memory resources, the PostgreSQL store, history, records through the API, namespace purges, thread mounts, what a run executes with, cursors and audit | This chapter                                                   |

## Tables

```
memories   (mem_)
  id  organization_id  workspace_id  name  description NULL  kind  type  provider_id NULL  namespace NULL
  guide NULL  always_load  labels  version  created_by_id  updated_by_id  created_at  updated_at
  kind IN ('file', 'record')
  CHECK ((type = 'postgres') = (kind = 'file'))
  CHECK ((type = 'postgres') = (provider_id IS NULL) AND (provider_id IS NULL) = (namespace IS NULL))
  CHECK (kind = 'file' OR always_load = '[]')
  UNIQUE (provider_id, namespace)
  (workspace_id, provider_id) -> memory_providers

memory_file_stores
  memory_id  seq  pruned_through_seq  content_bytes  history_bytes  file_count
  PRIMARY KEY (memory_id) -> memories ON DELETE CASCADE
  CHECK (pruned_through_seq <= seq)
  CHECK (content_bytes >= 0 AND history_bytes >= 0 AND file_count >= 0)

memory_files   (mfile_)
  id  memory_id  path  content  size  version  description NULL
  updated_by_run_id NULL  updated_by_principal_id NULL  created_at  updated_at
  memory_id -> memory_file_stores ON DELETE CASCADE
  UNIQUE (memory_id, path)

memory_file_revisions
  memory_id  seq  path  op  moved_path NULL  previous_content NULL
  run_id NULL  tool_call_id NULL  principal_id NULL  created_at
  PRIMARY KEY (memory_id, seq)
  memory_id -> memory_file_stores ON DELETE CASCADE
  op IN ('create', 'update', 'delete', 'move_out', 'move_in')
  CHECK ((op IN ('move_out', 'move_in')) = (moved_path IS NOT NULL))
  CHECK ((op IN ('create', 'move_in')) = (previous_content IS NULL))
  INDEX (memory_id, path, seq)
  INDEX (run_id) WHERE run_id IS NOT NULL

thread_memories
  thread_id  memory_id  organization_id  workspace_id  name  access  recall  created_at
  PRIMARY KEY (thread_id, name)
  UNIQUE (thread_id, memory_id)
  (workspace_id, memory_id) -> memories ON DELETE CASCADE
  access IN ('read', 'write')
```

- `memories.guide` is the memory's own guide; `NULL` inherits the deployment's (below), and `""` gives the memory none. `always_load` is the list of paths whose content leads a file memory's context. A memory's identity, scope, authorship, kind, type, provider and namespace never change.
- A record memory names its Memory Provider and its `namespace` in that provider's backend; a file memory names neither. A namespace belongs to one memory, and the memory references its provider by `(workspace_id, provider_id)`, so the database refuses a provider of another workspace.
- `memory_file_stores` is the memory's write lock and bookkeeping: `seq` numbers its changes, `pruned_through_seq` is the highest change whose revision was pruned, and the counters give the bytes of current content, the bytes of retained history and the number of files. It exists exactly as long as its memory.
- A file's `version` is the `seq` of its last change, and its ETag is `"{id}:{version}"`. `description` is derived from the content by the [file format](../a13n-harness/21-file-memory.md#file-format). A file keeps its ID when it moves.
- A revision holds the content its change replaced: `NULL` for a creation and for the destination of a move. A move is two revisions, `move_out` at the source and `move_in` at the destination, each naming the other path in `moved_path`. `run_id`, `tool_call_id` and `principal_id` attribute the change: a run's tool call sets all three, an API edit only the principal.
- Revision and file rows hang off their memory and carry no tenancy columns of their own; every path to them resolves the memory first.

## Memories

A memory is created (`write`) with `{name, description?, labels, type, provider_id?, namespace?, guide?, always_load}`. A guide is at most `memory.guide_bytes` UTF-8 bytes (`invalid_argument` at `guide`).

- **File memory.** `type` is `postgres`, the default, and `provider_id` and `namespace` are left out (`invalid_argument`). Creation also creates the memory's store row. Each `always_load` path must be a valid [file path](../a13n-harness/21-file-memory.md#file-format) (`invalid_argument` at `always_load.{index}`) and the paths must be unique; at most 64, and a path need not exist.
- **Record memory.** `provider_id` names a Memory Provider that is enabled and usable in the workspace ([04](04-resources.md#provider-resources)), and `type` must be its type (`invalid_argument` at `provider_id` or `type`). `always_load` must be empty. `namespace` is 1 to 256 printable characters with no whitespace and no `*`, which mem0 filters read as a wildcard; left out, the memory takes `a13n-` followed by the first 32 hex characters of the SHA-256 of its ID. An explicit namespace adopts whatever records the backend already holds under it. A namespace another memory of the provider owns is `already_exists` (kind `memory`, the namespace as `key`); one whose purge is still pending is 409 `conflict` with reason `namespace_purging` (kind `memory_provider`, details `namespace`). Claiming a namespace and staging its purge take a transaction-scoped advisory lock on the provider and namespace, so a claim never passes a purge being staged.

Memory paths take the memory's ID. The view adds `inherited_guide`, what a null `guide` resolves to: `memory.default_guide.file` or `memory.default_guide.record` for the memory's kind, else the Harness's built-in default for that kind; and, for a file memory, `file_count`, `content_bytes` and `history_bytes` from the store row, which are null for a record memory. Lists filter by `label`, `kind` and `type`, ordered by ID.

`PATCH` (`write`, `If-Match`) changes `name`, `description`, `labels`, `guide` and a file memory's `always_load`; `description: null` clears the description and `guide: null` returns to the inherited guide. A change applies to attempts that start afterwards.

`DELETE` (`write`, `If-Match`) deletes the memory with its thread mounts in the same transaction, and a file memory's files and history with it; deleting a thread mount bumps that thread's version. A record memory's namespace, adopted or not, is emptied by a [purge](#namespace-purge) staged in the same transaction. A run that froze a mount of the memory finds it deleted at its next memory call ([execution](#execution)).

Every change is audited as `memory.{create | update | delete}`.

## The PostgreSQL store

`PostgresFileStore` implements the Harness `SearchableFileStore` for one memory, and the API's file operations use the same functions. Each call runs in its own short session or transaction; none spans a tool call.

**Writes.** A change (write, delete, move, restore) locks the memory's store row, which linearizes the memory's changes. Under the lock it reads the file, checks the expected version (`version_mismatch` with the current file, or `None` when there is none), validates the path and content against the file format of `memory.*` settings, writes the file, appends one revision per changed path, updates the counters, prunes history and checks the byte total, and commits. Writing the same text a file already holds changes nothing. A `move` onto an existing file is `already_exists`. A missing store row is `memory_deleted`.

**Limits.** A file holds at most `memory.max_file_bytes` (`too_large`). The memory's current content and retained history together fit `memory.max_total_bytes`:

1. Each changed path keeps its newest `memory.revisions_per_file` revisions.
2. While content plus history exceeds the total, the oldest revisions of the memory are pruned first.
3. Only current content alone over the total refuses the change, with `memory_full`.

Lowering a limit keeps existing content readable; the next change of a file enforces it.

**Change feed.** Because changes are numbered under the lock, the revisions form a gap-free feed. `changes(since)` answers the store's current `seq` as its cursor and the distinct paths with a revision after `since`. It answers `FullResync` when `since` is absent, not a number, beyond the current `seq`, or older than `pruned_through_seq`, since pruned revisions can no longer list their paths. Purging a file's history therefore makes every older cursor resync.

**Search.** `search` matches lines in SQL, in path and line order, returning up to `limit` lines and whether more matched. A literal pattern is a substring, lower-cased on both sides unless case-sensitive; a regular expression uses PostgreSQL's syntax (`~`, or `~*` without case sensitivity). An invalid expression, or a search cut off by `database.statement_timeout`, is `invalid_pattern`.

**Purge.** The contract's `purge()` removes every file and revision (`write`); the Service itself uses it only through memory deletion's cascade.

## Files and history through the API

People read and correct what agents wrote through the same store. Reads need `read`; edits and restores need `run`, since anyone who may run an agent can make it write through the memory tools; a history purge needs `write`, because it destroys evidence.

- `GET …/files?prefix=` lists files (without content) in path order under a directory prefix (`""` for all, else a path ending in `/`); `GET …/files/{path}` returns one with its content and ETag.
- `POST …/files {path, content}` creates a file (201, `already_exists` for a taken path). `PUT …/files/{path} {content}` replaces the file `If-Match` names. `POST …/files/move {source, destination}` moves the file `If-Match` names to a free path. `DELETE …/files/{path}` deletes the file `If-Match` names.
- `GET …/revisions?path=&run_id=` lists revisions newest first, without content. `GET …/revisions/{seq}` adds `previous_content`, `content` (the path's content right after the change: what the path's next retained change replaced, else what is there now) and unified-diff `hunks` between them.
- `POST …/revisions/{seq}/restore` sets the revision's path back to the content that change replaced, as a new change: restoring a creation or a move's destination deletes the file there. `If-Match` names the file currently at the path; with no file there it must be left out, and naming one that is gone is `precondition_failed`. The answer is `{path, file}`, with `file` null when the restore deleted it.
- `DELETE …/revisions?path=` deletes every retained revision of one path and answers `{purged}`; the file itself stays.

A store refusal maps to the API as: `not_found` (kind `memory_file`), `already_exists`, `invalid_argument` at the path, pattern or `content` field, `payload_too_large` (`limit`), and `conflict` with reason `memory_full` (`limit`). A file or revision route on a record memory is 409 `conflict` with reason `memory_kind` (details `expected: "file"`). File changes are audited on the memory as `memory.file.{create | update | move | delete | restore}` and `memory.history.purge`, with paths and sequence numbers, never content. A run's writes are not audited; their revisions attribute them.

## Memory Providers

A Memory Provider is the [provider resource](04-resources.md#provider-resources) kind `memory` (`/memory-providers`): an account of a record memory backend, whose `type` selects a Harness `MemoryProviderDefinition` ([08](08-providers.md#registry)). Memory Providers back record memories only; the Service stores file memories itself, and `postgres` is no provider type. A provider's test opens a store on the namespace `a13n-probe` and lists one page of it, which reads and changes nothing a memory owns.

A record store is opened from values read in a short session, after it closes: the provider's configuration and revealed credential, and the memory's namespace. Every backend call runs outside any database session, over the host's outbound client under the [endpoint policy](08-providers.md#outbound-endpoint-policy), bounded by `providers.operation_seconds` and `providers.response_bytes`. A transport failure, a refused endpoint or an answer over the byte bound is the store error `unavailable` for a read or a purge, and `write_unconfirmed` for an add, an update or a delete, which may have happened.

## Records through the API

People read and correct what agents recorded through the same stores. Reads need `read`; adding, updating and deleting need `run`, since anyone who may run an agent can make it write through the record tools. Every call also needs the memory's provider enabled (`disabled`, kind `memory_provider`).

- `GET …/records?limit=&cursor=` lists records in the backend's order, a page of up to `limit` (1 to 100, default 50); `cursor` is the store's own opaque `next_cursor`. A self-hosted mem0 server lists only its first 1000 records of a namespace; search still reaches every record ([Harness: mem0](../a13n-harness/21a-record-memory.md#mem0)).
- `POST …/records/search {query, limit}` answers up to `limit` (1 to 100, default 10) records closest in meaning to `query`, closest first, with their `score`; `next_cursor` is null. The query travels in the body, never the URL.
- `POST …/records {text}` adds a record (201). `PUT …/records/{record} {text}` replaces its whole text; records carry no version, so it takes no `If-Match` and the last writer wins. `DELETE …/records/{record}` deletes it (204).

A record is `{id, text, score, updated_at}`, with `score` and `updated_at` null when the backend does not report them. A text holds 1 to `memory.record_chars` characters and is not blank (`invalid_argument` at `text`). A store refusal maps to the API as: `record_not_found` to `not_found` (kind `memory_record`), including a record of another namespace; `invalid_text` to `invalid_argument` at `text`; `invalid_cursor` to `invalid_argument` at `cursor`; `write_unconfirmed` to 409 `conflict` with reason `write_unconfirmed`, since the write may or may not have happened; and anything else to `unavailable` with dependency `memory:{type}`. A record route on a file memory is 409 `conflict` with reason `memory_kind` (details `expected: "record"`).

Record changes are audited on the memory as `memory.record.{create | update | delete}` with the record ID, never its text, after the backend confirms them. A run's record writes are not audited; its history holds the tool calls that made them.

## Namespace purge

Deleting a record memory stages one `memory_purge` [outbox](07-facts-and-delivery.md#outbox) delivery in its transaction, keyed by the memory ID, with target `{provider_id, namespace}`. Its handler reads the provider, enabled or not, since a purge finishes a deletion rather than starting new use, and calls the store's `purge()` outside any session within `providers.operation_seconds`. A purge the backend confirms settles `delivered`. A store error or a timeout raises `Undelivered` with its code, so the outbox retries it with backoff and ends it `dead` after the `memory_purge` policy’s `max_attempts`; a provider type the deployment no longer registers ends it `dead` at once with `type_unavailable`. Settings keep twice `providers.operation_seconds` below the `memory_purge` policy’s `lease_seconds`, so a purge settles within its claim. The mem0 Platform accepts a purge and completes it asynchronously, so its delivery settles on acceptance; a self-hosted mem0 server purges only for an admin key, and without one every attempt is `unavailable` until the delivery ends `dead`. While a purge is `pending`, no memory can claim its namespace; once it is `delivered` or `dead`, the namespace is free again, and a new memory adopting it after a dead purge finds what the backend kept.

## Mounts

A thread's **memory mounts** name the memories its later runs use: `{name, memory_id, access, recall}`. A name matches `^[a-z][a-z0-9-]{0,62}$`, the model addresses the memory by it, and `access` is `read` (reading and searching tools) or `write` (every tool). `recall`, true by default, lets a record memory recall records into each run's first input; file memories ignore it. `POST …/threads/{thread}/memories` adds one, `PATCH …/threads/{thread}/memories/{name} {access?, recall?}` changes one, and `DELETE …/threads/{thread}/memories/{name}` removes one; all need `run` and the thread `If-Match`, answer with the new thread ETag, and are audited as `thread_memory.create`, `.update` and `.delete`. Adding and changing need an open thread, and adding a memory of the thread's workspace. Changing a mount's memory is explicit: remove the name, then add it again. `GET` lists the mounts with the thread ETag.

- A duplicate name is `already_exists` (kind `mount`); a memory already mounted on the thread is `conflict` (`already_mounted`).
- A thread holds at most `memory.mounts_per_thread` mounts: a path that would add more is `conflict` (`memory_mount_limit`, details `limit`). A request naming more than 32 is `invalid_argument`.
- Mounting locks the named memories `FOR SHARE` in ID order, so none is deleted before the mounts commit.

Mounts reach a thread in these ways:

- **New threads and forks** may name initial `memories`, added in the transaction that creates the thread. A fork also copies the origin thread's mounts; copied and named mounts count together.
- **Agent defaults.** An agent configuration's `memory_mounts` ([04](04-resources.md#agents)) join at the thread's **first acceptance**, before any run of the thread has sealed: each default whose name and memory the thread does not use yet is mounted. A default whose memory is gone refuses the start, and the entry fails with `invalid_argument` at `memory_mounts.{index}.memory_id`; defaults that would exceed the limit fail it with `memory_mount_limit`. Afterwards only the thread's own mounts count, so removing a default's mount keeps it removed.
- **Child threads** adopt their parent run's frozen memory mounts, except memories deleted since, and then take their own agent's defaults at their first acceptance.
- **Archive** removes the thread's memory mounts with its environment mounts ([05](05-runs.md#waiting-interrupt-and-fork)).

Mount edits affect later runs only. **Acceptance** freezes the thread's mounts, ordered by name, into `runs.memory_mounts` `[{name, memory_id, access, recall}]`, which never changes afterwards.

## Execution

The attempt's plan session reads each frozen mount's memory, skipping memories deleted since acceptance, and the root agent's enabled `memory` tools. A record memory's plan also reads its provider, and the mount is skipped when the provider is disabled or its type is not registered. Only the root agent of the run's inline graph gets memory: inline subagents do not, and async children have their own runs.

For its file memories, the attempt builds the Harness `FileMemoryCapability` with:

- one `PostgresFileStore` per mount, bound to its memory, and a `FileMount` with the mount's name and access, the memory's `guide`, or its `inherited_guide` when null, its `always_load` paths and the memory ID as cursor key;
- limits from `memory.*`: the file format, `context_bytes`, `always_load_bytes` and `write_retries`;
- the enabled file tools of the `memory` toolset, `file_view`, `file_grep`, `file_create`, `file_edit`, `file_append`, `file_move` and `file_delete`; tool permissions apply to their tool IDs `memory.file.*`. With the toolset disabled the run still receives its memories' context, with no tools;
- `Origin(run_id, principal_id)`, to which each tool call adds its ID;
- the run's memory cursors.

For its record memories, the attempt builds the Harness `RecordMemoryCapability` with:

- one record store per mount, opened when the attempt starts and closed when it ends, as [Memory Providers](#memory-providers) open them; a mount whose store does not open is skipped with a warning;
- a `RecordMount` with the mount's name, access and `recall`, and the memory's `guide`, or its `inherited_guide` when null;
- limits from `memory.*`: `record_chars`, `recall_limit`, `recall_bytes` and `recall_seconds`;
- the enabled record tools of the `memory` toolset, `record_search`, `record_list`, `record_add`, `record_update` and `record_delete`, with tool IDs `memory.record.*`. With them disabled the run still recalls.

Recall is [the Harness's](../a13n-harness/21a-record-memory.md#recall): at a run's first input, each mount with `recall` searches for the input's closest records and adds them as untrusted data; a search that fails or times out skips that memory and never fails the run.

**Per-call checks.** Before each store call the store checks the run's principal under the run's frozen authority: `read` for reads, `run` for changes. A refusal is the store error `forbidden`, and a memory deleted meanwhile is `memory_deleted`; a record memory's provider disabled meanwhile is `unavailable`. Each fails only that tool call or recall. The effective access of a call is therefore the authority, intersected with the mount's access, intersected with the agent's tool configuration. A grant revoked during the run is caught by the attempt's authority renewal ([05](05-runs.md#claim-heartbeat-and-authority)). A record store's checks read the database in their own short session, and its backend call follows after the session closes. Record store calls are not paid dispatches and do not pass the run's call check ([05](05-runs.md#execute)).

**Cursors.** `runs.memory_cursors` maps memory IDs to the change-feed cursor of the context the run's history holds. A new run starts from its parent's cursors for each memory still mounted under the same name; any other memory, including one rebound to another name, gets full context. The `Boundaries` capability snapshots the capability's cursors together with the state it exports at each boundary, and the checkpoint commit stores that snapshot with the state pointer in the same fenced transaction ([05](05-runs.md#assignment-and-incorporation)); a completed or waiting outcome commits the final snapshot. A recovered attempt therefore starts from cursors that match its restored history, and context the lost attempt delivered after its last commit is delivered again. What the Harness does with the cursors is [its](../a13n-harness/21-file-memory.md#context-projection-and-cursors): context only at a run's first input, full when there is no cursor or the feed resyncs, changed paths otherwise, and cleared cursors after a context restore. A run's own writes appear in its thread's next change list.

**Crash recovery.** A process can stop after a write commits and before the next checkpoint. The write tools declare no recovery, so the recovered attempt reports an unknown outcome for that call, and the model reads the file, or searches the records, before writing again. A committed file revision keeps the run and tool call ID. Durable deduplication of repeated writes is not provided.

## Settings

| Setting                                                                          | Governs                                                                                  |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `memory.max_file_bytes`, `description_chars`, `frontmatter_bytes`, `path_bytes`  | the file format every store call and API edit applies                                    |
| `memory.revisions_per_file`, `memory.max_total_bytes`                            | history retention and the per-memory byte total                                          |
| `memory.mounts_per_thread`                                                       | mounts per thread, checked when mounts are added and at acceptance                       |
| `memory.guide_bytes`, `memory.default_guide.file`, `memory.default_guide.record` | a guide's size, and the deployment's guide per kind for memories that inherit            |
| `memory.context_bytes`, `memory.always_load_bytes`, `memory.write_retries`       | a run's memory context budget, its always-loaded share per memory, and tool CAS re-reads |
| `memory.record_chars`                                                            | a record's length, through the API and the record tools, at most 8000                    |
| `memory.recall_limit`, `memory.recall_bytes`, `memory.recall_seconds`            | records recalled per memory, a recall block's size, and the recall timeout               |

Loading refuses `always_load_bytes` above `context_bytes`, `frontmatter_bytes` not below `max_file_bytes`, `max_file_bytes` above `max_total_bytes`, a default guide over `guide_bytes`, and twice `providers.operation_seconds` not below the `memory_purge` policy’s `lease_seconds` ([09](09-runtime.md#settings)).

## Invariants

- A memory's changes are linearized by its store row lock, numbered without gaps, and each writes one revision per changed path in the same transaction.
- A change never overwrites content its caller did not name: tools and the API both write under a version check.
- Retained history and current content fit `memory.max_total_bytes`; only current content over it refuses a change.
- A change cursor never skips a change: pruning below it forces a full resync.
- A run's memory mounts are frozen at acceptance; its cursors move only with its checkpoints.
- Deleting a memory deletes its thread mounts and a file memory's files and history together, and every later call of a run holding it fails with `memory_deleted`.
- A namespace belongs to at most one memory, and never to a new one while a purge of it is pending.
- Deleting a record memory stages its namespace purge in the same transaction.
- No database session or transaction is open during a Memory Provider call.
