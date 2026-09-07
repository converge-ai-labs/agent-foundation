# Agent Stream Protocol Observation

## Design Position

`a13n-stream-protocol` is the shared process-local adapter from public Harness stream items to Agent User Interaction Protocol events. One `HarnessAguiObserver` converts the items for one Harness Run, uses standard AG-UI events where their semantics match directly, falls back to `CUSTOM` for every other public observation, applies an optional Host processor, and accumulates the resulting events in observation order. A fresh observer can atomically reconstruct that process-local state by folding a finite Host-supplied history of the same public source items before live observation continues.

The package does not define another execution or lifecycle layer. It does not run or resume an Agent, manufacture missing Harness lifecycle observations, accept application commands, retain or select durable history, assign Host event identities, or own a transport. A Host consumes each live Harness item once, routes each Run to one observer, and decides whether and how to retain source history, persist, broadcast, filter, compact, or render the returned AG-UI events.

## Boundaries

| Concern                                        | Owner                          | Relationship                                                                                             |
| ---------------------------------------------- | ------------------------------ | -------------------------------------------------------------------------------------------------------- |
| Model, tool, and provider event semantics      | Pydantic AI                    | The observer maps its public events without redefining their lifecycle                                   |
| Process-local event correlation and lifecycle  | Harness                        | Supplies ordered `HarnessEvent` and terminal `HarnessRunResultEvent` values with Thread and Run identity |
| Harness-to-AG-UI conversion                    | Agent Stream Protocol          | Uses standard AG-UI events where they apply directly and `CUSTOM` otherwise                              |
| Application visibility and filtering           | Host processor                 | May retain, replace declared content fields on, or drop each converted event                             |
| Process-local reconstruction and accumulation  | Agent Stream Protocol observer | Folds Host-supplied source history and retains post-processor events for one Run in observation order    |
| History retention, selection, and live cutover | Host                           | Supplies an exact finite source prefix and selects where subsequent live observation begins              |
| Persistence, event IDs, replay, and fan-out    | Host                           | Stores or delivers returned events under its own Session or Execution contract                           |
| HTTP, SSE, WebSocket, or in-process delivery   | Host transport                 | Serializes and carries AG-UI events without becoming their execution authority                           |
| Display state                                  | Renderer                       | Interprets AG-UI events for one surface                                                                  |

The [Harness event contract](../agent-harness/12-events-observability-and-usage.md) owns the source stream. [Agent UI local storage and recovery](../agent-cli/03-local-storage-and-recovery.md) own local retention. [Foundation Service](../foundation-service/README.md) owns any hosted durable lifecycle and event history.

## Dependency Direction

```mermaid
flowchart LR
    Host[Agent UI or another Host] --> Protocol[a13n-stream-protocol]
    Protocol --> Harness[a13n-harness]
    Harness --> Pydantic[Pydantic AI]
    Host --> Store[Host persistence and fan-out]
    Host --> Surface[CLI or embedding adapter]
```

The Harness imports no AG-UI, UI, Session, HTTP, or terminal-rendering type. Agent Stream Protocol depends only on public Harness and Pydantic AI stream types plus the upstream AG-UI schema library. It imports no Agent UI session implementation, Foundation persistence model, or transport framework.

Harness and Agent Stream Protocol are one release group. A `release/harness-v<version>` release assigns both distributions the same version, and the published Protocol artifact requires that exact Harness version. This shared release defines the supported source event union; runtime protocol-profile negotiation is not part of the process-local observer.

## Observer Contract

The public contract is intentionally small:

```python
type AguiEventProcessor = Callable[
    [HarnessStreamEvent[Any], Event],
    Event | None,
]


class HarnessAguiObserver:
    def __init__(
        self,
        *,
        processor: AguiEventProcessor | None = None,
    ) -> None: ...

    @property
    def thread_id(self) -> str | None: ...

    @property
    def run_id(self) -> str | None: ...

    async def resume(
        self,
        history: AsyncIterable[HarnessStreamEvent[Any]],
    ) -> None: ...

    def observe(
        self,
        item: HarnessStreamEvent[Any],
    ) -> tuple[Event, ...]: ...

    def snapshot(self) -> tuple[Event, ...]: ...
```

The first successfully observed item binds the observer to the source `thread_id` and `run_id`. Later items must carry the same correlation. A root Run and each exposed child Run therefore use separate observers even when their source items were delivered through one parent Harness stream.

`resume()` is valid only on a fresh, unbound observer. Its `history` is a finite asynchronous iterable containing the exact ordered public source-item prefix selected by the Host for one Harness Run. The Host owns history retention and decoding, cursor and gap semantics, duplicate exclusion, the finite replay boundary, and the subsequent replay-to-live cutover; the observer imports no storage or transport type and does not acknowledge the history source.

Each historical item follows the same conversion, processor, validation, and accumulation path as `observe()`, but `resume()` returns no historical events for republication. After successful exhaustion, `snapshot()` contains the reconstructed post-processor sequence and later `observe()` calls continue from the reconstructed multipart state. A non-empty history binds the fresh observer to its source `thread_id` and `run_id`; resumption never rewrites that source correlation, crosses into another Harness Run, reconstructs a Harness execution, or treats AG-UI events and display snapshots as source history.

The complete resumption is atomic with respect to observer state. The observer stages reconstruction separately and adopts it only after the history iterable exhausts successfully. An iteration failure, invalid source item, changed correlation, conversion failure, processor failure, or cancellation leaves the original observer fresh. Calling `resume()` after any successful observation or resumption is an error.

One observer is a serial-call object. While `resume()` is in progress, `observe()` and another `resume()` fail with `AguiObservationError`; properties and `snapshot()` continue to expose the pre-resumption fresh state. Failure or cancellation clears the in-progress gate so the Host can retry with another complete history iterable.

A processor is replay-stable: its result derives only from the supplied source item, converted event, and stable Host configuration. It retains no mutable processing state and performs no persistence, publication, acknowledgement, or other externally observable side effect. Replaying the same ordered history with the same configuration therefore reconstructs the same retained event sequence.

`observe()` performs one complete operation:

1. stage the multipart conversion state for the source item;
2. convert the source item to one or more AG-UI events;
3. call the optional processor once for each converted event;
4. omit only events for which the processor returns `None`, then frame retained oversized custom events;
5. commit the staged conversion state and accumulate retained events;
6. return detached copies of the events added by that call.

Without a processor, every converted event is retained unchanged. A replacement must preserve the AG-UI event type and source-derived Thread, Run, message, tool, and lifecycle correlation. A processor cannot turn another observation into a lifecycle event or expand one event into several.

All events produced from one source item are converted and processed before the observer changes its state or accumulator. A conversion failure, invalid processor replacement, or processor exception leaves the current source item unaccumulated. `snapshot()` returns detached copies of the complete post-processor event sequence. The observer does not compact chunks, remove lifecycle boundaries, create cursors, or apply a retention limit.

A Host persists incrementally from the values returned by live `observe()` calls rather than injecting a storage callback into the observer. Historical events reconstructed by `resume()` are accumulated for state and snapshot continuity but are not returned for duplicate publication. This keeps event conversion and reconstruction independent from asynchronous databases, brokers, and transports.

## Standard Event Conversion

The observer uses standard AG-UI events for direct semantic matches:

| Public source observation                            | AG-UI representation                                                                    |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Assistant `TextPart` start, delta, and end           | `TEXT_MESSAGE_START`, `TEXT_MESSAGE_CONTENT`, and `TEXT_MESSAGE_END`                    |
| `ThinkingPart` start, delta, signature, and end      | Reasoning message events and `REASONING_ENCRYPTED_VALUE`                                |
| Completed `ToolCallPart` at its end observation      | `TOOL_CALL_START`, complete `TOOL_CALL_ARGS`, and `TOOL_CALL_END`                       |
| Successful function or output tool return            | `TOOL_CALL_RESULT`                                                                      |
| Completed terminal `HarnessRunResultEvent`           | `RUN_FINISHED` with success outcome and a JSON-safe result when available               |
| Failed or cancelled terminal `HarnessRunResultEvent` | `RUN_ERROR` with the Harness-owned public failure or cancellation code                  |
| Suspension, non-success tool return, or retry prompt | Namespaced `CUSTOM` preserving the authoritative Harness correlation and public payload |
| Other native Pydantic AI `CapabilityEvent`           | Namespaced `CUSTOM` preserving Capability, Tool-call, Harness correlation, and payload  |

Native `a13n.context.model_input` Capability events map each input content item to standard user-role `TEXT_MESSAGE_START` / `TEXT_MESSAGE_CONTENT` / `TEXT_MESSAGE_END` events. Every event carries the same `role` and `metadata`, so a bounded consumer does not need a retained start event to enforce visibility. IDs derive from the source Run, sequence, and item index. The shared `ContentMetadata` projection contains `display` (default true), optional `source_id`, and `media` (default false); arbitrary native application metadata is not forwarded. Retained-history adapters use this same metadata projection. `display: false` content remains in observation and native history, but consumers omit it from normal presentation. It is not a security boundary. Processors cannot change role, identity, or visibility metadata.

Input content uses text deltas of at most 8192 code points without truncating the source body. Other Capability events, including compaction summaries, handoff summaries, file edits, and shell status, remain custom events with their native names and payloads. The observer does not manufacture assistant messages or render diffs for those events. Clients interpret the shared native facts into their own panels.

The observer maintains only the state needed to translate multipart events consistently: the current Harness model-request index, open part identities, accumulated tool name, and whether text or reasoning content has already been emitted. Harness model-request lifecycle observations remain visible as `CUSTOM`; they also provide the request index used when a Pydantic part has no native identity.

Tool names and mapping-valued argument deltas can be incomplete during Pydantic streaming. Their start and delta observations therefore remain visible through `CUSTOM`, while the completed `PartEndEvent` produces one coherent standard tool-call lifecycle from the final public `ToolCallPart`. The observer never mixes AG-UI chunk convenience events with explicit start/end events or concatenates independently serialized mapping deltas.

A text or reasoning part delta or end without a preceding start is normalized into a valid standard lifecycle by emitting the missing start from the information present in that same public event. A conflicting part kind or identity is a conversion error rather than a reason to rewrite prior events.

## Complete Custom Fallback

An observation without a direct standard representation is never silently dropped. It becomes:

- `a13n.harness.tool.<name>` for a typed Tool extra extension;
- `a13n.harness.<kind>` for another `HarnessExtensionEvent`;
- the unchanged native `CapabilityEvent.kind` for a Capability event; or
- `a13n.pydantic_ai.<event_kind>` for another Pydantic AI event.

The Tool specialization makes semantic events such as `a13n.harness.tool.filesystem.changed` directly subscribable without changing the source representation. Its `CUSTOM.value.event` remains the complete `HarnessExtensionEvent`, including Tool call correlation and the namespaced Tool event name.

The `CUSTOM.value` is:

```python
{
    "thread_id": item.thread_id,
    "run_id": item.run_id,
    "sequence": item.sequence,
    "occurred_at": item.occurred_at.isoformat(),
    "event": public_source_representation,
}
```

A Harness extension uses `model_dump(mode="json", by_alias=True)`. A Pydantic AI-compatible event uses the concrete runtime value's Pydantic JSON-mode serializer with aliases enabled; the observer does not snapshot the installed `AgentStreamEvent` union or maintain an event-kind registry. A native `CapabilityEvent` uses its concrete `kind` as the custom name and retains `kind`, `capability_id`, optional Tool-call correlation, and its public payload inside `CUSTOM.value.event`. Unknown native Capability events use the native event-family serializer to recover the original flattened payload. User-defined kinds require no first-party converter or allowlist. Serialization warnings are conversion failures rather than permission to emit a partial representation. A value may satisfy the Harness process-local `AgentStreamEventProtocol` while lacking a Pydantic-compatible JSON serializer; such a value fails conversion atomically, and the observer does not invent a serializer for it. The shared Harness/Protocol release defines these source fields; the fallback does not introduce a manual event allowlist, custom schema registry, or independent version negotiation.

A source item with a direct standard mapping is not duplicated as a second custom event. The processor receives both the source item and each converted event, and a Host can separately retain source records when its product requires them.

### Large Custom Events

All oversized custom events use a generic lossless framing codec after whole-event processing and validation. A custom event whose JSON encoding is at most 48 KiB remains intact. Larger events become ordered `CUSTOM` frames named `a13n.stream.fragment`, each carrying `{id, index, count, data}`. The ID derives from the source Thread, Run, sequence, and retained converted-event index; zero-based indices and count describe the fragments of the original custom event JSON. Data chunks contain at most 4096 code points, keeping each encoded frame below the App's 64 KiB payload bound. Framing does not rename domain fields, render content, or imply another event lifecycle.

`fragment_custom_event` exposes the same codec to other producers. Clients reassemble frames before interpreting the original custom name and value. `CustomEventAssembler` supports interleaved identities and retains at most eight pending events with a combined 64 MiB text budget by default. Invalid order, inconsistent counts, missing fragments, invalid JSON, or exceeded budgets never produce a partial domain event; the assembler reports an observation gap. Consumers own subscription lifetime and discard incomplete assemblies on reset. Transport loss remains possible and does not invalidate completed tool effects. Processors see the original complete custom name and value before fragmentation, so visibility filtering is independent of event size. Fragments are not independently meaningful domain observations.

## Lifecycle Ownership

Agent Stream Protocol translates explicit lifecycle facts; it does not create them from local control flow. Constructing or resuming an observer, opening a subscriber, catching an exception, losing a transport, or committing Host state does not by itself emit a run lifecycle event.

Model-request lifecycle extensions emitted by the Harness use the generic custom fallback because they are not equivalent to AG-UI Run lifecycle. Completed, failed, and cancelled terminal results have direct standard AG-UI terminal mappings. A suspended result remains `a13n.harness.run_result` with its exact deferred calls and approvals because one aggregate AG-UI interrupt would invent continuation correlation. If a reusable Run-start observation is required, the Harness must first expose that fact publicly; the Protocol does not infer `RUN_STARTED` from the first token or model request.

A Harness stream that ends through an unhandled exception or cleanup failure without a terminal item does not gain a synthetic terminal event. Host acceptance, persistence commit, cancellation request, reconnect, and external delivery remain separate Host facts.

## Host Processing and Persistence

The optional processor is the policy seam. It can drop observations or replace only declared standard-event content fields while preserving type, timestamp, role, lifecycle variant, custom name, identities, and source correlation. A `CUSTOM.value` is the public source representation and is therefore retained unchanged or dropped as a whole. Host-specific metadata belongs in the Host-owned retained or delivery envelope rather than a rewritten protocol structure. Agent Stream Protocol has no built-in allowlist that suppresses otherwise public Harness events and adds no second visibility policy over the public source stream.

Host persistence wraps AG-UI events in any IDs, sequence numbers, timestamps, transaction records, or replay cursors required by that Host. Those values are not part of the observer because their identity and durability depend on the owning Session, Execution, and store. When a Host supports observer reconstruction, it additionally retains or reconstructs the exact typed Harness source prefix accepted by the selected Harness/Protocol release and supplies that prefix through `resume()`. AG-UI delivery records, compacted display messages, and renderer snapshots are not substitutes for source history because conversion loses multipart source state.

The observer's in-memory accumulation and history reconstruction are conveniences for process-local continuation, inspection, and snapshot access, not a durable event log or replay authority. A Host that starts a new Harness Run after worker takeover creates a new observer for that new `run_id`; it may retain earlier Run projections in the same Host timeline without feeding them into the new observer.

A Host can derive a compact child display with one observer per child Run. Its replay-stable processor may drop encrypted reasoning and unrelated custom events, redact or truncate declared Tool content fields, and clear `RUN_FINISHED.result` when closed text already represents the final answer. The Host then compacts only closed message, reasoning, and completed Tool lifecycles. Open multipart state and running Tool calls remain observer state and are not publishable as a closed checkpoint. These are Host retention choices; Agent Stream Protocol owns neither the compact display schema nor its persistence or checkpoint acknowledgement.

## Failure and Compatibility

An invalid source type, source event without a Pydantic-compatible JSON representation, changed Run correlation, conflicting multipart identity, failed AG-UI construction, invalid processor replacement, or processor exception is reported to the caller. A failed `resume()` additionally leaves the observer fresh so the Host can retry with another complete history iterable. Completed output is normalized to JSON before accumulation; when a valid code-first output has no JSON representation, `RUN_FINISHED.result` is omitted and `rawEvent.result_omitted` records that presentation fact while the source Harness result remains available to the Host. The observer does not convert its own failure into a synthetic Harness or AG-UI lifecycle fact.

Standard AG-UI names and fields retain their upstream meaning. The selected Harness/Protocol release and its pinned AG-UI dependency define conversion and source-history compatibility. A Host pins that release with its renderer and owns migration or retention compatibility for source or projected events it stores. New public Harness event variants remain observable through `CUSTOM` even before a dedicated standard mapping is added.

## Invariants

1. Agent Stream Protocol observes public Harness stream items; observer resumption reconstructs observation state and never executes or resumes an Agent.
2. One observer binds to exactly one Harness Thread and Run, including every source item supplied during resumption.
3. A fresh observer atomically adopts a successfully exhausted finite source history or remains fresh after resumption failure.
4. Lifecycle facts originate in the Harness source stream; the observer does not infer them from Host or transport behavior.
5. A successfully converted direct semantic match uses standard AG-UI meaning, and every other successfully converted public observation falls back to `CUSTOM`.
6. No converted event is dropped by default; only the replay-stable Host processor can explicitly omit one.
7. One source item is processed and accumulated atomically, and returned events and snapshots are detached from observer state.
8. The observer owns no durable identity, history retention or selection, cursor, gap policy, durable replay, fan-out, backpressure, cancellation, or transport behavior.
9. AG-UI observation and reconstruction never become Harness continuation state or strengthen a Host lifecycle fact.
