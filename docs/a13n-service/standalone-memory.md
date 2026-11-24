---
title: Standalone memory reference
description: Create standalone versioned file memories and Mem0 semantic record memories, mount them directly on Service threads, and manage revision history.
---

Mount standalone **File memories** (PostgreSQL-backed Markdown trees with revision history) or **Record memories** (Mem0 semantic vector stores) directly on threads when you do not need a shared [Memory Space](memory.md).

## Create a file memory

Create a PostgreSQL-backed file store (`write` permission required; `guidance.system_preset` accepts `"general"` or `"journal"`):

```sh
curl -X POST "$A13N_URL/api/v1/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Team conventions", "type": "postgres",
       "guidance": {"system_preset": "general", "custom": "Record decisions with their reason.", "timezone": "UTC"},
       "always_load": ["README.md"]}'
```

Update metadata or `always_load` via `PATCH /api/v1/memories/{id}` with `If-Match`, or delete via `DELETE /api/v1/memories/{id}`.

### Files and history

Inspect, edit, or restore versioned files (64 KiB max per file, 32 MiB per memory):

| Endpoint                                              | Action                                                 |
| ----------------------------------------------------- | ------------------------------------------------------ |
| `GET /api/v1/memories/{id}/files?prefix=` / `/{path}` | List directory paths or read file content and ETag     |
| `POST /api/v1/memories/{id}/files`                    | Create `{path, content}`                               |
| `PUT` / `DELETE /api/v1/memories/{id}/files/{path}`   | Update or delete a file using its file `If-Match`      |
| `POST /api/v1/memories/{id}/files/move`               | Rename `{source, destination}` using source `If-Match` |
| `GET /api/v1/memories/{id}/revisions` / `/{seq}`      | Inspect change history and diff hunks                  |
| `POST /api/v1/memories/{id}/revisions/{seq}/restore`  | Restore a prior revision as a new commit               |

## Record memories

1. **Create a Memory Provider:** Register `mem0_platform` (`https://api.mem0.ai`) or self-hosted `mem0_oss` (`config.base_url`) via `POST /api/v1/memory-providers`, and verify with `POST /api/v1/memory-providers/{provider_id}/test`.
2. **Create the record memory:**

```sh
curl -X POST "$A13N_URL/api/v1/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "User facts", "type": "mem0_platform", "provider_id": "memprov_...",
       "guide": "Record one stable fact about the user per record."}'
```

### Read and edit records

At run start, `recall` (default `true`) injects the top matching records (`memory.recall_limit`, default 5). Manage records directly via `/api/v1/memories/{id}/records`: `GET` (paginate), `POST .../search` (`{query}`), `POST` (`{text}`), `PUT .../{record_id}` (`{text}`), and `DELETE .../{record_id}`.

## Mount a memory on a thread

Attach up to 8 standalone memories per thread using the **thread's** `If-Match` (or set agent-wide defaults in `agent.config.memory_mounts`):

```sh
curl -X POST "$A13N_URL/api/v1/threads/$THREAD/memories" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" -H "If-Match: $THREAD_ETAG" \
  -d '{"name": "team", "memory_id": "mem_...", "access": "write"}'
```

With the `memory` [toolset](tools.md#built-in-toolsets) enabled, file mounts expose `memory_file_*` ([file tools](../a13n-harness/memory.md#what-the-model-gets)) and record mounts expose `memory_record_*` (`read` mounts expose read/search tools only).
