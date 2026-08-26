# Agent Harness

`converge-agent-harness` is a process-local, code-first runtime for building Pydantic AI agents with provider-neutral context, tools, Environments, continuation state, events, and usage attribution.

## Install

```bash
pip install converge-agent-harness
```

Agent definitions are synchronous and trusted Python values. Runs, streams, provider operations, and cleanup are asynchronous.

## Build and Run

A minimal offline Agent needs only an `AgentSpec`, a model, the Harness builder, and fresh run bindings:

```python
from collections.abc import AsyncIterator

from converge_agent_harness import HarnessBuilder, RunBindings
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> AsyncIterator[str]:
    del messages, info
    yield "offline result"


agent = HarnessBuilder().build_code(
    AgentSpec(model="logical:example"),
    output_type=str,
    model=FunctionModel(stream_function=respond),
)
async with agent:
    result = await agent.run("Hello", bindings=RunBindings.local())
    print(result.output_or_raise())
```

Use `async with agent` so every executable is closed deterministically.

For a complete integration with real local tools, working state, structured suspension, rebuild, and resume, run the [Local Agent example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/local-agent).

## Capability Composition

Definition-selected Capabilities own Agent-loop lifecycle and compose reusable Toolsets. Toolsets implement model-facing schemas over narrow provider-neutral ports.

| Capability family        | Reusable Toolset                | Provider or state boundary                                      |
| ------------------------ | ------------------------------- | --------------------------------------------------------------- |
| Dynamic Environment      | `FileToolset`, `ShellToolset`   | `BoundEnvironment` file, shell, process, and port facets        |
| Runtime and file context | None                            | `AgentContext`, Environment files, bounded instructions         |
| Handoff and compaction   | `HandoffToolset` for delegation | Built subagents and native message history                      |
| Skills                   | None                            | `SkillSource` and Environment-backed materialization            |
| Working state            | `WorkingStateToolset`           | Embedded state or fresh `TaskStateRunCapability`                |
| User interaction         | `UserInteractionToolset`        | Native deferred requests and `DeferredToolResume`               |
| Process monitoring       | `MonitoredProcessToolset`       | Fresh `MonitoredProcessRunCapability`                           |
| Media                    | `MediaToolset`                  | Fresh `MediaRunCapability` and `MediaReader`                    |
| Documents                | `DocumentsToolset`              | Fresh `DocumentsRunCapability` and `DocumentConverter`          |
| Web                      | `WebToolset`                    | Fresh `WebRunCapability`, live `WebPolicy`, and provider ports  |
| Inline delegation        | `DelegationToolset`             | Declared child Agents and fresh `DelegationRunCapability`       |
| CodeAct                  | `CodeActToolset`                | Run-local Monty runtime and typed eligible-tool policy          |
| Usage                    | None                            | Native `RunUsage`, `RunUsageLedger`, and optional model pricing |

Provider-backed clients are fresh trusted run attachments. They do not enter definitions or `HarnessState`, and the Harness core does not depend on vendor SDKs.

## Inline Delegation and CodeAct

`DelegationCapability` exposes one blocking `delegate` tool over the finite `SubagentDefinition` collection. Each invocation runs the selected child through its canonical `ExecutableAgent.stream()` path and waits for a complete result. The Host supplies fresh child authority with `DelegationRunCapability`; a returned `child_instance_id` can continue only that child's private nested `HarnessState`.

Background submission, workers, receipts, waiting, cancellation routing, and durable delivery remain Host responsibilities. The Harness does not expose a background scheduler or background-delegation protocol.

`CodeActCapability` optionally exposes `run_code` and `run_program`. Eligible host tools must be published explicitly by their owner through a typed policy:

```python
from converge_agent_harness import (
    CodeActCapability,
    CodeActPolicyToolset,
    CodeActToolPolicy,
)
from pydantic_ai.capabilities import Capability
from pydantic_ai.toolsets import FunctionToolset


def double(value: int) -> int:
    return value * 2


codeact_tools = Capability(
    id="math-tools",
    toolsets=[
        CodeActPolicyToolset(
            wrapped=FunctionToolset([double], id="math-functions"),
            policy=CodeActToolPolicy(tools={"double": True}),
            reject_unknown_tools=True,
        )
    ],
)
capabilities = (codeact_tools, CodeActCapability())
```

Restricted code receives no ambient filesystem, network, process, environment, credentials, or clock access. Nested calls validate and execute through the active final Pydantic AI `ToolManager`, so ordinary Capability hooks, the Harness tool-execution boundary, policy, events, and usage remain authoritative. `run_code` state lasts only for the current logical run and can be cleared with `restart=True`; `run_program` rereads a `*.codeact.py` file through the current Environment and uses a fresh interpreter session.

## Mandatory Boundaries and Optional Filters

Every built Agent receives exactly one code-owned outer `ToolExecutionBoundaryCapability` and one innermost `MessageIntegrityFilterCapability`. Do not add another function-result wrapper. The execution boundary applies one redaction, bounding, and spill policy to locally executable function results; complete managed metadata additionally enables authorization, credentials, grants, dispatch retry, and invocation events.

Content and ColdStart filters are optional and selected by the definition:

```python
from converge_agent_harness import (
    ColdStartFilterCapability,
    ColdStartFilterConfiguration,
    ContentFilterCapability,
    ContentFilterConfiguration,
)

capabilities = (
    ContentFilterCapability(
        ContentFilterConfiguration(
            accepted_media=frozenset({"image", "document"}),
            max_media_items=16,
        )
    ),
    ColdStartFilterCapability(
        ColdStartFilterConfiguration(
            idle_seconds=3_600,
            max_string_chars=8_192,
            keep_head_chars=2_048,
            keep_tail_chars=2_048,
        )
    ),
)
```

Choose these only when the target model/provider needs the compatibility behavior. `SelfHealingModel` remains a precise one-shot provider-history repair path. Transport retry and semantic recovery are not Filters.

## Environments and Direct Local

Application code supplies a fresh `EnvironmentRunBinding` through `RunBindings`. The aggregate exposes provider-neutral file, shell, process, output, and port contracts. `DynamicEnvironmentCapability` projects the selected facets into model-facing tools; it does not create authority or manage provider lifecycle.

Direct Local is one provider binding for trusted embedded applications and teaching examples. Configure it with `DirectLocalEnvironmentConfiguration`, place it in an `EnvironmentTopologyRequest`, and create the aggregate with `create_environment_run_binding()`. Model calls to managed Environment tools still require a fresh `InvocationPolicyCapability`; provider availability is not authorization.

Remote sandbox or integration packages implement the same Environment interfaces without changing Agent definitions or Toolsets.

## Results, Resume, and Usage

`run()` returns one `HarnessRunResult`; `stream()` exposes the canonical single-consumer `HarnessStreamEvent` sequence of `HarnessEvent` values followed by one terminal `HarnessRunResultEvent`. The stream, every event, and the result expose both `thread_id` and `run_id`: `thread_id` identifies the independently advancing history, while `run_id` identifies only the current process-local execution.

A suspended or safely failed result may include a detached `HarnessState` candidate. A later run uses a newly built or existing executable, fresh `RunBindings`, the selected `previous_state`, and, for deferred tools, a correlated `DeferredToolResume`. Resume preserves `HarnessState.thread_id`; `HarnessState.fork()` copies portable continuation data into a new Thread with a new ID.

`HarnessState` is continuation data, not Host lifecycle authority. It excludes live providers, credentials, policy, execution leases, durable task systems, and cross-run accounting.

Pydantic AI `RunUsage` remains the sole process-local model-usage accumulator. `RunUsageLedger` preserves immutable model and provider attribution records for the logical run; durable aggregation, reconciliation, pricing catalogs, and billing belong to the Host.

## Next Steps

- Run the [Local Agent example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/local-agent).
- [Integrate Skill discovery](skills.md) with a direct FileOperator or a revision-bound Environment catalog.
- [Publish and load Harness plugins](plugins.md), including from a Host-managed directory without restarting the process.
- Read the [package README](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/agent-harness).
- Consult the [Agent Harness specification](https://github.com/converge-ai-labs/agent-foundation/tree/main/spec/agent-harness) for normative architecture and compatibility contracts.
