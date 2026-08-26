# Agents and Runs

Agent Harness keeps Agent construction code-first and process-local. It adds one reusable build boundary and one canonical logical-run boundary around Pydantic AI; it does not create a second Agent loop or a serialized Agent-definition language.

## Definition and Build

Use `HarnessBuilder.build_code()` for direct application composition:

```python
from converge_agent_harness import AgentSpec, HarnessBuilder, ModelConfiguration

executable = HarnessBuilder().build_code(
    AgentSpec(
        model="logical:support",
        instructions="Answer concisely.",
        model_config=ModelConfiguration(context_window=200_000),
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
from converge_agent_harness import AgentDefinition, HarnessBuilder

agent_definition = AgentDefinition(
    agent=AgentSpec(model="logical:support"),
    output_type=str,
    model=model,
    capabilities=capabilities,
)
executable = HarnessBuilder().build(agent_definition)
```

Both methods follow the same validation and construction path. Build is synchronous and inert with respect to model, Environment, and external provider I/O.

### Build-time values

An `AgentDefinition` fixes:

- the Harness `AgentSpec`, which remains a native Pydantic AI spec and may add resolved model characteristics;
- one output contract;
- a concrete or logical model selection;
- definition-selected Capabilities;
- trusted Harness middleware plugins;
- finite inline child definitions;
- self-healing and bounded model-recovery policy.

The output contract cannot change per run. Pass a Python output type or Pydantic AI `OutputSpec` through `output_type`, or use `AgentSpec.output_schema`; do not set both.

### Model configuration

`AgentSpec.model_config` holds resolved characteristics that complement Pydantic AI's provider `ModelProfile`; it is not provider request settings. Today it defines the context window plus proactive summarize and compaction ratios:

```python
spec = AgentSpec(
    model="logical:support",
    model_config=ModelConfiguration(
        context_window=200_000,
        proactive_context_management_threshold=0.65,
        compact_threshold=0.90,
    ),
)
```

When selected, `HandoffCapability()` derives its summarize reminder at 65% and `CompactionCapability()` derives its trigger at 90%. Explicit Capability token thresholds take precedence, and model configuration never enables either Capability by itself. A Host may resolve these values from its own preset catalog; the Harness does not infer a preset from the model name and does not yet ship concrete model declarations. Native Pydantic AI `AgentSpec` remains accepted when this extension is not needed.

## Mandatory Composition

Every executable receives one Harness-owned instance of each mandatory boundary:

- the function-tool execution boundary;
- message-integrity filtering;
- model-context coordination;
- model-ID resolution;
- model-request lifecycle events;
- usage attribution and reporting.

Application code must not add a second mandatory boundary. Optional request/history filters and feature Capabilities remain explicit definition choices.

## Fresh Run Bindings

`RunBindings` carries current, trusted run inputs:

```python
from converge_agent_harness import RunBindings

bindings = RunBindings.local(
    environment=environment_binding,
    model_binding=model_binding,
    model_context=model_context_binding,
    capabilities=run_capabilities,
    metadata={"request_kind": "interactive"},
)
```

`RunBindings.local()` supplies a local identity and a no-op Environment when omitted. Use it for embedded applications and tests. A Host can construct `RunBindings` directly with an exact `AgentInstanceContext`.

Create fresh bindings for every root, resumed, or child run. Do not persist or reuse live bindings as continuation state.

| Stable definition input     | Fresh run input                     |
| --------------------------- | ----------------------------------- |
| `AgentSpec`                 | Identity and Agent instance context |
| Output contract             | Environment binding                 |
| Agent behavior Capabilities | Model and model-context bindings    |
| Direct plugins              | Policy and provider collaborators   |
| Child topology              | Run-specific Capability selection   |
| Recovery policy             | Bounded non-authoritative metadata  |

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

| Status      | Meaning                                                     | Important fields                           |
| ----------- | ----------------------------------------------------------- | ------------------------------------------ |
| `completed` | A validated business output completed and cleanup succeeded | `output`, `state`, `usage`                 |
| `suspended` | Native deferred tools or approvals require later input      | `state`, `deferred`, `suspend_reason`      |
| `failed`    | The logical run ended with a safe normalized failure        | `failure`, optional safe `state` candidate |
| `cancelled` | Cancellation stopped the logical run                        | no business output                         |

Use `raise_for_status()` when only completion is acceptable. Use `output_or_raise()` to both validate status and return the typed output. Inspect `status`, `failure`, or `deferred` when the application handles other outcomes explicitly.

`all_messages()` returns the complete detached message history represented by the result. `new_messages()` returns only messages added by that logical run.

## Stream Events

`stream()` is lazy, single-entry, and single-consumer:

```python
from converge_agent_harness import HarnessEvent, HarnessRunResultEvent

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
| Exact provider-history incompatibility                | Harness `SelfHealingModel` wrapper                               |
| Interrupted model attempt inside one live logical run | `ModelRecoveryPolicy` and `HarnessRunStream`                     |
| Worker/process loss, durable replay, or delivery      | Embedding Host                                                   |

Self-healing is enabled by default and performs only supported one-shot history repairs. It is not a retry for arbitrary model or tool exceptions. Semantic model recovery is disabled by default; opt in with a bounded `ModelRecoveryPolicy` on the definition when continuing an interrupted model attempt is valid for the application.

Recovery never makes uncertain external side effects exactly once. When a tool or provider mutation may have been dispatched without an authoritative result, reconcile current provider state before retrying.

## Usage

`result.usage` and `stream.usage` use Pydantic AI's native `RunUsage`. `result.usage_records` additionally contains detached Harness attribution records for committed model requests and provider-reported usage.

Provider integrations can record stable non-model receipts through `AgentContext.record_provider_usage()`. A fresh `ModelCostRunCapability` can add application-selected pricing. Durable aggregation, reconciliation, billing, and exporter delivery remain Host concerns.

## Correlation

- `thread_id` identifies one independently advancing history. Resume preserves it; `HarnessState.fork()` creates another.
- `run_id` identifies one process-local logical execution. Every new root, resume, or replacement run receives a new value.
- inner model attempts and inline child runs have their own narrower correlation and do not replace Thread or root Run identity.

Identifiers are observations and routing keys, not authority.

## Cleanup

Use `async with executable` or call `await executable.close()` exactly when application ownership ends. Closing is idempotent and recursively closes built child executables. A closed executable cannot start another run.

A clean `HarnessRunResultEvent` means Harness-owned cleanup completed. It is still only a process-local candidate; a Host decides when to persist a checkpoint, commit durable completion, or deliver output externally.
