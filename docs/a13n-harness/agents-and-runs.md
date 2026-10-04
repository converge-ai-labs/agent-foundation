---
title: Agents and Runs
description: Build a reusable executable once, then run or stream each logical Run with fresh bindings.
---

Harness keeps Agent construction code-first and process-local. It adds one reusable build boundary and one canonical logical-Run boundary around Pydantic AI; it does not create a second Agent loop or a serialized Agent-definition language.

## Definition and Build

Use `HarnessBuilder.build()` for direct application composition:

```python
from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessModelCharacteristics,
)

executable = HarnessBuilder().build(
    AgentSpec(
        system_prompt="Answer concisely.",
        model_characteristics=HarnessModelCharacteristics(context_window_tokens=200_000),
    ),
    output_type=str,
    model=model,
    capabilities=capabilities,
    plugins=plugins,
    subagents=subagents,
)
```

Use an explicit `AgentDefinition` when the definition is assembled or retained separately:

```python
from a13n_harness import (
    AgentDefinition,
    HarnessBuilder,
)

agent_definition = AgentDefinition(
    agent=AgentSpec(),
    output_type=str,
    model=model,
    capabilities=capabilities,
)
executable = HarnessBuilder().build(agent_definition)
```

Both overloads follow the same validation and construction path. Build is synchronous and inert with respect to model, Environment, and external provider I/O. Known one-shot provider-history repairs are enabled by default. Set `HarnessBuilder(self_healing_enabled=False)` to disable automatic installation. Explicitly selected `SelfHealingModelCapability` instances keep their configured rules and remain active even with this flag disabled; they replace rather than duplicate the default.

### Build-time values

An `AgentDefinition` fixes:

- the Harness `AgentSpec`, which remains a native Pydantic AI spec and may add an ordered static system prompt, definition-level usage limits, and resolved model characteristics;
- one output contract;
- one string or concrete model selection;
- definition-selected Capabilities;
- trusted Harness middleware plugins;
- finite named child definitions (subagents);
- explicitly selected self-healing rules and bounded model-recovery policy.

The build, not the definition, fixes one more value: the default-on model-cost policy. It uses the current pricing catalog, or the catalog passed as `build(..., pricing_catalog=...)`.

The output contract cannot change per Run. Pass a Python output type or Pydantic AI `OutputSpec` through `output_type`, or use `AgentSpec.output_schema`; do not set both.

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

### Cold-start retention

Harness `AgentSpec.cold_start_filter` defaults to a one-hour idle interval. Once the latest model response is at least that old, the filter shortens oversized strings in already-consumed tool results. Pending results, user inputs, and thinking are preserved. This deliberately trades an old cache prefix for a smaller cold request; it does not detect or adapt to provider cache retention.

```python
from a13n_harness.filters import ColdStartFilterConfiguration

spec = AgentSpec(cold_start_filter=ColdStartFilterConfiguration(idle_seconds=3_600))
disabled = spec.with_updates(cold_start_filter=None)
```

Successful direct `view` results for files under published Skill directories are marked at read time and exempt from cold-start trimming, regardless of file extension. The marker survives saved-history resume; it does not bypass initial output limits, prove the file was read completely, or prevent compaction and handoff from replacing the history. Older unmarked results keep the ordinary trimming behavior, and CodeAct does not transfer an inner read's exemption to its combined output.

Plain Pydantic AI specs receive the same default. An explicitly composed `ColdStartFilterCapability` keeps its policy; `None` disables automatic installation rather than removing an authored Capability.

### Usage limits and retries

Harness `AgentSpec.usage_limits` is Pydantic AI's native `UsageLimits`. The default permits 1,000 model requests for one logical Run and leaves token, tool-call, and cost limits unset:

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

A Run can exactly replace the complete definition value:

```python
result = await executable.run(
    "Complete the bounded analysis",
    usage_limits=UsageLimits(request_limit=100, total_tokens_limit=200_000),
)
```

The override is not a field-by-field merge. Keep stable workload budgets on `AgentSpec`; use the invocation argument for a narrower or otherwise deliberately different one-Run budget. Inline children receive the strictest value for each field across their own definition and their `SubagentDefinition.usage_limits`, with an independent usage accumulator. Parent limits do not impose a tree-wide cap.

`AgentSpec.retries` remains the native Pydantic AI setting:

```python
spec = AgentSpec(retries={"tools": 2, "output": 1})
```

When omitted, Pydantic AI allows one function-tool retry and one output-validation retry. An integer sets both; a mapping configures them independently. This does not configure provider transport retries or enable Harness model-interruption recovery. `ModelRecoveryPolicy` remains disabled by default and, when enabled, has its own bounded consecutive-failure budget. A complete, accepted primary model response resets the recovery count and backoff, including a tool-call response; partial output and auxiliary requests such as compaction do not. Recovery only retries recognized transient model-request failures. Permanent and unknown failures stop without consuming the remaining retries. The default is five attempts per failure streak, including the initial attempt, rather than five attempts over the entire Run. Keep `UsageLimits` to bound overall Run usage.

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

The prompt is fixed for one built definition. When a Run of a later definition resumes non-empty `HarnessState`, the Harness removes historical `SystemPromptPart` values and places the current ordered blocks at the beginning of the first request. Removing the prompt from the new definition removes those historical parts. A provider-suspended response remains an in-progress native request and is not rewritten; resume it with a compatible definition.

`AgentSpec.instructions` remains the native Pydantic AI instruction plane. Static and dynamic instructions keep their per-request lifecycle and are not merged into or replaced by `system_prompt` reconciliation. Capability- and Toolset-owned guidance also remains instructions.

Toolsets contribute usage guidance only for tools active in their current surface. Harness `AgentSpec.toolset_instructions` defaults to `True`; set it to `False` to suppress Toolset-owned instruction blocks for every Run of that definition:

```python
spec = AgentSpec(
    instructions="Follow the application policy.",
    toolset_instructions=False,
)
```

Override that default for one logical Run with `RunBindings.toolset_instructions`:

```python
bindings = RunBindings.embedded(toolset_instructions=True)
result = await executable.run("Inspect the workspace", bindings=bindings)
```

`None` inherits the Agent default. This switch does not suppress explicit `AgentSpec.instructions`, Capability-owned feature guidance, tool schemas, or tool availability.

### Model selection

See [Models](models.md#model-selection).

### Model authoring aliases

See [Models](models.md#model-authoring-aliases).

### Model characteristics

See [Models](models.md#model-characteristics).

### Automatic model request affinity

See [Models](models.md#model-request-affinity).

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

`RunBindings` carries current, trusted Run inputs:

```python
from a13n_harness import (
    HarnessObservationContext,
    RunBindings,
)

bindings = RunBindings.embedded(
    environment=environment_binding,
    model_resolver=model_resolver,
    model_context=model_context_binding,
    capabilities=run_capabilities,  # Invocation policy and/or MCP only.
    web=web_binding,  # WebBinding; requires a selected WebCapability.
    skill_selection=frozenset({"code-review"}),
    metadata={"request_kind": "interactive"},
    observation=HarnessObservationContext(
        name="interactive-agent-run",
        labels=("interactive",),
        metadata={"channel": "web"},
    ),
)
```

`RunBindings.embedded()` supplies an embedded identity and optional advanced integrations. Use it when an embedded application needs run Capabilities, a model resolver, model-context middleware, metadata, or an advanced `EnvironmentRuntime`. Ordinary `run()` and `stream()` calls can omit `bindings`; run normalization creates fresh embedded bindings and an empty Environment runtime when no Environment input is supplied. A Host can construct `RunBindings` directly with an exact `AgentInstanceContext`.

Create fresh bindings for every root, resumed, or child Run. Do not persist or reuse live bindings as continuation state. Optional feature providers and overrides use `web`, `document_converter`, `file_media_understanding`, `skill_selection`, `task_state`, and `client_toolsets`; each is consumed by its selected feature Capability rather than a companion Run Capability. Leave selection fields `None` to retain defaults; an explicit empty Skill set or client-tool tuple selects none. The Host owns provider lifetime, including any deliberately shared transport.

Stable definition inputs: `AgentSpec`, output contract, Agent behavior Capabilities, direct plugins, child topology, and recovery policy. Fresh Run inputs: identity and Agent instance context, Environment runtime, model resolver and model-context binding, policy and provider collaborators, typed feature overrides and current policy, and bounded non-authoritative metadata and Observation context.

## Input

Pass one native input value directly:

```python
result = await executable.run(
    "Summarize the change",
    bindings=bindings,
)
```

Or produce semantic input after the current Environment aggregate enters, before model execution. The Host supplies authoritative Provider state when constructing fresh adapters; Harness does not restore a Provider from portable observations after entry, and target preparation may still be lazy:

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
result = await executable.run("Do the work", bindings=bindings)
output = result.output_or_raise()
```

A result has one status:

| Status      | Meaning                                                           | Important fields                           |
| ----------- | ----------------------------------------------------------------- | ------------------------------------------ |
| `completed` | A validated business output completed and cleanup succeeded       | `output`, `state`, `usage`                 |
| `suspended` | A supported native deferred tool or approval requires later input | `state`, `deferred`, `suspend_reason`      |
| `failed`    | The logical Run ended with a safe normalized failure              | `failure`, optional safe `state` candidate |
| `cancelled` | Cancellation stopped the logical Run                              | no business output                         |

Use `raise_for_status()` when only completion is acceptable. Use `output_or_raise()` to both validate status and return the typed output. Inspect `status`, `failure`, or `deferred` when the application handles other outcomes explicitly. Roots and Host-managed children can return `suspended` when fresh `RunBindings.deferred_tools_supported` is enabled (the default); built-in inline children always disable deferred tools. If disabled, dynamic deferral is denied inside the same model loop, and unexpected terminal deferral becomes `failed` with `deferred_tools_unsupported`.

`all_messages()` returns the complete detached message history represented by the result. `new_messages()` returns only messages added by that logical Run.

## Stream Events

`stream()` is lazy, single-entry, and single-consumer:

```python
from a13n_harness import (
    HarnessEvent,
    HarnessRunResultEvent,
)

async with executable.stream("Do the work", bindings=bindings) as stream:
    async for item in stream:
        if isinstance(item, HarnessEvent):
            consume_observation(item)
        elif isinstance(item, HarnessRunResultEvent):
            result = item.result
```

The public stream union contains:

- `HarnessEvent`, wrapping a native Pydantic AI `AgentStreamEvent` or a bounded `HarnessExtensionEvent`;
- one terminal `HarnessRunResultEvent`, emitted only after owned Run resources close successfully.

Every item carries the same `thread_id` and `run_id` for that logical Run plus a monotonically increasing `sequence`. Inline child observations can also appear in the parent stream with their child correlation preserved.

A stream entered and exited without iteration does not start model execution. Iteration starts the canonical event path.

## Active Stream Operations

After stream entry:

- `stream.context` exposes the fresh `AgentContext` to trusted embedding code;
- `stream.usage` returns a detached `RunUsageSummary` of current local Context usage, not the mutable native accumulator;
- `await stream.export_state()` returns the latest safe portable state boundary;
- `await stream.steer(input, input_id=None)` delivers non-empty native user content through Pydantic AI's active-run `priority="asap"` queue and returns its enqueue ID. A Host-chosen `input_id` is recorded on the delivered request. `steering_input_ids(state.message_history)` from `a13n_harness.capabilities.steering` lists the IDs present in exported state;
- `stream.cancel()` requests semantic cancellation;
- `stream.result` becomes available only after the terminal result event is delivered.

`steer()` is available only while an inner Pydantic run is active. When automatic compaction is configured, the Harness also retains accepted initial and steering inputs for later compact replay, preserving structured and multimodal content. This favors retaining user context over exact-once replay; it is not a durable command or receipt protocol.

Always use the stream as an async context manager. Early consumer exit, exceptions, task cancellation, or an explicit cancellation request still trigger Harness cleanup.

## Recovery Layers

Recovery has narrow owners:

| Failure class                                         | Owner                                                            |
| ----------------------------------------------------- | ---------------------------------------------------------------- |
| Provider transport retry                              | Model provider/client and native Pydantic AI retry configuration |
| Exact provider-history incompatibility                | Default-on `SelfHealingModelCapability` and `SelfHealingModel`   |
| Interrupted model attempt inside one live logical Run | `ModelRecoveryPolicy` and `HarnessRunStream`                     |
| Worker/process loss, durable replay, or delivery      | Embedding Host                                                   |

Self-healing is enabled by default and performs only supported one-shot history repairs around the final effective Model, including a concrete, Run-resolved, or natively inferred Model. `HarnessBuilder(self_healing_enabled=False)` disables automatic self-healing for the root and inline children, including compaction through the same Agent; only explicitly selected `SelfHealingModelCapability` instances stay active. A rebuilt saved definition or Host capture gets these repairs without a change to its saved schema. Independent native tool-review and media-understanding Agents do not inherit the primary Agent's request Capabilities. Self-healing is not a retry for arbitrary model or tool exceptions. Semantic model recovery is disabled by default; opt in with a bounded `ModelRecoveryPolicy` on the definition when continuing an interrupted model attempt is valid for the application.

An interrupted attempt retains text already emitted, even when the stream stops during a subsequent tool call. The next attempt receives that partial response as interrupted history, not as completed output. Unfinished thinking and tool arguments are excluded; invalid provider-native call/return groups are removed without erasing surrounding recoverable text. Failed or cancelled Runs export the same filtered history for a later Host-selected continuation.

Recovery never makes uncertain external side effects exactly once. When a tool or provider mutation may have been dispatched without an authoritative result, reconcile current provider state before retrying.

## Usage

See [Usage, limits, and pricing](usage-and-limits.md) for native usage, provider attribution, catalog updates, and custom valuation.

## Correlation

- `thread_id` identifies one independently advancing history. Resume preserves it; `HarnessState.fork()` creates another.
- `run_id` identifies one process-local logical execution. Every new root, resume, or replacement Run receives a new value.
- inner model attempts and inline child Runs have their own narrower correlation and do not replace Thread or root Run identity.

Identifiers are observations and routing keys, not authority.

## Cleanup

`ExecutableAgent` is immutable reusable build output and owns no entered Model, plugin, Capability, client, or child resource, so it has no `close()` method or async context-manager lifecycle. `run()` internally scopes and closes one `HarnessRunStream`. Callers that use `stream()` must enter that stream with `async with`; early exit then closes the Run's temporary resources deterministically.

A clean `HarnessRunResultEvent` means Harness-owned Run cleanup completed. It is still only a process-local candidate; a Host decides when to persist a checkpoint, commit durable completion, or deliver output externally.
