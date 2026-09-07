# Agent Stream Protocol

`a13n-stream-protocol` converts public `a13n-harness` stream items into typed [AG-UI](https://docs.ag-ui.com/) events. Use it when a Host needs one consistent projection for a browser, terminal, event store, or another AG-UI consumer without interpreting private Harness state.

The package provides one main class, `HarnessAguiObserver`. One observer:

- binds to one Harness Thread and Run;
- converts text, reasoning, tool, and terminal observations;
- preserves every other public Harness observation as a namespaced `CUSTOM` event;
- optionally applies a replay-stable Host processor;
- returns incremental events and retains a detached process-local snapshot;
- can reconstruct its state from an exact Host-supplied Harness source history.

It does not run or resume an Agent, persist events, assign durable event IDs, reconnect Redis, manage replay cursors, or serve SSE or WebSocket traffic.

## Install

```bash
pip install a13n-stream-protocol
```

A published Stream Protocol release pins the matching `a13n-harness` release. In a repository checkout, both packages resolve from the shared workspace.

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

The framing preserves the complete JSON structure rather than truncating the source. The assembler bounds pending content and rejects incomplete or inconsistent sequences; check `assembler.gap` and replace the assembler when resetting a subscription. These are best-effort observations, not a durable event log. See the [framing contract](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/agent-stream-protocol/00-overview.md#large-custom-events) for limits and fields.

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

A Host can retain both Harness Run projections in one Session, Foundation Run, or Execution timeline, but it must not feed the earlier Harness Run into the new observer. `resume()` never reconstructs model execution, tools, credentials, Environment authority, leases, or `HarnessState`.

## Errors and Atomicity

`AguiObservationError` reports semantic conversion failures such as:

- Thread or Run correlation changing within one observer;
- a multipart part changing kind or identity;
- a processor changing a structural field or event type;
- calling `resume()` on a non-fresh observer;
- observing or starting another resume while resume is in progress.

Invalid Python input types raise `TypeError`. A failed `observe()` leaves that source item unaccumulated. A failed or cancelled `resume()` leaves the complete original observer fresh.

Serialize the state-changing `observe()` and `resume()` calls. Properties and `snapshot()` may be read while resume is in progress and expose the pre-resume fresh state. The explicit resume gate prevents asynchronous history reconstruction from being overwritten by live observation while it waits for the next historical item.

## Ownership Summary

| Concern                                                           | Owner                 |
| ----------------------------------------------------------------- | --------------------- |
| Harness execution, source lifecycle, result, and `HarnessState`   | Harness               |
| Harness-to-AG-UI conversion and process-local reconstruction      | Agent Stream Protocol |
| Visibility policy expressed by a replay-stable processor          | Host processor        |
| Source-history retention, cursor, gap detection, and live cutover | Host                  |
| Durable AG-UI IDs, persistence, replay, and fan-out               | Host                  |
| SSE, WebSocket, Redis, or in-process delivery                     | Host transport        |
| Rendered view state                                               | Renderer              |

## Next Steps

- Read the [Agent Harness guide](../agent-harness/index.md) for building, streaming, and resuming Agents.
- Read the [package README](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/agent-stream-protocol) for package and release details.
- Consult the [Agent Stream Protocol specification](https://github.com/converge-ai-labs/agent-foundation/tree/main/spec/agent-stream-protocol) for the normative observation and compatibility contract.
