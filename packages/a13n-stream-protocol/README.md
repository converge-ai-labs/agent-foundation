# Agent Stream Protocol

Stream Protocol (`a13n-stream-protocol`) converts public Harness streams to typed AG-UI 1.0 events and normalizes them into compact display items. It maps text, reasoning, tools, and terminal observations to standard events and preserves other public observations as `CUSTOM` events.

The repository directory is `packages/a13n-stream-protocol`, the Python distribution is `a13n-stream-protocol`, and the import package is `a13n_stream_protocol`.

## Usage

```python
from a13n_stream_protocol import HarnessAguiStreamObserver

observer = HarnessAguiStreamObserver(retain_events=False)
async with executable.stream(input_value, bindings=bindings) as stream:
    async for item in stream:
        new_events = observer.observe(item)
        await host.persist_and_publish(new_events)
```

One stream observer binds to the root Thread and Run on its first successful source item. Forwarded inline children have independent state and carry `subagentRunId`, preserving native message and tool-call IDs. Independently executed asynchronous children use separate stream observers. `HarnessAguiObserver` remains available for sources containing exactly one Run.

A Host that retains the exact public Harness source history can atomically rebuild a fresh observer before continuing with live items:

```python
observer = HarnessAguiStreamObserver()
await observer.resume(host.source_history(run_id=run_id, through=cursor))

async for item in host.live_source(run_id=run_id, after=cursor):
    new_events = observer.observe(item)
    await host.persist_and_publish(new_events)
```

The history is a finite async iterable for one root stream, including its inline child observations. `resume()` accumulates its post-processor AG-UI events without returning them for duplicate publication, leaves the observer fresh if reconstruction fails, and knows nothing about storage, cursors, gaps, or replay-to-live cutover. Those remain Host responsibilities.

A Host can filter or adjust converted values before accumulation:

```python
from ag_ui.core import Event
from ag_ui.core.events import CustomEvent
from a13n_harness import HarnessStreamEvent
from a13n_stream_protocol import HarnessAguiObserver


def process_event(
    source: HarnessStreamEvent[object],
    event: Event,
) -> Event | None:
    del source
    if isinstance(event, CustomEvent) and event.name == "a13n.harness.diagnostic":
        return None
    return event


observer = HarnessAguiObserver(processor=process_event)
```

A replacement must retain the same AG-UI event type and source-derived correlation. The processor is synchronous, replay-stable, and does not retain mutable processing state or persist, publish, or acknowledge events. The Host acts on the complete batch returned by live `observe()` calls.

## Ownership

The package owns only:

- standard Harness-to-AG-UI conversion;
- generic `CUSTOM` fallback for unmapped public events;
- multipart text, reasoning, and tool-call observation state;
- optional replay-stable Host processing;
- atomic process-local reconstruction from supplied source history;
- detached incremental results and optional accumulated event snapshots;
- shared display normalization, stable item identities, and compact checkpoint continuation.

The Host owns persistence, paging, suffix selection, delivery, and rendering policy. Harness owns execution lifecycle and continuation state.

## Compact display checkpoints

Use `DisplayFold` from `a13n_stream_protocol.display` to retain accumulated display content rather than every token event:

```python
from a13n_stream_protocol.display import DisplayFold, DisplaySnapshot

fold = DisplayFold(run_id)
async for source in run_stream:
    await host.persist_and_publish(fold.fold(fold.events(source), source))

checkpoint_json = fold.export().model_dump_json()
restored = DisplayFold.restore(DisplaySnapshot.model_validate_json(checkpoint_json))
```

A checkpoint retains stable items, active content, semantic position, fragment assemblies, and native conversion cursors. Export does not complete open blocks. Restoring the checkpoint and consuming an intact suffix produces the same display as uninterrupted folding. It does not restart model generation or tool execution. The [replay guide](../../docs/a13n-stream-protocol/replay.md) includes a complete offline round trip.

Observers default to `retain_events=True` for inspection. Set it to `False` for live-only conversion; `snapshot()` then raises rather than returning an incomplete history. `DisplayFold` uses this non-retaining mode. Explicit observer `export()` / `restore()` resumes conversion without an old delivery journal and requires the same processor policy.

## Dependencies

The source manifest declares an unversioned dependency on `a13n-harness`, so uv resolves Harness from the workspace during repository development. Conversion uses the upstream `ag-ui-protocol` models, Pydantic serialization, and the lightweight `pydantic-ai-slim` event runtime. The package does not depend on Harness UI, a Host persistence model, or a transport framework.

Release automation replaces the workspace-oriented Harness dependency in publishable metadata with an exact same-version requirement. Both the sdist and wheel therefore install only the Harness version released with that Stream Protocol artifact.

## Versioning

Agent Stream Protocol and `a13n-harness` form the Harness release group. A `release/a13n-harness-v<version>` tag publishes both distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`. This package pins the exact Harness version. Harness UI is versioned and released independently.

The accepted architecture and observation contract are defined in the [Agent Stream Protocol specification](../../spec/a13n-stream-protocol/README.md).
