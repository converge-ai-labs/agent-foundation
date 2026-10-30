---
title: State and resume
description: Save a completed turn, restart the program, and continue the same Thread.
---

Save `result.state` when your application accepts a turn. On the next turn, load that state and pass it as `previous_state`. Harness continues the same Thread with a new Run; your application supplies the current Model, tools, and access policy again.

## Continue a Thread

Use the Python environment from [Getting started](getting-started.md). Save this complete offline program as `conversation.py`:

```python title="conversation.py"
import argparse
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from a13n_harness import AgentSpec, HarnessBuilder, HarnessState
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    del info
    turn = 1 + sum(isinstance(message, ModelResponse) for message in messages)
    yield f"turn-{turn}"


async def main(checkpoint: Path, prompt: str) -> None:
    previous = (
        HarnessState.model_validate_json(checkpoint.read_text(encoding="utf-8"))
        if checkpoint.exists()
        else None
    )
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    result = await executable.run(prompt, previous_state=previous)
    answer = result.output_or_raise()
    if result.state is None:
        raise RuntimeError("Completed turn has no continuation state")

    temporary = checkpoint.with_suffix(checkpoint.suffix + ".tmp")
    temporary.write_text(result.state.model_dump_json(), encoding="utf-8")
    temporary.replace(checkpoint)
    print(answer)
    print(f"Thread: {result.thread_id}")
    print(f"Run: {result.run_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("prompt")
    args = parser.parse_args()
    asyncio.run(main(args.checkpoint, args.prompt))
```

Run it twice in the same directory, using a new checkpoint filename:

```bash
uv run python conversation.py tutorial-thread.json "Draft the plan"
uv run python conversation.py tutorial-thread.json "Review the plan"
```

The first process prints `turn-1`; the second prints `turn-2`. Both print the same Thread ID and different Run IDs. The deterministic Model counts earlier responses, so this checks that saved history reaches a newly built executable. It does not generate a real plan.

The program saves only completed turns and replaces the file after serialization succeeds. Use one writer per checkpoint file. For an application with concurrent requests or multiple workers, put checkpoint selection and publication behind the application's storage coordination; see [Host Checkpointing](#host-checkpointing).

## Serialize State

The example uses the public JSON round trip:

```python
payload = result.state.model_dump_json()
restored = HarnessState.model_validate_json(payload)
```

These lines assume the completed result has a state, as checked above. Store the complete envelope. `state.to_json_bytes()` is also available when your storage accepts UTF-8 bytes. Both forms validate public message history; neither stores an executable or an open connection.

## State Contents

| Saved in `HarnessState`                          | Supplied or managed by your application                  |
| ------------------------------------------------ | -------------------------------------------------------- |
| Schema version and stable Thread ID              | Executable definition and revision                       |
| Public Model message history                     | Model, tools, Capabilities, plugins, clients             |
| Versioned JSON Capability data                   | Current identity, credentials, policy, approvals         |
| Portable state for compatible Environment mounts | Mount selection, target lifecycle, current authorization |
| References from an optional Host state store     | Referenced bytes, retention, and store access            |

A new Run gets fresh bindings, context, and usage accounting by default. State carries continuation data; the Host reconstructs current authority. See [Usage](usage-and-limits.md) for explicit accounting continuation.

If you use `RunBindings.state_store`, keep the referenced values as long as a selected checkpoint needs them and bind a compatible store on resume. Without a store, those values remain inside the state. Execution records, durable asynchronous children, delivery, and long-term memory belong to their own Host or feature stores.

## Fork a Thread

Use `fork()` to branch an existing conversation into an independently advancing Thread. In an async function with a built `executable` and loaded `restored` state:

```python
branch = restored.fork()
assert branch.thread_id != restored.thread_id
assert branch.message_history == restored.message_history
assert branch.environment_states == {}

result = await executable.run("Explore another approach", previous_state=branch)
```

Save the branch under a different application record or file. `fork()` preserves continuation content and gives retained inline children fresh Thread identities, while clearing portable Environment state. It does not copy Host-managed asynchronous children or select backing resources.

For an application-selected identity, use `HarnessState.new(thread_id=...)` at creation or `state.fork(thread_id=...)` when branching. Treat IDs as opaque correlation values and keep the branch ID distinct from the source.

## Export While Streaming

For most applications, save the terminal result after checking its status. A Host that needs intermediate checkpoints can export during an active stream:

```python
async with executable.stream("Continue the task", previous_state=restored) as stream:
    async for item in stream:
        candidate = await stream.export_state()
        # Apply the application's checkpoint policy before saving candidate.
```

This integration fragment exports the latest safe public message boundary, not arbitrary token deltas. The Host decides when a candidate is current and safe to publish. Exporting state alone does not commit a checkpoint; an optional state store may write referenced content during export.

### Compact a saved checkpoint

A Host can call `await executable.compact(previous_state=restored)` to produce a summary-only Run. Check its result before selecting the returned state. A successful compressed checkpoint is immediately saveable; the next ordinary `run()` or `stream()` restores runtime context before continuing work. This explicit operation needs no Compaction Capability or threshold. See [Context management](context.md) for the full workflow and output-mode restrictions.

## Structured Suspension

An approval or external tool can finish a Run with `status="suspended"`, a `state`, a `deferred` request envelope, and `suspend_reason="deferred"`. The application collects the responses after that Run closes, then starts another Run.

For an approval-only workflow, follow this sequence. This integration fragment assumes `executable` declares an approval-required tool and `approvals` contains the decisions your application collected:

```python
from a13n_harness import DeferredToolResume, RunBindings

first = await executable.run("Perform the action", bindings=RunBindings.embedded())
if first.status != "suspended" or first.state is None or first.deferred is None:
    raise RuntimeError("Expected an approval suspension")

# approvals maps each target tool_call_id to the collected bool/ToolApproved/ToolDenied.
results = first.deferred.build_results(approvals=approvals)
accepted = DeferredToolResume(first.deferred, results)
second = await executable.run(
    previous_state=first.state,
    bindings=RunBindings.embedded(),
    deferred_resume=accepted,
)
```

For a complete runnable declaration and external-result flow, use [Client tools](client-tools.md). For user questions, follow [Human-in-the-loop](human-in-the-loop.md). Cover every pending item exactly once in its original category; an approval map does not answer external calls.

Persist the full Harness `DeferredToolRequests` envelope alongside its checkpoint. Each `calls` or `approvals` entry contains `model_call`, `target_call`, and `target_identity`. Display `target_call` and key replies by its `tool_call_id`; Harness uses the retained model call to correlate history. Import these request types and `DeferredToolResume` from `a13n_harness`.

Resume checks history correlation, current target identity, argument schemas, and fresh policy. `ToolApproved.override_args` can supply replacement arguments. An approval does not reserve a resource or override current authorization. Host-managed children can use this flow when their bindings support deferred tools; built-in inline children disable them. A Host without a deferred lifecycle sets `deferred_tools_supported=False`.

## Recover an Interrupted Deferred Resume

Accepted external input may not yet be in a checkpoint's history. Keep the accepted `DeferredToolResume` beside that checkpoint until history incorporates it:

1. Retain the complete requests and results when the application accepts them.
2. During the resumed stream, pair the current checkpoint with `stream.pending_deferred_input`.
3. Publish that pair together. Clear the stored accepted batch when the remainder is `None`.
4. After interruption, load both records and resume with `recovery=True`.

`stream.pending_deferred_input` remains available after shutdown. It retires consumed input before history transformations, so compaction cannot revive it. Pair it with the current canonical checkpoint, never an older snapshot. For a detached batch and an untransformed matching history, `accepted.remaining(history)` computes the unanswered remainder.

The following fragment assumes `checkpoint` and optional `accepted` were loaded from that application storage:

```python
recovery_input = (
    DeferredToolResume(accepted.requests, accepted.results, recovery=True)
    if accepted is not None
    else None
)
result = await executable.run(
    previous_state=checkpoint,
    bindings=RunBindings.embedded(),
    deferred_resume=recovery_input,
    tool_recovery="declared",
)
```

Recovery consumes retained external results, failures, and denials even with `tool_recovery="never"`. It skips results already incorporated in history. A previous positive approval is not permission to replay an uncertain local action; unresolved work follows the current recovery policy and current approval rules.

## Resume Unanswered Tool Calls

A missing result after a crash means the operation's outcome may be unknown. First supply any retained accepted input as described above. For remaining unanswered history calls, choose the per-Run `tool_recovery` policy:

| Mode                   | Behavior                                                                             |
| ---------------------- | ------------------------------------------------------------------------------------ |
| `"declared"` (default) | Replay tools explicitly marked recovery-retryable; record unknown results for others |
| `"never"`              | Record unknown results for all unanswered calls                                      |
| `"always"`             | Replay unanswered calls still available in the current tool surface                  |

Recorded results, including an existing unknown-result return or argument retry, stay authoritative. Removed tools receive unknown results. These modes do not change newly generated calls or guarantee exactly-once external effects.

### Declare Retryable Tools at Their Source

Mark a tool only when repeating it is acceptable even if the previous operation completed. For example, a read-only lookup can use:

```python
from a13n_harness.tools import recovery_retryable
from pydantic_ai.capabilities import Capability


@recovery_retryable
def lookup_status(record_id: str) -> str:
    """Read a status from the demonstration catalog."""
    return {"item-1": "ready"}.get(record_id, "unknown")


catalog = Capability(id="catalog", tools=[lookup_status])
```

Pass `catalog` to the builder's `capabilities` argument. The helper returns a native `Tool`; it can also copy an existing Tool while preserving its settings. For instance methods, mark the bound method while building the Capability. Dynamic Toolsets can mark their current tools the same way, or set `RECOVERY_RETRY_SAFE_METADATA_KEY` to boolean `True` in tool metadata.

The declaration is read from the current tool definition. It is independent of provider transport retries and `HarnessToolMetadata.idempotency`: an idempotency key alone does not prove that replay reuses the original upstream operation.

### Resume with a Policy

In an async function with a selected `checkpoint` and current executable:

```python
result = await executable.run(previous_state=checkpoint, tool_recovery="declared")
```

Harness processes restored calls before the next Model request. Current argument validation, approval requirements, external-execution requirements, and managed invocation policy still apply. A fresh, complete `DeferredToolResume` supplies its collected batch directly; interrupted deferred recovery uses `recovery=True` and the selected policy.

## Safe Failure Candidates

A failed result may carry normalized state that retains visible text and completed reasoning while removing unsafe unfinished parts. Decide whether to select it according to the application's recovery policy. Before repeating an uncertain mutation, inspect the external system or use the operation's established idempotency contract.

`RunCleanupError.outcome` is also an uncertain candidate: clean terminal delivery did not complete. Keep it separate from the application's last committed checkpoint until that outcome is reconciled.

## Capability State

A Capability stores its portable data under a stable ID and exact version. Inside a hook or tool with the current `AgentContext` named `context`:

```python
from pydantic import BaseModel


class CounterState(BaseModel):
    value: int


current = await context.state.read("acme.counter", CounterState, version="1")
await context.state.write(
    "acme.counter",
    CounterState(value=(current.value if current else 0) + 1),
    version="1",
)
```

The state API validates and detaches the JSON payload. The owning Capability interprets its version; unread namespaces can remain opaque across Runs. Keep credentials, clients, locks, and Environment lifecycle data in their owning integrations.

## Shell Executions and Observations

Portable state and live process control have separate lifetimes:

- A standalone `process-*` reference belongs to one Harness Run.
- A top-level Shell execution held by a Host's `ToolExecutionRuntime` can keep its `exec-*` reference across Runs of the same Thread while that runtime owns it.
- After a Host restart, provider-supported discovery may find retained commands and assign new references. Loading message history does not recreate the old runtime.

Run close releases unretained observations; the Provider's cleanup policy determines command survival. Current authorization still applies to retained controls. Queries do not reset output; native completion does not prove complete output capture. See [Environment tools](environments.md) for available actions and provider limits.

## Host Checkpointing

For a durable Host, make checkpoint publication an explicit application operation. Keep the selected state together with its definition revision, provenance, remaining accepted input, and referenced content. Rebuild current identity, credentials, policy, and Environment bindings when admitting another Run.

The Host separately owns worker attempts, leases, concurrent-writer coordination, durable completion, and output delivery. A Harness terminal result confirms its Run outcome; a successful Host save confirms publication. See [Embedding in a Host](hosting.md) for those boundaries and the [Agent Application example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app) for a single-application implementation.
