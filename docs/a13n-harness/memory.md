---
title: Memory
description: Give an Agent file or record memory that outlives a single Thread.
---

Memory gives an Agent knowledge that outlives one Thread: preferences, decisions, conventions, and facts. Several Threads can share one memory. A memory comes in one of two kinds:

|                        | File memory                                              | Record memory                                                         |
| ---------------------- | -------------------------------------------------------- | --------------------------------------------------------------------- |
| Unit                   | A text file with a path                                  | A short record with an ID                                             |
| How the model finds it | An index at the start of a Run, then view and grep       | Recall at the start of a Run, then similarity search                  |
| Writes                 | Each write checks its condition against the current file | The last write wins                                                   |
| Stores                 | `DirectoryFileStore`, or your own `FileStore`            | mem0 through `MEM0_PLATFORM` or `MEM0_OSS`, or your own `RecordStore` |

Use [working state](context.md#working-state) for tasks and notes that belong to one Thread.

## File memory

File memory keeps a small tree of text files. Each write checks its condition against the current file when it runs, so a change based on stale content fails instead of overwriting another Thread's work.

### Mount a local memory

`DirectoryFileStore` keeps a memory as plain files under one directory. `FileMemoryCapability` mounts it under a name:

```python
import asyncio

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import FileMemoryCapability, FileMount, MemoryCursors
from a13n_harness.providers.memory import DirectoryFileStore


async def main() -> None:
    store = DirectoryFileStore(".memory/user")
    cursors = MemoryCursors()
    memory = FileMemoryCapability(
        [FileMount("user", store, "write", always_load=("README.md",))],
        cursors=cursors,
    )
    executable = HarnessBuilder().build(
        AgentSpec(model="openai-responses:gpt-5"),
        output_type=str,
        capabilities=(memory,),
    )

    first = await executable.run("I prefer answers in Chinese. Remember that.")
    print(first.output_or_raise())
    second = await executable.run("What do you know about me?", previous_state=first.state)
    print(second.output_or_raise())


asyncio.run(main())
```

The model addresses this memory by its mount name, `user`. Memory mounts are separate from Environment mounts. For isolated execution, keep the backing store outside the Agent's accessible filesystem.

### What the model gets

- **Instructions.** Each mount is listed with its name, access, and guide. `guide=None` uses `DEFAULT_FILE_GUIDE`, a short rule for what to keep and how to organize it; pass your own text, or `""` for no guide.
- **Tools.** `memory_file_*` tools read and change memory through the mount's access rules and version checks.
- **Context.** At the start of each Run, one `<memory-context>` block per memory shows its `always_load` files and an index with one `path: description` line per file. With cursors, a later Run instead gets only the changed paths and the new content of changed `always_load` files, or no block when nothing changed.

| Tool                 | Does                                                     | Fails when                                            |
| -------------------- | -------------------------------------------------------- | ----------------------------------------------------- |
| `memory_file_view`   | Lists a directory or reads a file with its version       | The path does not exist                               |
| `memory_file_grep`   | Finds lines containing a text, literal and ignoring case | The regular expression is invalid (with `regex=True`) |
| `memory_file_create` | Creates a new file                                       | The path exists                                       |
| `memory_file_edit`   | Replaces `old_string` with `new_string`                  | `old_string` does not occur exactly once              |
| `memory_file_append` | Adds text at the end of a file                           | The file does not exist                               |
| `memory_file_move`   | Renames a file                                           | The source is missing or the destination exists       |
| `memory_file_delete` | Deletes a file at the version the model viewed           | The file changed or no longer exists                  |

A call that fails its condition or a version check changes nothing and returns the current file, so the model can read it and decide again. For example, two Threads viewed a file that contains "likes tea" and "reply in English". One Thread edits "reply in English" to "reply in Chinese". The other Thread then edits "likes tea" to "likes coffee". Both changes remain. A later edit of "reply in English" by either Thread fails and returns the file as it is now.

A `read` mount offers only `memory_file_view` and `memory_file_grep`. `tools=("view", "grep")` narrows the tools for every mount. Tool permission rules can target the tool IDs `memory.file.view` through `memory.file.delete`.

### Write good memory files

A file is UTF-8 text, at most 64 KiB by default. Its index line uses the frontmatter `description` when present, otherwise its first non-empty line:

```markdown
---
description: Language and tone preferences
---
- Reply in Chinese.
- Keep answers short.
```

Paths are relative, such as `prefs/language.md`; directories exist implicitly. `always_load` names files whose full content leads the memory's full context; with cursors, a later Run gets such a file again only when it changed. Only the code that builds the mount chooses them, so a Thread cannot pin its own writes into every later Thread.

### Keep context small across Runs

`MemoryCursors` records, for each memory, the store cursor of the context last delivered to the Thread. With the same cursors, a later Run gets only the changes since the last delivered context, or nothing when nothing changed. Persist `cursors.snapshot()` next to the Thread's `HarnessState` and pass `MemoryCursors(saved)` when you build the next Run's Capability. Without cursors, every Run gets full context. After automatic compaction or a handoff replaces the history, the Capability clears the cursors, so the next Run gets full context again.

`DirectoryFileStore` has no change journal. Any change to the memory gives the next Run full context rather than a change list.

All memory context of one Run shares a budget:

```python
from a13n_harness.capabilities import FileMemoryLimits

limits = FileMemoryLimits(context_bytes=16_384, always_load_bytes=4_096, write_retries=3)
memory = FileMemoryCapability([FileMount("user", store, "write")], limits=limits, cursors=cursors)
```

`always_load` files come first, then the indexes share the rest. A large index collapses directories into lines such as `archive/ (37 files)` and is cut with a pointer to `memory_file_view` when it still does not fit.

### Share a memory between processes

Several processes can mount the same directory. `DirectoryFileStore` serializes each change with a lock file under `.a13n-memory/`, which it never lists. Cursors belong to a Thread, so keep one `MemoryCursors` per Thread, next to its `HarnessState`. The directory store keeps no history; back the directory up yourself, or implement a `FileStore` that keeps history, as the Service's file memory store does.

### Bring your own file store

A store implements the `FileStore` protocol from `a13n_harness.providers.memory`: `list`, `read`, compare-and-swap `write`, `move`, and `delete`, `changes` for the context cursor, and `purge`. Add `search` to implement `SearchableFileStore`; otherwise `memory_file_grep` reads the files. `validate_path()` and `describe()` apply the shared file rules. `Origin` tells a store with history which Run, principal, and tool call made each change.

The [File Memory specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/21-file-memory.md) defines the complete store contract, context budget, and failure codes.

## Record memory

Record memory keeps short records, such as "prefers green tea", and recalls the ones closest in meaning to each Run's input. Records have no versions: the last write wins.

### Mount mem0

`MEM0_OSS` opens a namespace of a self-hosted [mem0 REST server](https://docs.mem0.ai/open-source/features/rest-api) as a `RecordStore`, and `RecordMemoryCapability` mounts it under a name:

```python
import asyncio

import httpx2

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import RecordMemoryCapability, RecordMount
from a13n_harness.providers.memory import MEM0_OSS


async def main() -> None:
    async with (
        httpx2.AsyncClient(timeout=30) as http,
        MEM0_OSS.open({"base_url": "http://localhost:8888"}, namespace="alice", http=http) as store,
    ):
        memory = RecordMemoryCapability([RecordMount("facts", store, "write")])
        executable = HarnessBuilder().build(
            AgentSpec(model="openai-responses:gpt-5"),
            output_type=str,
            capabilities=(memory,),
        )

        first = await executable.run("I drink green tea. Remember that.")
        print(first.output_or_raise())
        # A new Thread recalls the record.
        second = await executable.run("What should I order at the cafe?")
        print(second.output_or_raise())


asyncio.run(main())
```

The namespace is the mem0 `user_id` that holds this memory's records; give each memory its own. Without `http`, the store opens its own client, which reaches only public HTTPS endpoints, so a local server needs a client of your own. The hosted Platform takes an API key and defaults to `https://api.mem0.ai`:

```python
import os

from a13n_harness.providers.memory import MEM0_PLATFORM

async with MEM0_PLATFORM.open({}, {"api_key": os.environ["MEM0_API_KEY"]}, namespace="alice") as store:
    ...
```

Both add records verbatim, confirm every write by reading it back, and never show or change a record of another namespace.

### What the model gets from records

- **Instructions.** Each mount is listed with its name, access, and guide. `guide=None` uses `DEFAULT_RECORD_GUIDE`; pass your own text, or `""` for no guide.
- **Recall.** At the start of each Run, one `<memory-recall>` block per memory shows the records closest to the input's text. `recall=False` on a mount turns it off. A recall that fails or takes longer than `recall_seconds` is skipped, and the Run goes on.
- **Tools.** The `memory_record_*` tools search, list, and change records.

| Tool                   | Does                                            | Fails when                                           |
| ---------------------- | ----------------------------------------------- | ---------------------------------------------------- |
| `memory_record_search` | Finds the records closest in meaning to a query | The store is unavailable                             |
| `memory_record_list`   | Lists records one page at a time                | The cursor is not a `next_cursor` the store returned |
| `memory_record_add`    | Adds a record                                   | The text is blank or longer than `record_chars`      |
| `memory_record_update` | Replaces a record's whole text                  | The record does not exist in this memory             |
| `memory_record_delete` | Deletes a record                                | The record does not exist in this memory             |

A write that the store cannot confirm fails with `write_unconfirmed`: it may or may not have happened, so the tool instructions tell the model to search before it writes again. A `read` mount offers only search and list, and `tools=("search", "add")` narrows the tools for every mount. Tool permission rules can target the tool IDs `memory.record.search` through `memory.record.delete`.

`RecordMemoryLimits` sets the record size and each Run's recall:

```python
from a13n_harness.capabilities import RecordMemoryLimits

limits = RecordMemoryLimits(record_chars=8000, recall_limit=5, recall_bytes=8192, recall_seconds=2.0)
memory = RecordMemoryCapability([RecordMount("facts", store, "write")], limits=limits)
```

A recall block holds up to `recall_limit` records per memory and keeps the closest ones that fit `recall_bytes`.

### Bring your own record store

A store implements the `RecordStore` protocol from `a13n_harness.providers.memory`: `search`, `list` with a cursor, `add`, `update`, `delete`, and `purge`, all bound to one namespace. `validate_record_text()` applies the shared text rule, and `MemoryStoreError` carries the failure codes the tools report.

The [Record Memory specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/21a-record-memory.md) defines the complete store contract, the mem0 rules, and recall.
