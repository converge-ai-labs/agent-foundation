# Agent Harness

`converge-agent-harness` is the process-local Pydantic AI execution foundation for Converge agents. The repository directory is `packages/agent-harness`, the Python distribution is `converge-agent-harness`, and the import package is `converge_agent_harness`.

## Capability composition

Agent definitions compose behavior through Pydantic AI Capabilities. The first-party feature Capabilities own lifecycle hooks and select pure Toolsets; the Toolsets depend only on provider-neutral ports such as `FileOperator`, `MediaReader`, `DocumentConverter`, and `WebClient`.

```python
from converge_agent_harness import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    HandoffCapability,
    RuntimeContextCapability,
    SelfHealingModelCapability,
    UserInteractionCapability,
    WorkingStateCapability,
)

capabilities = (
    DynamicEnvironmentCapability(
        DynamicEnvironmentConfiguration(
            file_tools=True,
            shell_tools=True,
            process_tools=True,
            port_tools=False,
            max_reference_entries=1_024,
        )
    ),
    RuntimeContextCapability(),
    SelfHealingModelCapability(),
    HandoffCapability(),
    WorkingStateCapability(),
    UserInteractionCapability(),
)
```

Embedding code supplies current Environment and provider collaborators through `RunBindings`. Media, document, and Web implementations stay behind their typed run collaborators rather than becoming dependencies of the Harness core. Static callers may import reusable Toolsets from `converge_agent_harness.toolsets`; their model-facing JSON results use named `TypedDict` contracts in the corresponding Toolset modules. Managed invocation policy and client-tool contracts are available from `converge_agent_harness.tools`.

## Execution boundary and filters

Every built Agent includes one outer `ToolExecutionBoundaryCapability` and one innermost `MessageIntegrityFilterCapability`; application definitions do not install either boundary manually. First-party Toolsets own semantic progressive disclosure and can use the shared typed helper to save a fuller redacted result in a run-private model-readable file. The execution boundary preserves ordinary Pydantic dispatch and remains the sole mandatory final validation, redaction, and larger hard-size fallback for locally executable function-tool text/JSON results. Complete trusted `HarnessToolMetadata` additionally selects managed authorization, credentials, grants, retry, and invocation events.

Request/history filters live in `converge_agent_harness.filters`. Message integrity is mandatory; `ContentFilterCapability` and `ColdStartFilterCapability` are optional definition-selected filters for native multimodal request compatibility and cold-cache reduction of already-consumed tool-result strings. `SelfHealingModelCapability` is the recommended explicit selection for known one-shot provider-history repairs: it installs `SelfHealingModel` around the final effective request Model. It is not enabled implicitly. Interrupted-stream `ModelAttempt` recovery remains in the Harness rather than a request filter or the self-healing wrapper.

Every `HarnessState` carries the stable `thread_id` of one independently advancing history. Resume preserves that ID, `HarnessState.fork()` creates a new one, and each process-local `HarnessRunStream`, event, and result pairs it with a fresh `run_id`. The stream's public union is `HarnessStreamEvent`.

## Runnable examples and guides

The [Agent Application example](../../examples/agent-app/README.md) progresses from the minimal application-owned build and fresh bindings to managed Direct Local tools, structured resume, and Host-owned persistence and recovery. The [plugin integration example](../../examples/plugins/README.md) publishes and selects a real Harness plugin distribution.

The [Agent Harness user guide](../../docs/agent-harness/index.md) covers installation, first-party feature families, filters, Environments, results, resume, and usage. The [plugin guide](../../docs/agent-harness/plugins.md) covers packaging, configuration, lifecycle, and discovery from a Host-managed plugin directory without a process restart.

## Versioning

Agent Harness, `converge-agent-environment-provider`, and `converge-agent-stream-protocol` form the Harness release group. A `release/harness-v<version>` tag publishes all three distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`. Published Harness metadata pins the exact Provider version, and published Stream Protocol metadata pins the exact Harness version. Agent UI releases independently and selects a Harness release explicitly.

The accepted architecture and public contract are defined in the [Agent Harness specification](../../spec/agent-harness/README.md).
