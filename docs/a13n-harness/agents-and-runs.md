---
title: Agents and Runs
description: Build reusable ExecutableAgent instances in Python, run or stream turns with fresh RunBindings, steer active execution, and configure limits and recovery.
---

Build one reusable `ExecutableAgent` from an `AgentSpec`, then call `run()` for a final result or `stream()` for live events. Each call starts a fresh Run; passing `previous_state=first.state` continues the same Thread.

## Build and run an Agent

Save this offline example as `runs.py` and run `uv run python runs.py`:

```python title="runs.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessEvent,
    HarnessRunResultEvent,
    RunBindings,
)
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    del info
    turn = 1 + sum(isinstance(message, ModelResponse) for message in messages)
    yield f"turn-{turn}"


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(system_prompt="Answer concisely."),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    first = await executable.run("Draft a plan")
    print(first.output_or_raise())

    async with executable.stream(
        "Review the plan",
        previous_state=first.state,
        bindings=RunBindings.embedded(metadata={"stage": "review"}),
    ) as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent):
                print(f"event {item.sequence}")
            elif isinstance(item, HarnessRunResultEvent):
                print(item.result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

### Build-time inputs

| Input                               | Where to set it                              |
| ----------------------------------- | -------------------------------------------- |
| Prompt, model ID, settings, budgets | `AgentSpec`                                  |
| Typed output contract               | `output_type` (or `AgentSpec.output_schema`) |
| Concrete Model instance             | `model` (with `AgentSpec.model=None`)        |
| Tools and feature behavior          | `capabilities`                               |
| Middleware & subagents              | `plugins`, `subagents`                       |
| Interrupted-model retry policy      | `model_recovery`                             |

## Run to a Result

Call `result.output_or_raise()` for the common success path, or branch on `result.status`:

| Status      | Next step                                               | Key fields                            |
| ----------- | ------------------------------------------------------- | ------------------------------------- |
| `completed` | Use `output` and persist `state` if continuing          | `output`, `state`, `usage`            |
| `suspended` | Collect deferred tool results or approvals, then resume | `state`, `deferred`, `suspend_reason` |
| `failed`    | Inspect `failure` and decide whether to keep `state`    | `failure`, optional `state`           |
| `cancelled` | Handle cancellation and inspect any partial `state`     | optional `state`                      |

Use `all_messages()` for full history and `new_messages()` for messages added by this Run. See [State and resume](state-and-resume.md) for checkpoint persistence and [Client tools](client-tools.md) for deferred tools.

## Stream Events

`executable.stream(...)` yields two item types, each carrying `thread_id`, `run_id`, and a monotonically increasing `sequence`:

- `HarnessEvent` — native agent events and Harness extension events.
- `HarnessRunResultEvent` — the final terminal result after Run resources close.

Keep all stream consumption inside `async with`. Use [Stream Protocol](../a13n-stream-protocol/index.md) to project these events to AG-UI.

### Active stream operations

| Operation                                  | Purpose                                                |
| ------------------------------------------ | ------------------------------------------------------ |
| `stream.context` / `stream.usage`          | Inspect the active `AgentContext` or current usage     |
| `await stream.export_state()`              | Export the latest safe portable checkpoint             |
| `await stream.steer(input, input_id=None)` | Enqueue steering guidance while the Run is active      |
| `stream.cancel()`                          | Request cancellation                                   |
| `stream.result`                            | Read the final result after the terminal event arrives |

## Fresh Run Bindings

Create fresh `RunBindings` for every root, resumed, or child Run to supply request-scoped collaborators, identity, Environment authority, and metadata:

```python
from a13n_harness import HarnessObservationContext

bindings = RunBindings.embedded(
    metadata={"request_kind": "interactive"},
    observation=HarnessObservationContext(
        name="interactive-agent-run",
        labels=("interactive",),
        metadata={"channel": "web"},
    ),
)
result = await executable.run("Summarize the change", bindings=bindings)
```

Keep live bindings out of saved `HarnessState`. When building input requires the entered Environment, pass `input_factory=make_input`.

## Configure the definition

### Load and refine presets

Load a YAML preset and create an immutable variant with `with_updates()`:

```python
preset = AgentSpec.from_file("research-agent.yaml")
local = preset.with_updates(
    model_settings={"temperature": 0.1, "max_tokens": 8_000},
    toolset_instructions=False,
)
```

- `system_prompt` sets static prompt blocks (updated automatically on subsequent Runs over saved history).
- `instructions` supplies per-request instructions. Set `toolset_instructions=False` to suppress automatic toolset guidance.

### Usage limits and retries

```python
from pydantic_ai.usage import UsageLimits

spec = AgentSpec(
    usage_limits=UsageLimits(request_limit=100, total_tokens_limit=200_000),
    retries={"tools": 2, "output": 1},
)
```

- Default Harness limits allow `1,000` model requests per Run (pass `UsageLimits(request_limit=None)` to remove the ceiling). Passing `usage_limits=` to `run()` replaces the whole definition budget.
- `AgentSpec.retries` defaults to `5` for tool failures and `3` for output validation failures.
- For interrupted model streams, configure `model_recovery=ModelRecoveryPolicy(...)` on `HarnessBuilder().build(...)`.

### Cold-start retention

After one hour of idle time since the last model response, Harness automatically trims oversized strings in older consumed tool results (exempting read `SKILL.md` files). Customize or disable it on `AgentSpec`:

```python
from a13n_harness.filters import ColdStartFilterConfiguration

spec = AgentSpec(cold_start_filter=ColdStartFilterConfiguration(idle_seconds=3_600))
disabled = spec.with_updates(cold_start_filter=None)
```

## Related guides

- [Models](models.md) — model selection, aliases, characteristics, and request affinity
- [Usage and limits](usage-and-limits.md) — token accounting and cost tracking
- [Observation](observation.md) — correlation IDs and OpenTelemetry tracing
- [Hosting](hosting.md) — production composition and cleanup
