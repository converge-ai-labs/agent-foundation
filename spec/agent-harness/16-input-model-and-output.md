# Input, Model, and Output Boundaries

## Design Position

The Harness preserves native Pydantic AI input, Model, settings, profile, messages, deferred values, and output semantics. It adds four narrow boundaries:

1. normalized code-first semantic input visible to Harness middleware;
2. optional fresh run-scoped resolution of a logical model ID;
3. optional exact one-shot provider-history self-healing;
4. bounded logical-run recovery after a recoverable model interruption.

It does not add a hosted input wire format, model registry, settings/profile system, provider route-pin schema, output mode, or Capability-only retry framework.

## Input

```python
type NativeRunInput = str | Sequence[UserContent]
type RunInputValue = NativeRunInput


@dataclass(frozen=True, slots=True)
class RunPreparationContext:
    run_id: str
    instance: AgentInstanceContext
    environment: BoundEnvironment
    metadata: Mapping[str, JsonValue]


type RunInputFactory = Callable[
    [RunPreparationContext],
    Awaitable[RunInputValue],
]


@dataclass(frozen=True, slots=True)
class SemanticRunInput:
    value: str | tuple[UserContent, ...] | None
```

`run()` and `stream()` accept either an immediate value or one async factory, never both. The factory runs exactly once after the Environment core has entered initial provider scopes, restored compatible portable Environment data against the fixed initial topology, entered ordered Environment run extensions, and activated the Host-retained controller, but before plugin middleware or Pydantic execution. It can use trusted run identity, metadata, scoped readiness, and the entered Environment without depending on `DynamicEnvironmentCapability` or receiving a live Pydantic run handle.

Normalization rules are deliberately small:

- `None` means no new input and does not manufacture an empty prompt;
- a string must be non-empty;
- a `Sequence[UserContent]` must be non-empty and is copied to a tuple;
- bytes and non-sequence values are rejected.

Plugins receive `SemanticRunInput` and can replace it through the same normalization. The trusted `AgentContext` cannot be replaced. Hosted correlation, content references, artifact policy, and transport schemas belong to the Host adapter that creates native Pydantic `UserContent` before calling the Harness.

`DeferredToolResults` is not user content and does not enter `RunInputValue`, `SemanticRunInput`, or plugin input rewriting. The Harness accepts the following separate correlation envelope:

```python
@dataclass(frozen=True, slots=True)
class DeferredToolResume:
    requests: DeferredToolRequests
    results: DeferredToolResults
```

`requests` is the exact terminal pending value returned by the prior Harness run or accepted by the Host, not a value reconstructed from message metadata. `run()` and `stream()` accept it through `deferred_resume=` together with prior `HarnessState` and fresh bindings. The Harness verifies request/result categories, complete coverage, pending message identity, and the current assembled surface before forwarding only `results` through Pydantic AI's native `deferred_tool_results=` parameter on the first `ModelAttempt`. A later `ModelAttempt` uses the already incorporated public message history and receives no deferred results again. Both nested values are defensively detached. The envelope carries no authority and is not stored in `HarnessState`; the full validation and Host boundary is owned by [Tool Execution](07-tool-execution.md#approval-and-deferred-calls).

## Model Resolution

Every built Agent receives one thin Pydantic `ResolveModelId` Capability. It uses the fresh `AgentContext.model_binding` only when Pydantic asks to resolve a string model ID.

```python
class ModelRunBinding(ABC):
    async def resolve_model(
        self,
        context: ModelResolutionContext[AgentContext],
        model_id: str,
    ) -> Model: ...
```

Resolution follows these rules:

1. A concrete `Model` supplied at build time is used directly and does not call `ModelRunBinding`.
2. A logical string reaches the thin resolver after the fresh run context exists.
3. With a `ModelRunBinding`, the resolver calls it and requires a native `Model`; an invalid value or exception becomes `ModelResolutionError`.
4. Without a binding, the resolver returns `None`, deliberately delegating to Pydantic AI's native model inference chain.

The Harness has no second model profile, provider settings, registry, route envelope, or fallback policy. Embedded applications may use native inference. A hosted worker that requires fail-closed logical aliases supplies a binding whose own trusted configuration returns an allowed Model or raises.

`RunBindings` supplies a fresh binding for each logical Harness run. The same binding and `AgentContext` are shared by all internal recovery attempts. Pydantic's `ModelResolutionContext` carries the effective Agent dependencies and native resolution semantics.

### Settings and Profile

Pydantic AI retains the complete layering:

- `ModelSettings` expresses request intent and tuning;
- native Model/provider settings merge under upstream rules;
- `Model.profile` and provider adapters own compatibility and rendering facts;
- Capabilities own reusable Agent-loop behavior.

The Harness does not serialize `ModelProfile`, merge profile keys, copy provider settings into a Host schema, or translate `AgentSpec` field by field. A hosted model-integration adapter may own a durable configuration, but it constructs a native Model before returning from `ModelRunBinding`.

## Thread Affinity

One independently advancing Pydantic message history is one Thread. `HarnessState.thread_id` is its provider-neutral stable identity, and every fresh `AgentContext` restores that ID from the selected State. The root, every inline child's nested State, every Host-managed child's State, and every explicit `HarnessState.fork()` therefore have separate Threads even when a Host groups them under one workload instance, Session, or trace.

A selected model integration reads `AgentContext.thread_id` and can map it to provider-specific routing, session, thread, or prompt-cache settings. For example, an OpenAI-compatible integration can set `openai_prompt_cache_key`, which the provider renders as `prompt_cache_key`. Those rendered values remain provider integration details rather than additional Harness fields or a portable wire schema.

The mapping follows these rules:

1. Distinct independently advancing message histories have distinct `thread_id` values and receive distinct provider model-session and prompt-cache affinity. A child never inherits its parent's value, and sibling children never share one merely because they have the same definition, `AgentInstanceRef`, Session, Host Execution, or Environment.
2. A continuation selected from the same `HarnessState` preserves the exact ID across fresh Harness runs, worker replacement, and reconstructed model bindings. Every internal `ModelAttempt` in one logical run uses the same ID.
3. A transient Harness `run_id`, model-attempt ID, tool-call ID, `AgentInstanceRef`, or Host `session_id` is not the affinity source. Those values rotate independently or group histories under other policy domains.
4. The fresh `ModelRunBinding` reads the stable ID from `ModelResolutionContext.deps`, combines it only with its selected model/provider namespace and current policy as needed, and returns a native Model configured with matching affinity. No fresh binding can replace the context's ID. A provider that needs an additional opaque non-derivable continuation selector keeps it in the Host's provider-specific envelope associated with the matching State.
5. `HarnessState` stores the provider-neutral ID, messages, and portable continuation data, but no provider session, route, credential, or rendered prompt-cache key. `AgentContextState` and Delegation State do not duplicate the ID; a nested child `HarnessState` carries its own.
6. `HarnessState.fork()` copies portable continuation data while generating a new ID. Ordinary state copies, serialization, checkpoint selection, and export preserve the ID. A Host or trusted plugin remains able to perform an intentional complete-state transformation inside the existing trust boundary.
7. The ID and derived affinity improve provider cache locality and best-effort continuation or retry behavior. They grant no authority, do not select a checkpoint, and cannot make an interrupted request or side effect exactly once.

A concrete build-time Model remains valid for embedded use. When one reusable executable can serve several Agent instances and the provider supports provider-native affinity, trusted composition supplies request-dynamic native settings or uses a logical model with `ModelRunBinding`; one static cache or session value cannot be shared across those independent histories. Native explicit setting precedence remains owned by Pydantic and the provider integration, but the resulting value must preserve the Thread isolation above.

## Request and History Filters

Filter Capabilities are the narrow family of copy-on-write transformations applied to native Pydantic messages at the public model-request boundary. They do not form a second pipeline API, model profile, retry framework, or authority plane. Each Filter owns one request/history invariant and composes through ordinary Pydantic Capability ordering.

`MessageIntegrityFilterCapability` is mandatory and innermost. For ordinary function tools, a `ToolReturnPart` or tool-specific `RetryPromptPart` is retained only when its call ID belongs to the most recent `ModelResponse` and has not already received a result. A model-level retry prompt abandons that response's pending function calls. Orphan and duplicate results are removed while the active final `ModelRequest` envelope is preserved. Provider-native call/return rendering and validation remain with the native Model adapter; interrupted-response repair remains with Harness state recovery.

`ContentFilterCapability` is an optional definition-selected request filter for native multimodal content. It inspects both `UserPromptPart` and ordinary `ToolReturnPart` content because user input and native function tools can introduce the same Pydantic AI content types. Its frozen configuration explicitly declares the accepted media families and finite request limits:

```python
class ContentFilterConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted_media: frozenset[Literal[
        "image", "audio", "video", "document", "uploaded_file"
    ]]
    max_media_items: int
    max_binary_bytes: int
```

The configuration is compatibility policy selected by trusted embedding code, not a second `ModelProfile`, provider capability registry, or inference from a model name. A dynamic `ModelRunBinding` that routes among incompatible media surfaces selects an Agent definition or trusted policy valid for that route; model content never widens the policy.

The Content Filter preserves accepted native Pydantic values. It classifies `BinaryContent` by canonical media type, treats image/audio/video/document URLs and `UploadedFile` as their native families, rejects credential-bearing URLs, and enforces aggregate item and inline-binary byte limits before provider serialization. An unsupported, unsafe, or over-limit item is replaced in place by one bounded explanatory text value; non-media content, tool-call identity, and request ordering remain unchanged. Scalar content remains scalar for a one-to-one replacement, while list and tuple shapes retain their sequence shape. This filter does not upload, fetch, decode, compress, spill, or persist content and grants no authority.

`ColdStartFilterCapability` is optional. After its configured idle interval since the latest `ModelResponse`, it shortens oversized string leaves in ordinary tool results strictly before that latest response. Those results have already been consumed by the model; the latest response and every later request, including pending tool results, remain exact. Structured dictionaries, lists, and tuples retain their shape and short hint fields; native media and non-string values remain unchanged. The filter does not spill content or replace explicit compaction.

The tool execution boundary does not duplicate Filter behavior. It preserves native multimodal parts and owns oversized current function-tool text/JSON before message integration as specified by [Tool Execution](07-tool-execution.md#dispatch-retry-and-results). Conversely, Filter Capabilities never authorize a tool or spill its return.

Former global history processors resolve to one current owner:

| Behavior                                                                | Owner                                                        |
| ----------------------------------------------------------------------- | ------------------------------------------------------------ |
| Orphan and duplicate ordinary tool results                              | Mandatory `MessageIntegrityFilterCapability`                 |
| Unsupported, unsafe, or over-limit request media                        | Optional `ContentFilterCapability`                           |
| Cold-cache reduction of already-consumed tool-result strings            | Optional `ColdStartFilterCapability`                         |
| Current tool-return redaction, bounds, and spill                        | `ToolExecutionBoundaryCapability`                            |
| Runtime, file, Environment, handoff, working-state, and process notices | Their focused context or Environment Capabilities            |
| Accepted live user or Agent messages                                    | Native enqueue plus Host delivery acceptance                 |
| Media acquisition, transformation, or upload                            | Optional Media Capability/provider integration               |
| System instructions and provider request rendering                      | `AgentSpec`, native Model profile, and provider adapter      |
| Exact provider-history rejection repair                                 | Selected `SelfHealingModelCapability` and `SelfHealingModel` |
| Interrupted-history normalization and `ModelAttempt` recovery           | Harness state recovery and `HarnessRunStream`                |

Malformed current tool arguments, ordinary provider reasoning projection, and transport retry remain upstream Model/adapter/client concerns rather than generic Filters. An exact residual provider incompatibility uses an explicitly selected `SelfHealingModelCapability` or another narrowly scoped compatibility Capability only when the native profile lacks the required public behavior.

## Narrow Self-Healing

`SelfHealingModelCapability` is an optional code-first Capability with stable ID `converge.model.self-healing`. It is not installed by default. Applications that need the supported repairs should select it explicitly through an Agent definition, native Agent spec, run binding, or trusted plugin contribution.

The Capability runs at the innermost model-request wrapper boundary. After logical resolution and native model inference have selected the effective request Model, it copies the request context and wraps that Model exactly once in `SelfHealingModel`. This preserves the native resolver chain, covers concrete, run-resolved, and natively inferred Models uniformly, and leaves the original request context unchanged. An already wrapped Model is reused.

`SelfHealingModelCapability` accepts an optional sequence of `ModelRecoveryRule` values. `None` selects the built-in rules; an explicit empty sequence selects no rules. `SelfHealingModel` has the same rule semantics and preserves the native Model interface and profile.

For one non-streaming request, the wrapper:

1. calls the wrapped Model;
2. on an exception, finds the first exact configured recovery rule that matches;
3. mutates the request-local message list through that rule;
4. retries the request once only when the repair changed at least one value;
5. otherwise re-raises the original exception.

For streaming, only stream establishment can replay. Once a stream has been yielded, emitted content cannot be retracted; a later interruption belongs to the `ModelAttempt` recovery loop.

Default rules are narrow tested provider repairs:

| Rule                            | Repair                                                                              |
| ------------------------------- | ----------------------------------------------------------------------------------- |
| Oversized request payload       | Replace inline images with an explicit removal reminder                             |
| Invalid provider item ID        | Remove provider-bound response IDs, metadata, reasoning state, and compaction parts |
| Incomplete Anthropic thinking   | Remove thinking parts                                                               |
| Modified Anthropic thinking     | Remove thinking parts                                                               |
| Stale or unverifiable reasoning | Remove thinking parts                                                               |

The wrapper does not retry generic transport, rate-limit, tool, output-validation, or cancellation failures. Provider/client `RetryConfig` owns transport retry.

Custom `ModelRecoveryRule` values contain one exact matcher and one history repair function. Their safety is the caller's responsibility; the wrapper still permits at most one replay per request. Selecting the Capability is recommended for production Agents that need these known provider-history repairs; direct `SelfHealingModel` construction remains available for callers that already own one concrete Model.

## ModelAttempt Recovery

`ModelRecoveryPolicy` owns recovery after an inner `ModelAttempt` fails at a recoverable model boundary. It is disabled by default.

```python
@dataclass(frozen=True, slots=True)
class ModelRecoveryPolicy:
    enabled: bool = False
    max_attempts: int = 5
    continuation_prompt: RunInputValue = DEFAULT_RECOVERY_PROMPT
    prompt_factory: RecoveryPromptFactory | None = None
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
```

`max_attempts` is total, including the first attempt. Backoff uses full jitter from zero to the bounded exponential ceiling. A prompt factory may be synchronous or asynchronous and receives the failure, next attempt index, and detached latest messages.

Recoverable failures are narrowly classified model API errors, non-output-exhaustion `UnexpectedModelBehavior`, and public history showing an interrupted model-request boundary. Harness errors, cancellation, usage limits, exhausted output validation, tool failures, and native deferred/HITL results are hard stops.

Each retry uses normalized interrupted history and new semantic input while preserving one outer Harness run, context, Environment, plugin graph, and `RunUsage`. Each `ModelAttempt` receives a unique model-attempt ID. Detailed lifecycle semantics are owned by [Execution Context and Lifecycle](06-execution-context-and-lifecycle.md#model-attempt-recovery).

Provider-suspended continuation is not attempt recovery. Pydantic owns the public suspended message semantics; the Harness does not append a generic route pin or force a special hosted resolver contract.

## Stream Boundary

Pydantic `AgentStreamEvent` values are the source events. The Harness validates each event, adds public Harness correlation and sequence, and lets trusted plugin middleware transform the stream. Public sequence numbers are assigned after transformation.

Events from a recoverable failed attempt remain visible. A later attempt continues the logical run but cannot retract earlier observations. Consumers therefore use the terminal result to determine the logical outcome rather than treating any intermediate model event as completion.

The Harness does not buffer events for replay, fan out consumers, or persist the event stream. It does retain the public part-finalization observations required to sanitize Pydantic's interrupted response: append-only partial text may remain explicitly interrupted, finalized thinking may remain with its signature, and unfinished thinking is excluded. It never presents an unfinished part as complete. [Harness State and Resume](10-snapshot-and-resume.md#interrupted-history-normalization) owns the exact continuation rules.

## Output Boundary

Pydantic `OutputSpec`, object `AgentSpec.output_schema`, output validators, output tools, and retry behavior own output production. The Harness fixes one business-output contract when `HarnessBuilder` constructs the reusable Agent; `run()` and `stream()` expose no per-run `output_type` override.

Exactly one build-time source is valid:

1. **Code-first output.** `AgentDefinition.output_type` is a native `OutputSpec[OutputT]` and `AgentSpec.output_schema` is absent. Native Python types including `BaseModel`, dataclasses, `TypedDict`, constrained or annotated types, unions, output markers, and synchronous or asynchronous output functions preserve Pydantic semantics and the resulting `ExecutableAgent[OutputT]` type.
2. **Declarative object schema.** `AgentDefinition.output_type is None` and `AgentSpec.output_schema` contains a valid object JSON Schema. The Harness constructs native `StructuredDict` from that detached schema, so the provider-facing structured-output schema remains exact and the public executable/result type is `dict[str, JsonValue]`. Native `StructuredDict` validates the returned Python value as a JSON object; it does not claim to be a second independent Draft 2020-12 instance validator.

Neither source silently defaults to text. Callers request text explicitly with `output_type=str`. Supplying both sources is ambiguous and fails with `output_contract_conflict`; supplying neither fails with `output_contract_missing`. Native Pydantic validation rejects a non-object or otherwise invalid declarative schema.

A business output that directly or transitively includes `DeferredToolRequests` or a subclass through a parameterized collection, structured `BaseModel`/`RootModel`, dataclass or `TypedDict` field, union, `Annotated` value, PEP 695 type alias, output marker, or callable return type is rejected at definition construction; that native value is reserved as the Harness suspension control outcome. Completed candidate validation recursively enforces the same reservation across supported structured Python instances and built-in containers. At build time the Harness forms `[effective_business_output, DeferredToolRequests]` and supplies that complete contract once to `Agent.from_spec()`. Every `ModelAttempt` uses the built Agent contract without a run override, preserving suspension support, Agent-level output validators, and one stable output Toolset across recovery.

The Harness builds one matching process-local output adapter from the effective build-time contract, including return annotations of synchronous, `Awaitable`, and `Coroutine` output functions, and uses it to validate plugin-produced completed output. If Pydantic cannot generate a schema for an otherwise valid arbitrary process-local code-first return type, the adapter permits arbitrary types rather than rejecting the upstream output contract. Declarative output uses the schema-derived `StructuredDict` adapter and therefore accepts only string-keyed JSON-object values under native semantics.

A Pydantic result whose output is `DeferredToolRequests` becomes a suspended Harness result rather than a completed business output. `.calls` and `.approvals` retain their native distinct meanings. The later Host or caller supplies the exact pending requests and matching Pydantic results through `DeferredToolResume` in a new logical run with prior state and fresh bindings.

Trusted plugins may replace the complete result candidate, including output, usage, and state. The Harness revalidates field combinations, output type, message suffix, and run correlation. It does not enforce state provenance or require state history to match the result message view.

## Failure Semantics

| Failure                                         | Outcome                                                       |
| ----------------------------------------------- | ------------------------------------------------------------- |
| Invalid immediate or factory input              | Typed input/run error before model work                       |
| Invalid or uncorrelated deferred continuation   | Typed run/deferred error before new model or tool work        |
| Binding raises or returns a non-Model           | `ModelResolutionError`                                        |
| No binding for a logical string                 | Delegate to native Pydantic inference                         |
| Exact self-healing repair succeeds              | Replay the same request once                                  |
| Exact repair does not match or changes nothing  | Propagate the original Model error                            |
| Recoverable model interruption with budget      | Start another `ModelAttempt` after cancellation-aware backoff |
| Recovery budget exhausted                       | Failed result with `model_recovery_exhausted`                 |
| Missing or conflicting build-time output source | `DefinitionError` before Agent construction                   |
| Invalid declarative object JSON Schema          | `DefinitionError` retaining the native validation cause       |
| Output validation retries exhausted             | No Harness `ModelAttempt` recovery                            |
| Native deferred/HITL output                     | Suspended result with native `DeferredToolRequests`           |
| Invalid plugin-completed output                 | `PluginError(code="plugin_result_invalid")`                   |

## Boundaries

| Concern                               | Owner                                                        |
| ------------------------------------- | ------------------------------------------------------------ |
| Native input, Model, profile, output  | Pydantic AI                                                  |
| Semantic input and thin resolution    | Harness                                                      |
| Thread identity and provider affinity | `HarnessState`, `AgentContext`, and model integration        |
| Exact one-shot history repair         | Selected `SelfHealingModelCapability` and `SelfHealingModel` |
| Interrupted `ModelAttempt` recovery   | Harness run coordinator                                      |
| Provider transport retry              | Provider/client and Pydantic AI                              |
| Hosted model catalog and policy       | Host adapter                                                 |
| Durable deferred execution            | Host                                                         |

## Trade-offs

### Thin Resolver vs. a Model Framework

One always-installed `ResolveModelId` seam supports fresh hosted authority without changing embedded inference. Returning `None` when no binding exists preserves upstream behavior and avoids a second registry.

### One-shot Repair vs. Broad Automatic Replay

Exact repairs recover known provider-history incompatibilities with a bounded replay. General replay is unsafe after emitted output or side effects and therefore belongs to the separate attempt, provider, or Host layer.
