# Memory: file memories and their mounts

## Design position

A memory is a workspace resource that agents read and write across conversations. The Service offers one kind, `file`, with one type, `postgres`: a small tree of text files whose content, history and change feed live in PostgreSQL. A thread mounts memories under names; each run freezes the thread's mount set at acceptance and reaches its memories only through the Harness file memory tools. Every write is compare-and-swap at the moment of the call, so two conversations never overwrite each other silently: a stale write fails and returns the current content.

A memory is live, not revisioned: its files are data that runs change, and its configuration (guide and always-loaded paths) is read when an attempt starts. Nothing a run writes is rolled back when the run fails. Every change keeps the content it replaced as a revision, so people can see, diff and restore what agents wrote.

## Boundaries

| Concern                                                                                                                | Owner                                                     |
| ---------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| The store contract, file format, mount instructions, file tools, context projection and cursor semantics               | [Harness: file memory](../a13n-harness/21-file-memory.md) |
| Verbs and execution authority                                                                                          | [03](03-tenancy.md#authorization)                         |
| Agent configuration, its default mounts' validation and the `memory` toolset                                           | [04](04-resources.md#agents)                              |
| When acceptance freezes mounts, the checkpoint commit and child threads                                                | [05](05-runs.md)                                          |
| Memory settings and their bounds                                                                                       | [09](09-runtime.md#settings)                              |
| Routes, preconditions and cursors                                                                                      | [10](10-api.md)                                           |
| Memory resources, the PostgreSQL store, history, thread mounts, what a run executes with, cursor persistence and audit | This chapter                                              |

## Tables

```
memories   (mem_)
  id  organization_id  workspace_id  key  name  description NULL  kind  type  guide NULL  always_load  labels
  version  created_by_id  updated_by_id  created_at  updated_at
  kind IN ('file')
  type IN ('postgres')
  UNIQUE (workspace_id, key)

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
  thread_id  memory_id  organization_id  workspace_id  name  access  created_at
  PRIMARY KEY (thread_id, name)
  UNIQUE (thread_id, memory_id)
  (workspace_id, memory_id) -> memories ON DELETE CASCADE
  access IN ('read', 'write')
```

- `memories.guide` is the memory's own guide; `NULL` inherits the deployment's (below), and `""` gives the memory none. `always_load` is the list of paths whose content leads the memory's context. A memory's identity, scope and authorship never change.
- `memory_file_stores` is the memory's write lock and bookkeeping: `seq` numbers its changes, `pruned_through_seq` is the highest change whose revision was pruned, and the counters give the bytes of current content, the bytes of retained history and the number of files. It exists exactly as long as its memory.
- A file's `version` is the `seq` of its last change, and its ETag is `"{id}:{version}"`. `description` is derived from the content by the [file format](../a13n-harness/21-file-memory.md#file-format). A file keeps its ID when it moves.
- A revision holds the content its change replaced: `NULL` for a creation and for the destination of a move. A move is two revisions, `move_out` at the source and `move_in` at the destination, each naming the other path in `moved_path`. `run_id`, `tool_call_id` and `principal_id` attribute the change: a run's tool call sets all three, an API edit only the principal.
- Revision and file rows hang off their memory and carry no tenancy columns of their own; every path to them resolves the memory first.

## Memories

A memory is created (`write`) with `{key, name, description?, labels, type: "postgres", guide?, always_load}`, which also creates its store row. `key` is unique in the workspace (`already_exists`). A guide is at most `memory.guide_bytes` UTF-8 bytes (`invalid_argument` at `guide`). Each `always_load` path must be a valid [file path](../a13n-harness/21-file-memory.md#file-format) (`invalid_argument` at `always_load.{index}`) and the paths must be unique; at most 64, and a path need not exist.

Memory paths take the memory's ID. The view adds `effective_guide`, the guide a run uses: the memory's own, else `memory.default_guide.file`, else the Harness's built-in default; and `file_count`, `content_bytes` and `history_bytes` from the store row. Lists filter by `label`, ordered by ID.

`PATCH` (`write`, `If-Match`) changes `name`, `description`, `labels`, `guide` and `always_load`; `description: null` clears the description and `guide: null` returns to the inherited guide. A change applies to attempts that start afterwards.

`DELETE` (`write`, `If-Match`) deletes the memory with its files, history and thread mounts in the same transaction; deleting a thread mount bumps that thread's version. A run that froze a mount of it finds it deleted at its next memory call ([execution](#execution)).

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

A store refusal maps to the API as: `not_found` (kind `memory_file`), `already_exists`, `invalid_argument` at the path, pattern or `content` field, `payload_too_large` (`limit`), and `conflict` with reason `memory_full` (`limit`). File changes are audited on the memory as `memory.file.{create | update | move | delete | restore}` and `memory.history.purge`, with paths and sequence numbers, never content. A run's writes are not audited; their revisions attribute them.

## Mounts

A thread's **memory mounts** name the memories its later runs use: `{name, memory_id, access}`. A name matches `^[a-z][a-z0-9-]{0,62}$`, the model addresses the memory by it, and `access` is `read` (view and search) or `write` (every tool). `POST …/threads/{thread}/memories` adds one and `DELETE …/threads/{thread}/memories/{name}` removes one; both need `run` and the thread `If-Match`, answer with the new thread ETag, and are audited as `thread_memory.create` and `.delete`. Adding needs an open thread and a memory of the thread's workspace. Changing a mount's memory or access is explicit: remove the name, then add it again. `GET` lists the mounts with the thread ETag.

- A duplicate name is `already_exists` (kind `mount`); a memory already mounted on the thread is `conflict` (`already_mounted`).
- A thread holds at most `memory.mounts_per_thread` mounts: a path that would add more is `conflict` (`memory_mount_limit`, details `limit`). A request naming more than 32 is `invalid_argument`.
- Mounting locks the named memories `FOR SHARE` in ID order, so none is deleted before the mounts commit.

Mounts reach a thread in these ways:

- **New threads and forks** may name initial `memories`, added in the transaction that creates the thread. A fork also copies the origin thread's mounts; copied and named mounts count together.
- **Agent defaults.** An agent configuration's `memory_mounts` ([04](04-resources.md#agents)) join at the thread's **first acceptance**, before any run of the thread has sealed: each default whose name and memory the thread does not use yet is mounted. A default whose memory is gone refuses the start, and the entry fails with `invalid_argument` at `memory_mounts.{index}.memory_id`; defaults that would exceed the limit fail it with `memory_mount_limit`. Afterwards only the thread's own mounts count, so removing a default's mount keeps it removed.
- **Child threads** adopt their parent run's frozen memory mounts, except memories deleted since, and then take their own agent's defaults at their first acceptance.
- **Archive** removes the thread's memory mounts with its environment mounts ([05](05-runs.md#waiting-interrupt-and-fork)).

Mount edits affect later runs only. **Acceptance** freezes the thread's mounts, ordered by name, into `runs.memory_mounts` `[{name, memory_id, access}]`, which never changes afterwards.

## Execution

The attempt's plan session reads each frozen mount's memory, skipping memories deleted since acceptance, and the root agent's enabled `memory` tools. Only the root agent of the run's inline graph gets memory: inline subagents do not, and async children have their own runs.

The attempt builds the Harness `FileMemoryCapability` with:

- one `PostgresFileStore` per mount, bound to its memory, and a `FileMount` with the mount's name and access, the memory's `effective_guide`, its `always_load` paths and the memory ID as cursor key;
- limits from `memory.*`: the file format, `context_bytes`, `always_load_bytes` and `write_retries`;
- the enabled tools of the `memory` toolset, whose tools are `file_view`, `file_grep`, `file_create`, `file_edit`, `file_append`, `file_move` and `file_delete`; tool permissions apply to their tool IDs `memory.file.*`. With the toolset disabled the run still receives its memories' context, with no tools;
- `Origin(run_id, principal_id)`, to which each tool call adds its ID;
- the run's memory cursors.

**Per-call checks.** Before each store call the store checks the run's principal under the run's frozen authority: `read` for reads, `run` for changes. A refusal is the store error `forbidden`, and a memory deleted meanwhile is `memory_deleted`; both fail only that tool call. The effective access of a call is therefore the authority, intersected with the mount's access, intersected with the agent's tool configuration. A grant revoked during the run is caught by the attempt's authority renewal ([05](05-runs.md#claim-heartbeat-and-authority)).

**Cursors.** `runs.memory_cursors` maps memory IDs to the change-feed cursor of the context the run's history holds. A new run starts from its parent's cursors for each memory still mounted under the same name; any other memory, including one rebound to another name, gets full context. The `Boundaries` capability snapshots the capability's cursors together with the state it exports at each boundary, and the checkpoint commit stores that snapshot with the state pointer in the same fenced transaction ([05](05-runs.md#assignment-and-incorporation)); a completed or waiting outcome commits the final snapshot. A recovered attempt therefore starts from cursors that match its restored history, and context the lost attempt delivered after its last commit is delivered again. What the Harness does with the cursors is [its](../a13n-harness/21-file-memory.md#context-projection-and-cursors): context only at a run's first input, full when there is no cursor or the feed resyncs, changed paths otherwise, and cleared cursors after a context restore. A run's own writes appear in its thread's next change list.

**Crash recovery.** A process can stop after a write commits and before the next checkpoint. The write tools declare no recovery, so the recovered attempt reports an unknown outcome for that call and the model reads the file before writing again. The committed revision keeps the run and tool call ID. Durable deduplication of repeated writes is not provided.

## Settings

| Setting                                                                         | Governs                                                                                  |
| ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `memory.max_file_bytes`, `description_chars`, `frontmatter_bytes`, `path_bytes` | the file format every store call and API edit applies                                    |
| `memory.revisions_per_file`, `memory.max_total_bytes`                           | history retention and the per-memory byte total                                          |
| `memory.mounts_per_thread`                                                      | mounts per thread, checked when mounts are added and at acceptance                       |
| `memory.guide_bytes`, `memory.default_guide.file`                               | a guide's size, and the deployment's guide for memories that inherit                     |
| `memory.context_bytes`, `memory.always_load_bytes`, `memory.write_retries`      | a run's memory context budget, its always-loaded share per memory, and tool CAS re-reads |

Loading refuses `always_load_bytes` above `context_bytes`, `frontmatter_bytes` not below `max_file_bytes`, `max_file_bytes` above `max_total_bytes`, and a default guide over `guide_bytes` ([09](09-runtime.md#settings)).

## Invariants

- A memory's changes are linearized by its store row lock, numbered without gaps, and each writes one revision per changed path in the same transaction.
- A change never overwrites content its caller did not name: tools and the API both write under a version check.
- Retained history and current content fit `memory.max_total_bytes`; only current content over it refuses a change.
- A change cursor never skips a change: pruning below it forces a full resync.
- A run's memory mounts are frozen at acceptance; its cursors move only with its checkpoints.
- Deleting a memory deletes its files, history and thread mounts together, and every later call of a run holding it fails with `memory_deleted`.
