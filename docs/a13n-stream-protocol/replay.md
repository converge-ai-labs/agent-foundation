---
title: Replay and recovery
description: Save an unfinished display, restore it, and continue from the next event or source item.
---

A consumer restart should continue an unfinished message instead of displaying it twice. Save the display content and its parsing state at the same position, then supply the intact suffix. The examples below separate that task from rebuilding event conversion and continuing Agent execution.

## Choose the recovery path

Choose from the data your Host retained:

| Saved data                         | API                                                              | Result                                              |
| ---------------------------------- | ---------------------------------------------------------------- | --------------------------------------------------- |
| Display items and continuation     | `DisplayFold.restore(snapshot)`                                  | Restore messages, tool calls, and parsing state     |
| Observer continuation              | `HarnessAguiStreamObserver.restore(continuation, processor=...)` | Continue conversion for the same root stream        |
| Exact finite Harness source prefix | `await observer.resume(history)`                                 | Rebuild conversion by replaying source observations |
| `HarnessState`                     | Start a new Harness Run                                          | Continue Agent execution                            |

Store the checkpoint with the Host position used to select its suffix. Resume immediately after that position, with no gap or overlap. Keep the Harness/Protocol release and processor policy consistent across reconstruction.

## Restore a compact display checkpoint

Run this complete example in the [quickstart environment](getting-started.md). It cuts a message after `Hello`, serializes the checkpoint, and restores it before receiving ` world`:

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

The assertions verify the whole restored snapshot, final text, and completed state. `export()` returns a detached copy and leaves active messages and tool calls open. In your Host, persist `checkpoint_json` and the corresponding delivery position together. Supply the same `processor=` when constructing and restoring a fold that uses a processor.

For a native Harness stream, use the fold's built-in converter. The following integration fragment assumes `run_id`, `run_stream`, and your Host's storage/delivery method:

```python
fold = DisplayFold(run_id)
async for source in run_stream:
    observed = fold.fold(fold.events(source), source)
    await host.persist_and_publish(observed)
```

Passing `source` lets the fold mark failed tool results as well as normalize the converted events. Export after processing a source item. The checkpoint includes native conversion state, so a restored fold can accept the next source item through the same loop.

When full exports become expensive, use `capture_delta()`:

1. Keep the items from earlier captures.
2. Merge the captured changes by item ID; a correction keeps the existing ID.
3. Save the merged items with the new capture's continuation and matching Host position.

The delta contains only changed items, so it needs the earlier prefix to form a complete checkpoint. Checkpoint capture and live delivery tracking are independent. For paging, retire immutable items only and preserve the next ordinal. Display types and content limits are in the [API reference](api-reference.md#display-apis).

## Resume from Source History

Use source replay when your Host retained Harness observations rather than a converter checkpoint. Create a fresh observer, rebuild it from a finite prefix, then follow the next source item. `source_journal`, `cursor`, and `host` here are application-owned:

```python
from a13n_stream_protocol import HarnessAguiStreamObserver

observer = HarnessAguiStreamObserver(retain_events=False)
await observer.resume(source_journal.read_prefix(run_id=run_id, through=cursor))

async for item in source_journal.tail(run_id=run_id, after=cursor):
    await host.persist_and_publish(observer.observe(item))
```

`resume()` consumes an asynchronous iterable and returns `None`. It rebuilds state without publishing the historical events. With `retain_events=True`, those events also appear in `snapshot()`; the example avoids that extra journal.

### What the Host must guarantee

Provide the exact ordered prefix for one root stream and its inline children. Decode it into public Harness source items. Feed serialized AG-UI records to `DisplayFold`, not to `observer.resume()`.

Select the prefix boundary and live suffix using the same Host cursor. Exclude duplicate delivery and detect gaps before conversion. Wait for replay to finish before feeding live observations; one observer accepts serial calls.

If you saved `observer.export()`, restore it directly with `HarnessAguiStreamObserver.restore(saved, processor=process_event)`. Restore creates a non-retaining observer. Supply the processor again because executable callbacks are not part of the saved continuation.

### Failure and retry

Failed or cancelled `resume()` leaves the observer fresh. Repair the history source and retry with the complete prefix. After successful observation or replay, continue with `observe()`; another `resume()` on that observer is rejected.

## Resume Is Not Agent Recovery

Use [Harness State and Resume](../a13n-harness/state-and-resume.md) to continue model or tool execution. That creates a new Run with a new observer; retain the old Run's display separately. The same distinction applies to a Host-triggered compaction Run: its execution checkpoint and the display of its summary are separate saved values.

## Errors and Atomicity

`AguiObservationError` identifies invalid correlation, replay use, or processor replacement. `TypeError` identifies an invalid Python input type. A failed `observe()` leaves that source item uncommitted in the observer, allowing the Host to correct the cause before retrying conversion.

After successful conversion, persistence and delivery follow your Host's transaction contract. A failure there does not undo the observer's successful conversion. Use the retained batch or a selected checkpoint and exact suffix to recover delivery, rather than blindly feeding the same source item again.
