# Agent Harness

`a13n-harness` is the process-local Pydantic AI execution foundation for Agent Foundation agents. The repository directory is `packages/agent-harness`, the Python distribution is `a13n-harness`, and the import package is `a13n_harness`.

## Capability composition

Agent definitions compose behavior through Pydantic AI Capabilities. The first-party feature Capabilities own lifecycle hooks and select pure Toolsets; the Toolsets depend only on provider-neutral ports such as `FileOperator`, `MediaReader`, `DocumentConverter`, and `WebClient`. Native MCP composition uses `pydantic_ai.capabilities.MCP` in `AgentSpec.capabilities` or as a trusted process-local Capability; the default Harness dependency includes local MCP client support rather than requiring a separate extra.

```python
from a13n_harness import (
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

Embedding code passes an `EnvironmentProvider` or entered `EnvironmentResource` directly through `run(..., environment=...)`, or supplies a named mixed mapping through `environments=...`. Source type defines ownership: the Harness owns a Provider through one complete ephemeral lifecycle, while it borrows one fresh attachment from an entered Host-owned Resource. Ordinary calls can omit `RunBindings`; advanced topology bindings and current provider collaborators use fresh `RunBindings.embedded()` values. General media URL reading, document conversion, and Web implementations stay behind typed run collaborators. Environment file multimedia understanding has built-in image, video, and audio Pydantic AI Agents selected by `A13N_HARNESS_*_UNDERSTANDING_MODEL`, with native support declared through the `model_config` construction key and read from `AgentSpec.model_configuration.capabilities`, plus a typed run collaborator available as an override. Static callers may import reusable Toolsets from `a13n_harness.toolsets`; their model-facing JSON results use named `TypedDict` contracts in the corresponding Toolset modules. Managed invocation policy and client-tool contracts are available from `a13n_harness.tools`.

The shell Toolset exposes exactly `shell_exec`, `shell_wait`, `shell_status`, `shell_input`, `shell_signal`, and `shell_kill`. `shell_exec(background=True)` uses a real Environment process and returns an opaque `process-N` reference; the Harness never simulates background execution with an in-process task. `ProcessManager` stores the exact portable provider process identity and unread output offsets in `AgentContextState`, so a compatible Thread continuation can lazily rebind the same process through a fresh current Environment. During every root or child Turn it waits on the real provider process, can enqueue a completion hint, and invokes optional `ProcessEventHook` values. Turn cleanup does not kill the process. The Host must separately preserve the provider resource and output, and must use provider events or polling to wake a later Run; aliases never retarget a restored process.

## Model construction

`a13n_harness.infer_model()` is an optional construction helper that always returns a native Pydantic AI `Model`. It normalizes supported compatibility aliases, accepts caller-owned ordinary or gateway provider factories, applies synchronous Model patches in order, and can wrap the result with case-insensitive common request-header defaults. Request-specific native headers win. `create_model_http_client()` creates a caller-owned `httpx2` provider client with transport timeouts and Pydantic AI's Tenacity retry transport. Its default policy retries transient transport failures and HTTP `429`/`502`/`503`/`504` up to five total attempts, respects `Retry-After`, and can be customized with `ModelHttpRetryConfig` or disabled with `retry=None`; request headers remain native `ModelSettings.extra_headers`. Callers can bypass both helpers and pass any self-constructed Model to `HarnessBuilder.build(model=...)`; provider credentials, clients, retries, and resource lifecycle remain owned by the caller's integration.

## Execution boundary and filters

Every built Agent includes one outer `ToolExecutionBoundaryCapability` and one innermost `MessageIntegrityFilterCapability`; application definitions do not install either boundary manually. First-party Toolsets own semantic progressive disclosure and can use the shared typed helper to save a fuller redacted result in a run-private model-readable file. The execution boundary preserves ordinary Pydantic dispatch and remains the sole mandatory final validation, redaction, and larger hard-size fallback for locally executable function-tool text/JSON results. Metadata-absent tools, including locally executed dynamic MCP tools, default to explicit truncation when oversized. Complete trusted `HarnessToolMetadata` additionally selects managed authorization, credentials, grants, retry, and invocation events.

Request/history filters live in `a13n_harness.filters`. Message integrity is mandatory; `ContentFilterCapability` and `ColdStartFilterCapability` are optional definition-selected filters for native multimodal request compatibility and cold-cache reduction of already-consumed tool-result strings. `SelfHealingModelCapability` is the recommended explicit selection for known one-shot provider-history repairs: it installs `SelfHealingModel` around the final effective request Model. It is not enabled implicitly. Interrupted-stream `ModelAttempt` recovery remains in the Harness rather than a request filter or the self-healing wrapper.

Every `HarnessState` carries the stable `thread_id` of one independently advancing history. Resume preserves that ID, `HarnessState.fork()` creates a new one, and each process-local `HarnessRunStream`, event, and result pairs it with a fresh `run_id`. The stream's public union is `HarnessStreamEvent`.

## Runnable examples and guides

The [Agent Application example](../../examples/agent-app/README.md) is one repeated conversation with Harness stream output, successful-turn state persistence, recovery after application restart, and one Harness-owned temporary local Environment per turn. The [plugin integration example](../../examples/plugins/README.md) publishes and selects real Harness and Environment extension distributions.

The [Agent Harness user guide](../../docs/agent-harness/index.md) covers installation, first-party feature families, filters, Environments, results, resume, and usage. The [plugin guide](../../docs/agent-harness/plugins.md) covers packaging, configuration, lifecycle, and discovery from a Host-managed plugin directory without a process restart.

## Versioning

Agent Harness, `a13n-environment-provider`, and `a13n-stream-protocol` form the Harness release group. A `release/harness-v<version>` tag publishes all three distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`. Published Harness metadata pins the exact Provider version, and published Stream Protocol metadata pins the exact Harness version.

The accepted architecture and public contract are defined in the [Agent Harness specification](../../spec/agent-harness/README.md).
