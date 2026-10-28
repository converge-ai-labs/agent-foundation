---
title: Public API and payload reference
sidebarTitle: API and payload reference
description: Look up observer and display APIs, decode custom payloads, and choose processor replacements.
---

Use this page while implementing a consumer. For a runnable starting point, use the [quickstart](getting-started.md); for integration order and checkpoint handling, use [Events and processors](events.md) and [Replay and recovery](replay.md).

## Public names

Import these names from `a13n_stream_protocol`:

| Name                         | Use it to                                                                       |
| ---------------------------- | ------------------------------------------------------------------------------- |
| `HarnessAguiObserver`        | Convert a strictly single-Run source                                            |
| `HarnessAguiStreamObserver`  | Convert one root stream and its attributed inline children                      |
| `AguiEventProcessor`         | Type a synchronous `(source, event) -> event or None` callback                  |
| `AguiObservationError`       | Handle invalid observation, correlation, replay, or replacement                 |
| `AUTHORED_INPUT_EVENT_NAMES` | Select authored user and steering input                                         |
| `ContentMetadata`            | Read display, source, media, and application metadata                           |
| `tool_result_content`        | Project public tool values into text or ordered content parts without media I/O |
| `fragment_custom_event`      | Split an oversized CUSTOM event                                                 |
| `CustomEventAssembler`       | Reassemble complete CUSTOM events from ordered frames                           |
| `__version__`                | Read the installed distribution version                                         |

Both observer classes accept `processor=None` and `retain_events=True`:

| Member                                 | Behavior                                                          |
| -------------------------------------- | ----------------------------------------------------------------- |
| `observe(item)`                        | Return the new converted events as a detached tuple               |
| `event_count`                          | Count retained post-processor frames                              |
| `snapshot(*, start=0, stop=None)`      | Read detached frames in the half-open range `[start, stop)`       |
| `await resume(history)`                | Rebuild a fresh observer from a finite asynchronous source prefix |
| `export()`                             | Detach conversion continuation, excluding the event journal       |
| `restore(continuation, processor=...)` | Create a non-retaining observer from saved conversion state       |
| `thread_id`, `run_id`                  | Read the bound source identity; initially `None`                  |

### Display APIs

Import `DisplayFold`, `DisplaySnapshot`, `DisplayContinuation`, `Item`, and `StreamPosition` from `a13n_stream_protocol.display`.

| Operation                                                               | Result                                                                 |
| ----------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `DisplayFold(run_id, *, attempt=0, full_content=False, processor=None)` | Create a fold for conversion and display accumulation                  |
| `fold.events(source)`                                                   | Convert a native source item to serialized AG-UI events                |
| `fold.fold(events, source=None)`                                        | Accumulate events and return sequence/item references                  |
| `fold.export()`                                                         | Detach all items with their continuation                               |
| `DisplayFold.restore(snapshot, processor=None)`                         | Restore that checkpoint                                                |
| `fold.capture_delta()`                                                  | Detach items changed since the last capture, with current continuation |
| `fold.export_continuation()`                                            | Detach parser state separately                                         |

Default previews cap each text, argument, or result field at 262,144 characters and mark truncation. Large observation payloads retain their name rather than a full payload copy. `full_content=True` keeps full display content; retained conversation size still grows with the number and size of items. Use the [checkpoint procedure](replay.md#restore-a-compact-display-checkpoint) to keep content, continuation, and suffix selection together.

## Fragment a complete custom event

Run this in-memory round trip to check a consumer's framing path:

```python
from ag_ui.core.events import CustomEvent
from a13n_stream_protocol import CustomEventAssembler, fragment_custom_event

original = CustomEvent(name="example.document", value={"text": "x" * 60_000})
frames = fragment_custom_event(original, identity="document-1")
assembler = CustomEventAssembler()
complete = None
for frame in frames:
    complete = assembler.accept(frame.model_dump(mode="json", by_alias=True))
assert complete is not None
assert complete["name"] == "example.document"
assert complete["value"] == original.value
assert not assembler.gap
```

Pass the complete serialized CUSTOM envelope to `accept()`. Events whose JSON occupies at most 48 KiB of UTF-8 stay intact. Larger events become `a13n.stream.fragment` frames containing `id`, `index`, `count`, and string `data`. Use distinct identities for events that can interleave.

The assembler defaults to a 64 MiB pending-byte budget and eight pending identities. Both limits must be positive. `accept()` returns a complete event or `None` while waiting or rejecting a sequence. Invalid, inconsistent, out-of-order, nested, or over-budget fragments set `gap`, which stays set for that assembler.

For a fresh subscription, create a fresh assembler. For checkpoint continuation, `export()` and `restore(state)` preserve pending fragments; `DisplayFold` includes that state in its checkpoint. The Host handles transport termination and timeouts, because silence alone cannot reveal a missing final frame.

## Input metadata and media

Read input roles and message IDs from CUSTOM `value.event.role` and `value.event.message_id`. Metadata remains at the top level. `ContentMetadata` defaults to `display=True`, `source_id=None`, and `media=False`; `from_native()` reads a dict or returns defaults for other input types. Extra application metadata is preserved as presentation data.

Hide `display=False` content in normal presentation. Use `AUTHORED_INPUT_EVENT_NAMES` for user and steering text. Cache markers have no presentation content. Media observations use `a13n.input.media` and `media=True`:

| Native input      | Public descriptor                                  |
| ----------------- | -------------------------------------------------- |
| Binary bytes      | Kind, media type, size, and `payload_omitted=True` |
| HTTP(S) file URL  | URL and available media type                       |
| Other URL schemes | Omitted payload                                    |
| Uploaded file     | File ID, provider name, and media type             |

Resolve references through your Host's current access policy. The projection performs no media fetch and supplies no authority to access a file. Keep original model input separately if your application needs to restore it.

## Terminal events

Read the outcome to choose the next UI action:

| Event                                                     | Interpretation                                                    |
| --------------------------------------------------------- | ----------------------------------------------------------------- |
| `RUN_FINISHED`, success                                   | Display the completed result and usage                            |
| `RUN_FINISHED`, `outcome.type="interrupt"`                | Present deferred calls or approvals to the Host's answer workflow |
| `RUN_FINISHED`, `outcome.type="cancelled"`                | Show cancellation                                                 |
| `RUN_ERROR`                                               | Show the safe failure code/message and usage                      |
| `SUBAGENT_STARTED`, `SUBAGENT_FINISHED`, `SUBAGENT_ERROR` | Update the attributed inline child's display                      |

For an interrupt, `id` identifies the deferred target for answers, while `toolCallId` identifies the model call for transcript correlation. Direct calls use the same ID for both. Host answer validation remains authoritative. A non-JSON-safe completed result is omitted and marked `result_omitted` in `rawEvent`.

Source metadata retains Thread, Run, sequence, and occurrence time. Use Host records to confirm durable result storage, external delivery, and billing; terminal presentation alone does not establish those outcomes.

## Processor replacement limits

A processor receives the complete event before fragmentation. Return `None` to omit it, or replace one of the following content fields while preserving all structural fields:

| Event family                                    | Mutable field     |
| ----------------------------------------------- | ----------------- |
| Text/reasoning content and tool argument deltas | `delta`           |
| Encrypted reasoning                             | `encrypted_value` |
| Tool result                                     | `content`         |
| Run finished                                    | `result`          |
| Run error                                       | `message`         |
| CUSTOM and unlisted events                      | None              |

Keep type, IDs, timestamps, lifecycle, and source correlation unchanged. Omit a CUSTOM event as a whole when policy requires hiding its payload. Validation finishes before the source item commits. Keep processors deterministic and side-effect-free; persist or publish the batch only after `observe()` returns.
