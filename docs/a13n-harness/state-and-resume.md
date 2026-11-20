---
title: State and Resume
description: Save conversation checkpoints, serialize state to JSON, fork threads, and resume paused agent runs after human-in-the-loop approvals.
---

Production agent systems must survive application restarts, support multi-turn dialogues, and pause for external human feedback. **Harness** encapsulates all conversation history, tool outcomes, and capability data in a lightweight, serializable `HarnessState` checkpoint.

## Continue a Thread

The simplest way to maintain conversation continuity across turns is passing `previous_state`:

```python title="continue_thread.py"
import asyncio
import os

from a13n_harness import AgentSpec, HarnessBuilder


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(
            model=os.environ["HARNESS_MODEL"],
            instructions="Maintain context accurately across turns.",
        ),
        output_type=str,
    )

    # Turn 1
    first = await executable.run("Remember this code: 8492")
    print("Turn 1:", first.output_or_raise())

    # Turn 2: resume context from first.state
    second = await executable.run(
        "What code did I ask you to remember?",
        previous_state=first.state,
    )
    print("Turn 2:", second.output_or_raise())

    # Continuity assertions
    assert second.thread_id == first.thread_id
    assert second.run_id != first.run_id


if __name__ == "__main__":
    asyncio.run(main())
```

When `previous_state` is supplied, Harness retains the underlying `thread_id`, injects previous messages into model context, and allocates a new `run_id` for tracking the turn.

## Serialize State

`HarnessState` is a standard Pydantic model. You can serialize it to JSON for persistence in PostgreSQL, Redis, or SQLite:

```python
# Serialize state to a JSON string or dict
json_data = state.model_dump_json()

# Restore state from JSON string in a later process
from a13n_harness import HarnessState
restored_state = HarnessState.model_validate_json(json_data)
```

The resulting JSON payload is compact and portable across worker nodes.

## State Contents

`HarnessState` encapsulates everything required to reconstitute the conversation:

| Field | Purpose |
| :--- | :--- |
| `thread_id` | Unique UUID string identifying the conversational lineage |
| `messages` | Chronological list of user prompts, model responses, and tool call parts |
| `capabilities` | State dictionaries preserved by active capabilities (e.g., CodeAct variables, working notes) |
| `metadata` | Arbitrary custom key-value pairs attached by the host application |

Sensitive authentication credentials, API keys, and open network sockets stay in `RunBindings` and are never serialized into `HarnessState`.

## Fork a Thread

To explore alternative conversation branches without modifying the original thread, fork the state:

```python
# Create an independent branch with a new thread_id
forked_state = state.fork()

assert forked_state.thread_id != state.thread_id
assert len(forked_state.messages) == len(state.messages)

# Continue the forked branch independently
forked_result = await executable.run(
    "Explore an alternate plan",
    previous_state=forked_state,
)
```

Forking clones message history and capability state while assigning a fresh `thread_id`. The original thread history remains completely untouched.

## Export While Streaming

When delivering tokens over streaming interfaces, you can export state checkpoints incrementally before the full run finishes:

```python
async with executable.stream("Generate an extensive technical report") as stream:
    async for item in stream:
        if isinstance(item, HarnessEvent):
            # Inspect interim state if needed
            pass
        elif isinstance(item, HarnessRunResultEvent):
            # Final terminal checkpoint
            final_state = item.result.state
```

The terminal `HarnessRunResultEvent` provides the definitive, consistent state checkpoint after all underlying tool resources have closed.

### Compact a saved checkpoint

For long-running conversations that exceed context limits, Harness provides compaction utilities to prune historical tool calls and summarize earlier turns while retaining critical system invariants.

## Structured Suspension

When an agent needs human confirmation or external system input before executing a tool, Harness gracefully suspends execution rather than timing out:

```python
result = await executable.run("Delete production database cluster")

if result.status == "suspended":
    # 1. Persist suspended state
    save_checkpoint(result.state)

    # 2. Extract pending tool calls or questions
    for deferred in result.deferred:
        print(f"Pending approval: {deferred.tool_name} with args {deferred.arguments}")
```

The run status transitions to `suspended`, saving full execution state and exposing pending calls in `result.deferred`.

## Recover an Interrupted Deferred Resume

Once the human decision or external result is collected, resume the suspended run using `DeferredToolResume`:

```python
from a13n_harness import DeferredToolResume

resume_payload = DeferredToolResume(
    tool_call_id="call_abc123",
    content="Action approved by admin",
)

resumed_result = await executable.run(
    resume_payload,
    previous_state=result.state,
)
```

Harness re-enters the execution loop at the exact point of suspension, delivering the external result to the waiting model.

## Resume Unanswered Tool Calls

If an external tool provider crashes mid-invocation, Harness allows the host to inject an explicit recovery policy.

### Declare Retryable Tools at Their Source

When registering tools, tag idempotent operations as retryable:

```python
def fetch_weather(city: str) -> dict:
    """Fetch current weather data (idempotent query)."""
    return {"city": city, "temp_c": 22}

# Declare tool with retryable policy
fetch_weather.__retryable__ = True
```

### Resume with a Policy

When resuming after an unhandled tool interruption, specify how unanswered calls should be handled:

```python
from a13n_harness import ResumePolicy

resumed = await executable.run(
    "Retry pending operations",
    previous_state=interrupted_state,
    resume_policy=ResumePolicy.RETRY_IDEMPOTENT,
)
```

Idempotent tools are safely re-executed, while non-idempotent operations prompt the model for confirmation.

## Safe Failure Candidates

If a run fails due to model rate limits or transient network disconnects, `result.state` captures a safe fallback candidate:

```python
try:
    result = await executable.run("Process data")
except Exception:
    # Harness attaches partial safe state to the result object
    pass
```

You can inspect the failure reason via `result.failure` and decide whether to roll back to the previous turn's state or retry the failed step.

## Capability State

Capabilities maintain their own isolated state namespaces within `HarnessState`:

```python
# Capability state is automatically scoped
state.capabilities.get("working_state")
state.capabilities.get("codeact_runtime")
```

This modular isolation ensures that custom capabilities cannot accidentally corrupt core message structures or cross-contaminate other plugins.

## Shell Executions and Observations

When paired with environment execution, Harness tracks active subshell PIDs and background processes inside capability state. If a run pauses or ends while a background build process continues, the process handle remains recorded for subsequent turns to monitor or terminate.

## Host Checkpointing

In high-concurrency production deployments, follow the standard host checkpointing pattern:

1. Always write `result.state` atomically to your database within the same transaction that acknowledges user message delivery.
2. Index records by `thread_id` and timestamp.
3. On node restarts, load the most recent `HarnessState` checkpoint corresponding to the target thread.
