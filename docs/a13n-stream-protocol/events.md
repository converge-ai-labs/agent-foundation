---
title: Events and processors
description: Connect a Harness stream, publish event batches, and filter content before delivery.
---

Once the [offline quickstart](getting-started.md) works, replace its printing with your application's delivery path. Convert each public source item in order, serialize the returned batch, and store or send it through your Host.

## Observe a Harness Run

This integration fragment assumes an existing `executable`, `input_value`, `bindings`, and a Host-owned `persist_and_publish()` method:

```python
from a13n_stream_protocol import HarnessAguiStreamObserver

observer = HarnessAguiStreamObserver(retain_events=False)
async with executable.stream(input_value, bindings=bindings) as run_stream:
    async for item in run_stream:
        new_events = observer.observe(item)
        await host.persist_and_publish(new_events)
```

`new_events` contains only events produced by the current item. A text part with initial content can produce both a start and a content event. An omitted observation can produce an empty batch. Publish these incremental batches rather than repeatedly sending the accumulated snapshot.

Create one stream observer per independently executed root or asynchronous child Run. It binds `thread_id` and `run_id` on the first successful observation and tracks forwarded inline children separately. A strictly single-Run source can use `HarnessAguiObserver`.

Inline-child output carries `subagentRunId` while retaining its native message and tool-call IDs. Include that attribution in your display keys. Child completion follows the accepted delegation result after output projection and state retention, so the parent display reflects the result Harness actually accepted.

### Serialize events

The observer returns typed AG-UI models. Serialize them with wire aliases before handing JSON-compatible payloads to a database or transport:

```python
from ag_ui.core import Event
from pydantic import TypeAdapter

EVENT_ADAPTER = TypeAdapter(Event)

payloads = [
    EVENT_ADAPTER.dump_python(event, mode="json", by_alias=True)
    for event in new_events
]
```

Keep your durable delivery ID and cursor in a Host envelope around the event. This preserves the protocol's source correlation while giving consumers a stable reconnect position.

### Event mappings

Use standard event types for messages, tool calls, and Run outcomes. Handle CUSTOM events by their names and payloads:

| Source observation                     | AG-UI output                                                                             |
| -------------------------------------- | ---------------------------------------------------------------------------------------- |
| Logical Run start                      | `RUN_STARTED` with `protocolVersion: "1.0"`                                              |
| Text start, content, end               | `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END`                         |
| Reasoning content and signature        | Reasoning message events and `REASONING_ENCRYPTED_VALUE`                                 |
| Completed tool-call arguments          | `TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`                                     |
| Successful tool result                 | `TOOL_CALL_RESULT`                                                                       |
| Completed, cancelled, or suspended Run | `RUN_FINISHED` with the corresponding outcome                                            |
| Failed Run                             | `RUN_ERROR`                                                                              |
| Inline delegation                      | `SUBAGENT_STARTED`, `SUBAGENT_FINISHED`, `SUBAGENT_ERROR`                                |
| Native `CapabilityEvent`               | `CUSTOM` using its native `kind`                                                         |
| Other unmatched public observation     | Namespaced `CUSTOM`, such as `a13n.harness.lifecycle` or `a13n.pydantic_ai.final_result` |

A completed argument list marks the tool call's input, not successful execution. Read the result separately. [Terminal events](api-reference.md#terminal-events) describes outcomes and deferred-call IDs.

### Content and large custom events

Custom payloads retain their meaning: file edits carry before/after data, compaction summaries carry summary data, and shell observations carry status. Render each as the appropriate observation. A compaction summary can arrive before checkpoint publication; show “checkpoint saved” only after the Host has accepted and persisted the completed result.

Input observations carry role and message ID inside `value.event`, with presentation metadata at the top level. Honor `display: false` in normal presentation. Tool results can contain ordered text, image, audio, video, or document parts. Public projection omits binary payloads and keeps supplemental tool media model-only; see [input metadata and media](api-reference.md#input-metadata-and-media).

A CUSTOM event larger than 48 KiB becomes `a13n.stream.fragment` frames. Keep one assembler for the subscription and feed it each complete serialized CUSTOM envelope:

```python
from a13n_stream_protocol import CustomEventAssembler

assembler = CustomEventAssembler()

# payload is a complete serialized CUSTOM event from this subscription.
complete = assembler.accept(payload)
if complete is not None:
    render_custom(complete["name"], complete["value"])
```

Interpret only the completed event. Check `assembler.gap` to report rejected or missing fragments, and reset the assembler when starting a new subscription. A missing final frame needs the Host's termination or timeout handling. The [fragment example](api-reference.md#fragment-a-complete-custom-event) demonstrates an offline round trip and lists the bounds.

## Apply a Host Processor

Pass a synchronous processor when the Host needs to hide an event or change a permitted content field. This example suppresses diagnostic observations and strips leading and trailing null characters from text deltas:

```python
from typing import Any

from ag_ui.core import Event
from ag_ui.core.events import CustomEvent, TextMessageContentEvent
from a13n_harness import HarnessStreamEvent
from a13n_stream_protocol import HarnessAguiStreamObserver


def process_event(
    source: HarnessStreamEvent[Any],
    event: Event,
) -> Event | None:
    del source
    if isinstance(event, CustomEvent) and event.name == "a13n.harness.diagnostic":
        return None
    if isinstance(event, TextMessageContentEvent):
        return event.model_copy(update={"delta": event.delta.strip("\x00")})
    return event


observer = HarnessAguiStreamObserver(processor=process_event, retain_events=False)
```

The observer applies the processor before fragmentation and validates every replacement. Keep event identity, correlation, and structure unchanged; the [replacement table](api-reference.md#processor-replacement-limits) lists writable fields. To suppress CUSTOM content, omit the whole event.

Make the processor a pure function of its inputs and stable Host configuration. Replaying the same source must produce the same events. Store and publish after `observe()` returns, outside the processor. This also keeps an invalid replacement from partially committing the current source item.

## Read the Accumulated Snapshot

For debugging or in-process inspection, construct the observer with the default `retain_events=True`. Capture the current count and read a fixed prefix in batches:

```python
stop = observer.event_count
for start in range(0, stop, 100):
    events = observer.snapshot(start=start, stop=min(start + 100, stop))
    inspect_batch(events)
```

Returned events are detached copies. Changes to those copies leave the observer unchanged. The journal has no retention cap, and its positions are local indices rather than transport cursors.

With `retain_events=False`, `observe()` returns the same converted events, `event_count` stays zero, and `snapshot()` raises `AguiObservationError`. Choose [DisplayFold checkpoints](replay.md#restore-a-compact-display-checkpoint) when you need accumulated display content without retaining every token event.
