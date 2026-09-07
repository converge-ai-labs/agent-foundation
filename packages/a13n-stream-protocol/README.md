# Agent Stream Protocol

`a13n-stream-protocol` observes public `a13n-harness` streams as typed AG-UI events. It maps text, reasoning, tool, and terminal observations to standard AG-UI events, exposes every other public observation through a namespaced `CUSTOM` fallback, applies an optional Host processor, and accumulates the resulting events for process-local use.

The repository directory is `packages/a13n-stream-protocol`, the Python distribution is `a13n-stream-protocol`, and the import package is `a13n_stream_protocol`.

## Usage

```python
from a13n_stream_protocol import HarnessAguiObserver

observers: dict[tuple[str, str], HarnessAguiObserver] = {}

async with executable.stream(input, bindings=bindings) as stream:
    async for item in stream:
        correlation = (item.thread_id, item.run_id)
        observer = observers.get(correlation)
        if observer is None:
            observer = HarnessAguiObserver()
            observers[correlation] = observer

        new_events = observer.observe(item)
        await host.persist_and_publish(new_events)
```

One observer binds to the Thread and Run correlation on its first successful source item. Use a separate observer for each root or child Run, including child events forwarded through a parent stream. An integration whose source contains exactly one Run can use one observer directly.

A Host that retains the exact public Harness source history can atomically rebuild a fresh observer before continuing with live items:

```python
observer = HarnessAguiObserver()
await observer.resume(host.source_history(run_id=run_id, through=cursor))

async for item in host.live_source(run_id=run_id, after=cursor):
    new_events = observer.observe(item)
    await host.persist_and_publish(new_events)
```

The history is a finite async iterable for one Run. `resume()` accumulates its post-processor AG-UI events without returning them for duplicate publication, leaves the observer fresh if reconstruction fails, and knows nothing about storage, cursors, gaps, or replay-to-live cutover. Those remain Host responsibilities.

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
- detached incremental results and accumulated snapshots.

The Host owns source-history retention and selection, cursors, gaps, replay-to-live cutover, persistence, event identities, fan-out, backpressure, cancellation, transport, and rendering policy. The Harness owns source lifecycle facts and continuation state.

## Dependencies

The source manifest declares an unversioned dependency on `a13n-harness`, so uv resolves Harness from the workspace during repository development. Conversion uses the upstream `ag-ui-protocol` models, Pydantic serialization, and the lightweight `pydantic-ai-slim` event runtime. The package does not depend on Harness UI, a Host persistence model, or a transport framework.

Release automation replaces the workspace-oriented Harness dependency in publishable metadata with an exact same-version requirement. Both the sdist and wheel therefore install only the Harness version released with that Stream Protocol artifact.

## Versioning

Agent Stream Protocol, `a13n-harness`, and `a13n-environment` form the Harness release group. A `release/a13n-harness-v<version>` tag publishes all three distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`. Published Harness metadata pins the exact Provider version, and this package pins the exact Harness version. Harness UI is versioned and released independently.

The accepted architecture and observation contract are defined in the [Agent Stream Protocol specification](../../spec/a13n-stream-protocol/README.md).
