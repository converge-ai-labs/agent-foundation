---
title: Agents and Runs
description: Build an Agent once, run a turn, inspect its outcome, and stream the next turn.
---

Build one executable for an Agent's behavior and output contract. Call `run()` when you need the final result, or `stream()` when you need events as work happens. Each call gets a fresh Run context. Passing the previous result's state continues its Thread.

## Definition and Build

Use the checkout and environment from [Getting started](getting-started.md). Save this offline program as `runs.py` in the checkout root:

```python title="runs.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessEvent,
    HarnessRunResultEvent,
    RunBindings,
)
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    del info
    turn = 1 + sum(isinstance(message, ModelResponse) for message in messages)
    yield f"turn-{turn}"


async def main() -> None:
    executable = HarnessBuilder().build(
        AgentSpec(system_prompt="Answer concisely."),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    first = await executable.run("Draft a plan")
    print(first.output_or_raise())
    if first.state is None:
        raise RuntimeError("Completed turn has no continuation state")

    async with executable.stream(
        "Review the plan",
        previous_state=first.state,
        bindings=RunBindings.embedded(metadata={"stage": "review"}),
    ) as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent):
                print(f"event {item.sequence}")
            elif isinstance(item, HarnessRunResultEvent):
                second = item.result
                print(second.output_or_raise())
                assert second.thread_id == first.thread_id
                assert second.run_id != first.run_id


if __name__ == "__main__":
    asyncio.run(main())
```

Run `uv run python runs.py`. Expect `turn-1`, event sequence lines, then `turn-2`. The deterministic Model counts earlier responses; the assertions check that the two Runs share one Thread.

The builder composes trusted Python objects into a reusable executable. Harness wraps native Agent execution with state, resource, and event handling. Building the executable does not make a Model request.

### Build-time values

Choose these once for each executable:

| Input                                                                   | Put here                             |
| ----------------------------------------------------------------------- | ------------------------------------ |
| Instructions, static system prompt, Model ID, settings, default budgets | `AgentSpec`                          |
| Python output type or native `OutputSpec`                               | `output_type`                        |
| A concrete Model instead of a string ID                                 | `model`, with `AgentSpec.model=None` |
| Tools and feature behavior                                              | `capabilities`                       |
| Trusted Harness middleware                                              | `plugins`                            |
| Named child definitions                                                 | `subagents`                          |
| Optional interrupted-Model retry policy                                 | `model_recovery`                     |

The output contract is fixed at build time. Choose `output_type`, or set `AgentSpec.output_schema` and leave `output_type=None`. Supplying both is rejected. String Model selection belongs in `AgentSpec.model`; a concrete Model belongs in the builder's `model` argument. Choose one of those sources too.

When an application assembles definitions separately, replace the builder call in `runs.py` with this equivalent form and add the import:

```python
from a13n_harness import AgentDefinition

agent_definition = AgentDefinition(
    agent=AgentSpec(system_prompt="Answer concisely."),
    output_type=str,
    model=FunctionModel(stream_function=respond),
)
executable = HarnessBuilder().build(agent_definition)
```

`AgentDefinition` holds process-local objects. A Host that stores Agent configuration reconstructs them before building. The builder also selects the default-on cost policy and pricing catalog; see [Usage](#usage).

## Run to a Result

For a simple application, `result.output_or_raise()` checks completion and returns the typed output. When the application supports other outcomes, branch on `result.status`:

| Status      | Application action                                                   | Useful fields                         |
| ----------- | -------------------------------------------------------------------- | ------------------------------------- |
| `completed` | Accept output and select a checkpoint                                | `output`, `state`, `usage`            |
| `suspended` | Collect deferred results or approvals, then resume                   | `state`, `deferred`, `suspend_reason` |
| `failed`    | Inspect the failure and decide whether to retain its state candidate | `failure`, optional `state`           |
| `cancelled` | Handle cancellation without treating it as completed output          | optional `state`                      |

`raise_for_status()` checks completion without returning output. `all_messages()` returns detached complete history; `new_messages()` returns messages added by this logical Run. Saving a selected checkpoint is the application's responsibility; follow [State and resume](state-and-resume.md).

Roots and Host-managed children support deferred tools when `RunBindings.deferred_tools_supported=True`, the default. Built-in inline children disable them. With support disabled, dynamic deferral is denied inside the Model loop; an unexpected terminal deferral becomes a `deferred_tools_unsupported` failure. See [Client tools](client-tools.md) for the suspension/resume workflow.

## Stream Events

The example consumes two public item types:

- `HarnessEvent` carries a native Agent event or Harness extension event.
- `HarnessRunResultEvent` carries the sole terminal result after owned Run resources close successfully.

Each item has the root `thread_id`, `run_id`, and a monotonically increasing `sequence`. Inline child observations can appear in that stream with their child correlation preserved. Use [Stream Protocol](../a13n-stream-protocol/index.md) to project these observations into AG-UI events.

A stream has one entry and one consumer. Iteration starts Model execution; entering and exiting without iteration does not. Keep the entire consumer inside `async with`, including application callbacks that can raise or stop early.

## Fresh Run Bindings

The example omits bindings for the first turn and creates them for the second. Omission creates fresh embedded bindings and an empty Environment runtime when no Environment input is supplied.

Use `RunBindings.embedded()` when adding current application collaborators or metadata. For example, inside `main()` after building the executable:

```python
from a13n_harness import HarnessObservationContext

bindings = RunBindings.embedded(
    metadata={"request_kind": "interactive"},
    observation=HarnessObservationContext(
        name="interactive-agent-run",
        labels=("interactive",),
        metadata={"channel": "web"},
    ),
)
result = await executable.run("Summarize the change", bindings=bindings)
```

Create fresh bindings for each root, resumed, or child Run. A Host can construct `RunBindings` directly with its `AgentInstanceContext`. Bindings supply current identity, Environment authority, Model resolution, invocation policy, and feature collaborators. Metadata and observation labels describe the Run; they grant no permissions.

Feature fields include `web`, `document_converter`, `file_media_understanding`, `skill_selection`, `task_state`, and `client_toolsets`. Select the corresponding feature Capability in the definition, then supply its typed collaborator or override here. `None` retains selection defaults; an empty Skill set or client-tool tuple selects none. Run `capabilities` are limited to supported invocation-policy and MCP composition, not arbitrary feature activation.

The Host owns collaborator lifetimes, including deliberately shared transports. Keep live bindings out of saved continuation data. [Tools and dependencies](tools-and-dependencies.md) explains where application clients and per-Run resources belong.

## Input

Pass a string or another supported native input directly to `run()` or `stream()`. Use `input_factory` when producing input requires the current entered Environment. In `runs.py`, replace the first `run()` call with:

```python
async def make_input(preparation):
    return f"Describe the task for Run {preparation.run_id}"

first = await executable.run(input_factory=make_input)
```

`input` and `input_factory` are mutually exclusive. The factory runs after initial Environment registration and before Model execution; plugins then receive normalized semantic input. Target activation can still be lazy. The Host supplies authoritative provider state through fresh bindings rather than restoring a provider from saved observations after entry.

## Active Stream Operations

Use these on an entered stream from trusted application code:

| Operation                                  | Purpose                                                           |
| ------------------------------------------ | ----------------------------------------------------------------- |
| `stream.context`                           | Access the fresh `AgentContext`                                   |
| `stream.usage`                             | Read a detached summary of current local usage                    |
| `await stream.export_state()`              | Obtain the latest safe portable checkpoint candidate              |
| `await stream.steer(input, input_id=None)` | Enqueue non-empty user content while an inner Model Run is active |
| `stream.cancel()`                          | Request semantic cancellation                                     |
| `stream.result`                            | Read the result after its terminal event was delivered            |

`steer()` returns an enqueue ID and uses the native active-run `priority="asap"` queue. A supplied `input_id` is recorded when delivered; `steering_input_ids(state.message_history)` from `a13n_harness.capabilities.steering` finds delivered IDs in exported history. An enqueue receipt alone does not prove durable delivery.

Automatic compaction retains accepted initial and steering inputs for replay, including structured and multimodal content. That favors preserving user context over exact-once replay. For interrupted deferred input, pair the checkpoint with `stream.pending_deferred_input`; see [State and resume](state-and-resume.md#recover-an-interrupted-deferred-resume).

## Configure the definition

The following fragments change definition settings before building. Add them to the construction part of `runs.py`, or use them in your application's definition factory.

### Refine a loaded preset

Create `research-agent.yaml` in the checkout root:

```yaml title="research-agent.yaml"
instructions: Answer with concise findings.
toolset_instructions: true
```

Load it and create a local variant:

```python
preset = AgentSpec.from_file("research-agent.yaml")
local = preset.with_updates(
    model_settings={"temperature": 0.1, "max_tokens": 8_000},
    toolset_instructions=False,
)
```

Pass `local` instead of `AgentSpec(...)` to the builder. `with_updates()` validates an independent deep copy and replaces complete top-level fields. To merge nested settings, construct the merged field explicitly. Unknown fields, duplicate alias/name updates, and invalid values fail immediately. The optional positional mapping supports serialization aliases such as `model_characteristics` and `$schema`; keyword arguments use Python field names.

### System prompt and instructions

Use `system_prompt` for static definition-owned blocks. A list preserves order; `None` or an empty list supplies none:

```python
spec = AgentSpec(
    system_prompt=[
        "You are the support Agent.",
        "Answer with verified account information only.",
    ],
    instructions="Follow the application policy.",
    toolset_instructions=False,
)
```

On a later Run over saved history, Harness replaces historical system-prompt parts with the current definition's blocks. Removing the prompt removes the old blocks. A provider-suspended response is still an in-progress native request: resume it with a compatible definition.

`instructions` keeps its per-request lifecycle. Capability guidance also remains instructions. Toolsets contribute guidance for their active tools by default; `toolset_instructions=False` suppresses only those Toolset-owned instruction blocks. Tool schemas, availability, explicit Agent instructions, and Capability guidance remain unchanged.

Override that switch for one Run with `RunBindings.embedded(toolset_instructions=True)`. The binding's default `None` inherits the definition setting.

### Usage limits and retries

Set a normal workload budget on the definition:

```python
from pydantic_ai.usage import UsageLimits

spec = AgentSpec(
    usage_limits=UsageLimits(request_limit=100, total_tokens_limit=200_000),
    retries={"tools": 2, "output": 1},
)
```

A Run's `usage_limits=UsageLimits(...)` replaces the whole definition value. Omitting it uses the definition budget. Default Harness limits allow 1,000 Model requests per logical Run, with token, tool-call, and cost limits unset. Use `UsageLimits(request_limit=None)` to remove the request ceiling explicitly. Native Pydantic AI specs receive the same Harness defaults.

Inline children combine their own definition and edge limits by taking the strictest value per field. They have independent usage accumulators; the parent's limit is not a tree-wide budget.

Keep the retry mechanisms separate:

| Setting                             | Default              | Applies to                                        |
| ----------------------------------- | -------------------- | ------------------------------------------------- |
| `AgentSpec.retries` tools category  | 5 retries            | Model correction after function-tool failures     |
| `AgentSpec.retries` output category | 3 retries            | Model correction after output-validation failures |
| `ModelRecoveryPolicy`               | Disabled             | Recognized transient Model-request interruption   |
| Provider/client retry settings      | Integration-specific | Transport requests                                |

For `AgentSpec.retries`, an integer sets both categories, including zero. A mapping replaces only supplied categories; omitted categories keep Harness defaults. Explicit Tool or Toolset retry limits take precedence. Five tool retries permit five corrections; the sixth failure exhausts that budget. See [Recovery Layers](#recovery-layers) before enabling interrupted-Model recovery.

### Cold-start retention

After at least one hour since the latest Model response, the default cold-start filter shortens oversized strings in consumed tool results. Pending results, user input, and thinking stay intact. This reduces old history at the cost of changing its cache prefix; it does not detect provider cache expiry.

```python
from a13n_harness.filters import ColdStartFilterConfiguration

spec = AgentSpec(cold_start_filter=ColdStartFilterConfiguration(idle_seconds=3_600))
disabled = spec.with_updates(cold_start_filter=None)
```

Successful direct `view` results for files under published Skill directories receive a marker exempting them from this trimming. The marker survives saved-history resume. Initial output limits and later compaction or handoff still apply; a marked result can be incomplete. Older unmarked results and combined CodeAct output follow ordinary trimming.

Native specs receive the same default filter. `None` disables automatic installation; it leaves an explicitly composed `ColdStartFilterCapability` and its policy in place.

### Model selection

Choose a string Model ID or a concrete provider Model using [Model selection](models.md#model-selection).

### Model authoring aliases

Resolve application-friendly names with [Model authoring aliases](models.md#model-authoring-aliases).

### Model characteristics

Supply context-window and media characteristics through [Model characteristics](models.md#model-characteristics).

### Automatic model request affinity

See [Model request affinity](models.md#model-request-affinity) for Thread-derived provider correlation.

## Recovery Layers

Start with normal status handling. Add automatic recovery only for the failure class it owns:

| Failure                                        | Owner                               |
| ---------------------------------------------- | ----------------------------------- |
| Provider transport failure                     | Provider/client retry configuration |
| Recognized provider-history incompatibility    | Default-on self-healing             |
| Transient Model interruption within a live Run | Optional `ModelRecoveryPolicy`      |
| Worker loss, durable replay, output delivery   | Embedding Host                      |

Self-healing repairs recognized stale provider item IDs, invalid thinking history, and oversized inline media once before retrying the request. It applies to the final effective Model, including Run-resolved Models, inline children, and same-Agent compaction. Select `SelfHealingModelCapability(rules=...)` from `a13n_harness.models` to replace the rules. `HarnessBuilder(self_healing_enabled=False)` disables automatic installation while retaining explicitly selected self-healing Capabilities.

To enable bounded transient recovery, add this import and argument to the builder call in `runs.py`:

```python
from a13n_harness import ModelRecoveryPolicy

executable = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=respond),
    model_recovery=ModelRecoveryPolicy(enabled=True, max_attempts=3),
)
```

Here a failure streak allows three attempts including the first. A complete accepted primary response, including a tool-call response, resets the count and backoff. Partial output and auxiliary requests such as compaction do not. Permanent or unknown errors stop immediately. Keep usage limits to bound the whole Run; the policy default, when enabled without another limit, is five attempts per streak.

An interrupted attempt preserves emitted text as interrupted history. It removes unfinished thinking and tool arguments, and invalid provider-native call/return groups, while retaining surrounding recoverable text. Failed or cancelled Runs can export that filtered history for Host-selected continuation. Before repeating an uncertain external mutation, reconcile its outcome; recovery does not make external effects exactly once.

## Mandatory Composition

The builder installs the tool-execution boundary, message-integrity filtering, Model-context coordination, Model-ID resolution, Thread affinity, request lifecycle events, and usage reporting. Application code adds optional Capabilities and filters rather than duplicate instances of these mandatory components.

## Usage

Use [Usage, limits, and pricing](usage-and-limits.md) for usage records, provider attribution, catalog updates, and custom valuation. Cost policy is selected at build time, including an optional `pricing_catalog` argument.

## Correlation

Use `thread_id` to associate independently advancing history and `run_id` to identify one logical execution. Resume preserves the Thread ID and creates a new Run ID; `HarnessState.fork()` creates another Thread. Internal attempts and inline children retain their own narrower correlation. IDs are routing and observation values, not authorization.

## Cleanup

`ExecutableAgent` is reusable build output with no entered resource lifetime and no `close()` method. `run()` consumes and closes its internal stream. With `stream()`, the caller owns the `async with` scope; early exit, exceptions, and cancellation still trigger cleanup.

A clean terminal event confirms Harness-owned Run cleanup. The Host separately decides when to save state, commit durable completion, and deliver output. For that application boundary, continue with [Embedding in a Host](hosting.md).
