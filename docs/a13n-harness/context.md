---
title: Context and working state
description: Inject dynamic runtime context, manage persistent working notes and task lists, and automatically compact long conversation histories.
---

Context Capabilities prepare each model request, working state carries structured tasks and notes across turns, and compaction keeps long conversations within the model's context window.

## Choose the right mechanism

| Need                                     | Mechanism                             |
| ---------------------------------------- | ------------------------------------- |
| Current time, usage, or Run metadata     | Runtime context                       |
| Workspace layout or project `AGENTS.md`  | Workspace outline and file context    |
| Tasks and notes across turns             | Working state in `HarnessState`       |
| Shorter history for long-running Threads | Handoff or proactive compaction       |
| Recovery from provider context overflow  | Context recovery (enabled by default) |
| Durable knowledge shared across Threads  | [File or Record memory](memory.md)    |

## Context composition

Implement `AbstractModelContextCapability` (or pass `RunBindings.model_context`) to inject request blocks. Use `request.history.matches_latest(block)` so static guidance is only appended when it changes:

```python
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextPlacement,
    ModelContextProjection,
)


class WorkspaceGuide(AbstractModelContextCapability):
    async def wrap_model_context(self, ctx, request, handler):
        projection = await handler(request)
        block = ModelContextBlock(
            source_id="example.guide",
            key="workspace",
            placement=ModelContextPlacement.REQUEST_EPILOGUE,
            content="Workspace guidance: run focused tests before committing.",
        )
        if request.history.matches_latest(block):
            return projection
        return ModelContextProjection(blocks=(*projection.blocks, block))
```

## Working state

`WorkingStateCapability` stores structured notes and task lists inside `HarnessState` so they survive across turns, checkpoints, and compaction without bloating raw message history. Use [Memory](memory.md) instead when facts should be shared across separate Threads.

## Run configuration and host rules

Pass `RunConfiguration` on `RunBindings` to share settings and an HTTP(S) host allowlist across tools and plugins:

```python
from a13n_harness import RunBindings, RunConfiguration

configuration = RunConfiguration(
    allowed_hosts={"api.example.com", "docs.example.com"},
    extensions={"example.reader": {"images": True}},
)
bindings = RunBindings.embedded(configuration=configuration)
```

### Host rules and regular expressions

`allowed_hosts=None` permits all hosts; an empty set denies all. Entries match the normalized full hostname (`re.fullmatch`), either as exact hostnames or `regex:` patterns:

```python
configuration = RunConfiguration(
    allowed_hosts={
        "api.vendor.example",
        r"regex:(api|docs)\.example\.com",
        r"regex:(?:[a-z0-9-]+\.)*assets\.example\.com",
    },
)
```

## Filters

Request filters transform outbound model requests without mutating saved history:

- **Cold-start filter:** Trims oversized strings in older consumed tool results after idle time (exempting read `SKILL.md` files).
- **Image preparation filter:** Enforces `model_characteristics.image_input` limits (count, retention ratio, max base64 bytes, GIF policy).

## Background compaction

When a Thread approaches its `compact_threshold`, proactive compaction summarizes older turns and emits `CompactionSummaryEvent`. Hosts can also run compaction out-of-band and pass the resulting `compaction_result` alongside `previous_state` to `run()` or `stream()`.
