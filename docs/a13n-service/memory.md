# Memory

A memory is a small tree of text files that agents keep across conversations: preferences, decisions, conventions and reference facts. It belongs to a workspace, and several conversations can use it at once. The Service keeps its files, and the history of every change, in PostgreSQL.

Threads **mount** memories under names. Each run freezes the thread's memory mounts when it is accepted, sees each memory's context at its start, and changes the memories through the `memory_file_*` tools. Every write checks the file's version at the moment it runs, so a change based on stale content fails and returns the current file instead of overwriting another conversation's work. [File memory](../a13n-harness/memory.md) explains what the model sees and how to write good memory files.

All API paths below are under `/api/v1/workspaces/{workspace_id}`.

## Create a memory

Creating and changing memories needs `write`:

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"key": "team", "name": "Team conventions", "type": "postgres",
       "guide": "Keep one file per topic. Record decisions with their reason.",
       "always_load": ["README.md"]}'
```

- `key` is unique in the workspace. `type` is `postgres`, the only type.
- `guide` tells agents what belongs in this memory and how to organize it, up to `memory.guide_bytes`. Leave it out or set it to `null` to use the deployment's guide (`memory.default_guide.file`, else the built-in one); `""` gives the memory no guide. The memory's `effective_guide` shows the guide runs use.
- `always_load` names up to 64 paths whose full content leads the memory's context in every run. A path need not exist yet. Only people who may change the memory choose them, so a conversation cannot pin its own writes into every later one.
- `PATCH …/memories/{memory_id}` with the memory's `If-Match` changes `name`, `description`, `labels`, `guide` and `always_load`. Runs that start afterwards use the change.
- `DELETE …/memories/{memory_id}` with `If-Match` deletes the memory with its files, history and thread mounts. A running run that uses it gets `memory_deleted` from its next memory tool call.

`GET …/memories` lists the workspace's memories, filtered by `label`. Each shows `file_count`, `content_bytes` and `history_bytes`.

## Mount a memory on a thread

A thread's memory mounts decide what its later runs use:

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/threads/$THREAD/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" -H "If-Match: $THREAD_ETAG" \
  -d '{"name": "team", "memory_id": "mem_...", "access": "write"}'
```

- `name` matches `^[a-z][a-z0-9-]{0,62}$`; the model addresses the memory by it. `access` is `read`, which offers only viewing and searching, or `write`, which offers every tool.
- A thread mounts each name and each memory once. To change a mount's memory or access, remove it and add it again.
- A thread holds at most `memory.mounts_per_thread` memory mounts (8 by default); a request beyond that is `409 conflict` with reason `memory_mount_limit`.
- Mount changes take the **thread's** `If-Match`, return the thread's new ETag, need `run`, and affect runs accepted afterwards. `GET …/threads/{thread_id}/memories` lists the mounts with the thread's ETag, and `DELETE …/threads/{thread_id}/memories/{name}` removes one.
- New threads and forks take initial mounts in their `memories` field. A fork copies its origin thread's memory mounts. Archiving a thread removes them.

To give every conversation of an agent a memory, set the agent's `memory_mounts` to `[{name, memory_id, access}]`. They join a thread when its first run is accepted, for each name and memory the thread does not use yet, within the thread's limit. Afterwards the thread's own mounts decide, so removing one keeps it removed. A default whose memory was deleted fails that first run with `invalid_argument`.

An async [subagent](agents-and-runs.md#subagents)'s thread starts with the parent run's memory mounts and then adds its own agent's defaults. Inline subagents get no memory tools or context.

## What agents get

The `memory` [toolset](tools.md#built-in-toolsets) is enabled by default. Its tools are the Harness [file memory tools](../a13n-harness/memory.md#what-the-model-gets), with tool keys `file_view` through `file_delete` and permission IDs `memory.file.view` through `memory.file.delete`. Disabling a tool removes it from every mount; disabling the toolset leaves the run its memories' context without tools.

At the start of a run, each memory adds one context block: its always-loaded files and an index of its files the first time a conversation sees it, and afterwards only the files changed since, including changes by other conversations and people. A conversation whose history was compacted gets full context again. A run's memory context shares `memory.context_bytes` (32 KiB by default), of which each memory's always-loaded files take at most `memory.always_load_bytes`.

Each memory call checks the run's access to the workspace: `read` to view and search, `run` to change a file. A refused call fails with `forbidden`. A failed call changes nothing; the model reads the file and decides again.

A worker can stop after a memory write and before the run records its progress. The recovered attempt then reports that the call's outcome is unknown, and the model reads the file before writing again. The write itself keeps its run and tool call in the history.

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

Each revision records who made the change: `run_id` and `tool_call_id` for an agent's tool call, `principal_id` for both agents and people. A file's ETag changes with every change, so an edit based on an old read answers `412 precondition_failed`.

A file holds at most `memory.max_file_bytes` (64 KiB by default). Each file keeps its latest `memory.revisions_per_file` changes (10 by default), and one memory's content and history together fit `memory.max_total_bytes` (32 MiB by default): the oldest history is pruned first, and only content alone over the limit refuses a change, with `409 conflict` and reason `memory_full`. See the [settings reference](configuration-reference.md#memory) for every `memory.*` setting.
