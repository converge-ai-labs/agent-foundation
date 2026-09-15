# Agent Harness

`a13n-harness` is the process-local Pydantic AI execution foundation for Agent Foundation agents. The repository directory is `packages/a13n-harness`, the Python distribution is `a13n-harness`, and the import package is `a13n_harness`.

## Capability composition

Agent definitions compose behavior through Pydantic AI Capabilities. The first-party feature Capabilities own lifecycle hooks and select pure Toolsets; the Toolsets depend only on provider-neutral ports such as `FileOperator`, `MediaReader`, `DocumentConverter`, and `WebClient`. Native MCP composition uses `pydantic_ai.capabilities.MCP` in `AgentSpec.capabilities` or as a trusted process-local Capability; the default Harness dependency includes local MCP client support rather than requiring a separate extra.

```python
from a13n_harness.capabilities import (
    HandoffCapability,
    RuntimeContextCapability,
    UserInteractionCapability,
    WorkingStateCapability,
)
from a13n_harness.environment import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
)
from a13n_harness.models import SelfHealingModelCapability

capabilities = (
    DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
    RuntimeContextCapability(),
    SelfHealingModelCapability(),
    HandoffCapability(),
    WorkingStateCapability(),
    UserInteractionCapability(),
)
```

Embedding code constructs one fresh Provider `Environment` per independent Run and passes it through `run(..., environment=...)`, or supplies a named mapping of `Environment` and `EnvironmentMount` values through `environments=...`. The Host selects Provider configuration and current state before construction; Harness enters the adapters, owns only Run-local routing and access policy, exports cached state, and closes adapters without destroying backing targets. Only explicit Host policy constructs a fresh lifecycle adapter and calls `destroy()`. Ordinary calls can omit `RunBindings`; an advanced `EnvironmentRuntime` uses fresh `RunBindings.embedded()` values. General media URL reading, document conversion, and Web implementations stay behind typed run collaborators. Environment file multimedia understanding has built-in image, video, and audio Pydantic AI Agents selected by `A13N_HARNESS_*_UNDERSTANDING_MODEL`, with native support declared through the `model_characteristics` construction key and read from `AgentSpec.model_characteristics.capabilities`, plus a typed run collaborator available as an override. Static callers may import reusable Toolsets from `a13n_harness.toolsets`; their model-facing JSON results use named `TypedDict` contracts in the corresponding Toolset modules. Managed invocation policy and client-tool contracts are available from `a13n_harness.tools`.

Long-term memory uses the opt-in `Mem0Capability` with a required Host-owned backend. OSS is the primary native HTTP backend; Platform has a separate native SDK adapter. The Host opens and closes transports; the Capability has no environment fallback or Run-owned client. See the [memory guide](../../docs/a13n-harness/context-and-memory.md#mem0-long-term-memory).

```python
from a13n_harness.capabilities import Mem0Capability, Mem0Scope
from a13n_harness.capabilities.mem0_backends import open_mem0_oss

async with open_mem0_oss(base_url=mem0_url, api_key=mem0_api_key) as backend:
    capabilities = (Mem0Capability(backend=backend, scope=Mem0Scope.USER),)
    # Build and execute Agents within this Host-owned lifetime.
```

`thread`, `agent`, and `user` scopes resolve only from trusted run context and identity claims; the model never supplies entity IDs. Automatic recall is bounded and input-only, and the optional Toolset exposes only search, list, and explicit add. The Harness performs no automatic terminal transcript extraction; the embedding Host can dispatch extraction after its own durable checkpoint commit.

The shell Toolset is derived from effective Environment actions. A shell-only Environment exposes completion-only `shell_exec`; a process-capable Environment exposes exactly `shell_exec`, `shell_wait`, `shell_input`, and `shell_signal`. `shell_exec` waits briefly and returns a Run-owned `process-*` reference only when the command remains live. `shell_wait` reads retained output non-consumingly from explicit caller offsets, while input and signal tools never read output. Run cleanup kills and releases every owned process before Environment close. Process references, offsets, and observations never enter `AgentContextState` or `HarnessState`, and continuation Runs cannot rebind them.

`SubagentCapability()` provides Harness-private inline delegation with no Host scheduler. It recursively runs declared children, borrows the active parent Environment mapping without re-entering or closing adapters, and stores complete child continuation only in parent Agent state. `SubagentCapability(async_enabled=True, operator=...)` instead exposes the standard six async tools through a Host-owned `SubagentOperator`. Harness resolves child Identity, context, and usage ceilings before dispatch but provides no default async manager, execution store, background task registry, parent-state mirror, or shutdown lifecycle.

## Model construction

`a13n_harness.infer_model()` is an optional construction helper that always returns a native Pydantic AI `Model`. It normalizes supported compatibility aliases, accepts caller-owned ordinary or gateway provider factories, applies synchronous Model patches in order, and can wrap the result with case-insensitive common request-header defaults. Request-specific native headers win. `create_model_http_client()` creates a caller-owned `httpx2` provider client with transport timeouts and Pydantic AI's Tenacity retry transport. Its default policy retries transient transport failures and HTTP `429`/`502`/`503`/`504` up to five total attempts, respects `Retry-After`, and can be customized with `ModelHttpRetryConfig` or disabled with `retry=None`; request headers remain native `ModelSettings.extra_headers`. Callers can bypass both helpers and pass any self-constructed Model to `HarnessBuilder.build(model=...)`; provider credentials, clients, retries, and resource lifecycle remain owned by the caller's integration.

## Execution boundary and filters

Every built Agent includes one outer `ToolExecutionBoundaryCapability` and one innermost `MessageIntegrityFilterCapability`; application definitions do not install either boundary manually. First-party Toolsets own semantic progressive disclosure and can use the shared typed helper to save a fuller redacted result in a run-private model-readable file. The execution boundary preserves ordinary Pydantic dispatch and remains the sole mandatory final validation, redaction, and larger hard-size fallback for locally executable function-tool text/JSON results. Metadata-absent tools, including locally executed dynamic MCP tools, default to explicit truncation when oversized. Complete trusted `HarnessToolMetadata` additionally selects managed authorization, credentials, grants, retry, and invocation events.

Request/history filters live in `a13n_harness.filters`. Message integrity is mandatory; `ContentFilterCapability` is optional for native multimodal request compatibility. `AgentSpec.cold_start_filter` enables cold compression of already-consumed tool-result strings after one hour of model inactivity by default; configure it with `ColdStartFilterConfiguration` or set it to `None` to disable automatic installation. `SelfHealingModelCapability` is the recommended explicit selection for known one-shot provider-history repairs: it installs `SelfHealingModel` around the final effective request Model. It is not enabled implicitly. Interrupted-stream `ModelAttempt` recovery remains in the Harness rather than a request filter or the self-healing wrapper.

Every `HarnessState` carries the stable `thread_id` of one independently advancing history. A trusted Host can select the initial ID through `HarnessState.new(thread_id=...)`; resume preserves it, and `HarnessState.fork(thread_id=...)` creates a distinct Host-selected or generated branch. Each process-local `HarnessRunStream`, event, and result pairs that stable identity with a fresh `run_id`. The stream's public union is `HarnessStreamEvent`.

## Runnable examples and guides

The [Agent Application example](../../examples/agent-app/README.md) is one repeated conversation with Harness stream output, successful-turn state persistence, recovery after application restart, and one fresh Direct Local Environment per turn. The [plugin integration example](../../examples/plugins/README.md) publishes and selects real Harness and Environment extension distributions.

The [Agent Harness user guide](../../docs/a13n-harness/index.md) covers installation, first-party feature families, filters, Environments, results, resume, and usage. The [plugin guide](../../docs/a13n-harness/plugins.md) covers packaging, configuration, lifecycle, and discovery from a Host-managed plugin directory without a process restart.

## Versioning

Agent Harness, `a13n-environment`, and `a13n-stream-protocol` form the Harness release group. A `release/a13n-harness-v<version>` tag publishes all three distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`. Published Harness metadata pins the exact Provider version, and published Stream Protocol metadata pins the exact Harness version.

The accepted architecture and public contract are defined in the [Agent Harness specification](../../spec/a13n-harness/README.md).
