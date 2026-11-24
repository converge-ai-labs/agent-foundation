---
title: Harness quickstart
sidebarTitle: Quickstart
description: Build your first type-safe Python agent offline, stream token events, maintain multi-turn thread state, and connect external model providers.
---

Build and run your first **Harness** agent offline with zero API keys, then extend it to multi-turn state, live streaming, and real LLM providers.

## Requirements

Install Python 3.13+, Git, and [`uv`](https://docs.astral.sh/uv/), then sync the workspace:

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

## Run an offline Agent and continue a Thread

Save as `app.py` and run `uv run python app.py`:

```python title="app.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    del messages, info
    yield "Hello from Harness!"


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(instructions="You are a helpful assistant."),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    first = await executable.run("First turn")
    second = await executable.run("Second turn", previous_state=first.state)
    assert second.thread_id == first.thread_id and second.run_id != first.run_id
    print(second.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

| Concept                       | Role                                                                        |
| ----------------------------- | --------------------------------------------------------------------------- |
| `AgentSpec`                   | Declarative prompt, model route, limits, and serializable Capabilities      |
| `HarnessBuilder().build(...)` | Compiles `AgentSpec`, `output_type`, and Capabilities into a reusable Agent |
| `executable.run(...)`         | Executes one logical Run and returns `HarnessRunResult`                     |
| `result.state`                | Portable `HarnessState` checkpoint passed via `previous_state=`             |

## Stream live events

Use `async with executable.stream(...) as stream:` to render incremental tokens while guaranteeing resource cleanup:

```python
from a13n_harness import HarnessEvent, HarnessRunResultEvent
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta

async with executable.stream("Stream a greeting") as stream:
    async for item in stream:
        if isinstance(item, HarnessRunResultEvent):
            item.result.raise_for_status()
        elif isinstance(item, HarnessEvent):
            ev = item.event
            if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                print(ev.part.content, end="", flush=True)
            elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                print(ev.delta.content_delta, end="", flush=True)
```

## Switch to a real Model and add Capabilities

Set `model` on `AgentSpec` (omitting builder `model=`) and attach modular [Capabilities](capabilities.md):

```python
import os
from a13n_harness.capabilities import RuntimeContextCapability, WorkingStateCapability

executable = HarnessBuilder().build(
    AgentSpec(
        model=os.environ["HARNESS_MODEL"],
        instructions="Provide concise, actionable answers.",
    ),
    output_type=str,
    capabilities=(RuntimeContextCapability(), WorkingStateCapability()),
)
```

```bash
export OPENAI_API_KEY="sk-..."
export HARNESS_MODEL="openai-responses:gpt-5"
uv run python app.py
```

## Next steps

- [Agents and Runs](agents-and-runs.md) — `RunBindings`, steering, limits, and retries
- [Tools and dependencies](tools-and-dependencies.md) — custom Python tools and request-scoped dependencies
- [State and Resume](state-and-resume.md) — JSON checkpoint persistence, forks, and human-in-the-loop resume
- [Environments](environments.md) — sandboxed file, shell, and desktop tools
