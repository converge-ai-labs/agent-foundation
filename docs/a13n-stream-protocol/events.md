---
title: Events and processors
description: Convert a Harness Run into AG-UI events, read snapshots, and apply Host processors.
---

Use one `HarnessAguiObserver` per `(thread_id, run_id)` and feed it each public source item in order. It converts observations, not private state. Start with the [offline example](getting-started.md), then add your Host's transport and persistence.

## Observe a Harness Run

Create one observer for each root or exposed child Run, then route every public source item by its Thread and Run correlation:

```python
from a13n_stream_protocol import HarnessAguiObserver

observers: dict[tuple[str, str], HarnessAguiObserver] = {}

async with executable.stream(input_value, bindings=bindings) as run_stream:
    async for item in run_stream:
        correlation = (item.thread_id, item.run_id)
        observer = observers.get(correlation)
        if observer is None:
            observer = HarnessAguiObserver()
            observers[correlation] = observer

        new_events = observer.observe(item)
        await host.persist_and_publish(new_events)

snapshots = {
    correlation: observer.snapshot()
    for correlation, observer in observers.items()
}
```

A parent Harness stream can forward inline-child observations while preserving each child's own `thread_id` and `run_id`. Routing first prevents a root observer from rejecting a child Run. If an integration guarantees that its source contains exactly one Run, it can use one `HarnessAguiObserver` directly.

`observe()` returns only the AG-UI events produced by that source item. A single source item can produce several events—for example, a text part with initial content produces both `TEXT_MESSAGE_START` and `TEXT_MESSAGE_CONTENT`.

The first successfully observed item binds `observer.thread_id` and `observer.run_id`. Later items passed to that observer must have the same correlation. Use another observer when the Harness starts another Run, even when both Runs advance the same Thread.

### Event mappings

The observer uses standard AG-UI events when the semantics match directly:

| Harness observation                                      | AG-UI output                                                     |
| -------------------------------------------------------- | ---------------------------------------------------------------- |
| Text part start, delta, and end                          | `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END` |
| Reasoning start, content, signature, and end             | Reasoning message events and `REASONING_ENCRYPTED_VALUE`         |
| Completed tool call                                      | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`             |
| Successful tool result                                   | `TOOL_CALL_RESULT`                                               |
| Completed Run result                                     | `RUN_FINISHED`                                                   |
| Failed or cancelled Run result                           | `RUN_ERROR`                                                      |
| Native Pydantic AI `CapabilityEvent`                     | `CUSTOM` event named by the native `kind`                        |
| Suspended result or another unmatched public observation | Namespaced `CUSTOM` event                                        |

Unmatched Harness extensions use names such as `a13n.harness.lifecycle`. Unmatched Pydantic AI events use names such as `a13n.pydantic_ai.final_result`. A native `CapabilityEvent` retains its concrete kind, Capability ID, optional Tool-call correlation, and public payload. Every custom value also retains the public Thread, Run, sequence, timestamp, and source-event representation.

### Content and large custom events

Capability events preserve their native name and payload, including user-defined kinds. A file edit remains before/after data, a summary remains summary data, and a shell status remains a status observation. The protocol does not turn them into assistant answers or pre-rendered panels. Clients decide their presentation. Actual model input uses user-role text events with shared `ContentMetadata`; normal displays omit content marked `display: false`.

Custom events larger than 48 KiB use generic `a13n.stream.fragment` frames. Reassemble them before inspecting the original event:

```python
from a13n_stream_protocol import CustomEventAssembler

assembler = CustomEventAssembler()

# For each CUSTOM payload in one live subscription:
complete = assembler.accept(payload)
if complete is not None:
    render_custom(complete["name"], complete["value"])
```

The framing preserves the complete JSON structure rather than truncating the source. The assembler bounds pending content and rejects incomplete or inconsistent sequences; check `assembler.gap` and replace the assembler when resetting a subscription. These are best-effort observations, not a durable event log. See the [framing contract](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-stream-protocol/00-overview.md#large-custom-events) for limits and fields.

### Serialize events

The returned values are typed `ag_ui.core.Event` models. Use the upstream Pydantic adapter when a transport or store needs JSON-compatible values:

```python
from ag_ui.core import Event
from pydantic import TypeAdapter

EVENT_ADAPTER = TypeAdapter(Event)

payloads = [
    EVENT_ADAPTER.dump_python(event, mode="json", by_alias=True)
    for event in new_events
]
```

The Host should wrap serialized events in its own durable IDs, ordering records, and delivery metadata rather than rewriting protocol correlation.

## Read the Accumulated Snapshot

`snapshot()` returns detached copies of all retained post-processor events observed so far:

```python
events = observer.snapshot()
```

Mutating an event returned by `observe()` or `snapshot()` does not mutate the observer. The snapshot is process-local convenience state, not a durable event log or Harness continuation value.

The observer does not compact streaming chunks or enforce a retention limit. A long-running Host should persist incremental results and apply its own bounded retention or projection policy.

## Apply a Host Processor

A processor can omit an event or replace approved content fields before the event is accumulated and returned:

```python
from typing import Any

from ag_ui.core import Event
from ag_ui.core.events import CustomEvent, TextMessageContentEvent
from a13n_harness import HarnessStreamEvent
from a13n_stream_protocol import HarnessAguiObserver


def process_event(
    source: HarnessStreamEvent[Any],
    event: Event,
) -> Event | None:
    del source

    if (
        isinstance(event, CustomEvent)
        and event.name == "a13n.harness.diagnostic"
    ):
        return None

    if isinstance(event, TextMessageContentEvent):
        return event.model_copy(update={"delta": event.delta.strip("\x00")})

    return event


observer = HarnessAguiObserver(processor=process_event)
```

A replacement must preserve the AG-UI event type and structural correlation, including message, tool, Thread, Run, lifecycle, and source fields. Invalid replacements raise `AguiObservationError` without committing the current source item.

A processor used with `resume()` must be replay-stable: the same ordered source history and stable Host configuration must produce the same retained event sequence. It must not retain mutable processing state or perform persistence, publication, acknowledgements, or other externally observable effects.
