---
title: Memory
description: Attach versioned file memories or semantic Mem0 record stores to Harness agents so knowledge and user preferences persist across threads.
---

Use **Memory** when one Thread should share durable knowledge with future Threads. For tasks and notes inside a single Thread, use [working state](context.md#working-state).

| Memory kind   | Capability               | How the Model reads & writes                                            |
| ------------- | ------------------------ | ----------------------------------------------------------------------- |
| File memory   | `FileMemoryCapability`   | UTF-8 Markdown tree (`DirectoryFileStore`) with version-checked edits   |
| Record memory | `RecordMemoryCapability` | Short facts (`MEM0_OSS` / `MEM0_PLATFORM`) recalled by semantic meaning |

## File memory

Mount a persistent directory with `DirectoryFileStore` and `FileMount`, and keep a per-Thread `MemoryCursors` snapshot alongside `HarnessState` so unchanged files are not resent every turn:

```python title="memory_demo.py"
import asyncio
from tempfile import TemporaryDirectory
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import FileMemoryCapability, FileMemoryLimits, FileMount, MemoryCursors
from a13n_harness.providers.memory import DirectoryFileStore


async def main() -> None:
    with TemporaryDirectory() as directory:
        store = DirectoryFileStore(directory)
        cursors = MemoryCursors()
        memory = FileMemoryCapability(
            [FileMount("user", store, "write", always_load=("preferences.md",))],
            limits=FileMemoryLimits(context_bytes=16_384, always_load_bytes=4_096),
            cursors=cursors,
        )
        executable = HarnessBuilder().build(
            AgentSpec(model="openai-responses:gpt-5"),
            output_type=str,
            capabilities=(memory,),
        )
        result = await executable.run("Remember that I prefer concise Chinese replies.")
        print(result.output_or_raise())
        # Persist result.state.model_dump_json() and cursors.snapshot() together.
```

### What the model gets

Each `FileMount` injects a `<memory-context>` index (plus `always_load` files) and exposes `memory_file_*` tools (`read` mounts expose `view` and `grep` only):

| Tool                 | Action                                                            |
| -------------------- | ----------------------------------------------------------------- |
| `memory_file_view`   | List a directory or read a file and record its observed version   |
| `memory_file_grep`   | Search lines across files (`regex=True` optional)                 |
| `memory_file_create` | Create a new file (optional YAML `description` front matter)      |
| `memory_file_edit`   | Replace `old_string` with `new_string` (checks observed versions) |
| `memory_file_append` | Append text to an existing file                                   |
| `memory_file_move`   | Rename/move a file                                                |
| `memory_file_delete` | Delete a file at its observed version                             |

If another Thread modifies an observed file concurrently, writes return `read_conflict` so the Model rereads before retrying. Implement `FileStore` from `a13n_harness.providers.memory` to back file memory with a custom database.

## Record memory

Bind a Mem0 OSS or Mem0 Platform store to a tenant/user `namespace` and attach `RecordMemoryCapability`:

```python
import httpx2
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import RecordMemoryCapability, RecordMemoryLimits, RecordMount
from a13n_harness.providers.memory import MEM0_OSS  # or MEM0_PLATFORM

async with (
    httpx2.AsyncClient(timeout=30) as http,
    MEM0_OSS.open({"base_url": "http://localhost:8888"}, namespace="alice", http=http) as store,
):
    memory = RecordMemoryCapability(
        [RecordMount("facts", store, "write")],
        limits=RecordMemoryLimits(record_chars=8000, recall_limit=5, recall_bytes=8192),
    )
    executable = HarnessBuilder().build(
        AgentSpec(model="openai-responses:gpt-5"), output_type=str, capabilities=(memory,)
    )
    await executable.run("Remember that I drink green tea.")
```

At the start of a Run, `<memory-recall>` injects the closest records (`recall=False` disables automatic recall). Models query and mutate records with `memory_record_search`, `memory_record_list`, `memory_record_add`, `memory_record_update`, and `memory_record_delete` (`read` mounts expose search and list only). Implement `RecordStore` for custom vector/search backends.
