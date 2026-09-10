# Getting started

Convert an offline Harness Run into AG-UI events. This example uses the real Harness stream and converter but no provider credentials, browser, server, or network transport.

## Prepare the source workspace

Use Python 3.13 and the repository lockfile:

```console
git clone https://github.com/converge-ai-labs/agent-foundation.git
cd agent-foundation
uv sync --locked --package a13n-stream-protocol
```

For a published installation, install `a13n-stream-protocol` and use the API documented for that release. It requires the matching Harness version.

## Convert one Run

Save as `stream_example.py` and run `uv run python stream_example.py`:

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

Each printed line is an AG-UI event encoded as JSON. The output includes text events, public custom observations, and a terminal event; IDs and timestamps vary. It is **JSON Lines, not SSE**. The application chooses a delivery framing separately.

1. Harness owns Agent execution and scoped cleanup.
2. `observe(item)` converts one public source item, potentially into multiple AG-UI events.
3. The Pydantic adapter serializes the typed event with its wire aliases.
4. The Host would persist/publish the incremental events where this example prints them.

## Add child Runs or multiple concurrent Runs

Route source items by `(item.thread_id, item.run_id)` and keep an observer for each pair. A parent stream can forward child items without changing their correlation. Feeding them all into a single observer is a correlation error.

Use the [multi-Run routing example](events.md#observe-a-harness-run) before enabling delegation.

## Add a transport deliberately

The converter supplies no HTTP routes, replay cursor, durable event ID, retention policy, or reconnect loop. Your Host must decide:

- whether source observations, projected events, or both are retained;
- which content the consumer may see;
- how to signal gaps and terminal outcomes;
- how to cut over from replay to live delivery without overlap;
- how much observer state to retain in memory.

The observer accumulates post-processor events without a retention cap. Keep its lifetime scoped to one Run and account for long streams in your Host. Do not repeatedly publish `snapshot()` when you want only newly produced events.

## Next steps

[Events and processors](events.md) explains mappings, custom-event fragments, and filtering. [Replay and recovery](replay.md) explains reconstruction and why it is not Agent recovery.
