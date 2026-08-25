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
    HandoffCapability(),
    WorkingStateCapability(),
    UserInteractionCapability(),
)
```

Embedding code supplies current Environment and provider collaborators through `RunBindings`. Media, document, and Web implementations stay behind their typed run collaborators rather than becoming dependencies of the Harness core. Static callers may import reusable Toolsets from `converge_agent_harness.toolsets`; their model-facing JSON results use named `TypedDict` contracts in the corresponding Toolset modules. Managed invocation policy and client-tool contracts are available from `converge_agent_harness.tools`.

## Execution boundary and filters

Every built Agent includes one outer `ToolExecutionBoundaryCapability` and one innermost `MessageIntegrityFilterCapability`; application definitions do not install either boundary manually. First-party Toolsets own semantic progressive disclosure and can use the shared typed helper to save a fuller redacted result in a run-private model-readable file. The execution boundary preserves ordinary Pydantic dispatch and remains the sole mandatory final validation, redaction, and larger hard-size fallback for locally executable function-tool text/JSON results. Complete trusted `HarnessToolMetadata` additionally selects managed authorization, credentials, grants, retry, and invocation events.

Request/history filters live in `converge_agent_harness.filters`. Message integrity is mandatory; `ContentFilterCapability` and `ColdStartFilterCapability` are optional definition-selected filters for native multimodal request compatibility and cold-cache reduction of already-consumed tool-result strings. Model-specific one-shot history repair remains in `SelfHealingModel`, and interrupted-stream `ModelAttempt` recovery remains in the Harness rather than either filter.

Every `HarnessState` carries the stable `thread_id` of one independently advancing history. Resume preserves that ID, `HarnessState.fork()` creates a new one, and each process-local `HarnessRunStream`, event, and result pairs it with a fresh `run_id`. The stream's public union is `HarnessStreamEvent`.

## Runnable examples and guides

The [Local Agent example](../../examples/local-agent/README.md) builds an offline Agent, executes a managed file tool through a Direct Local Environment, records working state, suspends for structured input, and resumes with fresh bindings. The [plugin integration example](../../examples/plugins/README.md) publishes and selects a real Harness plugin distribution.

The [Agent Harness user guide](../../docs/agent-harness/index.md) covers installation, first-party feature families, filters, Environments, results, resume, and usage. The [plugin guide](../../docs/agent-harness/plugins.md) covers packaging, configuration, lifecycle, and discovery from a Host-managed plugin directory without a process restart.

## Versioning

Agent Harness and `converge-agent-stream-protocol` form the Harness release group. A `release/harness-v<version>` tag publishes both distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`. The published Stream Protocol artifact pins this exact Harness version; Agent UI releases independently and selects a Harness release explicitly.

The accepted architecture and public contract are defined in the [Agent Harness specification](../../spec/agent-harness/README.md).
