# File memory

File memory gives an Agent a small tree of text files that outlives one conversation: preferences, decisions, conventions, and reference facts. Several conversations can share one memory. Each write checks its condition against the current file when it runs, so a change based on stale content fails instead of overwriting another conversation's work.

Use it for durable facts that later conversations should find. Use [working state](context.md#working-state) for tasks and notes that belong to one conversation.

## Mount a local memory

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

The model addresses the memory by its mount name, `user`. It never sees the directory path.

## What the model gets

- **Instructions.** Each mount is listed with its name, access, and guide. `guide=None` uses `DEFAULT_FILE_GUIDE`, a short rule for what to keep and how to organize it; pass your own text, or `""` for no guide.
- **Tools.** The `memory_file_*` tools are the only way to read and change a memory. Shell and Environment file tools cannot reach it.
- **Context.** At the start of each run, one `<memory-context>` block per memory shows its `always_load` files and an index with one `path: description` line per file, or only the files that changed since the conversation last saw it.

| Tool                 | Does                                                     | Fails when                                            |
| -------------------- | -------------------------------------------------------- | ----------------------------------------------------- |
| `memory_file_view`   | Lists a directory or reads a file with its version       | The path does not exist                               |
| `memory_file_grep`   | Finds lines containing a text, literal and ignoring case | The regular expression is invalid (with `regex=True`) |
| `memory_file_create` | Creates a new file                                       | The path exists                                       |
| `memory_file_edit`   | Replaces `old_string` with `new_string`                  | `old_string` does not occur exactly once              |
| `memory_file_append` | Adds text at the end of a file                           | The file does not exist                               |
| `memory_file_move`   | Renames a file                                           | The source is missing or the destination exists       |
| `memory_file_delete` | Deletes a file at the version the model viewed           | The file changed or no longer exists                  |

A failed call changes nothing and returns the current file, so the model can read it and decide again. For example, when two conversations viewed a file containing "likes tea" and "reply in English", one can edit "reply in English" to "reply in Chinese" and the other can then edit "likes tea" to "likes coffee": both changes remain. A later edit of "reply in English" fails and returns the file as it is now.

A `read` mount offers only `memory_file_view` and `memory_file_grep`. `tools=("view", "grep")` narrows the tools for every mount. Tool permission rules can target the tool IDs `memory.file.view` through `memory.file.delete`.

## Write good memory files

A file is UTF-8 text, at most 64 KiB. Its index line uses the frontmatter `description` when present, otherwise its first non-empty line:

```markdown
---
description: Language and tone preferences
---
- Reply in Chinese.
- Keep answers short.
```

Paths are relative, such as `prefs/language.md`; directories exist implicitly. `always_load` names files whose full content every run starts with. Only the code that builds the mount chooses them, so a conversation cannot pin its own writes into every later conversation.

## Keep context small across runs

`MemoryCursors` records where each memory's context in the conversation stands. With the same cursors, a later run gets only the files changed since the last delivered context, or nothing when nothing changed. Persist `cursors.snapshot()` next to the conversation's `HarnessState` and pass `MemoryCursors(saved)` when you build the next run's Capability. Without cursors, every run gets full context. After automatic compaction or a handoff replaces the history, the Capability clears the cursors, so the next run gets full context again.

`DirectoryFileStore` has no change journal. Any change to the memory gives the next run full context rather than a change list.

All memory context of one run shares a budget:

```python
from a13n_harness.capabilities import FileMemoryLimits

limits = FileMemoryLimits(context_bytes=16_384, always_load_bytes=4_096, write_retries=3)
memory = FileMemoryCapability([FileMount("user", store, "write")], limits=limits, cursors=cursors)
```

`always_load` files come first, then the indexes share the rest. A large index collapses directories into lines such as `archive/ (37 files)` and is cut with a pointer to `memory_file_view` when it still does not fit.

## Share a memory between processes

Several processes can mount the same directory. `DirectoryFileStore` serializes each change with a lock file under `.a13n-memory/`, which it never lists. Cursors belong to a conversation, so keep one `MemoryCursors` per conversation. The directory store keeps no history; back the directory up yourself, or use a store with history, such as the Service's.

## Bring your own store

A store implements the `FileStore` protocol from `a13n_harness.providers.memory`: `list`, `read`, compare-and-swap `write`, `move`, and `delete`, `changes` for the context cursor, and `purge`. Add `search` to implement `SearchableFileStore`; otherwise `memory_file_grep` reads the files. `validate_path()` and `describe()` apply the shared file rules. `Origin` tells a store with history which run, principal, and tool call made each change.

The [File Memory specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/21-file-memory.md) defines the complete store contract, context budget, and failure codes.
