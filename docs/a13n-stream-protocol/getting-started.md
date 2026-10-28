---
title: Stream Protocol quickstart
sidebarTitle: Quickstart
description: Run an offline Agent, convert its output, and inspect the resulting AG-UI events.
---

This example runs Harness with a test model and prints one JSON object per event. You will see where execution ends and where your application's storage or transport begins. It needs Python 3.13 and `uv`, with no model credentials.

## Prepare the source checkout

Clone the repository and install its locked Stream Protocol dependencies:

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-stream-protocol
```

Run the remaining steps from this directory. In an existing application, install the published `a13n-stream-protocol` package and follow the documentation for its release; it pins the matching Harness version.

## Convert one Run

Save the following as `stream_example.py`:

```python
import asyncio

from a13n_harness import AgentSpec, HarnessBuilder, HarnessRunResultEvent
from a13n_stream_protocol import HarnessAguiObserver
from ag_ui.core import Event
from pydantic import TypeAdapter
from pydantic_ai.models.test import TestModel


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=TestModel(custom_output_text="Hello from the stream"),
    )
    observer = HarnessAguiObserver()
    adapter = TypeAdapter(Event)
    terminal = None

    # This example has no children: every item belongs to the same Run.
    async with executable.stream("Say hello") as stream:
        async for item in stream:
            for event in observer.observe(item):
                print(adapter.dump_json(event, by_alias=True).decode())
            if isinstance(item, HarnessRunResultEvent):
                terminal = item.result

    assert terminal is not None
    assert terminal.output_or_raise() == "Hello from the stream"
    assert observer.snapshot()


if __name__ == "__main__":
    asyncio.run(main())
```

Run it:

```console
uv run python stream_example.py
```

The program exits after its assertions pass. In the output, find `RUN_STARTED`, the text containing `Hello from the stream`, and `RUN_FINISHED`. Public CUSTOM observations appear alongside them. IDs and timestamps vary between executions.

Read the loop in three steps:

1. `executable.stream()` runs the Agent and yields public Harness items. Its context manager owns scoped cleanup.
2. `observer.observe(item)` converts one item into zero or more typed AG-UI events.
3. `dump_json(..., by_alias=True)` writes wire field names such as `messageId`. Your Host can store or send that event where the example prints it.

The output uses JSON Lines. An SSE endpoint would add its own framing around these events.

## Add child Runs or multiple concurrent Runs

Switch to `HarnessAguiStreamObserver` when the root stream forwards inline-child observations. It tracks each child's output within the root stream. Use a separate observer for every independently executed root or asynchronous child Run, including another Run on the same Thread.

Follow the [stream observer example](events.md#observe-a-harness-run) when you add delegation. The quickstart's single-Run observer intentionally rejects observations from other Runs.

## Add a transport deliberately

For a live integration, replace printing with your Host's persistence and delivery function. Publish each new batch once, then decide what a reconnecting consumer can recover:

| Consumer requirement               | Host setup                                                                                       |
| ---------------------------------- | ------------------------------------------------------------------------------------------------ |
| Live events stored elsewhere       | Set `retain_events=False` and persist batches returned by `observe()`                            |
| In-process event inspection        | Keep the default journal and read `snapshot()`                                                   |
| Restorable messages and tool calls | Use `DisplayFold` and save [display checkpoints](replay.md#restore-a-compact-display-checkpoint) |

Give the transport durable IDs, ordering, retention, and a replay-to-live boundary. Define how clients learn about a delivery gap. Apply visibility policy before publishing. These decisions belong to your application; Stream Protocol supplies the event conversion and display state.

## Next steps

Use [Events and processors](events.md) to connect the live stream and filter content. Use [Replay and recovery](replay.md) to restore an unfinished message and verify that its final content matches uninterrupted delivery.
