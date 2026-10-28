---
title: Memory
description: Save preferences across Threads, control shared writes, and connect file or record stores.
---

Use memory when one conversation should teach another: a user's preferred language, a project's conventions, or a decision worth keeping. The Host chooses which Threads share a store and which can change it. Use [working state](context.md#working-state) for the tasks and notes of one Thread.

Choose the memory kind by how the Agent should find information:

| Need                                                   | Choose        | How the model reads it                         | Write behavior                                          |
| ------------------------------------------------------ | ------------- | ---------------------------------------------- | ------------------------------------------------------- |
| Organized notes with names such as `prefs/language.md` | File memory   | A bounded index, then file view or text search | Conditions and observed versions protect shared changes |
| Short facts found by meaning                           | Record memory | Automatic recall, then similarity search       | Records have IDs; the last write wins                   |

Start with the local file example below. It needs the Python environment from [Getting Started](getting-started.md), but no Model credentials or memory server. The mem0 example later needs both a running backend and a configured Model.

## File memory

A file memory is a small tree of UTF-8 text files. `DirectoryFileStore` keeps that tree on disk; `FileMemoryCapability` gives the model named access to it. The store outlives individual Runs, and multiple Threads can mount it.

### Mount a local memory

This program uses deterministic Models to make the two steps visible: the first Thread calls `memory_file_create`; a new, read-only Thread receives that file in its starting context. Save it as `memory_demo.py`:

```python title="memory_demo.py"
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import FileMemoryCapability, FileMount, MemoryCursors
from a13n_harness.providers.memory import DirectoryFileStore


async def write_preference(messages, info):
    receipts = [
        part for message in messages if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "memory_file_create"
    ]
    if not receipts:
        yield {0: DeltaToolCall(name="memory_file_create", json_args=json.dumps({
            "memory": "user",
            "path": "preferences.md",
            "content": "Language preferences\nReply in Chinese.\n",
        }))}
    else:
        assert receipts[-1].content["path"] == "preferences.md"
        yield "Preference saved."


async def read_preference(messages, info):
    prompt = "\n".join(
        str(part.content) for message in messages if isinstance(message, ModelRequest)
        for part in message.parts if isinstance(part, UserPromptPart)
    )
    assert "<memory-context" in prompt and "Reply in Chinese." in prompt
    assert "memory_file_create" not in {tool.name for tool in info.function_tools}
    yield "Your preferred language is Chinese."


async def main() -> None:
    with TemporaryDirectory() as directory:
        store = DirectoryFileStore(directory)
        writer_cursors = MemoryCursors()
        writer = HarnessBuilder().build(
            AgentSpec(), output_type=str,
            model=FunctionModel(stream_function=write_preference),
            capabilities=(FileMemoryCapability(
                [FileMount("user", store, "write")], cursors=writer_cursors,
            ),),
        )
        first = await writer.run("Remember my preferred language.")
        print(first.output_or_raise())
        assert (Path(directory) / "preferences.md").exists()

        # Share the store; give the new Thread its own cursor and access policy.
        reader = HarnessBuilder().build(
            AgentSpec(), output_type=str,
            model=FunctionModel(stream_function=read_preference),
            capabilities=(FileMemoryCapability(
                [FileMount("user", store, "read", always_load=("preferences.md",))],
                cursors=MemoryCursors(),
            ),),
        )
        second = await reader.run("What language should you use?")
        assert second.thread_id != first.thread_id
        print(second.output_or_raise())


asyncio.run(main())
```

Run `python memory_demo.py`. It prints `Preference saved.` and `Your preferred language is Chinese.`. The assertion checks that the second Run belongs to a different Thread, so the preference came from the shared store rather than conversation history.

The example removes its temporary directory when it finishes. In an application, use a persistent path such as `DirectoryFileStore(".memory/user")` and a configured [Model](models.md). Let the Model choose memory tools from the user's request. The Host owns the directory's lifetime and backups.

`user` is a memory mount name, not an Environment mount. Keep the backing directory outside the Agent's accessible filesystem when you need writes to pass exclusively through memory permissions and version checks.

### What the model gets

A mount supplies three things:

1. **Guidance:** its name, access mode, and what belongs there. `guide=None` selects `DEFAULT_FILE_GUIDE`; supply your own guide or `""` to omit it.
2. **Tools:** the `memory_file_*` operations below. A `read` mount exposes view and grep. `tools=("view", "grep")` restricts the Capability's tools across all mounts; permission rules use IDs such as `memory.file.view` and `memory.file.delete`.
3. **Starting context:** a `<memory-context>` block with an index and selected file contents. Memory text is data written by conversations. The mount's guide is the instruction for using it.

| Tool                 | Use it to                                                    | Required condition                            |
| -------------------- | ------------------------------------------------------------ | --------------------------------------------- |
| `memory_file_view`   | List a directory or read a file and its version              | The path exists                               |
| `memory_file_grep`   | Find matching lines; literal and case-insensitive by default | With `regex=True`, the pattern must be valid  |
| `memory_file_create` | Create a file                                                | The path is free                              |
| `memory_file_edit`   | Replace `old_string` with `new_string`                       | The old text occurs exactly once              |
| `memory_file_append` | Add text to a file                                           | The file exists                               |
| `memory_file_move`   | Rename a file                                                | The source exists and the destination is free |
| `memory_file_delete` | Remove the version the model viewed                          | That version is still current                 |

Writes also check files this conversation has observed in the same memory. Suppose Thread A reads a preference file, then Thread B changes its language preference. A's next edit fails with `read_conflict`, even if the particular text A wanted to replace still matches. To continue:

1. Read the changed file again.
2. Reconsider the intended edit using the new content.
3. Issue a new write in a later model step.

A reread in the same tool batch cannot refresh a write already chosen by that response. Failure details do not count as a reread. The failed call changes nothing; earlier successful calls remain committed.

These observations survive a saved `HarnessState`. Viewed files, matching grep results, individual index entries, and always-loaded files establish version checks; collapsed directory counts do not. Unobserved unrelated files may change freely. The checks apply within one memory, rather than across several stores. If a process stops after a write but before its result is saved, inspect the file before attempting the write again.

### Write good memory files

Give each topic its own relative path and a useful description. Files are UTF-8 text, at most 64 KiB by default; directories exist implicitly. The index uses the frontmatter `description`, or the first non-empty line when no description is provided:

```markdown
---
description: Language and tone preferences
---
- Reply in Chinese.
- Keep answers short.
```

Use `always_load=("preferences.md",)` for a small file the model needs at the start. The Host chooses this list. Contents receive priority within the context budget; other files remain discoverable through the index and tools.

### Keep context small across Runs

Keep one `MemoryCursors` object per Thread. It records which memory context that Thread has received. A later Run receives changed paths and changed always-loaded files, or no block if nothing changed. Without cursors, every Run receives full context. `DirectoryFileStore` has no change journal, so any change causes a full context refresh instead of an incremental list.

Save `cursors.snapshot()` alongside the accepted `HarnessState`; cursors are not embedded automatically in that state. Rebuild the mount over the same store and restore both values when continuing. This helper accepts the saved values and a configured Model, and returns the values your Host should save together:

```python
from pydantic_ai.models import Model

from a13n_harness import AgentSpec, HarnessBuilder, HarnessState
from a13n_harness.capabilities import FileMemoryCapability, FileMount, MemoryCursors
from a13n_harness.providers.memory import DirectoryFileStore


async def continue_with_memory(
    directory: str, state_json: str, saved_cursors: dict[str, str | None], model: Model,
) -> tuple[str, str, dict[str, str | None]]:
    cursors = MemoryCursors(saved_cursors)
    memory = FileMemoryCapability(
        [FileMount("user", DirectoryFileStore(directory), "write")], cursors=cursors,
    )
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=model, capabilities=(memory,),
    )
    result = await executable.run(
        "Continue using my saved preferences.",
        previous_state=HarnessState.model_validate_json(state_json),
    )
    output = result.output_or_raise()
    assert result.state is not None
    return output, result.state.model_dump_json(), cursors.snapshot()
```

Capture the initial pair with `first.state.model_dump_json()` and `writer_cursors.snapshot()` after checking the first result succeeded. Use a persistent directory for this continuation; the earlier demo deliberately deletes its store. Mounts and access policy are rebuilt by the Host, while observed file versions travel in `HarnessState`. When primary history is replaced through compaction or handoff, the Capability clears its observations and context cursors so the next ordinary Run starts with fresh memory context.

To change the budget, use this configuration inside the scope where you opened `store` and created the Thread's `cursors`, then pass `memory` to the builder:

```python
from a13n_harness.capabilities import FileMemoryLimits

limits = FileMemoryLimits(context_bytes=16_384, always_load_bytes=4_096, write_retries=3)
memory = FileMemoryCapability([FileMount("user", store, "write")], limits=limits, cursors=cursors)
```

All file memories in a Run share `context_bytes`, including wrappers and view hints. Always-loaded contents are bounded, and indexes use the remaining space. Large indexes collapse directories into entries such as `archive/ (37 files)` before truncation. If even minimal blocks do not fit, one short view-and-cleanup reminder replaces them. Read omitted content with `memory_file_view`: delivering a clipped block still advances the cursor, so unchanged omitted files are not resent automatically.

### Share a memory between processes

Point each process's `DirectoryFileStore` at the same directory, and retain separate cursors and `HarnessState` for every Thread. Store mutations use a cross-process lock in the hidden `.a13n-memory/` directory. Direct filesystem editors bypass that lock, so route concurrent application writes through the store as well.

The directory store keeps current files, not revision history. Back it up, or use a history-aware `FileStore` such as the one owned by Service when restoration is part of your product.

### Bring your own file store

Implement `FileStore` from `a13n_harness.providers.memory` when memory belongs in another backend:

1. Provide `list`, `read`, `write`, `move`, `delete`, `changes`, and `purge` for one memory namespace.
2. Give each file an opaque version. Check the target's expected version and the mutation's `dependencies` in the same transaction or lock as the mutation. Dependencies map paths to observed versions; `None` means the path was absent. A mismatch must raise `read_conflict` before any storage effect.
3. Return a change cursor from `changes`, or `FullResync` when an incremental list is unavailable. Use `validate_path()` and `describe()` for shared path and file rules.
4. Optionally implement `SearchableFileStore.search`. Each match's version must come from the same read as its text. Otherwise Harness implements grep by reading files.

Stores with history can use `Origin` to attribute a mutation to its Run, principal, and tool call. See the [File Memory specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/21-file-memory.md) for signatures, budgets, and failure codes.

## Record memory

Choose record memory for facts such as “prefers green tea” that the Agent should retrieve by meaning. The backend ranks records against the Run's input. Each record has an ID, and updates replace its text without file-style version checks.

### Mount mem0

Before running this example, start a [mem0 REST server](https://docs.mem0.ai/open-source/features/rest-api) at `http://localhost:8888` and configure the Model credentials from [Getting Started](getting-started.md). Use a test namespace: the program asks the Model to save a durable record. If your server requires authentication, supply its API key to `MEM0_OSS.open` as the credential mapping `{"api_key": ...}`.

`MEM0_OSS.open` binds a store to `namespace="alice"`; `RecordMount` exposes it to the Model as `facts`. The explicitly supplied HTTP client allows the local endpoint. Both context managers stay open through the Runs:

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

The second Run starts a new Thread. Its input drives recall from the same store, so the saved preference can inform the cafe answer. With a real Model, inspect the memory tool result to confirm a write; wording and tool choices can vary.

The namespace maps to mem0's `user_id`. Allocate it deliberately for the user or group sharing the memory. Both providers add text verbatim, check namespace ownership, and read back writes to confirm them.

For hosted mem0, use `MEM0_PLATFORM` and its API key. This helper accepts a configured Model and a key obtained by your Host; call it from your application's async entry point:

```python
from pydantic_ai.models import Model

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import RecordMemoryCapability, RecordMount
from a13n_harness.providers.memory import MEM0_PLATFORM


async def remember_in_platform(model: Model, api_key: str) -> str:
    async with MEM0_PLATFORM.open({}, {"api_key": api_key}, namespace="alice") as store:
        memory = RecordMemoryCapability([RecordMount("facts", store, "write")])
        executable = HarnessBuilder().build(
            AgentSpec(), output_type=str, model=model, capabilities=(memory,),
        )
        result = await executable.run("Remember that I drink green tea.")
        return result.output_or_raise()
```

The Platform endpoint defaults to `https://api.mem0.ai`. Without a supplied `http` client, the provider opens and closes its own client, restricted to public HTTPS endpoints. A supplied client remains caller-owned, as in the local example.

### What the model gets from records

Each mount contributes its name, access, and guide. `guide=None` selects `DEFAULT_RECORD_GUIDE`; use custom text or `""` to omit the guide. At the Run's first input, a `<memory-recall>` block supplies the closest records. Set `recall=False` on the mount to disable it. Failed or timed-out recall is skipped so the Run can continue.

| Tool                   | Use it to               | Required condition                     |
| ---------------------- | ----------------------- | -------------------------------------- |
| `memory_record_search` | Find records by meaning | The store can answer the query         |
| `memory_record_list`   | Read a page of records  | Continue with a returned `next_cursor` |
| `memory_record_add`    | Save one short fact     | Non-blank text within `record_chars`   |
| `memory_record_update` | Replace a record's text | The record belongs to this memory      |
| `memory_record_delete` | Remove a record         | The record belongs to this memory      |

A `read` mount exposes search and list. `tools=("search", "add")` narrows the Capability's tools across mounts; tool permission IDs run from `memory.record.search` to `memory.record.delete`.

When a write returns `write_unconfirmed`, search the store before deciding whether to write again. The backend may have accepted the first request even though confirmation failed. An automatic retry could duplicate the fact.

Configure size and recall limits while the record `store` is open, then pass this `memory` to the builder:

```python
from a13n_harness.capabilities import RecordMemoryLimits

limits = RecordMemoryLimits(record_chars=8000, recall_limit=5, recall_bytes=8192, recall_seconds=2.0)
memory = RecordMemoryCapability([RecordMount("facts", store, "write")], limits=limits)
```

Each memory recalls at most `recall_limit` records, keeping the closest ones that fit `recall_bytes`. `recall_seconds` bounds the recall wait; `record_chars` bounds text written through the tools.

### Bring your own record store

Implement `RecordStore` from `a13n_harness.providers.memory` with `search`, cursor-based `list`, `add`, `update`, `delete`, and `purge`, all bound to one namespace. Use `validate_record_text()` for text validation and `MemoryStoreError` for failures. Keep uncertain writes distinguishable from confirmed refusal so callers know when to inspect existing records.

See the [Record Memory specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness/21a-record-memory.md) for the full store protocol, mem0 behavior, and recall rules.
