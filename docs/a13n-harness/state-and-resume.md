---
title: State and Resume
description: Save conversation checkpoints, serialize HarnessState to JSON, fork threads, and resume paused agent runs after human-in-the-loop approvals.
---

`HarnessState` is a portable Pydantic checkpoint holding a Thread's `thread_id`, message history, and per-Capability state (such as working notes, CodeAct `store` values, and child lineages)—while keeping credentials, live sockets, and Host policies strictly in `RunBindings`.

## Continue, serialize, and fork a Thread

```python title="continue_thread.py"
import os
from a13n_harness import AgentSpec, HarnessBuilder, HarnessState

executable = HarnessBuilder().build(
    AgentSpec(model=os.environ["HARNESS_MODEL"]),
    output_type=str,
)

# Turn 1 -> serialize to JSON for PostgreSQL / SQLite / disk
first = await executable.run("Remember code 8492.")
saved_json = first.state.model_dump_json()

# Turn 2 -> restore and continue the same thread_id in a new run_id
restored = HarnessState.model_validate_json(saved_json)
second = await executable.run("What code did I give you?", previous_state=restored)

# Or fork into an independent branch with a fresh thread_id
branch = restored.fork()
forked_result = await executable.run("Try an alternate approach.", previous_state=branch)
```

During streaming (`async with executable.stream(...) as stream:`), call `await stream.export_state()` for an interim safe checkpoint, or read the final `HarnessRunResultEvent.result.state` after Run cleanup finishes.

## Structured suspension

When a tool requests approval (`ask`) or external/human input (`ask_user_question` or client tools), the Run closes cleanly with `result.status == "suspended"`, `result.state`, and `result.deferred` (`deferred.approvals` and `deferred.calls`).

### Recover an interrupted deferred resume

Persist `result.state` and `result.deferred` together. Once answers/approvals are collected, resume in a fresh Run using `DeferredToolResume` (if a streaming resume is interrupted before the first model step commits, pair the exported checkpoint with `stream.pending_deferred_input`):

```python
from a13n_harness import DeferredToolResume

answers = first.deferred.build_results(
    approvals={approval_call_id: True},
    calls={question_call_id: {"answers": {"Which scope?": "Focused"}}},
)
resumed = await executable.run(
    previous_state=first.state,
    deferred_resume=DeferredToolResume(first.deferred, answers),
)
```

## Resume unanswered tool calls

If a process crashes mid-tool-batch and you resume from an interrupted checkpoint, Harness reconciles unanswered tool calls using their declared idempotency/retryability (such as `HarnessToolMetadata(idempotency="read_only")`) and your resume policy—re-running only declared-safe calls under fresh permissions while reporting non-retryable calls as interrupted rather than replaying uncertain writes.

## Host checkpointing

In production multi-worker Hosts:

1. Guard checkpoint writes with a per-Thread lease/fence so a stale worker cannot overwrite newer progress.
2. Commit `result.state` atomically alongside Environment state, `MemoryCursors`, and any pending `deferred` envelope before acknowledging completion to the client.
3. On failure (`result.status == "failed"`), inspect `result.failure` and decide whether to retain `result.state` or fall back to the prior turn's checkpoint.
