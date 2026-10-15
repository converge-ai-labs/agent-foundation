---
title: Memory
description: Workspace memories that agents keep across conversations, as versioned files or recalled records.
---

Memory keeps preferences, decisions and reference facts across conversations. Choose how agents share it:

| Task                                                                              | Use                                         |
| --------------------------------------------------------------------------------- | ------------------------------------------- |
| Share knowledge across sessions, keep session notes, and organize saved knowledge | [Memory Spaces](#memory-spaces)             |
| Mount a file store or mem0 namespace directly on threads                          | [Standalone memories](#standalone-memories) |

Resources belong to a workspace. Accepted runs keep their selected memory mounts; new selections apply to later runs. All API paths below are under `/api/v1`.

## Memory Spaces

A **Memory Space** holds shared knowledge and session notes in one versioned file store. Create Spaces for your application's users or projects, then select them in sessions.

1. Open **Memory Spaces → Spaces** and create a named Space. In **Configuration**, set its instructions, always-loaded files and optional organizer model.
2. In an agent's **Memory** section, enable memory participation and select the Spaces new sessions should use. Give each selection a tool-visible name and read or write access.
3. When starting a session, inherit the agent defaults or choose an explicit selection. The session header's **Memory** control changes participation and selection for future runs.
4. Open a Space to browse **Files**, inspect **File history**, review **Organization** rounds and see associated **Sessions**.

Each ordinary run sees `global/` and its own `sessions/{session_id}/` folder, with the binding's read or write access. Context includes `global/MEMORY.md` by default and the session's `MEMORY.md` when present. Workspace permissions govern management of all Space files through the Console and API.

### Select Spaces

Create a Space with `POST /memory-spaces` (`write`):

```json
{
  "name": "Product knowledge",
  "organization": {"enabled": true, "model": "your-model-key"}
}
```

Use its returned `id` in the agent's `config.memory`, `POST /sessions`, or `PATCH /sessions/{session_id}`:

```json
{
  "memory": {
    "enabled": true,
    "spaces": [{"name": "product", "space_id": "mspace_…", "access": "write"}]
  }
}
```

Session selection needs `run`; PATCH also needs the session's `If-Match`. Names and Space IDs must be unique in the selection. Omitted fields inherit agent defaults at the first accepted business run. An explicit `spaces` list replaces the selection; `[]` supplies no Space context or tools. The session keeps its choices across later agent changes and branches. Changing agent defaults or Space configuration needs `write`.

Turning participation off keeps the selection. Disabling a Space excludes it from new runs and preserves its files, history and bindings. Accepted runs retain their frozen mounts. Space mount names must be distinct from standalone thread mount names.

### Organize saved knowledge

Configure an organizer model, then choose **Organize now** or enable **Automatic organization**. Automatic rounds process saved changes, including API edits, while the store contains files. The organizer reads shared knowledge and all session folders to maintain `global/MEMORY.md`. Each round uses a fresh thread in a dedicated organizer session, with only that Space's memory tools and a 200-model-request ceiling.

In **Configuration → Organization**, choose **Any saved change** or **After a number of changes**. Count mode accumulates saved file changes from agents, Console and API. A rename counts once. Unchanged saves, rejected writes, history cleanup and organizer edits do not count. Each admitted round consumes the changes present when it starts; changes saved during the round remain for later, even if the round fails or is cancelled. Counts survive restarts and history cleanup. Existing Spaces start counting new changes when this capability is installed.

Set the **Check interval** (10–86400 seconds, default 10) to control how often the Space checks for eligible changes. Set the **Cooldown** (0–604800 seconds, default 300) to control the wait after a round ends or cannot start. Automatic organization requires both its trigger and cooldown, at the next eligible check. Checks share a 10-second service sweep, so these values are minimum delays. Saved deadlines keep their original timing; edited intervals apply to later checks and rounds. **Organize now** bypasses the interval, threshold and cooldown.

For example, include this policy when creating or updating a Space:

```json
{
  "organization": {
    "enabled": true,
    "model": "your-model-key",
    "check_interval_seconds": 60,
    "cooldown_seconds": 900,
    "trigger": {"type": "change_count", "count": 10}
  }
}
```

The **Organization** tab shows status and run history, with costs, traces and file revisions. These runs stay separate from business conversations. Failed or cancelled rounds retain saved edits. If retained history is insufficient to confirm progress, status reports a history gap; **Organize now** requests another round.

Read status at `GET /memory-spaces/{id}/organization`, request a round with `POST /memory-spaces/{id}/organizations`, and list rounds with `GET` on that same path. `/memory-spaces/{id}/sessions` lists associated business sessions. Lists paginate with `limit` and `cursor`. File and revision APIs use the Space's returned `memory_id` under `/memories/{memory_id}`. The backing store is managed through the Space and stays out of standalone Memory lists.

## Standalone memories

- A **file memory** is a small tree of text files. The Service keeps its files, and the history of every change, in PostgreSQL. Every write checks the file's version at the moment it runs, so a change based on stale content fails and returns the current file instead of overwriting another run's work. [File memory](../a13n-harness/memory.md) explains what the model sees and how to write good memory files.
- A **record memory** is a set of short text records, such as "The user prefers metric units", kept in a [mem0](https://mem0.ai) backend through a [Memory Provider](#set-up-a-memory-provider). At the start of each run, the records closest to the run's input are **recalled** into it. Records have no versions: the last write wins.

Threads **mount** memories under names. Each run freezes the thread's memory mounts when it is accepted, sees each memory's context or recalled records at its start, and changes the memories through the `memory_file_*` and `memory_record_*` tools.

## Create a file memory

Creating and changing memories needs `write`:

```sh
curl -X POST "$A13N_URL/api/v1/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Team conventions", "type": "postgres",
       "guide": "Keep one file per topic. Record decisions with their reason.",
       "always_load": ["README.md"]}'
```

- `type` is `postgres`, the Service's own store and the default. The response's `id` (`mem_…`) identifies the memory; memories have no key.
- `guide` tells agents what belongs in this memory and how to organize it, up to `memory.guide_bytes`. Leave it out or set it to `null` to use the deployment's guide for the memory's kind (`memory.default_guide.file` or `memory.default_guide.record`, else the built-in one); `""` gives the memory no guide. The memory's `inherited_guide` shows the guide `null` resolves to.
- `always_load` names up to 64 paths whose content is included first, within the context budget, when a thread first sees the memory. A path need not exist yet. Only principals with `write` on the memory set them, so a run cannot pin its own writes into every later thread.
- `PATCH …/memories/{memory_id}` with the memory's `If-Match` changes `name`, `description`, `labels`, `guide` and `always_load`. Attempts that start afterwards use the change, including a later attempt of a run that is already running.
- `DELETE …/memories/{memory_id}` with `If-Match` deletes the memory with its files, history and thread mounts. A running run that uses it gets `memory_deleted` from its next memory tool call.

`GET …/memories` lists the workspace's memories, filtered by `label`, `kind` (`file` or `record`) and `type`. A file memory shows `file_count`, `content_bytes` and `history_bytes`; they are null for a record memory.

## Record memories

### Set up a Memory Provider

A Memory Provider is a [provider](resources.md#providers) of kind `memory`: one account of a record memory backend. Add it to the workspace in `/api/v1/memory-providers`.

For the hosted **mem0 Platform**, use type `mem0_platform` with an API key from the [mem0 dashboard](https://app.mem0.ai/dashboard/api-keys); `config.base_url` defaults to `https://api.mem0.ai`:

```sh
curl -X POST "$A13N_URL/api/v1/memory-providers" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"type": "mem0_platform", "name": "mem0", "config": {}, "credential": {"api_key": "m0-..."}}'
```

For **self-hosted mem0**, run the [mem0 REST server](https://docs.mem0.ai/open-source/features/rest-api) with its own model, embedder and vector store, and use type `mem0_oss` with the server's address as `base_url`:

```sh
curl -X POST "$A13N_URL/api/v1/memory-providers" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"type": "mem0_oss", "name": "mem0",
       "config": {"base_url": "https://mem0.internal.example.com"}, "credential": {"api_key": "..."}}'
```

The self-hosted credential is optional and is sent as `X-API-Key`; purging a deleted memory's records needs the server's admin key. A plain-HTTP server needs an explicit origin allowance in the deployment's [outbound policy](configuration.md#outbound-requests). The self-hosted server lists at most 1000 records of a memory.

`POST …/memory-providers/{provider_id}/test` lists one page of a namespace no memory uses, which checks the address and key without changing anything.

### Create a record memory

```sh
curl -X POST "$A13N_URL/api/v1/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "User facts", "type": "mem0_platform", "provider_id": "memprov_...",
       "guide": "Record one stable fact about the user per record."}'
```

- `type` is the provider's type, and the provider must be an enabled Memory Provider of the workspace. `always_load` does not apply.
- Each record memory owns a **namespace** in the backend, which is mem0's `user_id`. By default it is `a13n-` and 32 hex characters derived from the memory's ID. Set `namespace` to adopt records that already exist under a `user_id`, such as ones your application wrote; it is 1 to 256 printable characters with no whitespace and no `*`. One namespace belongs to one memory: another memory using it is `409 already_exists`.
- `type`, `provider_id` and `namespace` never change.
- Deletion purges the namespace in the background. Until it finishes, a new memory cannot take that namespace (`namespace_purging`). If the backend keeps refusing, records can remain after the retry budget is exhausted; see [outbox policies](operations.md#outbox-retention-and-capacity).

### Read and edit records

People can read and correct what agents recorded. Reading needs `read`; adding, updating and deleting need `run`.

| Request                                      | Does                                                                                       |
| -------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `GET …/memories/{id}/records?limit=&cursor=` | Lists records in the backend's order, with `next_cursor` for the next page                 |
| `POST …/memories/{id}/records/search`        | Returns up to `limit` records closest in meaning to `{query}`, closest first, with `score` |
| `POST …/memories/{id}/records`               | Adds `{text}`                                                                              |
| `PUT …/memories/{id}/records/{record_id}`    | Replaces the record's whole text with `{text}`                                             |
| `DELETE …/memories/{id}/records/{record_id}` | Deletes the record                                                                         |

A record holds 1 to `memory.record_chars` characters (8000 by default). Records have no ETag, so the last write wins. A write the backend does not confirm answers `409 conflict` with reason `write_unconfirmed`: it may or may not have happened, so list or search before trying again. A backend that cannot answer is `503 unavailable` with dependency `memory:{type}`, and a disabled provider is `422 disabled`. Record changes are audited with the record ID, never the text.

Record responses also include optional `provenance`. In the Console, choose **View source** in a record's menu to see its known creation and last platform-confirmed write, the acting identity, and a link to an accessible run. Technical details include the run, attempt and tool-call IDs. Existing or imported records may have no source. An update to one of these records records only the latest write, not an invented creation source.

Sources are saved separately from provider content. Failed source indexing does not retry or fail a confirmed memory write. Direct provider edits may be missing; if the current text differs from the last recorded write, the Console warns that its sources may be stale. Matching text does not prove that no external edits occurred. This is write attribution, not a full version history or a citation of the user's original message.

### Recall

When a run starts, each mounted record memory with `recall` on searches for the `memory.recall_limit` records (5 by default) closest to the run's input, and the run receives them before its input as untrusted data, at most `memory.recall_bytes` (8 KiB) per memory. Recall waits at most `memory.recall_seconds` (2 seconds); a memory whose search fails or is too slow is skipped, and the run continues without it. Only the first input of a run recalls, so a run's history does not fill with repeated records.

## Mount a memory on a thread

A thread's memory mounts decide what its later runs use:

```sh
curl -X POST "$A13N_URL/api/v1/threads/$THREAD/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" -H "If-Match: $THREAD_ETAG" \
  -d '{"name": "team", "memory_id": "mem_...", "access": "write"}'
```

- `name` matches `^[a-z][a-z0-9-]{0,62}$`; the model addresses the memory by it. `access` is `read`, which offers only the reading and searching tools, or `write`, which offers every tool.
- `recall` (default `true`) lets a record memory recall records into each run; file memories ignore it.
- A thread mounts each name and each memory once. `PATCH …/threads/{thread_id}/memories/{name}` changes a mount's `access` and `recall`; to change its memory, remove it and add it again.
- A thread holds at most `memory.mounts_per_thread` memory mounts (8 by default); a request beyond that is `409 conflict` with reason `memory_mount_limit`.
- Mount changes take the **thread's** `If-Match`, return the thread's new ETag, need `run`, and affect runs accepted afterwards. `GET …/threads/{thread_id}/memories` lists the mounts with the thread's ETag, and `DELETE …/threads/{thread_id}/memories/{name}` removes one.
- New threads and forks take initial mounts in their `memories` field. A fork copies its origin thread's memory mounts. Archiving a thread removes them.

To give every thread of an agent a memory, set the agent's `memory_mounts` to `[{name, memory_id, access, recall}]`. They join a thread when its first run is accepted, for each name and memory the thread does not use yet. Afterwards the thread's own mounts decide, so removing one keeps it removed. A default whose memory was deleted fails that first run with `invalid_argument`, and defaults that would take the thread over its limit fail it with `memory_mount_limit`.

An async [subagent](agents-and-runs.md#subagents)'s thread starts with the parent run's memory mounts and then adds its own agent's defaults. Inline subagents get no memory tools or context.

## What agents get

The `memory` [toolset](tools.md#built-in-toolsets) is enabled by default. Its tools are:

- the Harness [file memory tools](../a13n-harness/memory.md#what-the-model-gets), with tool keys `file_view` through `file_delete` and permission IDs `memory.file.view` through `memory.file.delete`, offered for mounted file memories;
- the record tools `memory_record_search`, `memory_record_list`, `memory_record_add`, `memory_record_update` and `memory_record_delete`, with tool keys `record_search` through `record_delete` and permission IDs `memory.record.search` through `memory.record.delete`, offered for mounted record memories.

Disabling a tool removes it from every mount; disabling the toolset leaves the run its memories' context and recall without tools.

At the start of a run, each file memory adds one context block: its always-loaded files and an index of its files the first time a thread sees it, and afterwards only the files changed since, including changes by other threads and people. A thread whose history was compacted gets full context again. A run's memory context shares `memory.context_bytes` (32 KiB by default), of which each memory's always-loaded files take at most `memory.always_load_bytes`. Files can be truncated or omitted; use `memory_file_view` to read them in full. If minimal blocks cannot fit, a short view-and-cleanup reminder replaces them without interrupting the run. These bounded results still advance the context cursor, so unchanged files are not automatically sent again.

Each memory call checks the run's access to the workspace: `read` to view, list and search, `run` to change a file or record. A refused call fails with `forbidden`, and a call to a record memory whose provider was disabled fails with `unavailable`. A failed file call changes nothing; the model reads the file and decides again.

A worker can stop after a memory write and before the run records its progress. The recovered attempt then reports that the call's outcome is unknown, and the model reads the file, or searches the records, before writing again. A file write keeps its run and tool call in the history.

## Files and history

People can read and correct what agents wrote. Reading needs `read`, editing and restoring need `run`, and purging history needs `write`. `{path}` is the file's path, such as `prefs/language.md`.

| Request                                        | Does                                                                                                           |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `GET …/memories/{id}/files?prefix=`            | Lists files without content, in path order, under a directory prefix ending in `/`                             |
| `GET …/memories/{id}/files/{path}`             | Returns one file with its content and `ETag`                                                                   |
| `POST …/memories/{id}/files`                   | Creates `{path, content}`; `409 already_exists` when the path is taken                                         |
| `PUT …/memories/{id}/files/{path}`             | Replaces the content of the file `If-Match` names                                                              |
| `POST …/memories/{id}/files/move`              | Moves `{source, destination}` of the file `If-Match` names to a free path                                      |
| `DELETE …/memories/{id}/files/{path}`          | Deletes the file `If-Match` names                                                                              |
| `GET …/memories/{id}/revisions`                | Lists changes newest first, filtered by `path` or `run_id`                                                     |
| `GET …/memories/{id}/revisions/{seq}`          | One change with `previous_content`, the `content` it left, and unified-diff `hunks`                            |
| `POST …/memories/{id}/revisions/{seq}/restore` | Puts back the content that change replaced, as a new change; `If-Match` names the file now at the path, if any |
| `DELETE …/memories/{id}/revisions?path=`       | Deletes the retained history of one path; the file stays                                                       |

Each revision records who made the change: `principal_id` always (for an agent's change, the run's principal), plus `run_id` and `tool_call_id` for an agent's tool call. A file's ETag changes with every change, so an edit based on an old read answers `412 precondition_failed`.

A file holds at most `memory.max_file_bytes` (64 KiB by default). Each file keeps its latest `memory.revisions_per_file` changes (10 by default), and one memory's content and history together fit `memory.max_total_bytes` (32 MiB by default): the oldest history is pruned first, and only content alone over the limit refuses a change, with `409 conflict` and reason `memory_full`. See the [settings reference](configuration-reference.md#memory) for every `memory.*` setting.

## Inspect memories loaded for a reply

An assistant reply can include `memory_sources`. The thread SSE stream sends it in `delta.item.memory_sources` alongside `TEXT_MESSAGE_END`. The run items API returns the same array in the reply's `content.memory_sources`. Save or render the array with the reply. A stream boundary, not message end alone, confirms checkpoint durability.

Each source names the mount (`memory`) and the Service resource (`memory_id`). Record sources contain the IDs and SHA-256 text digests of records that fit the recall budget. File sources distinguish context status from actual loaded files: `files` lists paths, digests, byte counts and whether the delivered prefix was truncated. A file index alone does not mean that all listed files were read. `sources_truncated` means the reference list is partial.

This evidence describes automatic memory delivery during that run. It does not prove the model cited a memory, inventory inherited conversation history, or include explicit memory tool reads. A missing field means no source evidence was captured; an empty `records` or `files` list must be interpreted with its source's `status`. Old replies are not backfilled.

History reads use saved evidence without searching the memory provider again. Editing or deleting a memory does not rewrite earlier source evidence. Digests cannot reconstruct the old text. Current memory content remains subject to the memory API's authorization and availability. Applications decide how to display sources and offer editing controls.
