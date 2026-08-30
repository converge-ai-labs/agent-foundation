# Agents and Runs

Agent Harness keeps Agent construction code-first and process-local. It adds one reusable build boundary and one canonical logical-run boundary around Pydantic AI; it does not create a second Agent loop or a serialized Agent-definition language.

## Definition and Build

Use `HarnessBuilder.build()` for direct application composition:

```python
from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessModelCharacteristics,
    SelfHealingModelCapability,
)

executable = HarnessBuilder().build(
    AgentSpec(
        system_prompt="Answer concisely.",
        model_characteristics=HarnessModelCharacteristics(context_window=200_000),
    ),
    output_type=str,
    model=model,
    capabilities=(SelfHealingModelCapability(), *capabilities),
    plugins=plugins,
    subagents=subagents,
)
```

Use an explicit `AgentDefinition` when the definition is assembled or retained separately:

```python
from a13n_harness import AgentDefinition, HarnessBuilder

agent_definition = AgentDefinition(
    agent=AgentSpec(),
    output_type=str,
    model=model,
    capabilities=capabilities,
)
executable = HarnessBuilder().build(agent_definition)
```

Both overloads follow the same validation and construction path. Build is synchronous and inert with respect to model, Environment, and external provider I/O. Self-healing is optional rather than implicitly enabled; selecting `SelfHealingModelCapability()` is recommended for production Agents that need its known one-shot provider-history repairs.

### Build-time values

An `AgentDefinition` fixes:

- the Harness `AgentSpec`, which remains a native Pydantic AI spec and may add an ordered static system prompt, definition-level usage limits, and resolved model characteristics;
- one output contract;
- one string or concrete model selection;
- definition-selected Capabilities;
- trusted Harness middleware plugins;
- finite inline child definitions;
- self-healing and bounded model-recovery policy;
- one default-on build-time model-cost policy.

The output contract cannot change per run. Pass a Python output type or Pydantic AI `OutputSpec` through `output_type`, or use `AgentSpec.output_schema`; do not set both.

### Refine a loaded preset

Use `with_updates()` to create a validated local variant without mutating the loaded preset:

```python
preset = AgentSpec.from_file("research-agent.yaml")
local = preset.with_updates(
    model="anthropic:claude-sonnet-4-6",
    model_settings={"temperature": 0.1, "max_tokens": 8_000},
    toolset_instructions=False,
)
```

The optional positional mapping supports dynamic fields and serialization aliases such as `model_characteristics` or `$schema`. Keyword overrides support the ordinary Python field names. Unknown fields, duplicate alias/name updates, and invalid values fail immediately. Updates replace complete top-level fields and do not recursively merge nested provider settings, metadata, schemas, or Capability arguments; construct an explicitly merged field when that behavior is intended.

This happens before `HarnessBuilder.build()` and returns an independent deep copy. It is not the temporary run-scoped context manager exposed by Pydantic AI's built Agent.

### Usage limits and retries

Harness `AgentSpec.usage_limits` is Pydantic AI's native `UsageLimits`. The default permits 1,000 model requests for one logical run and leaves token, tool-call, and cost limits unset:

```python
from a13n_harness import AgentSpec
from pydantic_ai.usage import UsageLimits

spec = AgentSpec(
    usage_limits=UsageLimits(
        request_limit=300,
        total_tokens_limit=500_000,
        cost_limit="25.00",
    ),
)
```

A plain Pydantic AI `AgentSpec` receives the same 1,000-request Harness default. To remove the request-count ceiling explicitly, use `UsageLimits(request_limit=None)`; passing no `usage_limits` argument to `run()` means “use the definition value,” not “disable limits.”

A run can exactly replace the complete definition value:

```python
result = await executable.run(
    "Complete the bounded analysis",
    usage_limits=UsageLimits(request_limit=100, total_tokens_limit=200_000),
)
```

The override is not a field-by-field merge. Keep stable workload budgets on `AgentSpec`; use the invocation argument for a narrower or otherwise deliberately different one-run budget. Inline children receive the strictest value for each field across the parent effective limit, child `AgentSpec`, authored edge, and current Host policy.

`AgentSpec.retries` remains the native Pydantic AI setting:

```python
spec = AgentSpec(retries={"tools": 2, "output": 1})
```

When omitted, Pydantic AI allows one function-tool retry and one output-validation retry. An integer sets both; a mapping configures them independently. This does not configure provider transport retries or enable Harness model-interruption recovery. `ModelRecoveryPolicy` remains disabled by default and, when enabled, has its own bounded total-attempt budget.

### System prompt and instructions

Use the Harness `AgentSpec.system_prompt` field for definition-owned static system-prompt content. A string creates one block, a list preserves ordered blocks, and `None` or an empty list supplies no block:

```python
spec = AgentSpec(
    system_prompt=[
        "You are the support Agent.",
        "Answer with verified account information only.",
    ],
)
```

The prompt is fixed for one built definition. When a later definition resumes non-empty `HarnessState`, the Harness removes historical `SystemPromptPart` values and places the current ordered blocks at the beginning of the first request. Removing the prompt from the new definition removes those historical parts. A provider-suspended response remains an in-progress native request and is not rewritten; resume it with a compatible definition.

`AgentSpec.instructions` remains the native Pydantic AI instruction plane. Static and dynamic instructions keep their per-request lifecycle and are not merged into or replaced by `system_prompt` reconciliation. Capability- and Toolset-owned guidance also remains instructions.

Toolsets contribute usage guidance only for tools active in their current surface. Harness `AgentSpec.toolset_instructions` defaults to `True`; set it to `False` to suppress Toolset-owned instruction blocks for every run of that definition:

```python
spec = AgentSpec(
    instructions="Follow the application policy.",
    toolset_instructions=False,
)
```

Override that default for one logical run with `RunBindings.toolset_instructions`:

```python
bindings = RunBindings.embedded(toolset_instructions=True)
result = await executable.run("Inspect the workspace", bindings=bindings)
```

`None` inherits the Agent default. This switch does not suppress explicit `AgentSpec.instructions`, Capability-owned feature guidance, tool schemas, or tool availability.

### Model selection

Use exactly one model source. Put a native or Host-logical string in `AgentSpec.model`:

```python
executable = HarnessBuilder().build(
    AgentSpec(model="openai-responses:gpt-5"),
    output_type=str,
)
```

Pass a concrete Pydantic AI Model through `model=` and leave `AgentSpec.model` unset. You can construct it yourself or use the optional Harness helper:

```python
from a13n_harness import HarnessBuilder, infer_model

model = infer_model(
    "openai-responses:gpt-5",
    provider_factory=provider_factory,
    patches=(apply_provider_profile,),
)
executable = HarnessBuilder().build(
    AgentSpec(system_prompt="Answer concisely."),
    output_type=str,
    model=model,
)
```

`infer_model()` always returns a native Pydantic AI Model. It maps bare `openai:` to the modern `openai-responses:` provider, accepts legacy Google Cloud prefixes, applies synchronous Model patches in order, and can add caller-selected static common headers without overriding request-specific `ModelSettings.extra_headers`. You can bypass it and pass any native Model directly.

Without a run model resolver, `HarnessBuilder` uses this same helper for every string in `AgentSpec.model`. The literal `gateway@provider:model` form selects Pydantic AI's public Gateway Provider and its standard `PYDANTIC_AI_GATEWAY_API_KEY` and optional `PYDANTIC_AI_GATEWAY_BASE_URL` configuration:

```python
executable = HarnessBuilder().build(
    AgentSpec(model="gateway@openai:gpt-5"),
    output_type=str,
)
```

A named custom gateway uses one builder-level factory:

```python
executable = HarnessBuilder(
    gateway_provider_factory=gateway_provider_factory,
).build(
    AgentSpec(model="company@openai:gpt-5"),
    output_type=str,
)
```

The factory receives `(gateway_name, provider_name)` and returns a Pydantic AI `Provider`. It owns credentials, provider SDK configuration, retries, any HTTP client, and that client's lifecycle. The same builder factory applies to recursively built subagents. Use a fresh `RunBindings.model_resolver` instead when route authorization, credentials, or policy vary by run.

For direct-provider model facts, the package includes a small immutable official catalog:

```python
from a13n_harness import get_official_model_catalog

models = get_official_model_catalog()
characteristics = models["anthropic:claude-sonnet-5"].characteristics
```

Entries contain only a provider-qualified official model ID, objective `HarnessModelCharacteristics`, and an official source URL. They do not contain gateway routes, credentials, request presets, reasoning settings, aliases, labels, or application defaults. Lookup is explicit; `HarnessBuilder` does not silently apply catalog characteristics.

For run-specific routing, credentials, or tenant policy, pass an async function or async callable object through `RunBindings.model_resolver`. It receives the Pydantic `ModelResolutionContext` and string selection and returns a native Model. No Harness base class is required. A resolver can call Harness `infer_model()` with current Host-owned factories and patches, or return a self-constructed Model.

### Model authoring aliases

Use the two parallel resolvers when an authoring surface wants short, explicit names while keeping concrete values everywhere else. Context budgets resolve to Harness `HarnessModelCharacteristics`; provider request choices resolve independently to native `ModelSettings`:

```python
from a13n_harness import (
    AgentSpec,
    HarnessModelCharacteristics,
    resolve_model_characteristics,
    resolve_model_settings,
)
from pydantic_ai.settings import ModelSettings

model = "anthropic:claude-sonnet-5"
characteristics = resolve_model_characteristics(
    model,
    aliases=("anthropic:context-1m",),
    overrides=HarnessModelCharacteristics(compact_threshold=0.85),
)
settings = resolve_model_settings(
    model,
    aliases=(
        "anthropic:interleaved-thinking",
        "anthropic:max-output-128k",
    ),
    overrides=ModelSettings(temperature=0.2),
)

spec = AgentSpec(
    model=model,
    model_characteristics=characteristics,
    model_settings=settings,
)
```

The built-in characteristics aliases are:

- `anthropic:context-200k` for a `200_000`-token Harness context budget;
- `anthropic:context-400k` for a `400_000`-token Harness context budget;
- `anthropic:context-1m` for a `1_000_000`-token Harness context budget.

These values control Harness lifecycle thresholds only. They do not select a provider context variant, add beta headers, change request settings, or widen the model's actual context capability.

The built-in settings aliases are:

- `anthropic:interleaved-thinking`, which selects Anthropic adaptive thinking without forcing effort, token limits, cache behavior, beta headers, or context management;
- `anthropic:thinking-disabled`, which disables thinking and removes an effort inherited from an earlier alias;
- `anthropic:max-output-32k`, `anthropic:max-output-64k`, and `anthropic:max-output-128k`, which set `max_tokens` to `32_768`, `65_536`, and `131_072`, respectively.

A max-output alias is an explicit request limit, not a compatibility claim. The selected model and provider still validate whether the request is supported.

Within each resolver, aliases apply in declaration order and concrete overrides apply last. Alias resolution requires a provider-qualified direct or gateway model string and fails immediately for an unknown or incompatible alias. `resolve_model_characteristics()` returns `None` when neither aliases nor overrides provide characteristics; `resolve_model_settings()` returns an ordinary detached native settings dictionary. A Host can add private choices through immutable custom catalogs, but resolves every alias before persisting an Agent revision. `AgentSpec`, `HarnessBuilder`, workers, and Harness state never contain alias names.

### Model characteristics

The `model_characteristics` construction and serialization key holds resolved characteristics that complement Pydantic AI's provider `ModelProfile`; it is not provider request settings. Python code reads the value through `spec.model_characteristics` without conflicting with Pydantic's class-level `model_config`. It defines explicit Harness model capabilities together with the context window and proactive summarize and compaction ratios:

```python
spec = AgentSpec(
    model="logical:support",
    model_characteristics=HarnessModelCharacteristics(
        context_window=200_000,
        proactive_context_management_threshold=0.65,
        compact_threshold=0.90,
    ),
)
```

When selected, `HandoffCapability()` derives its summarize reminder at 65% and `CompactionCapability()` derives its trigger at 90%. Explicit Capability token thresholds take precedence, and model characteristics never enable either Capability by itself. A Host may resolve these values from its own preset catalog, the Harness official model catalog, or characteristics aliases; `HarnessBuilder` never infers one from the model name. Native Pydantic AI `AgentSpec` remains accepted when this extension is not needed.

### Automatic model request affinity

Every upstream model request receives two defaults from the current `AgentContext.thread_id`:

```python
ModelSettings(
    openai_prompt_cache_key=thread_id,
    extra_headers={"x-session-id": thread_id},
)
```

The value remains stable across continuation from the same `HarnessState` and differs for independent roots, children, siblings, and forks. An explicit `openai_prompt_cache_key` wins, and an explicit `ModelSettings.extra_headers` entry overrides `x-session-id` case-insensitively. The Harness does not use the transient `run_id` or mutate caller settings.

Pydantic provider adapters consume only settings they recognize. Non-OpenAI adapters ignore `openai_prompt_cache_key`; OpenAI and OpenAI-compatible adapters may transmit it as `prompt_cache_key`. If an upstream endpoint rejects either automatic field, disable that patch before constructing `HarnessBuilder`:

```bash
export A13N_HARNESS_MODEL_REQUEST_X_SESSION_ID_ENABLED=false
export A13N_HARNESS_MODEL_REQUEST_OPENAI_PROMPT_CACHE_KEY_ENABLED=false
```

The switches are independent and default to enabled. They accept `1/true/yes/on` or `0/false/no/off`, case-insensitively. Invalid values fail builder construction. Each builder snapshots both switches once, so changing the environment does not alter existing builders or executables. A disabled patch leaves any explicit setting untouched.

## Mandatory Composition

Every executable receives one Harness-owned instance of each mandatory boundary:

- the function-tool execution boundary;
- message-integrity filtering;
- model-context coordination;
- model-ID resolution;
- Thread-derived model-request affinity;
- model-request lifecycle events;
- usage attribution and reporting.

Application code must not add a second mandatory boundary. Optional request/history filters and feature Capabilities remain explicit definition choices.

## Fresh Run Bindings

`RunBindings` carries current, trusted run inputs:

```python
from a13n_harness import HarnessObservationContext, RunBindings

bindings = RunBindings.embedded(
    environment=environment_binding,
    model_resolver=model_resolver,
    model_context=model_context_binding,
    capabilities=run_capabilities,
    metadata={"request_kind": "interactive"},
    observation=HarnessObservationContext(
        name="interactive-agent-run",
        labels=("interactive",),
        metadata={"channel": "web"},
    ),
)
```

`RunBindings.embedded()` supplies an embedded identity and optional advanced integrations. Use it when an embedded application needs run Capabilities, a model resolver, model-context middleware, metadata, or an advanced `EnvironmentRuntime`. Ordinary `run()` and `stream()` calls can omit `bindings`; run normalization creates fresh embedded bindings and an empty Environment runtime when no Environment input is supplied. A Host can construct `RunBindings` directly with an exact `AgentInstanceContext`.

Create fresh bindings for every root, resumed, or child run. Do not persist or reuse live bindings as continuation state.

| Stable definition input     | Fresh run input                                            |
| --------------------------- | ---------------------------------------------------------- |
| `AgentSpec`                 | Identity and Agent instance context                        |
| Output contract             | Environment runtime                                        |
| Agent behavior Capabilities | Model resolver and model-context binding                   |
| Direct plugins              | Policy and provider collaborators                          |
| Child topology              | Run-specific Capability selection                          |
| Recovery policy             | Bounded non-authoritative metadata and Observation context |

## Input

Pass one native input value directly:

```python
result = await executable.run(
    "Summarize the change",
    bindings=bindings,
)
```

Or produce semantic input after the Environment is entered and previous portable Environment state is restored:

```python
async def make_input(preparation):
    return f"Run {preparation.run_id} in the current workspace"

result = await executable.run(
    input_factory=make_input,
    bindings=bindings,
    previous_state=state,
)
```

`input` and `input_factory` are mutually exclusive. Plugins receive normalized semantic input after the factory completes.

## Run to a Result

`run()` consumes the canonical stream and returns its sole terminal result:

```python
async with executable:
    result = await executable.run("Do the work", bindings=bindings)

output = result.output_or_raise()
```

A result has one status:

| Status      | Meaning                                                      | Important fields                           |
| ----------- | ------------------------------------------------------------ | ------------------------------------------ |
| `completed` | A validated business output completed and cleanup succeeded  | `output`, `state`, `usage`                 |
| `suspended` | A root native deferred tool or approval requires later input | `state`, `deferred`, `suspend_reason`      |
| `failed`    | The logical run ended with a safe normalized failure         | `failure`, optional safe `state` candidate |
| `cancelled` | Cancellation stopped the logical run                         | no business output                         |

Use `raise_for_status()` when only completion is acceptable. Use `output_or_raise()` to both validate status and return the typed output. Inspect `status`, `failure`, or `deferred` when the application handles other outcomes explicitly. A child invocation never returns `suspended`: dynamic deferral is denied inside the same model loop, and an unexpected terminal deferred output becomes `failed` with `subagent_deferred_unsupported`.

`all_messages()` returns the complete detached message history represented by the result. `new_messages()` returns only messages added by that logical run.

## Stream Events

`stream()` is lazy, single-entry, and single-consumer:

```python
from a13n_harness import HarnessEvent, HarnessRunResultEvent

async with executable.stream("Do the work", bindings=bindings) as stream:
    async for item in stream:
        if isinstance(item, HarnessEvent):
            consume_observation(item)
        elif isinstance(item, HarnessRunResultEvent):
            result = item.result
```

The public stream union contains:

- `HarnessEvent`, wrapping a native Pydantic AI `AgentStreamEvent` or a bounded `HarnessExtensionEvent`;
- one terminal `HarnessRunResultEvent`, emitted only after owned run resources close successfully.

Every item carries the same `thread_id` and `run_id` for that logical run plus a monotonically increasing `sequence`. Inline child observations can also appear in the parent stream with their child correlation preserved.

A stream entered and exited without iteration does not start model execution. Iteration starts the canonical event path.

## Active Stream Operations

After stream entry:

- `stream.context` exposes the fresh `AgentContext` to trusted embedding code;
- `stream.usage` exposes the live native `RunUsage` accumulator;
- `await stream.export_state()` returns the latest safe portable state boundary;
- `await stream.steer(input)` delivers non-empty native user content through Pydantic AI's active-run `priority="asap"` queue and returns its enqueue ID;
- `stream.cancel()` requests semantic cancellation;
- `stream.result` becomes available only after the terminal result event is delivered.

`steer()` is available only while an inner Pydantic run is active. When automatic compaction is configured, the Harness also retains accepted initial and steering inputs for later compact replay, preserving structured and multimodal content. This favors retaining user context over exact-once replay; it is not a durable command or receipt protocol.

Always use the stream as an async context manager. Early consumer exit, exceptions, task cancellation, or an explicit cancellation request still trigger Harness cleanup.

## Recovery Layers

Recovery has narrow owners:

| Failure class                                         | Owner                                                            |
| ----------------------------------------------------- | ---------------------------------------------------------------- |
| Provider transport retry                              | Model provider/client and native Pydantic AI retry configuration |
| Exact provider-history incompatibility                | Selected `SelfHealingModelCapability` and `SelfHealingModel`     |
| Interrupted model attempt inside one live logical run | `ModelRecoveryPolicy` and `HarnessRunStream`                     |
| Worker/process loss, durable replay, or delivery      | Embedding Host                                                   |

Self-healing is opt-in through `SelfHealingModelCapability` and performs only supported one-shot history repairs around the final effective Model, including a concrete, run-resolved, or natively inferred Model. It is not a retry for arbitrary model or tool exceptions. Semantic model recovery is disabled by default; opt in with a bounded `ModelRecoveryPolicy` on the definition when continuing an interrupted model attempt is valid for the application.

Recovery never makes uncertain external side effects exactly once. When a tool or provider mutation may have been dispatched without an authoritative result, reconcile current provider state before retrying.

## Usage

`result.usage` and `stream.usage` use Pydantic AI's native `RunUsage`. `result.usage_records` additionally contains detached Harness attribution records for committed model requests and provider-reported usage.

Provider integrations can record stable non-model receipts through `AgentContext.record_provider_usage()`.

Model-cost valuation is enabled by default. `HarnessBuilder` inserts `CatalogModelCostCapability`, which uses an immutable catalog assembled from the pinned `genai-prices` snapshot plus Harness pricing replacements. Read or export the complete snapshot with `get_default_pricing_catalog()`:

```python
from a13n_harness import get_default_pricing_catalog

pricing = get_default_pricing_catalog()
entry = pricing["openai:gpt-5.5"]
exported = pricing.model_dump(mode="json")
```

To replace prices, create complete `ModelPricingEntry` values and pass a shallow update dictionary. Each value replaces the entire entry at that `provider:model` key; nested fields are not merged:

```python
from a13n_harness import CatalogModelCostCapability, HarnessBuilder

costs = CatalogModelCostCapability(
    pricing_updates={replacement.key: replacement},
)
executable = HarnessBuilder().build(
    spec,
    output_type=str,
    capabilities=(costs,),
)
```

One custom `AbstractModelCostCapability` supplied through build-time `capabilities=` atomically replaces the default. More than one is a definition error. Use `NoModelCostCapability()` to explicitly preserve only provider or upstream-library cost without Harness valuation. Inline child runs inherit the parent's selected policy so the shared usage tree is valued consistently; the same child definition uses its own build-time policy when executed independently.

Pricing failure or model lookup miss does not fail the Agent run. Usage records identify the pricing status, catalog revision, selected rule, and actual cost source. Durable aggregation, reconciliation, negotiated discounts, billing, and exporter delivery remain Host concerns.

## Correlation

- `thread_id` identifies one independently advancing history. Resume preserves it; `HarnessState.fork()` creates another.
- `run_id` identifies one process-local logical execution. Every new root, resume, or replacement run receives a new value.
- inner model attempts and inline child runs have their own narrower correlation and do not replace Thread or root Run identity.

Identifiers are observations and routing keys, not authority.

## Cleanup

Use `async with executable` or call `await executable.close()` exactly when application ownership ends. Closing is idempotent and recursively closes built child executables. A closed executable cannot start another run.

A clean `HarnessRunResultEvent` means Harness-owned cleanup completed. It is still only a process-local candidate; a Host decides when to persist a checkpoint, commit durable completion, or deliver output externally.
