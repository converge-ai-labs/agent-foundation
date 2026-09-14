# Replay and recovery

There are two different restart problems: reconstructing a UI projection for the **same** Run, and starting a **new** Harness Run from a checkpoint. `HarnessAguiObserver.resume()` solves only the first.

## Choose the recovery path

| What changed?                                                   | What to do                                                                              |
| --------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Observation consumer restarted; same source Run still available | Replay the exact finite source prefix into a fresh observer, then consume its live tail |
| Agent worker restarted from `HarnessState`                      | Create a new observer for the new Run ID                                                |
| Only rendered messages or AG-UI records were retained           | Do not claim they can reconstruct Harness multipart source state                        |
| Source history has a gap                                        | Report/reconcile it in the Host; do not silently skip it                                |

## Resume from Source History

Use `resume()` when a fresh process needs to reconstruct the observer for an existing Harness Run. The `source_journal` methods below illustrate Host-owned history and live-tail interfaces; they are not provided by this package:

```python
observer = HarnessAguiObserver(processor=process_event)

await observer.resume(
    source_journal.read_prefix(
        run_id=run_id,
        through=handoff_cursor,
    )
)

async for item in source_journal.tail(
    run_id=run_id,
    after=handoff_cursor,
):
    new_events = observer.observe(item)
    await host.persist_and_publish(new_events)
```

The argument is a finite `AsyncIterable[HarnessStreamEvent[Any]]`. It must yield the exact ordered public source prefix for one Run and then finish at the Host-selected handoff point. After `resume()` returns, pass later live items to `observe()`.

`resume()`:

1. creates isolated staging state;
2. processes every historical item through the same conversion and processor path as `observe()`;
3. atomically adopts the reconstructed state only after the iterable finishes successfully;
4. returns `None`, so historical events are not accidentally published again.

After a successful resume:

```python
historical_projection = observer.snapshot()
next_events = observer.observe(next_live_item)
```

The snapshot contains the reconstructed historical AG-UI projection, while `next_events` contains only the new live output.

### What the Host must guarantee

The Host owns the source-history contract around `resume()`:

- retain or reconstruct typed public `HarnessStreamEvent` values;
- select a finite prefix for exactly one `run_id`;
- preserve source order and exclude duplicate delivery;
- detect retention gaps rather than silently omitting source items;
- establish a replay-to-live cutover with neither a gap nor overlap;
- decode or migrate retained source values for the selected Harness/Protocol release;
- retain durable AG-UI event IDs independently from Harness source sequence.

AG-UI delivery records, compacted display messages, and renderer snapshots cannot replace Harness source history. They are lossy projections and do not contain enough information to reconstruct multipart conversion state.

### Failure and retry

If history iteration, conversion, correlation validation, or processing fails—or if the task is cancelled—the original observer remains fresh. The Host can open another complete history iterable and retry:

```python
observer = HarnessAguiObserver(processor=process_event)

try:
    await observer.resume(primary_history)
except Exception:
    await observer.resume(reopened_complete_history)
```

Do not call `resume()` after a successful observation or a previous successful resume. While a resume is in progress, `observe()` and another `resume()` raise `AguiObservationError`. Properties and `snapshot()` continue to expose the pre-resume fresh state until reconstruction commits.

## Resume Is Not Agent Recovery

Observer reconstruction and Harness recovery are separate operations.

When only the observation consumer restarts and the same Run source history remains available, reconstruct one observer from that Run's prefix and continue its live tail.

When worker takeover starts a new Harness Run from `HarnessState`, the new Run has a new `run_id` and sequence domain. Create a new observer:

```python
previous_observer = HarnessAguiObserver(processor=process_event)
await previous_observer.resume(previous_run_history)

# Worker recovery starts another Harness Run.
current_observer = HarnessAguiObserver(processor=process_event)
async for item in current_run_stream:
    new_events = current_observer.observe(item)
    await host.persist_and_publish(new_events)
```

A Host can retain both Harness Run projections in one Session, Service Run, or Execution timeline, but it must not feed the earlier Harness Run into the new observer. `resume()` never reconstructs model execution, tools, credentials, Environment authority, leases, or `HarnessState`.

## Errors and Atomicity

`AguiObservationError` reports semantic conversion failures such as:

- Thread or Run correlation changing within one observer;
- a multipart part changing kind or identity;
- a processor changing a structural field or event type;
- calling `resume()` on a non-fresh observer;
- observing or starting another resume while resume is in progress.

Invalid Python input types raise `TypeError`. A failed `observe()` leaves that source item unaccumulated. A failed or cancelled `resume()` leaves the complete original observer fresh.

Serialize the state-changing `observe()` and `resume()` calls. Properties and `snapshot()` may be read while resume is in progress and expose the pre-resume fresh state. The explicit resume gate prevents asynchronous history reconstruction from being overwritten by live observation while it waits for the next historical item.
