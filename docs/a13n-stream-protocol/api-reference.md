---
title: Public API and payload reference
sidebarTitle: API and payload reference
description: The package's public names, custom events, input metadata, and terminal events.
---

Stream Protocol exposes AG-UI 1.0 conversion, content projection, and compact display normalization. Hosts own transport and checkpoint storage; Harness owns execution continuation.

## Public names

| Export from `a13n_stream_protocol` | Purpose                                                                                                   |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `HarnessAguiObserver`              | Bind one Thread/Run, observe source items, snapshot detached events, or resume from finite source history |
| `HarnessAguiStreamObserver`        | Observe one root stream with attributed inline children and independent multipart state                   |
| `AUTHORED_INPUT_EVENT_NAMES`       | Names for authored user and steering input                                                                |
| `tool_result_content`              | Project a public execution value into text or ordered upstream content parts without media I/O            |
| `AguiEventProcessor`               | Synchronous `(source, event) -> event or None` Host projection callback                                   |
| `AguiObservationError`             | Invalid correlation, observation, replay, or processor replacement                                        |
| `ContentMetadata`                  | Presentation conventions plus opaque extra metadata                                                       |
| `fragment_custom_event`            | Split an oversized CUSTOM event without losing its domain JSON                                            |
| `CustomEventAssembler`             | Reassemble ordered frames within one bounded subscription                                                 |
| `__version__`                      | Installed distribution version                                                                            |

Both observer classes accept `processor=None` and `retain_events=True`. They expose `observe(item)`, `snapshot(*, start=0, stop=None)`, `event_count`, async `resume(history)`, `export()`, classmethod `restore(continuation, processor=...)`, and read-only `thread_id` / `run_id`. Restore creates a non-retaining observer. IDs are unbound until observation or reconstruction succeeds.

### Display APIs

Import display types from `a13n_stream_protocol.display`, not the package root:

| API                                                                | Purpose                                                                      |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| `DisplayFold(run_id, *, attempt=0, full_content=False)`            | Convert native observations and normalize raw events into display items      |
| `fold.events(source)`                                              | Convert one native source item with the fold's non-retaining stream observer |
| `fold.fold(events, source=None)`                                   | Accumulate events and return sequence/item references                        |
| `fold.export()` / `DisplayFold.restore(snapshot)`                  | Detach and restore a `DisplaySnapshot` containing items and continuation     |
| `fold.export_continuation()`                                       | Detach parser state separately from display items                            |
| `DisplaySnapshot`, `DisplayContinuation`, `Item`, `StreamPosition` | Validate and serialize checkpoint content and continuation                   |

The default display preview limits each text, argument, or result field to 262,144 characters and marks truncation. Large observation payloads keep their name rather than an unbounded payload copy. `full_content=True` retains full display content. Neither mode is constant-memory: retained items still grow with the conversation.

See [Events and processors](events.md) for live conversion and [Replay and recovery](replay.md) for executable checkpoint restore and source reconstruction.

## Fragment a complete custom event

This complete example performs an in-memory round trip without network or an Agent:

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

Pass the **complete serialized CUSTOM envelope** to `accept()`, not only its `value`. Events at or below 48 KiB encoded UTF-8 remain intact. Larger events become `a13n.stream.fragment` events with `id`, `index`, `count`, and string `data`. Choose identities unique enough for interleaved events in your subscription.

The assembler defaults to 64 MiB pending bytes and eight pending identities; both bounds must be positive. It returns `None` until an entire event is reconstructed, or on rejected fragments. Invalid, inconsistent, out-of-order, nested, or over-budget sequences set the sticky `gap` flag. It never publishes a partial domain event.

One assembler belongs to one live subscription. Create a new assembler on reconnect. A missing tail with no subsequent frame cannot be detected from silence alone; the Host owns stream termination/timeouts and gap presentation. Fragment assembly is not durable replay.

## Input metadata and media

`ContentMetadata` defaults to `display=True`, `source_id=None`, and `media=False`, and allows opaque extra metadata. `from_native()` reads a metadata dict or returns defaults for a non-dict input. Metadata is presentation data, not instructions or authorization.

Text input uses source-specific CUSTOM events such as `a13n.input.user`; `role` and `message_id` live inside `value.event`, while presentation metadata remains top-level. A client normally hides content with `display=False`. Cache markers produce no presentation content. Media uses `a13n.input.media` with `media=True`:

| Native input      | Projection                                                          |
| ----------------- | ------------------------------------------------------------------- |
| Binary bytes      | Kind, media type, size, and `payload_omitted=True`; no byte payload |
| HTTP(S) file URL  | Reference URL and optional available media type                     |
| Other URL schemes | Payload omitted rather than embedding inline payloads               |
| Uploaded file     | File ID, provider name, and media type                              |

This is one-way observation, not a codec for restoring model input or a media-storage service. Resolving an application media reference still requires current Host access policy.

## Terminal events

- Completed output becomes `RUN_FINISHED` with success outcome and usage. Non-JSON-safe output is omitted and marked `result_omitted` in `rawEvent` rather than serialized as arbitrary Python.
- Suspended output becomes `RUN_FINISHED` with `outcome.type="interrupt"` and one interrupt per deferred call or approval. `id` and `toolCallId` preserve the native call ID; Host pending-answer policy remains authoritative.
- Cancellation becomes `RUN_FINISHED` with `outcome.type="cancelled"`.
- Failure becomes `RUN_ERROR` with safe failure code/message and usage.
- Inline children use `SUBAGENT_STARTED`, `SUBAGENT_FINISHED`, and `SUBAGENT_ERROR`; their content carries `subagentRunId` rather than nested root lifecycles.

Source correlation includes Thread, Run, sequence, and occurrence time. A terminal presentation event does not mean the Host durably committed the result, delivered a message, or settled billing.

## Processor replacement limits

The processor sees complete domain events **before** fragmentation. It can return `None` to omit an event. Replacements preserve event type, IDs, timestamps, lifecycle/source correlation, and every structural field. Only these content fields are mutable:

| Event family                                      | Mutable field     |
| ------------------------------------------------- | ----------------- |
| Text/reasoning content, tool-call argument deltas | `delta`           |
| Encrypted reasoning                               | `encrypted_value` |
| Tool result                                       | `content`         |
| Run finished                                      | `result`          |
| Run error                                         | `message`         |
| CUSTOM and unlisted events                        | None              |

CUSTOM payload rewriting is not supported; omit the event if policy requires suppression. Replacements are validated before the source item is committed. Replay processors must be deterministic and side-effect-free; publish/persist only after the observer returns its newly committed events.
