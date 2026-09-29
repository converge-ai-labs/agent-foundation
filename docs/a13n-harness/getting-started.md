---
title: Harness quickstart
sidebarTitle: Quickstart
description: Build and run an offline agent, then connect a model provider.
---

## Requirements

- Python 3.13 or later
- [uv](https://docs.astral.sh/uv/)

These guides track the source API on `main`. To reproduce them with matching dependencies, clone the repository and synchronize the locked Harness package:

```bash
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-harness
```

The offline example uses a `FunctionModel` from the installed runtime dependency; it needs no provider key.

## Run an offline Agent

Create `app.py`:

```python title="app.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
)
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> AsyncIterator[str]:
    del messages, info
    yield "Hello from the Harness"


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )

    result = await executable.run("Say hello")
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

Run it:

```bash
uv run python app.py
```

The output is:

```text
Hello from the Harness
```

The deterministic `FunctionModel` keeps this path offline and makes it suitable for tests.

## Understand the execution path

`HarnessBuilder` combines `AgentSpec` and the offline `FunctionModel` into a reusable executable. Each `run()` starts a scoped execution and returns output plus continuation state. Here the model is passed to the builder, so `AgentSpec.model` is unset; choose one model source when building an Agent.

## Use a real model

In `app.py`, replace the `executable = ...` block in `main()` with:

```python
    executable = HarnessBuilder().build(
        AgentSpec(
            model="openai-responses:gpt-5",
            instructions="Answer clearly and concisely.",
        ),
        output_type=str,
    )
```

Set the OpenAI provider credential and run the **edited** file:

```bash
export OPENAI_API_KEY=your-api-key
uv run python app.py
```

The `respond` function and `FunctionModel` imports from the offline example are no longer needed. For other providers or short-lived credentials, see [Models](models.md) and [Authentication and HTTP clients](model-authentication.md).

## Build once and continue a Thread

An executable is reusable across calls. Each call creates a new `run_id`; passing `previous_state` continues the same Harness Thread:

```python
first = await executable.run("Remember that the release is Friday.")
second = await executable.run(
    "When is the release?",
    previous_state=first.state,
)
```

Persist the returned `HarnessState` to continue after a process restart; reconstruct current credentials and Environment resources separately.

## Stream public events

To print text as it arrives, replace `main()` in the offline `app.py` with this version (and add the imports shown):

```python
from a13n_harness import HarnessEvent, HarnessRunResultEvent
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond)
    )
    async with executable.stream("Say hello") as stream:
        async for item in stream:
            if isinstance(item, HarnessRunResultEvent):
                item.result.raise_for_status()
            elif isinstance(item, HarnessEvent):
                event = item.event
                if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                    print(event.part.content, end="", flush=True)
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    print(event.delta.content_delta, end="", flush=True)
    print()
```

Run `uv run python app.py` again. The `async with` scope closes Run resources even if the consumer stops early. For a reusable streaming application with persisted state, see the [agent-app example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app).

## Add capabilities

Add optional Capabilities when your Agent needs their tools:

```python
from a13n_harness.capabilities import (
    RuntimeContextCapability,
    WorkingStateCapability,
)

executable = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=respond),
    capabilities=(RuntimeContextCapability(), WorkingStateCapability()),
)
```

Keep current credentials and per-request clients in run bindings; see [Capabilities](capabilities.md) for other options.

## Next steps

- [Test the Agent offline](testing.md).
- Read [Agents and Runs](agents-and-runs.md) for model routing, streaming, results, cleanup, and usage.
- Read [Capabilities](capabilities.md) to select optional first-party behavior.
- Connect files, commands, processes, and ports with [Environments](../environments/index.md).
- Run the [Agent Application example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app) for repeated streaming turns, persisted state, and restart recovery.
