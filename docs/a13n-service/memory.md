---
title: Memory
description: Save knowledge, reuse it in another conversation, and inspect what agents changed.
---

Use memory for preferences, decisions and reference facts that should outlive a conversation. Start with a Memory Space for shared knowledge and session notes. Choose a standalone memory when your application needs to attach a particular store directly to a thread.

| Need                                                 | Choose                                          |
| ---------------------------------------------------- | ----------------------------------------------- |
| Shared project knowledge with separate session notes | [Memory Space](#memory-spaces)                  |
| A versioned file tree mounted on selected threads    | [Standalone file memory](#create-a-file-memory) |
| Short facts retrieved by meaning from mem0           | [Record memory](#record-memories)               |

Memories belong to a workspace and need no compute environment. The examples use `A13N_URL` for the Service base URL and `A13N_API_KEY` for a workspace-scoped key. API paths below are under `/api/v1`.

## Memory Spaces

A **Memory Space** keeps shared knowledge and session notes in one versioned file store. Try a complete save-and-reuse workflow before configuring automatic organization:

1. Open **Memories → Spaces** and create a Space named `Product knowledge`.
2. In an agent's **Memory** section, enable participation. Select that Space with the mount name `product` and write access. Keep the agent's memory tools enabled.
3. Start a new session with the agent. Ask: “Save our documentation language preference, English, in `global/preferences.md` in the `product` memory.”
4. Open the Space's **Files** and check the saved content. Use **File history** to inspect the author and revision.
5. Start another session with the same Space selected. Ask the agent to read `global/preferences.md` and report the saved preference.

Ordinary runs can access `global/` and their own `sessions/{session_id}/` folder. Put shared knowledge in `global/`; put conversation-specific notes in the session folder. Context includes `global/MEMORY.md` by default and the session's `MEMORY.md` when present. Workspace permissions govern Console and API management of all Space files.

### Select Spaces

Create a Space with `POST /memory-spaces` (`write`):

```json
{"name": "Product knowledge"}
```

Use the returned `id` in the agent's `config.memory`, or in `POST /sessions` or `PATCH /sessions/{session_id}`:

```json
{
  "memory": {
    "enabled": true,
    "spaces": [{"name": "product", "space_id": "mspace_...", "access": "write"}]
  }
}
```

Session selection needs `run`; PATCH also needs the session's `If-Match`. Each name and Space ID can appear once. Omitted fields inherit agent defaults at the first accepted business run. An explicit `spaces` list replaces the selection; `[]` supplies no Space context or tools. Later agent changes and branches preserve the session's settled choice. Use the session header's **Memory** control to change future runs.

Turning participation off preserves the selection. Disabling a Space preserves its files, history and bindings while excluding it from new runs. Accepted runs keep their selected mounts. Space mounts and standalone thread mounts share the mount limit and must have distinct names.

### Organize saved knowledge

Once the Space contains useful files, configure an organizer model in **Configuration → Organization** and choose **Organize now**. The organizer reads shared knowledge and all session folders, then maintains `global/MEMORY.md`. Review the resulting files and revisions in the **Organization** tab. Each round uses a fresh thread in a dedicated session, only that Space's memory tools, and a 200-model-request ceiling.

Enable **Automatic organization** to process later saved changes. Choose **Any saved change** or **After a number of changes**. For example, this creation or update fragment checks once a minute, requires ten changes and waits fifteen minutes between rounds:

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

Count mode includes saved file changes from agents, Console and API; a rename counts once. Unchanged saves, rejected writes, history cleanup and organizer edits do not count. An admitted round consumes the changes present at its start, including when it fails or is cancelled. Changes saved during the round remain for later. Counts survive restarts and history cleanup; existing stores begin counting new changes when the capability is installed.

**Check interval** defaults to 10 seconds (range 10–86400); **Cooldown** defaults to 300 seconds (range 0–604800). Automatic rounds require both their trigger and cooldown, evaluated on a shared 10-second service sweep. These are minimum delays. Recorded deadlines retain their timing; policy edits affect later checks and rounds. **Organize now** bypasses the interval, threshold and cooldown.

The **Organization** tab shows status, costs, traces and revisions separately from business conversations. Failed or cancelled rounds keep saved edits. A history-gap status means retained revisions cannot establish progress; use **Organize now** to request another round.

For API clients, `GET /memory-spaces/{id}/organization` reads status; `POST /memory-spaces/{id}/organizations` requests a round; `GET` on that path lists rounds. `/memory-spaces/{id}/sessions` lists associated business sessions. Lists use `limit` and `cursor`. File and revision APIs use the Space's `memory_id` under `/memories/{memory_id}`. Manage the backing store through the Space; it stays out of standalone memory lists and cannot be mounted directly.

### Write from current knowledge

Agents and organizers track the file versions their conversation has read. Each file mutation checks those observations within the same memory, atomically with the write. A changed source or target returns `read_conflict` and names the file to reread.

To continue, the agent reads that file with `memory_file_view`, considers the new content in its next response, then writes again. Harness supplies the version conditions. A write issued alongside the reread still uses the old observations. Observations persist with the conversation checkpoint. Standalone file memories use the same workflow.

## Standalone memories

A standalone memory is mounted directly on a thread under a tool-visible name:

- **File memory** stores a tree of text files and change history in Service's PostgreSQL database. Use it for organized knowledge that people and agents can inspect and edit. See [File memory](../a13n-harness/memory.md) for writing guidance.
- **Record memory** stores short facts in a mem0 backend. At run start, semantic search recalls records relevant to the input. Records use last-write-wins updates, without file versions.

Create a store, mount it on a thread, then ask the agent to save and read a small fact. Each run freezes its mounts when accepted; mount edits affect later runs. Deleting the entire memory removes its files and history, so keep a separate backup first.

## Create a file memory

With workspace `write` permission, create the store:

```sh
curl -X POST "$A13N_URL/api/v1/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Team conventions", "type": "postgres",
       "guide": "Keep one file per topic. Record decisions with their reason.",
       "always_load": ["README.md"]}'
```

Save the returned `id` (`mem_...`), then [mount it on a thread](#mount-a-memory-on-a-thread). `postgres` is the default type; memories have IDs rather than keys.

`guide` tells agents what to save and how to organize it. Omit it or use `null` to inherit the deployment's guide for that memory kind; `""` supplies no guide. `inherited_guide` shows the inherited text. `always_load` selects up to 64 paths to include first within the context budget when a thread first sees the memory. Those paths can be created later. Only a principal with `write` can change this selection.

`PATCH /memories/{id}` with the memory's `If-Match` changes its name, description, labels, guide and always-loaded paths. Changes apply to later attempts, including a later attempt of an existing run. `GET /memories` filters by `label`, `kind` and `type`; file memories expose file, content and history counters.

`DELETE /memories/{id}` needs `write` and `If-Match`. It removes the files, history and thread mounts. A running agent using that memory receives `memory_deleted` on its next memory tool call.

## Record memories

### Set up a Memory Provider

A [Memory Provider](resources.md#providers) connects the workspace to a record backend. Add one through `/memory-providers`, then test it before creating a memory.

For hosted mem0 Platform, use `mem0_platform`. Its `base_url` defaults to `https://api.mem0.ai`:

```sh
curl -X POST "$A13N_URL/api/v1/memory-providers" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"type": "mem0_platform", "name": "mem0", "config": {}, "credential": {"api_key": "m0-..."}}'
```

For a [self-hosted mem0 REST server](https://docs.mem0.ai/open-source/features/rest-api), configure its model, embedder and vector store, then register its address:

```sh
curl -X POST "$A13N_URL/api/v1/memory-providers" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"type": "mem0_oss", "name": "mem0",
       "config": {"base_url": "https://mem0.internal.example.com"}, "credential": {"api_key": "..."}}'
```

The self-hosted credential is optional and sent as `X-API-Key`; namespace deletion needs the server's admin key. Plain HTTP requires an explicit origin allowance in the [outbound policy](configuration.md#outbound-requests). The self-hosted server lists at most 1000 records per memory.

Call `POST /memory-providers/{provider_id}/test` to check the address and credential. The test reads one page from an unused namespace without changing records.

### Create a record memory

Use the enabled provider's ID and matching type:

```sh
curl -X POST "$A13N_URL/api/v1/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "User facts", "type": "mem0_platform", "provider_id": "memprov_...",
       "guide": "Record one stable fact about the user per record."}'
```

Each memory owns a backend **namespace**, mem0's `user_id`. Service generates one by default: `a13n-` plus 32 hexadecimal characters derived from the memory ID. Supply `namespace` to adopt existing records. It must contain 1–256 printable characters without whitespace or `*`. A namespace already owned by another memory returns `409 already_exists`.

The type, provider and namespace remain fixed. `always_load` applies only to file memories. Deleting a record memory schedules a background namespace purge; reuse is blocked with `namespace_purging` until it finishes. A backend that keeps refusing can retain records after retries are exhausted; see [outbox policies](operations.md#outbox-retention-and-capacity).

### Read and edit records

Read records with `read`; add, replace or delete them with `run`:

| Request                                     | Result                                                         |
| ------------------------------------------- | -------------------------------------------------------------- |
| `GET /memories/{id}/records?limit=&cursor=` | Records in backend order, with `next_cursor`                   |
| `POST /memories/{id}/records/search`        | Records closest to `{query}`, with scores and optional `limit` |
| `POST /memories/{id}/records`               | Add `{text}`                                                   |
| `PUT /memories/{id}/records/{record_id}`    | Replace the full text with `{text}`                            |
| `DELETE /memories/{id}/records/{record_id}` | Delete the record                                              |

Records hold 1–`memory.record_chars` characters (default maximum 8000) and have no ETag. After `409 conflict` with `write_unconfirmed`, list or search before retrying: the backend may already have written the change. An unavailable backend returns `503 unavailable`; a disabled provider returns `422 disabled`. Audits record IDs without text.

In the Console, choose **View source** to inspect the optional `provenance`: known creation, last platform-confirmed write, acting identity and an accessible run link. Technical details include attempt and tool-call IDs. Imported records can lack a creation source. Sources describe write attribution, not full version history or the user's original words.

Source indexing is separate from provider writes; an indexing failure does not retry or fail a confirmed write. Direct backend edits may be absent. The Console warns when current text differs from the last recorded write; matching text alone cannot establish that no external edits occurred.

### Recall

At run start, each mounted record memory with `recall` enabled searches using the run's first input. Defaults are five records, 8 KiB per memory and a two-second search limit, controlled by `memory.recall_limit`, `memory.recall_bytes` and `memory.recall_seconds`. Results enter the context as untrusted data. Failed or slow searches are skipped so the run can continue. Later inputs in the same run do not repeat recall.

## Mount a memory on a thread

Get the thread's current ETag from `GET /threads/{id}/memories`. Set `THREAD` to its ID and `THREAD_ETAG` to that ETag, including its quotes. Replace `mem_...` with the standalone memory ID:

```sh
curl -X POST "$A13N_URL/api/v1/threads/$THREAD/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" -H "If-Match: $THREAD_ETAG" \
  -d '{"name": "team", "memory_id": "mem_...", "access": "write"}'
```

Start a later run and ask the agent to save a convention in the `team` memory, then read it back. Inspect [Files and history](#files-and-history) or [records](#read-and-edit-records) to confirm the stored result.

Names follow `^[a-z][a-z0-9-]{0,62}$`. A thread can use each name and memory once. `read` access provides reading and search tools; `write` adds mutation tools. `recall` defaults to `true` for records and is ignored for files. The default mount limit is eight (`memory.mounts_per_thread`).

Mount edits need `run` and the **thread's** `If-Match`, and return a new thread ETag. `PATCH /threads/{id}/memories/{name}` changes access or recall; `DELETE` removes a mount. To replace the memory, remove the mount and add it again. Initial thread and fork requests accept `memories`; forks copy source mounts. Archiving removes thread mounts.

For agent-wide defaults, set `config.memory_mounts` to `[{name, memory_id, access, recall}]`. Defaults add unused names and memories when the thread's first run is accepted. Later thread changes remain in effect, including removals. A deleted default memory fails first acceptance with `invalid_argument`; exceeding the limit fails with `memory_mount_limit`.

Async [subagent](agents-and-runs.md#subagents) threads inherit the parent run's mounts before adding their own agent defaults. Inline subagents receive no memory context or tools.

## What agents get

The `memory` [toolset](tools.md#built-in-toolsets) is enabled by default. Mounted file memories use the Harness [file tools](../a13n-harness/memory.md#what-the-model-gets). Record memories use `memory_record_search`, `memory_record_list`, `memory_record_add`, `memory_record_update` and `memory_record_delete`. Tool keys use `file_*` or `record_*`; permission IDs use `memory.file.*` or `memory.record.*`. Disabling a tool removes it across mounts. Disabling the toolset leaves automatic context and recall available.

A file memory initially supplies its always-loaded files and file index. Later runs receive changed files, including edits from other threads and people. After compaction, the thread gets full context again. Context remains bounded by `memory.context_bytes` (default 32 KiB) and `memory.always_load_bytes`. Truncated or omitted files need an explicit `memory_file_view`. If even minimal blocks cannot fit, the run receives a view-and-cleanup reminder. These bounded deliveries still advance the context cursor, so unchanged omissions are not automatically resent.

Each tool call checks current workspace access: `read` for reads and searches, `run` for mutations. Refused calls return `forbidden`; record tools return `unavailable` for a disabled provider. Failed file calls leave the store unchanged. After a worker stops between a write and its checkpoint, the recovered attempt can report an unknown outcome. Read the file or search records before repeating that write.

## Files and history

To correct a saved file, read its current content and ETag, edit it, then inspect the new revision. Reading needs `read`; edits and restores need `run`; history cleanup needs `write`. `{path}` is a file path such as `prefs/language.md`.

| Request                                       | Result                                                                    |
| --------------------------------------------- | ------------------------------------------------------------------------- |
| `GET /memories/{id}/files?prefix=`            | List paths under a directory prefix ending in `/`                         |
| `GET /memories/{id}/files/{path}`             | Read content and file ETag                                                |
| `POST /memories/{id}/files`                   | Create `{path, content}` at a free path                                   |
| `PUT /memories/{id}/files/{path}`             | Replace content using the file's `If-Match`                               |
| `POST /memories/{id}/files/move`              | Move `{source, destination}` to a free path using the source's `If-Match` |
| `DELETE /memories/{id}/files/{path}`          | Delete using the file's `If-Match`                                        |
| `GET /memories/{id}/revisions`                | List newest changes first, filtered by `path` or `run_id`                 |
| `GET /memories/{id}/revisions/{seq}`          | Read previous content, resulting content and diff hunks                   |
| `POST /memories/{id}/revisions/{seq}/restore` | Restore what the revision replaced, as a new change                       |
| `DELETE /memories/{id}/revisions?path=`       | Remove one path's retained history, keeping the file                      |

Restore uses the current file's `If-Match` when a file exists at the path. An old file ETag returns `412 precondition_failed`; reread before editing again. Revisions record `principal_id`, plus `run_id` and `tool_call_id` for agent writes.

Defaults allow 64 KiB per file, ten retained revisions per file and 32 MiB of content plus history per memory. Old history is pruned first. Content alone over the limit returns `409 conflict` with `memory_full`. See the [memory settings](configuration-reference.md#memory) for limits and guide budgets.

## Inspect memories loaded for a reply

To show which memories were automatically loaded, save `memory_sources` with the reply. The thread SSE stream supplies it in `delta.item.memory_sources` with `TEXT_MESSAGE_END`; run items expose it in `content.memory_sources`. A stream boundary confirms checkpoint durability; message end alone does not.

Each entry identifies the mount (`memory`) and resource (`memory_id`). Record entries contain recalled IDs and SHA-256 text digests. File entries include delivery status and the paths, digests, byte counts and truncation flags of content actually loaded. Index entries alone do not mean file content was read. `sources_truncated` marks an incomplete reference list.

Use this as evidence of automatic delivery, not proof that the model used a particular fact. It excludes inherited conversation history and explicit tool reads. A missing field means sources were not captured; interpret empty lists with their `status`. Old replies are not backfilled.

History returns saved sources without querying the provider again. Later memory edits or deletion leave that evidence unchanged. Digests cannot reconstruct earlier text; viewing current content still needs an authorized memory API request and an available resource.
