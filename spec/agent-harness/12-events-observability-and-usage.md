# Events and Usage

## Design Position

`HarnessEvent` and `HarnessRunResultEvent` are stable process-local output seams. `AbstractCapability[AgentContext]` adapters produce Harness-owned observations, ordered Harness plugin middleware can transform or suppress non-terminal events and replace the complete result candidate, and one single-consumer `HarnessRunStream` preserves ordering and produces a terminal result event only after final validation and complete run-scoped teardown succeed.

Pydantic AI public events remain the source for model output and tool execution, and `RunCancelled` is the source terminal signal for native cancellation. The Harness adds only bounded model-request boundary observations plus correlation, context, state, recovery, managed-invocation, delegation, usage-attribution, and diagnostic events that Pydantic AI does not own. Pydantic AI `RequestUsage`, `RunUsage`, and `UsageLimits` remain authoritative for model-request usage, accumulation, and supported limits. When semantic recovery starts another `ModelAttempt`, events already delivered by the earlier attempt remain observations in the same logical Harness stream and cannot be retracted.

Durable event delivery, cross-run usage aggregation, valuation, billing, and lifecycle facts belong to the Host. [Harness Observation](19-observation-model.md) separately owns the OpenTelemetry hierarchy, fields, information boundary, and Host export profiles; telemetry never replaces this event or usage contract.

Event behavior inside model, node, or tool execution uses Pydantic Capability hooks with `RunContext[AgentContext]`. A first-class Harness plugin can observe the outer canonical stream and result through `wrap_run`, but it does not install a background event broker, second public stream, usage accumulator, durable log, or broadcast system.

## Boundary

The Harness does not define Host lifecycle events, a broker, SSE, webhook, durable replay, cross-process delivery guarantees, a telemetry backend, a universal resource taxonomy, a durable usage sink, a price catalog, invoices, or payment. OpenTelemetry ownership and exporter failure are defined by [Harness Observation](19-observation-model.md#sampling-export-and-lifecycle-failure).

## Event Model

```python
@runtime_checkable
class AgentStreamEventProtocol(Protocol):
    @property
    def event_kind(self) -> str: ...


class HarnessEvent(BaseModel):
    thread_id: str
    run_id: str
    sequence: int
    occurred_at: datetime
    event: (
        AgentStreamEvent
        | AgentStreamEventProtocol
        | HarnessExtensionEvent
    )
```

`AgentStreamEvent` remains the precise authoring type for the installed Pydantic AI release. `AgentStreamEventProtocol` is the minimal open runtime seam for native events added by later compatible releases and trusted plugin transformations: an event exposes one non-blank string `event_kind`, while its concrete type and payload remain owned by its producer. The Harness does not snapshot or reconstruct Pydantic AI's event union at plugin response boundaries. Process-local acceptance does not assert that an arbitrary payload is serializable by every Host transport. Harness-owned `HarnessExtensionEvent` values remain subject to their complete schema, redaction, finite-JSON, and payload-size validation after plugin unwind.

`thread_id` identifies the independently advancing Thread; `run_id` identifies the process-local Harness Run. Both are present on ordinary and terminal events, including failures that have no returned State. `sequence` is strictly increasing within one Run. The root Run's sequence includes the final `HarnessRunResultEvent`. Resume preserves `thread_id` while starting another `run_id` and sequence domain. Forwarded inline-child events retain the child's run ID and sequence; the Delegation Capability consumes the child's result event internally. Concurrent runs have causal correlation through their `AgentInstanceContext`; timestamps do not establish a total order.

```python
class HarnessExtensionEvent(BaseModel):
    schema_version: str
    kind: Literal[
        "context",
        "state",
        "recovery",
        "invocation",
        "delegation",
        "usage",
        "lifecycle",
        "tool",
        "diagnostic",
    ]
    payload: JsonValue
```

Extension payloads are small discriminated schemas owned by their subsystem. First-party producers construct a frozen typed Pydantic payload and pass only `model_dump(mode="json")` output to the open envelope; they do not assemble payload dictionaries ad hoc. The event envelope does not duplicate definition, lineage, policy, or host lifecycle fields already available from run context. [`Public API and Packaging`](14-public-api-and-packaging.md) owns `HarnessRunResultEvent` and the `HarnessStreamEvent` union.

## Adaptation

```mermaid
sequenceDiagram
    participant PAI as Pydantic AI
    participant Capability
    participant Emitter as HarnessEventEmitter
    participant Plugins as Harness plugins
    participant Stream as HarnessRunStream
    participant Host

    PAI-->>Stream: public AgentStreamEvent
    Capability->>Emitter: HarnessExtensionEvent
    Emitter-->>Stream: run-local extension
    Capability->>Emitter: bind exact inline-child stream
    Capability->>Emitter: forward through private bound child seam
    Emitter-->>Stream: validated child observation
    Stream->>Stream: sequence root events, validate child envelopes, and redact
    Stream->>Plugins: ordered event and result-candidate unwind
    Plugins-->>Stream: transformed observations and candidate
    Stream-->>Host: HarnessEvent values with backpressure
    Stream->>Stream: validate candidate and finish teardown
    Stream-->>Host: final HarnessRunResultEvent with usage snapshots
```

Model output and tool events preserve their public Pydantic AI types. This includes native `ThinkingPart`, `TextPart`, function-tool call and result events, `DeferredToolRequestsEvent`, and `DeferredToolResultsEvent`, plus compatible typed event families that Pydantic AI adds to its public stream. For these open Agent events, plugin unwind performs only the shallow `AgentStreamEventProtocol` check; it does not apply a second Pydantic schema validation to events already produced or transformed inside the trusted process. Harness extension events retain their separate Harness-owned validation. The Harness observes the public `ModelRequestNode` boundary but does not recreate provider transport, response-delta, thinking, generation, tool, client-call, or run-terminal lifecycle state machines and does not add a second `DEFERRED_TOOLS` control event. A transport can project a convenience client-tools payload, but only the terminal `HarnessRunResult.deferred` and a Host's accepted durable pending record have continuation meaning. High-frequency Pydantic deltas may be coalesced by a consumer without changing complete messages or `HarnessState`.

### First-party Event Contracts

One code-owned mandatory lifecycle observer emits `lifecycle` payloads at the public Pydantic `ModelRequestNode` boundary. `request_index` is the zero-based node ordinal within the logical Harness run, including internal recovery attempts, and `request_id` is `model-request-{request_index + 1}`. `model_request_started` is emitted before the node handler and includes `message_count`. Successful handler return emits `model_request_completed`; handler failure emits `model_request_failed` with a bounded stable `error_code`. The observation means that Harness entered or left the node boundary; it does not claim that a provider accepted, transmitted, or generated bytes. Raw exceptions and provider error bodies are excluded.

The Compaction Capability emits one `context_snapshot` before an eligible ordinary model request only when a latest provider-reported usage value exists. It contains `request_index`, `request_tokens`, and the configured `trigger_tokens`; `request_tokens` is the latest response's input-plus-output token count. Exact deferred/provider-suspended boundaries and histories without provider usage emit no snapshot. One eligible request emits at most one snapshot. The value is provider accounting for that completed request, not an estimate of the next outgoing request or a guaranteed context-window measurement.

Handoff and compaction have disjoint event lifecycles:

| Operation  | Event sequence                                                                      |
| ---------- | ----------------------------------------------------------------------------------- |
| Handoff    | `handoff_started`, `handoff_prepared`, then `handoff_completed` or `handoff_failed` |
| Compaction | `compaction_started`, then `compaction_completed` or `compaction_failed`            |

Each payload contains one concise kind-prefixed `operation_id`. A normal `summarize` call persists its handoff ID and prepared summary across model boundaries and continuation runs. Compaction creates a run-local operation ID after its provider-usage threshold succeeds; it neither uses Handoff state nor emits `compaction_prepared`.

`handoff_prepared` is emitted only after the validated summary is durably written to Handoff Capability state and includes only `summary_size` and `files_count`, never summary or file content. `handoff_completed` follows successful restored-history construction and durable pending-state clearing. `compaction_completed` follows successful plain-text nested execution and construction of the replacement history. `*_failed` contains only `failed_phase`, stable `error_code`, and `retryable`; raw exceptions are excluded. Handoff retains or clears pending state according to its retryability contract. Compaction has no pending durable operation: ordinary failure is non-retryable for that boundary, emits `compaction_failed`, and leaves the original history active. Cancellation propagates without being reclassified as failure. Compaction emits only `compaction_*`, never duplicate `handoff_*` observations.

Working State emits bounded revisioned committed deltas rather than snapshots. A `state` payload with `type="task_changed"` contains one `operation_id`, authoritative `state_revision`, reason `created`, `updated`, `claimed`, `completed`, `dependency_updated`, or `provider_observed`, and one task projection containing only `id`, `revision`, `subject`, `active_form`, `status`, `owner`, `blocks`, and `blocked_by`. Description, metadata, notes, and the full task map are excluded. Emission occurs only after authoritative persistence and the run's in-memory view both commit. One mutation that changes reciprocal dependency tasks emits one delta per changed task with the same operation ID and state revision. Failed and semantic no-op mutations emit nothing. A provider without a watch API guarantees observations only at Harness mutation and read boundaries.

Inline delegation emits typed `inline_delegation` payloads with action `started`, `completed`, or `failed`. Every action carries the same invocation ID, compact child instance ID, selected subagent name, bounded status, parent run and Agent-instance correlation, optional originating parent tool-call ID, and the child run ID whenever a child stream was created. `started` is emitted on the child run before child model work so it is forwarded in real time; terminal actions are emitted on the parent run after the complete child result or dispatch failure is classified. Uniform payload correlation, rather than envelope order or subagent-name matching, joins those observations. A pre-stream rejection has no child run ID and never fabricates one.

Each `usage_report` is likewise a typed payload containing its stable report ID, reporting reason, optional trigger record ID, valid chunk position and count, and one non-empty bounded record batch. Record schemas remain owned by the usage subsystem. First-party delegation and usage producers do not assemble free-form payload dictionaries.

A `tool` extension carries one typed `tool_extra` payload with the native `tool_call_id`, declared `tool_name`, stable Harness `tool_id`, namespaced event `name`, and bounded JSON `value`. `emit_tool_event()` derives native call correlation from `RunContext`, accepts a typed Pydantic value, and emits through the run-local canonical event path. Tool extra events describe semantic observations owned by the Toolset; they do not duplicate native call start, arguments, result, authorization, retry, or terminal lifecycle.

The first Tool extra contract is `filesystem.changed`. Its value contains one to 256 confirmed logical-path changes with action `created`, `modified`, `written`, `deleted`, `moved`, or `copied`; move and copy entries include exactly one logical destination. `written` reports a successful provider-neutral upsert or append when the FileOperator result does not distinguish creation from replacement; the Harness does not add a read-before-write race merely to guess a narrower action. A mutating file Tool emits at most one event after successful provider confirmation. Batch events include only successful items, and failed items, rejected calls, semantic no-ops, and forced deletion of an already absent path produce no change entry. Values exclude file contents, replacement strings, diffs, provider-native paths, receipts, and revisions.

Event delivery failure never rolls back committed Capability state. These process-local observations retain the backpressure, middleware, redaction, payload-size, and non-durability rules of the enclosing event stream.

The run-local Environment adapter reads an independent cursor from the bounded non-draining topology journal and emits exactly one bounded Harness `context` extension for every committed change, in publication order. It starts from `EnvironmentTopologyObserver.initial_topology_version`, so changes committed during `RunInputFactory` remain observable after `AgentContext` and Capabilities bind. Adapter cancellation or emitter/plugin failure can suppress later process-local delivery but cannot consume another observer cursor or roll back topology. Optional model-facing notification is a separate consumer delivered through native Pydantic enqueue or the next eligible public model-request hook; it can coalesce notices without coalescing Harness events. An enqueued notice remains observable through the ordinary `EnqueuedMessagesEvent`, while topology commit never depends on event or notice delivery.

`HarnessEventCapability` adapts Pydantic events and emits Harness extensions through the run-local emitter. Extensions cover:

| Kind         | Meaning                                                                                                                                     |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `context`    | Context contribution, omission, compaction, or successfully applied Environment-topology observation                                        |
| `state`      | State import or export observation, not durable snapshot status                                                                             |
| `recovery`   | Bounded inner-attempt interruption, backoff, restart, exhaustion, or normalized cancellation observation; never a durable Host retry fact   |
| `invocation` | Managed-tool preparation, authorization, approval, dispatch, retry, result-safety, or unknown-outcome observation; never a grant or receipt |
| `delegation` | Inline child or Host-managed asynchronous submission observation                                                                            |
| `usage`      | One bounded mixed-source `usage_report` emitted at a model-request or terminal reporting boundary; not durable billing proof                |
| `lifecycle`  | Bounded `ModelRequestNode` entry, completion, or safe failure observation; never provider transport or Host lifecycle authority             |
| `tool`       | Toolset-owned semantic extra observation correlated to one native Tool call; never a duplicate call or result lifecycle                     |
| `diagnostic` | Safe implementation/provider detail without lifecycle authority                                                                             |

## Run Stream and Content

```python
class HarnessEventEmitter(Protocol):
    async def emit(
        self,
        event: HarnessExtensionEvent,
    ) -> None: ...
```

The Harness creates one emitter for each process-local run and places only its `emit()` surface on `AgentContext` for Capability authors. `emit()` creates a run-local extension event and assigns its envelope fields. Inline delegation uses a private Harness-owned child-forwarding seam bound to one exact parent and child stream; arbitrary Capability or plugin code cannot submit a foreign child envelope through the public emitter. The seam preserves child Thread, Run, and source sequence, validates nested descendant registration and monotonic sequence, and seals that provenance through plugin processing. Plugins may transform or suppress the child event payload but cannot change its child correlation or source sequence. The emitter feeds the same ordered internal path as adapted Pydantic events. Harness plugin middleware sees those values before public delivery; already delivered values cannot be retracted. The emitter is not supplied by the Host and is not a general delivery service.

`HarnessRunStream` is class-based, lazily starts on first iteration, has exactly one consumer, and applies natural backpressure. It provides no replay or fan-out. An embedded application consumes it directly. A hosted worker consumes it once and projects events to any broker, SSE connection, WebSocket, log, or durable store selected by the host. Foundation Service's replayable lifecycle log is a separate Host contract: it atomically records bounded committed lifecycle facts and can selectively reference Harness observations without making every process-local delta durable.

Leaving the stream context before its terminal item cancels and drains the process-local run. A host that wants execution speed to be independent of a downstream client consumes the Harness stream into its own bounded delivery mechanism rather than asking the Harness to buffer unbounded events.

The harness redacts extension payloads before emission. Credentials, grants, transient prompt overlays, quarantined content, and provider-native secret fields are absent. Prompt, argument, and result bodies are excluded by default and included only under explicit content policy.

A state event or terminal `HarnessRunResultEvent` remains a process-local observation. Plugin middleware can replace a candidate but cannot grant Host durability, forge external side-effect evidence, or retract prior events. The result event is emitted only after final candidate validation and run-scoped teardown succeed, but it is still not a committed host lifecycle transition or durable checkpoint. Teardown failure raises `RunCleanupError` with any frozen primary outcome and produces no terminal event.

## Observation Boundary

[Harness Observation](19-observation-model.md) owns OpenTelemetry configuration, span ownership and hierarchy, trace and Thread correlation, the `a13n.*` registry, Langfuse and Logfire Host profiles, content boundaries, and exporter-failure semantics. Events and usage records remain independent process-local projections; one is never reconstructed from the other.

## Run Usage

Pydantic AI `RunUsage` remains the sole process-local accumulator for model requests, tokens, best-effort model cost, tool-call count, and native `UsageLimits`. The Harness adds a run-local append-only attribution ledger, not a second accumulator. Its immutable `UsageRecord` union preserves where independently produced usage came from:

- `ModelUsageRecord` captures one model response proven to have entered native `RunUsage`, including bounded request usage, model/provider attribution, response state, lineage, and pricing coverage;
- `ProviderUsageRecord` captures a stable provider receipt contributed by a managed tool or Capability, with provider-neutral measures and optional currency-denominated cost.

Provider records do not modify model token totals or native limits. Capability-owned paths record them through `AgentContext.record_provider_usage()`; raw provider metadata, credentials, content, and private provider enums are not part of the contract. Dedicated file media-understanding Agents contribute their aggregated model counters as provider-neutral receipts with source `files.media_understanding` and tool ID `filesystem.view`, keeping that nested model work distinct from the active Agent's native `RunUsage`. A failed or timed-out nested run contributes counters already proven by its run accumulator before the ordinary tool failure is returned; a pre-request failure with no counters contributes no fabricated receipt. Reusing the same provider/product/usage ID is idempotent, while conflicting semantic usage or attribution fails closed.

### Reporting Boundary

Every committed model request is a reporting boundary. After recording that request, the mandatory Usage Capability emits bounded `usage_report` extensions containing every record not included in an earlier report, including mixed provider usage produced since the previous boundary. Large batches may be split into deterministic chunks. A terminal flush reports provider records produced after the final model request, and `HarnessRunResult.usage_records` contains a detached complete run-local snapshot.

Report and record identities are stable for delivery retry and deduplication. Model identity is run-and-ordinal based; provider receipt identity is stable across Harness runs. Reports are process-local observations, not proof of durable ingestion or financial settlement. A Host consumes the canonical stream once and owns persistence, retry, cross-run aggregation, reconciliation, and billing.

The model commit observer uses public Pydantic node, message, and usage boundaries. Normal responses, handled interrupted partial responses, retry-producing requests, and requests that commit before a usage-limit failure remain attributable. Imported history, enqueued or synthetic responses, and history-only processing are not attributed merely because a `ModelResponse` is present.

### Cost Calculation

Model-cost valuation is a default-on build-time Capability role. Every built Agent contains exactly one `AbstractModelCostCapability` alongside the mandatory `UsageCapability`. If build code supplies no implementation, `HarnessBuilder` inserts `CatalogModelCostCapability`; one code-first custom subclass atomically replaces that default; more than one fails with `DefinitionError`. `NoModelCostCapability` is the explicit opt-out and preserves raw provider or upstream-library cost without Harness valuation. Model-cost Capabilities cannot enter through `RunBindings`, plugin contributions, or declarative Capability reconstruction.

`CatalogModelCostCapability` freezes one immutable `PricingCatalog` for the Agent definition. The default catalog normalizes the package's pinned `genai-prices` snapshot and then applies the Harness packaged overlay. Entries are keyed by `provider:model`; every `ModelPricingEntry` contains context window, ordered price rules, tiered price components, constraints, source metadata, and revision. The public `get_default_pricing_catalog()` exposes the complete normalized catalog. `PricingCatalog.with_updates()` and `CatalogModelCostCapability(pricing_updates=...)` use shallow dictionary-update semantics: each supplied value is a complete validated `ModelPricingEntry` replacement, never a recursive merge. A catalog revision identifies the resulting immutable content.

On the normal model-response path, the selected Capability receives content-free `ModelCostInput`, returns an optional `ModelCostQuote`, and a finite non-negative USD quote replaces provider-populated cost before native accumulation. Decline, invalid output, lookup miss, or failure falls back without failing the Agent run. The resulting model record names the selected pricing revision, rule and actual cost source, and whether Harness pricing was applied, declined, failed, disabled, or not reached. The default catalog preserves `genai-prices` usage-dimension, tier, start-date, and recurring UTC time-window semantics; request-start time selects conditional pricing.

Pricing input contains only model/provider identity, safe provider URL when available, request-start and response timestamps, and a copy of request usage with cost cleared. Prompt and response content, credentials, and arbitrary provider payloads are excluded. Live price refresh, currency conversion, negotiated discounts, invoices, and settlement remain Host concerns. Interrupted or short-circuited paths that bypass valuation retain the cost actually available and are not retroactively rewritten.

### Delegation, Resume, and Limits

Inline children share the parent's live `RunUsage` and inherit the parent's effective build-time model-cost Capability for the complete inline tree. The propagation is an internal trusted binding, not a Host-selectable `RunBindings` override; a child definition's own Capability remains its policy when that Agent runs independently. Children retain child-correlated attribution records. Their effective limits are narrowed by delegation policy; native checks do not promise an atomic tree-wide budget across concurrent children. The root terminal `HarnessRunResult.usage` therefore covers the complete inline descendant tree, while `HarnessRunResult.usage_records` is the local logical run's attribution snapshot only. Child records remain observable as child-correlated `usage_report` events and are not copied into the parent's local ledger or priced again. A Host that needs a tree-wide attribution view joins those immutable records by run lineage and stable record identity. Host-managed asynchronous children and later or resumed root runs normally use fresh accumulators and ledgers.

Neither `RunUsage`, the attribution ledger, provider receipts, a pricing catalog, nor a model-cost Capability enters `HarnessState`. Imported messages remain historical, so a resumed run reports only newly committed usage. Terminal cumulative `RunUsage` snapshots and child snapshots can overlap and are never summed as independent contributions; durable projections use immutable usage records instead.

## Failure Boundary

Missing provider usage is not fabricated. A handled failed, suspended, or cancelled result candidate snapshots the usage known at its outcome boundary; it becomes a delivered result only after run-scoped teardown succeeds. Pydantic AI `UsageLimitExceeded` is normalized as `status="failed"` with `SafeFailure.code="usage_limit_exceeded"`, `retry_hint="dependency_change"`, bounded normalized limit details, the terminal usage snapshot, and any latest complete state. Its output and deferred fields are absent. For inline delegation, `DelegationCapability` projects that failed child result through `pydantic_ai.exceptions.ToolFailed` with sanitized bounded content. External consumer cancellation still raises with ordinary async semantics; teardown does not invent usage. Consumer cancellation and early context exit follow the run-stream cleanup contract. OTel failure remains diagnostic.

Event delivery outside the process is a projection made by the single Harness stream consumer. A `CheckpointCapability` publishes an exported checkpoint candidate through its `CheckpointStore`; it never becomes authority for live `AgentContextState` or mutates another Capability's state.

## Compatibility

The event envelope and extension-event schemas evolve independently. Pydantic event and usage changes remain visible through the supported public types. Semantic changes to an extension kind use a new schema version.

## Trade-offs

- Passing through Pydantic events avoids a second model/tool vocabulary, while consumers must understand the supported public event union.
- A single-consumer stream gives bounded lifecycle and backpressure semantics, while hosts perform any replay or fan-out.
- Minimal harness extensions preserve cohesion but leave durable lifecycle projection to the host.
- Native `RunUsage` keeps upstream accumulation and limit semantics, while the attribution ledger preserves mixed-source facts for Host-owned persistence and reconciliation.

## Invariants

01. One event sequence belongs to one process-local run; each Harness-handled root outcome whose teardown succeeds ends with exactly one result event, while early exit, external cancellation, cleanup failure, and unhandled errors do not synthesize one.
02. Forwarded inline-child events preserve child correlation and never grant child result or control authority to the outer consumer.
03. Each `HarnessRunStream` has one consumer; replay and fan-out belong to the host.
04. Model-, node-, and tool-level event extensions are `AbstractCapability[AgentContext]` implementations; outer stream/result transforms are ordered Harness plugins.
05. Pydantic public events remain authoritative for model/tool event shape.
06. Harness events and OTel are not host lifecycle authority.
07. `RunUsage` is the only process-local usage accumulator; the Harness result stores a terminal copy.
08. Every built Agent has exactly one build-time `AbstractModelCostCapability`; it overrides a normally committed response only with a finite non-negative quote, while decline and failure fall back without failing the run and bypassed paths are reported as `not_reached` rather than falsely attributed.
09. Each proven native model or handled-partial commit creates one stable model record and flushes all pending mixed records in bounded reports; enqueue, imported history, and history processing cannot create model records merely by placing a response in messages.
10. Provider receipt identity is stable and idempotent; provider usage does not alter native model totals or limits, and conflicting receipt reuse fails closed.
11. Inline descendants share the root accumulator and inherit the root run's effective model-cost Capability while retaining child-correlated records; Host-managed asynchronous children and later runs use their independently built policy, fresh accumulators, and fresh ledgers.
12. Imported message history never becomes new usage for a resumed run, and no usage accumulator, ledger, provider receipt, pricing catalog, or model-cost Capability enters `HarnessState` or `AgentContextState`.
13. Sensitive and transient content is absent from pricing input, usage records, and default events; the separate Observation contract distinguishes safe Harness-authored fields from telemetry-visible upstream Pydantic structural and exception fields.
