---
title: Replay and recovery
description: Restore compact display checkpoints or rebuild an observer from exact source history.
---

Choose the recovery API from the data your Host retained.

## Choose the recovery path

| Retained data                         | API                                                              | Restores                                       |
| ------------------------------------- | ---------------------------------------------------------------- | ---------------------------------------------- |
| Display items and parser continuation | `DisplayFold.restore(snapshot)`                                  | Display content and AG-UI parsing state        |
| Observer conversion continuation      | `HarnessAguiStreamObserver.restore(continuation, processor=...)` | Conversion for the same root stream            |
| Exact finite Harness source prefix    | `await observer.resume(history)`                                 | Conversion; optional historical event snapshot |
| `HarnessState`                        | Start a new Harness Run                                          | Agent execution in a new Run                   |

Keep checkpoint items and continuation together. Resume at the next event, with no gap or overlap.

## Restore a compact display checkpoint

This example saves an unfinished message, restores it, and appends the remaining events:

```python
from a13n_stream_protocol.display import DisplayFold, DisplaySnapshot

prefix = [
    {"type": "TEXT_MESSAGE_START", "messageId": "message-1", "role": "assistant", "timestamp": 1000},
    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "message-1", "delta": "Hello", "timestamp": 1001},
]
suffix = [
    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "message-1", "delta": " world", "timestamp": 1002},
    {"type": "TEXT_MESSAGE_END", "messageId": "message-1", "timestamp": 1003},
]
fold = DisplayFold("run-example")
fold.fold(prefix)
checkpoint_json = fold.export().model_dump_json()

restored = DisplayFold.restore(DisplaySnapshot.model_validate_json(checkpoint_json))
restored.fold(suffix)

uninterrupted = DisplayFold("run-example")
uninterrupted.fold(prefix + suffix)
assert restored.export() == uninterrupted.export()
item = next(iter(restored.items.values()))
assert item.content["text"] == "Hello world"
assert item.state == "completed"
```

The restored message is identical to uninterrupted output. Export returns a detached checkpoint and leaves active messages and tool calls open.

To convert native Harness observations and accumulate the display:

```python
fold = DisplayFold(run_id)
async for source in run_stream:
    observed = fold.fold(fold.events(source), source)
    await host.persist_and_publish(observed)
```

Passing `source` also records failed tool results. This checkpoint saves native conversion state; after restore, feed the next source item through the same loop. `host.persist_and_publish` represents your application's storage and delivery.

For paging, retire only immutable items and preserve the next ordinal. The repository's private `a13n-ui/display` browser normalizer uses the checkpoint and subsequent AG-UI events. Display types and limits are in [API reference](api-reference.md#display-apis).

## Resume from Source History

Use `resume()` on a fresh observer when you retained exact Harness source history. The journal and Host methods below belong to your application:

```python
from a13n_stream_protocol import HarnessAguiStreamObserver

observer = HarnessAguiStreamObserver(retain_events=False)
await observer.resume(source_journal.read_prefix(run_id=run_id, through=cursor))

async for item in source_journal.tail(run_id=run_id, after=cursor):
    await host.persist_and_publish(observer.observe(item))
```

Supply the exact finite, ordered prefix for one root stream and its inline children. Continue after the same cursor, excluding duplicates and detecting gaps. `resume()` returns `None`; it does not republish history. With default `retain_events=True`, `snapshot()` also contains historical events.

### What the Host must guarantee

Keep the Harness/Protocol release and processor policy unchanged. Feed AG-UI records to the display fold, and Harness source records to `observer.resume()`.

To restore conversion without replay, save `observer.export()` and call `HarnessAguiStreamObserver.restore(saved, processor=process_event)`. Supply the processor again. The restored observer keeps no event journal.

### Failure and retry

Failed or cancelled `resume()` leaves the observer fresh; retry with complete history. After successful observation or resume, use `observe()` rather than another `resume()`. Serialize calls and wait for reconstruction before observing live items.

## Resume Is Not Agent Recovery

To continue model or tool execution, use [Harness State and Resume](../a13n-harness/state-and-resume.md). The new Harness Run needs a new observer; keep earlier Run projections separately.

## Errors and Atomicity

Invalid correlation or processor replacements raise `AguiObservationError`; invalid Python input types raise `TypeError`. A failed `observe()` leaves that source item uncommitted. Persist each successful batch through your Host's transaction and delivery contract.
