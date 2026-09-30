# Agent Harness

Harness (`a13n-harness`) is the embeddable execution SDK behind [Harness UI](../a13n-harness-ui/README.md) and [Service](../a13n-service/README.md). It builds reusable agents, runs them with current tools and Environments, streams observations, and returns state for continuation. Start with the [offline quickstart](../../docs/a13n-harness/getting-started.md); the Python import is `a13n_harness`.

## Capability composition

Build an `AgentSpec` with only the behavior your application needs. For example, to expose Environment tools and working state:

```python
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import WorkingStateCapability
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration

executable = HarnessBuilder().build(
    AgentSpec(model="openai-responses:gpt-5"),
    output_type=str,
    capabilities=(
        DynamicEnvironmentCapability(DynamicEnvironmentConfiguration()),
        WorkingStateCapability(),
    ),
)
```

Supply a fresh Provider `Environment` to each `run(..., environment=...)`, or use `environments={...}` for named mounts. Harness enters and closes adapters; your application selects their configuration, current state, access policy, and when to destroy a backing target. See [Environments](../../docs/a13n-harness/environments.md) and [Capabilities](../../docs/a13n-harness/capabilities.md).

## Model construction

Pass a native model directly through `HarnessBuilder.build(model=...)`, or select one in `AgentSpec.model`. `a13n_harness.infer_model()` constructs a model from supported aliases and caller-owned provider factories. `create_model_http_client()` builds a caller-owned `httpx2` client for provider requests. Configure credentials and close owned clients in your application. See [Models](../../docs/a13n-harness/models.md) and [Model authentication](../../docs/a13n-harness/model-authentication.md).

## Execution boundary and filters

Each built Agent includes the tool execution boundary, message integrity filter, and request-only image preparation. Image preparation splits tall static images, compresses images to encoded-byte and dimension limits, and retains the newest images without changing saved history or original files. Configure the selected model's `HarnessModelCharacteristics.image_input` with `ImageInputPolicy`, or set `image_input=None` to disable automatic preparation. See [Context](../../docs/a13n-harness/context.md#filters).

Add Capabilities for policy, context, memory, delegation, or model recovery. Toolsets are available from `a13n_harness.toolsets`, and managed tool contracts from `a13n_harness.tools`.

A completed Run returns a `HarnessState` with a stable `thread_id`. Pass it as `previous_state` to continue the Thread; each new Run receives a fresh `run_id`. The Host persists accepted state and reconstructs credentials and other current authority on resume. [State and resume](../../docs/a13n-harness/state-and-resume.md) covers serialization and interrupted calls.

## Runnable examples and guides

- [Agent application](../../examples/agent-app/README.md): offline streaming turns, checkpointing, and restart recovery.
- [Environment Providers](../../examples/environment-provider/README.md): construction, re-entry, and lifecycle.
- [Plugins and extensions](../../examples/plugins/README.md): packaged middleware, Capabilities, and Environment integrations.
- [Harness guide](../../docs/a13n-harness/index.md): the public API by task.

## Versioning

Harness and `a13n-stream-protocol` publish together at one exact version. Harness UI releases independently. The [Harness specification](../../spec/a13n-harness/README.md) owns the accepted contracts.
