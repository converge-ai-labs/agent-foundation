# Input, Model, and Output Boundaries

## Design Position

The Harness preserves native Pydantic AI input, Model, settings, profile, messages, deferred values, and output semantics. It adds seven narrow boundaries:

1. normalized code-first semantic input visible to Harness middleware;
2. developer-facing native Model inference and deterministic patch composition;
3. input-only convenience layers that immediately materialize selected model-characteristics and model-settings aliases;
4. optional fresh run-scoped resolution of a logical model ID;
5. one automatic request-correlation header derived from the active Thread;
6. optional exact one-shot provider-history self-healing;
7. bounded logical-run recovery after a recoverable model interruption.

It does not add a hosted input wire format, durable model registry, serialized settings/profile system, provider route-pin schema, output mode, or Capability-only retry framework.

## Input

```python
type NativeRunInput = str | Sequence[UserContent]
type RunInputValue = NativeRunInput


@dataclass(frozen=True, slots=True)
class RunPreparationContext:
    run_id: str
    instance: AgentInstanceContext
    environment: Environment
    metadata: Mapping[str, JsonValue]


type RunInputFactory = Callable[
    [RunPreparationContext],
    Awaitable[RunInputValue],
]


@dataclass(frozen=True, slots=True)
class SemanticRunInput:
    value: str | tuple[UserContent, ...] | None
```

`run()` and `stream()` accept either an immediate value or one async factory, never both. The factory runs exactly once after the Environment runtime has entered and atomically published its initial mounts, restored compatible portable Environment data into those mounts, entered ordered Environment run extensions, and activated the runtime, but before plugin middleware or Pydantic execution. It can use trusted run identity, metadata, scoped readiness, and the entered Environment without depending on `DynamicEnvironmentCapability` or receiving a live Pydantic run handle.

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

## Model Construction and Resolution

The Harness exports one optional developer-facing construction helper while retaining native Pydantic `Model` as the universal boundary:

```python
type ModelProviderFactory = Callable[[str], Provider[Any]]
type GatewayModelProviderFactory = Callable[[str, str], Provider[Any]]
type ModelPatch = Callable[[Model], Model]


class ModelHttpRetryConfig:
    attempts: int = 5
    backoff_multiplier: float = 1.0
    max_wait_seconds: float = 30.0
    retry_after_max_wait_seconds: float = 300.0
    status_codes: frozenset[int] = frozenset({429, 502, 503, 504})


def create_model_http_client(
    *,
    timeout: int = 600,
    connect: int = 5,
    transport: httpx2.AsyncBaseTransport | None = None,
    retry: ModelHttpRetryConfig | None = DEFAULT_MODEL_HTTP_RETRY_CONFIG,
) -> httpx2.AsyncClient: ...


def infer_model(
    model: Model | str,
    *,
    provider_factory: ModelProviderFactory = infer_provider,
    gateway_provider_factory: GatewayModelProviderFactory | None = None,
    common_headers: Mapping[str, str] | None = None,
    patches: Sequence[ModelPatch] = (),
) -> Model: ...
```

`infer_model()` is convenience composition, not a second Model interface. It follows these rules:

1. A native `Model` passes through without provider inference. A string is normalized at this single compatibility boundary: bare `openai:` selects the modern Responses API as `openai-responses:`, while `gemini:`, `google-gla:`, and `google-vertex:` normalize to `google-cloud:`. Other strings retain upstream Pydantic semantics.
2. An ordinary provider string uses the supplied `provider_factory`. The literal `gateway@provider:model` form always uses Pydantic AI's public Gateway Provider; Pydantic owns its standard environment configuration, provider client, authentication, and lifecycle. Any other `<name>@provider:model` form requires `gateway_provider_factory`; the Harness passes the normalized gateway and provider names while the factory owns provider construction, credentials, endpoint selection, optional SDK dependencies, retry transport, and any HTTP client.
3. `patches` run synchronously in declaration order after base inference. Every patch accepts and must return a native `Model`. This is the maintained extension point for Harness or application-specific Model wrappers, profiles, transports, and compatibility fixes without replacing Pydantic's Model contract.
4. Non-empty `common_headers` add an outer `RequestHeadersModel` after patches. It case-insensitively merges those defaults into native `ModelSettings.extra_headers` for both request and streaming request paths; request-specific headers win, including when their casing differs. The wrapper does not mutate caller mappings or the wrapped Model, inspect header meanings, or persist or emit values.
5. The returned object is always a native `Model` and can be passed directly to `HarnessBuilder.build(model=...)`, an `AgentDefinition`, or a `RunModelResolver`. A caller can bypass this helper completely and provide any self-constructed native Model.

`create_model_http_client()` is a narrow constructor for Pydantic AI's public `httpx2` provider-client and `AsyncHTTPX2TenacityTransport` surfaces. It requires positive integer timeout values and creates a new caller-owned client whose default connect timeout is `connect` and whose read, write, and pool timeouts are `timeout`. The default frozen `ModelHttpRetryConfig` makes at most five total attempts for explicit `httpx2` timeout, connection, and read errors plus HTTP `429`, `502`, `503`, and `504`. It respects `Retry-After` up to 300 seconds and otherwise uses exponential backoff with multiplier 1 and a 30-second maximum. The supplied `transport` is the wrapped underlying transport; `retry=None` disables automatic retry. Invalid configuration fails during construction.

This retry is Pydantic AI's lowest HTTP layer: the Model never sees intermediate attempts. It does not retry model validation, Tool execution, Harness recovery, provider SDKs that do not accept `httpx2`, non-replayable request bodies, or failures raised while a response stream is consumed after the transport returns. Exhaustion re-raises the final exception. Retries can add latency and duplicate provider cost when a connection fails after request transmission, so Hosts may provide a narrower config or disable retry under a larger end-to-end deadline.

The helper accepts no headers, credentials, endpoint, or provider configuration. Dynamic and common request headers remain `ModelSettings.extra_headers`, and a native request-level `ModelSettings.timeout` overrides client defaults according to the selected Pydantic provider. Integrations needing phase-specific timeout values or a different Tenacity policy may construct any compatible `httpx2.AsyncClient` directly.

Provider factories and patches are caller-owned synchronous construction collaborators. If either creates a client or another resource, its owner retains and closes that resource under its own explicit lifecycle. `RequestHeadersModel` delegates the native Model async context-manager lifecycle but does not invent a separate close contract. The Harness transport helper likewise neither attaches a client to a provider nor writes Pydantic private lifecycle fields. Missing provider dependencies fail through the selected provider integration.

Every built Agent also receives one thin Pydantic `ResolveModelId` Capability. It uses the fresh `AgentContext.model_resolver` only when Pydantic asks to resolve a string model ID.

```python
class RunModelResolver(Protocol):
    def __call__(
        self,
        context: ModelResolutionContext[AgentContext],
        model_id: str,
    ) -> Awaitable[Model]: ...
```

An async function or async callable object can satisfy this protocol structurally; callers do not subclass or register an implementation.

Resolution follows these rules:

1. A concrete `Model` supplied through `AgentDefinition.model` or `HarnessBuilder.build(model=...)` is used directly and does not call `RunModelResolver`. Its `AgentSpec.model` must be `None`.
2. A string selected only by `AgentSpec.model` reaches the thin resolver after the fresh run context exists.
3. With a `RunModelResolver`, the resolver calls the async callable directly and requires a native `Model`; an invalid value or exception becomes `ModelResolutionError`.
4. Without a resolver, the thin Capability calls Harness `infer_model()` with the builder's optional `gateway_provider_factory`. This makes Harness compatibility aliases and `gateway@` routing the default string path rather than delegating to a separate Pydantic inference call.

Resolution precedence is concrete Model, then fresh `RunModelResolver`, then Harness `infer_model()`. The Harness has no second provider profile, provider settings, registry, or route envelope. Harness-owned `AgentSpec.model_characteristics` contains only explicit lifecycle and behavior characteristics, including native media-understanding capabilities; it does not copy or infer provider profile fields. A hosted worker that requires fail-closed logical aliases supplies a binding whose own trusted configuration returns an allowed Model or raises. The builder-level gateway factory is construction policy shared by every recursively built child; current-run credentials or authorization remain in `RunModelResolver`.

`RunBindings` supplies a fresh resolver for each logical Harness run. The same resolver and `AgentContext` are shared by all internal recovery attempts. Pydantic's `ModelResolutionContext` carries the effective Agent dependencies and native resolution semantics. Freshness applies to current-run authority, credentials, policy, and affinity; the callable may reference a Host-owned concurrency-safe provider client whose lifecycle is broader than the run.

### Automatic Request Affinity

Every built Agent includes one mandatory final model-request Capability. Immediately before each upstream request, after effective settings and every earlier request hook have been applied, it copy-on-write adds these independent defaults from the active Thread:

```text
ModelSettings.extra_headers["x-session-id"] = AgentContext.thread_id
ModelSettings.openai_prompt_cache_key = AgentContext.thread_id
```

An explicit effective `extra_headers` entry wins case-insensitively, so `X-Session-ID` and `x-session-id` are the same override key. An explicit effective `openai_prompt_cache_key` also wins. The Capability does not mutate caller settings or header mappings, does not add transient run IDs, and applies to concrete, run-resolved, and inferred Models on both streaming and non-streaming paths. The selected Pydantic provider adapter remains responsible for consuming settings it recognizes: non-OpenAI adapters ignore the OpenAI-prefixed value, while an OpenAI or OpenAI-compatible adapter may render it as `prompt_cache_key`.

Both automatic defaults are enabled when the corresponding environment variable is absent. `HarnessBuilder` snapshots the switches once during synchronous construction:

| Environment variable                                         | Automatic default controlled                  |
| ------------------------------------------------------------ | --------------------------------------------- |
| `A13N_HARNESS_MODEL_REQUEST_X_SESSION_ID_ENABLED`            | `ModelSettings.extra_headers["x-session-id"]` |
| `A13N_HARNESS_MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED` | `ModelSettings.openai_prompt_cache_key`       |

Each switch accepts `1`, `true`, `yes`, or `on` and `0`, `false`, `no`, or `off`, case-insensitively and without surrounding whitespace. Any other present value fails builder construction. Setting one switch to false suppresses only that Harness-derived default; it neither removes an explicit effective setting nor changes the other switch. Existing builders and executables do not observe later environment changes. This process-level compatibility escape hatch lets a deployment disable a field rejected by an upstream OpenAI-compatible endpoint without adding another model capability registry or per-run flag.

The stable Thread ID gives continuations one correlation and cache-affinity value while keeping independent roots, children, siblings, and forks separate. `RequestHeadersModel` remains the lower-level helper for caller-selected static header defaults and does not own run metadata.

### Settings and Profile

Pydantic AI retains the complete layering:

- `ModelSettings` expresses request intent and tuning;
- native Model/provider settings merge under upstream rules;
- `Model.profile` and provider adapters own provider compatibility and rendering facts;
- Harness `AgentSpec.model_characteristics` owns explicit Harness lifecycle and feature characteristics that must be stable across providers, including native image, video, and audio understanding;
- Capabilities own reusable Agent-loop behavior.

For common authoring choices, the Harness exports two parallel synchronous input convenience layers. They preserve the existing separation between Harness lifecycle characteristics and native provider request settings:

```python
type ModelCharacteristicsTransform = Callable[[HarnessModelCharacteristics], HarnessModelCharacteristics]
type ModelSettingsTransform = Callable[[ModelSettings], ModelSettings]


@dataclass(frozen=True, slots=True)
class ModelCharacteristicsAlias:
    key: str
    provider: str
    transform: ModelCharacteristicsTransform


@dataclass(frozen=True, slots=True)
class ModelSettingsAlias:
    key: str
    provider: str
    transform: ModelSettingsTransform


class ModelCharacteristicsAliasCatalog(Mapping[str, ModelCharacteristicsAlias]): ...

class ModelSettingsAliasCatalog(Mapping[str, ModelSettingsAlias]): ...


def get_model_characteristics_alias_catalog() -> ModelCharacteristicsAliasCatalog: ...

def get_model_settings_alias_catalog() -> ModelSettingsAliasCatalog: ...


def resolve_model_characteristics(
    model: str,
    *,
    aliases: Sequence[str] = (),
    overrides: HarnessModelCharacteristics | None = None,
    catalog: ModelCharacteristicsAliasCatalog | None = None,
) -> HarnessModelCharacteristics | None: ...


def resolve_model_settings(
    model: str,
    *,
    aliases: Sequence[str] = (),
    overrides: ModelSettings | None = None,
    catalog: ModelSettingsAliasCatalog | None = None,
) -> ModelSettings: ...
```

The release-pinned default characteristics catalog is deliberately small:

| Alias                    | Concrete `HarnessModelCharacteristics` effect |
| ------------------------ | --------------------------------------------- |
| `anthropic:context-200k` | `context_window=200_000`                      |
| `anthropic:context-400k` | `context_window=400_000`                      |
| `anthropic:context-1m`   | `context_window=1_000_000`                    |

These context values are Harness lifecycle budgets used to derive proactive summarization and compaction thresholds. They do not select a provider context variant, add a beta header, change native `ModelSettings`, or widen the selected model's actual capability. The authoring integration remains responsible for choosing a compatible model and provider route.

The release-pinned default settings catalog contains:

| Alias                            | Concrete `ModelSettings` effect                                               |
| -------------------------------- | ----------------------------------------------------------------------------- |
| `anthropic:interleaved-thinking` | `anthropic_thinking={"type": "adaptive"}`                                     |
| `anthropic:thinking-disabled`    | `anthropic_thinking={"type": "disabled"}` and no inherited `anthropic_effort` |
| `anthropic:max-output-32k`       | `max_tokens=32_768`                                                           |
| `anthropic:max-output-64k`       | `max_tokens=65_536`                                                           |
| `anthropic:max-output-128k`      | `max_tokens=131_072`                                                          |

Adaptive thinking is the current Anthropic request form that enables interleaved thinking on supporting models. The alias does not force effort, token limits, prompt caching, beta headers, or an `anthropic_cm` value. The disabled transform removes an earlier `anthropic_effort`; an explicit concrete override supplied afterward remains authoritative. A max-output alias is only an explicit native request limit. It does not claim that every Anthropic model accepts that value, and the selected Model/provider remains responsible for compatibility validation.

Resolution follows these rules:

1. A non-empty alias sequence requires a provider-qualified model string. Direct `anthropic:model` and explicit gateway `gateway@anthropic:model` forms are compatible with the initial aliases; a Host-logical model ID is not, because its provider integration must be selected first.
2. Alias transforms apply synchronously in declaration order to a detached value in their own plane. Unknown aliases, provider mismatch, malformed model references, or transforms returning the wrong concrete type fail immediately.
3. Concrete `overrides` are copied and applied last. Settings overrides shallowly update the native settings dictionary. Characteristics overrides replace only fields explicitly set by the caller. Neither resolver mutates caller values, infers provider defaults, or validates whether a particular model version supports the selected budget or request setting.
4. `resolve_model_characteristics()` returns `None` when neither aliases nor overrides supply characteristics; otherwise it returns concrete `HarnessModelCharacteristics`. `resolve_model_settings()` always returns concrete native `ModelSettings`, including an empty dictionary when no input is supplied.
5. Callers place the concrete results in `AgentSpec.model_characteristics` and native `AgentSpec.model_settings`, pass them through ordinary Pydantic construction, or persist them under Host-owned concrete schemas. Alias names never enter `AgentSpec`, `AgentDefinition`, `HarnessBuilder`, `RunBindings`, `HarnessState`, a Host revision, or a worker reconstruction record.
6. A Host may construct immutable catalogs containing additional deployment-specific aliases, but resolves them only at its authoring or integration input boundary. Durable and process-local core contracts still contain concrete values. The Harness catalogs have no inheritance, dynamic discovery, environment loading, pricing coupling, or provider registry role.

The Harness does not serialize `ModelProfile`, merge profile keys, copy provider settings into a Host schema, or translate `AgentSpec` field by field. Explicit `HarnessModelCharacteristics.capabilities` are independent Harness facts and never derive from profile keys or a model name. A hosted model integration may retain durable gateway and provider configuration and use Harness `infer_model()` as its shared construction boundary. It supplies a Host-owned gateway provider factory or ordinary provider factory, composes required patches, and returns the native Model directly or through `RunModelResolver`.

An enterprise gateway integration remains explicit model construction, not another Capability or an ambient inference registry. It may select OAuth or WebSocket transport, attach provider profiles and bounded retry configuration, and reuse a Host-owned async client through its provider factory or patches. Harness-owned compatibility normalization and `RequestHeadersModel` provide the shared behavior that embedded applications, Agent UI, and Foundation Service would otherwise duplicate. The integration still owns credential handling, client lifecycle, provider compatibility, and route authorization; the Harness does not infer those facts from process environment.

## Thread Affinity

One independently advancing Pydantic message history is one Thread. `HarnessState.thread_id` is its provider-neutral stable identity, and every fresh `AgentContext` restores that ID from the selected State. The root, every inline child's nested State, every Host-managed child's State, and every explicit `HarnessState.fork()` therefore have separate Threads even when a Host groups them under one workload instance, Session, or trace. [Harness Observation](19-observation-model.md#pydantic-ai-fields) also uses this value as Pydantic `conversation_id`, but telemetry correlation never changes the affinity rules in this section.

The mandatory final request Capability maps `AgentContext.thread_id` to the default request-correlation header and OpenAI prompt-cache setting described above. A selected model integration may explicitly override either value with a provider-required derivation or add other provider-specific routing, session, or thread settings. Rendered provider values remain integration details rather than additional Harness fields or a portable wire schema.

The mapping follows these rules:

1. Distinct independently advancing message histories have distinct `thread_id` values and receive distinct provider model-session and prompt-cache affinity. A child never inherits its parent's value, and sibling children never share one merely because they have the same definition, `AgentInstanceRef`, Session, Host Execution, or Environment.
2. A continuation selected from the same `HarnessState` preserves the exact ID across fresh Harness runs, worker replacement, and reconstructed model resolvers. Every internal `ModelAttempt` in one logical run uses the same ID.
3. A transient Harness `run_id`, model-attempt ID, tool-call ID, `AgentInstanceRef`, or Host `session_id` is not the affinity source. Those values rotate independently or group histories under other policy domains.
4. The final request Capability derives its enabled defaults from the stable ID for every concrete, resolved, or inferred Model. A fresh `RunModelResolver` may read the same ID from `ModelResolutionContext.deps` when its provider requires an explicit namespaced override or additional affinity state, but no fresh binding can replace the context's ID. A provider that needs an additional opaque non-derivable continuation selector keeps it in the Host's provider-specific envelope associated with the matching State.
5. `HarnessState` stores the provider-neutral ID, messages, and portable continuation data, but no provider session, route, credential, or rendered prompt-cache key. `AgentContextState` and subagent projections do not duplicate the ID; an inline nested child `HarnessState` or an independently Host-owned child Thread carries its own.
6. `HarnessState.fork()` copies portable continuation data while generating a new ID. Thread-scoped first-party subagent and background-process projections invalidate copied compact mappings before operator attachment or process rebind, so the fork cannot control the source Thread's operator work. Ordinary state copies, serialization, checkpoint selection, and export preserve the ID. A Host or trusted plugin remains able to perform an intentional complete-state transformation inside the existing trust boundary.
7. The ID and derived affinity improve provider cache locality and best-effort continuation or retry behavior. They grant no authority, do not select a checkpoint, and cannot make an interrupted request or side effect exactly once.

A concrete build-time Model remains valid for embedded use because the automatic defaults are request-dynamic rather than Model defaults. One reusable executable therefore receives each active State's Thread affinity without sharing one static cache or session value across independent histories. Trusted composition may explicitly override a default with a provider-required derivation, but the resulting value must preserve the Thread isolation above.

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

The configuration is compatibility policy selected by trusted embedding code, not a second `ModelProfile`, provider capability registry, or inference from a model name. A dynamic `RunModelResolver` that routes among incompatible media surfaces selects an Agent definition or trusted policy valid for that route; model content never widens the policy.

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

`SelfHealingModelCapability` is an optional code-first Capability with stable ID `a13n.model.self-healing`. It is not installed by default. Applications that need the supported repairs should select it explicitly through an Agent definition, native Agent spec, run binding, or trusted plugin contribution.

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

For a root invocation, a Pydantic result whose output is `DeferredToolRequests` becomes a suspended Harness result rather than a completed business output. `.calls` and `.approvals` retain their native distinct meanings. The later Host or caller supplies the exact pending requests and matching Pydantic results through `DeferredToolResume` in a new logical run with prior state and fresh bindings. A child invocation resolves dynamic deferral as denied tool results inside the same loop; an unexpected terminal deferred output instead becomes a failed result with `code="subagent_deferred_unsupported"`.

Trusted plugins may replace the complete result candidate, including output, usage, and state. The Harness revalidates field combinations, output type, message suffix, and run correlation. It does not enforce state provenance or require state history to match the result message view.

## Failure Semantics

| Failure                                         | Outcome                                                       |
| ----------------------------------------------- | ------------------------------------------------------------- |
| Invalid immediate or factory input              | Typed input/run error before model work                       |
| Invalid or uncorrelated deferred continuation   | Typed run/deferred error before new model or tool work        |
| Binding raises or returns a non-Model           | `ModelResolutionError`                                        |
| No binding for a string model                   | Resolve through Harness `infer_model()`                       |
| Exact self-healing repair succeeds              | Replay the same request once                                  |
| Exact repair does not match or changes nothing  | Propagate the original Model error                            |
| Recoverable model interruption with budget      | Start another `ModelAttempt` after cancellation-aware backoff |
| Recovery budget exhausted                       | Failed result with `model_recovery_exhausted`                 |
| Missing or conflicting build-time output source | `DefinitionError` before Agent construction                   |
| Invalid declarative object JSON Schema          | `DefinitionError` retaining the native validation cause       |
| Output validation retries exhausted             | No Harness `ModelAttempt` recovery                            |
| Root native deferred/HITL output                | Suspended result with native `DeferredToolRequests`           |
| Unexpected child terminal deferred output       | Failed result with `subagent_deferred_unsupported`            |
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
| Official direct model facts           | Harness package catalog                                      |
| Hosted model availability and policy  | Host adapter                                                 |
| Durable deferred execution            | Host                                                         |

## Trade-offs

### Thin Resolver vs. a Model Framework

One always-installed `ResolveModelId` seam supports fresh hosted authority without changing embedded inference. Returning `None` when no binding exists preserves upstream behavior and avoids a second registry.

### One-shot Repair vs. Broad Automatic Replay

Exact repairs recover known provider-history incompatibilities with a bounded replay. General replay is unsafe after emitted output or side effects and therefore belongs to the separate attempt, provider, or Host layer.
