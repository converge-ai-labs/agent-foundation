---
title: Memory
description: Maintain long-term team knowledge across conversations, run automated Dreams-style distillation workflows, and review candidate diffs before publishing.
---

Persist durable knowledge across sessions using **Memory Spaces** (structured team & session Markdown with automated consolidation) or [Standalone memories](standalone-memory.md) (thread-mounted file trees and Mem0 semantic records).

| Need                                                     | Pattern                                                             |
| -------------------------------------------------------- | ------------------------------------------------------------------- |
| Shared team knowledge, session notes & curated diffs     | [Memory Spaces](#memory-spaces)                                     |
| Isolated versioned file tree mounted on specific threads | [Standalone file memory](standalone-memory.md#create-a-file-memory) |
| Semantic similarity recall of short facts                | [Record memory via Mem0](standalone-memory.md#record-memories)      |

## Memory Spaces

Each Memory Space (`POST /api/v1/memory-spaces` with `{"name": "Engineering Guidelines"}`, or **Memories → Spaces → Create Space**) organizes files into `global/` (with `global/MEMORY.md` loaded into prompt context) and `sessions/{session_id}/`.

Attach a Space on an agent revision or session:

```json
{
  "memory": {
    "enabled": true,
    "spaces": [{"name": "team_kb", "space_id": "mspace_abc123", "access": "write"}]
  }
}
```

Concurrent agent edits use optimistic version checks (`read_conflict` prompts the agent to re-read via `memory_file_view` before retrying). Inspect which files entered a reply via **Debug info** in Console or `memory_sources` on the `TEXT_MESSAGE_END` stream event.

### Automatic organization and conversation extraction

Configure an **Organizer** policy on a Space (or click **Organize now** in Console) to deduplicate notes and refresh `global/MEMORY.md`:

```json
{
  "organization": {
    "enabled": true,
    "model": "model-gemini-pro",
    "check_interval_seconds": 60,
    "cooldown_seconds": 900,
    "trigger": {"type": "change_count", "count": 10}
  }
}
```

### Automatically extract conversation knowledge

Submit ad-hoc or history-mining distillation jobs via `POST /api/v1/memory-organizations` (up to 100 `session_ids`), staging edits in an isolated **Candidate Tree** (`"output": "candidate"`) so admins can inspect side-by-side diffs in Console (**Review candidate → Apply all changes**) before publishing:

```json
{
  "target": {"kind": "space", "id": "mspace_abc123"},
  "model": "model-flagship-reasoning",
  "output": "candidate",
  "instructions": "Extract verified architecture decisions and production deployment policies.",
  "history": {
    "session_ids": ["ses_backend_refactor", "ses_auth_redesign"],
    "after": "2026-10-01T00:00:00Z",
    "max_runs": 50
  }
}
```

## Standalone memories

<span id="create-a-file-memory"></span> <span id="record-memories"></span> <span id="set-up-a-memory-provider"></span> <span id="create-a-record-memory"></span> <span id="read-and-edit-records"></span> <span id="recall"></span> <span id="mount-a-memory-on-a-thread"></span> <span id="what-agents-get"></span> <span id="files-and-history"></span> <span id="what-the-model-gets"></span>

- [Standalone file and record memories](standalone-memory.md)
