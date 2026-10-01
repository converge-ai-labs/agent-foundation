---
title: Events and processors
description: Convert a Harness Run into AG-UI events, read snapshots, and apply Host processors.
---

Use one `HarnessAguiStreamObserver` per root stream and feed it each public source item in order, including forwarded inline children. It converts observations, not private state. Start with the [offline example](getting-started.md), then add your Host's transport and persistence.

## Observe a Harness Run

Create one stream observer for each independently executed root or asynchronous child Run:

```python
from a13n_stream_protocol import HarnessAguiStreamObserver

observer = HarnessAguiStreamObserver()
async with executable.stream(input_value, bindings=bindings) as run_stream:
    async for item in run_stream:
        new_events = observer.observe(item)
        await host.persist_and_publish(new_events)

snapshot = observer.snapshot()
```

The stream observer preserves child native IDs and adds `subagentRunId` to child output. Namespace display keys by root, child attribution, and native ID. A child terminal comes from the authoritative delegation result after output projection and retention, not the child's private Run result. If the source contains exactly one Run, `HarnessAguiObserver` remains the smaller API.

`observe()` returns only the AG-UI events produced by that source item. A single source item can produce several events—for example, a text part with initial content produces both `TEXT_MESSAGE_START` and `TEXT_MESSAGE_CONTENT`.

The first successfully observed item binds `observer.thread_id` and `observer.run_id`. Later root items retain that correlation; each inline child has independently validated correlation and multipart state. Use another observer when the Harness starts another Run, even when both Runs advance the same Thread.

### Event mappings

The observer uses standard AG-UI events when the semantics match directly:

| Harness observation                          | AG-UI output                                                     |
| -------------------------------------------- | ---------------------------------------------------------------- |
| Text part start, delta, and end              | `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END` |
| Reasoning start, content, signature, and end | Reasoning message events and `REASONING_ENCRYPTED_VALUE`         |
| Completed tool call                          | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`             |
| Successful tool result                       | `TOOL_CALL_RESULT`                                               |
| Completed Run result                         | `RUN_FINISHED`                                                   |
| Logical Run start                            | `RUN_STARTED` with `protocolVersion: "1.0"`                      |
| Cancelled Run result                         | `RUN_FINISHED` with cancelled outcome                            |
| Suspended Run result                         | `RUN_FINISHED` with interrupt outcome                            |
| Failed Run result                            | `RUN_ERROR`                                                      |
| Inline delegation                            | `SUBAGENT_STARTED`, `SUBAGENT_FINISHED`, `SUBAGENT_ERROR`        |
| Native Pydantic AI `CapabilityEvent`         | `CUSTOM` event named by the native `kind`                        |
| Another unmatched public observation         | Namespaced `CUSTOM` event                                        |

Unmatched Harness extensions use names such as `a13n.harness.lifecycle`. Unmatched Pydantic AI events use names such as `a13n.pydantic_ai.final_result`. A native `CapabilityEvent` retains its concrete kind, Capability ID, optional Tool-call correlation, and public payload. Every custom value also retains the public Thread, Run, sequence, timestamp, and source-event representation.

### Content and large custom events

Capability events preserve their native name and payload, including user-defined kinds. A file edit remains before/after data, a summary remains summary data, and a shell status remains a status observation. The protocol does not turn them into assistant answers or pre-rendered panels. Clients decide their presentation. Input uses source-specific CUSTOM events with `role` and `message_id` inside `value.event` and top-level `ContentMetadata`; normal displays omit content marked `display: false`. Public tool execution values can contain ordered text, image, audio, video, or document parts. Supplemental tool media stays model-only; binary bytes never enter the public protocol.

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
