---
title: Context and working state
description: Inject dynamic runtime and workspace context, manage persistent working notes and task lists, and compact long conversation histories.
---

Context Capabilities enrich each model request, working state carries structured tasks and notes across turns, and compaction keeps long conversations within the model's context window.

## Choose the right mechanism

| Need                                     | Mechanism                                                                 |
| ---------------------------------------- | ------------------------------------------------------------------------- |
| Current time, usage, or Run metadata     | `RuntimeContextCapability`                                                |
| Workspace layout or project `AGENTS.md`  | `WorkspaceOutlineCapability` and `FileContextCapability`                  |
| Tasks and notes across turns             | [Working state](#working-state) (`WorkingStateCapability`)                |
| Shorter history for long-running Threads | `CompactionCapability`, `HandoffCapability`, or `executable.compact(...)` |
| Recovery from provider context overflow  | Context recovery (enabled by default)                                     |
| Durable knowledge shared across Threads  | [File or Record memory](memory.md)                                        |

## Built-in and custom context injection

Attach built-in workspace context Capabilities on `HarnessBuilder`, or implement `AbstractModelContextCapability` (using `request.history.matches_latest(block)` so static blocks are appended only when changed):

```python
from a13n_harness.capabilities import (
    FileContextCapability,
    RuntimeContextCapability,
    WorkspaceOutlineCapability,
)
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

`FileContextCapability` loads the workspace root's `AGENTS.md` once per logical Run and discovers immediate child `AGENTS.md` and `.agents/skills` entrypoint hints.

## Working state

`WorkingStateCapability` stores structured notes and task checklists inside `HarnessState` so they survive across turns, checkpoints, and history compaction without inflating raw message history. Use [Memory](memory.md) when facts should be shared across independent Threads.

## Run configuration and host rules

Pass `RunConfiguration` on `RunBindings` to share extension settings and an HTTP(S) host allowlist across tools and plugins:

### Host rules and regular expressions

`allowed_hosts=None` permits all hosts; an empty set denies all. Entries match the normalized full hostname (`re.fullmatch`), either as exact hostnames or `regex:` patterns:

```python
from a13n_harness import RunBindings, RunConfiguration

configuration = RunConfiguration(
    allowed_hosts={
        "api.vendor.example",
        r"regex:(api|docs)\.example\.com",
        r"regex:(?:[a-z0-9-]+\.)*assets\.example\.com",
    },
    extensions={"example.reader": {"images": True}},
)
bindings = RunBindings.embedded(configuration=configuration)
```

## Filters

Request filters transform outbound model requests without mutating saved history:

- **Cold-start filter:** Trims oversized strings in older consumed tool results after idle gaps (exempting read `SKILL.md` files).
- **Image preparation filter:** Enforces `model_characteristics.image_input` limits (count, retention ratio, max base64 bytes, GIF policy).

## Compact long Threads

Choose automatic in-Run compaction, explicit Agent-initiated handoff, or Host-triggered compaction between Runs:

```python
from a13n_harness.capabilities import (
    CompactionCapability,
    CompactionPolicy,
    HandoffCapability,
)

# 1. Automatic compaction during a Run (defaults to 90% of context_window_tokens):
compaction = CompactionCapability(CompactionPolicy(trigger_tokens=100_000))

# 2. Agent-called summarize tool (default reminder at 65% of context_window_tokens):
handoff = HandoffCapability()

# 3. Host-triggered out-of-band compaction on a settled checkpoint between Runs:
# compacted = await executable.compact(previous_state=saved_state)
```

After summarizing history, Harness preserves current working notes and tasks in the compacted `HarnessState` and emits `CompactionSummaryEvent`. Hosts running background compaction out-of-band can also pass `compaction_result` alongside `previous_state` to `run()` or `stream()`.
