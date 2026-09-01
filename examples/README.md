# Examples

This directory contains runnable, tested examples of public Agent Foundation extension points. Each independent project is small enough to read end to end while using the same packaging and runtime boundaries as an application or integration package.

## Start Here

| Goal                                        | Example                                                                          | What it demonstrates                                                                                           |
| ------------------------------------------- | -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Build a recoverable Agent application       | [Agent application example](agent-app/README.md)                                 | Offline streaming turns, successful-turn state persistence, restart recovery, and a fresh Environment per turn |
| Extend Agent-loop behavior                  | [Custom Capability example](plugins/README.md#custom-capability)                 | Host-authorized `AgentSpec` reconstruction and direct code composition of one custom Capability                |
| Wrap the complete Harness run               | [Harness plugin example](plugins/README.md#harness-plugin)                       | YAML/JSON configuration, runtime directory discovery, direct objects, parameters, and per-run isolation        |
| Span the complete Environment lifecycle     | [Environment run-extension example](plugins/README.md#environment-run-extension) | Explicit factory selection, public Harness execution, aggregate setup, and reverse-order cleanup               |
| Publish and compose an Environment Provider | [Environment Provider example](plugins/README.md#environment-provider)           | Entry-point and explicit Provider catalogs, strict configuration, fresh adapters, and multi-mount routing      |

Run every example and its focused checks from the repository root:

```bash
make examples-check-all
```

Or enter each project and run its paths directly:

```bash
cd examples/agent-app
uv sync --locked
uv run agent-app-example
uv run agent-app-example "first turn" "second turn"
uv run pytest

cd ../plugins
uv sync --locked
uv run plugin-example-capability-agent-spec
uv run plugin-example-capability-code
uv run plugin-example-environment-entrypoint
uv run plugin-example-environment-code
uv run plugin-example-environment-extension-entrypoint
uv run plugin-example-environment-extension-code
uv run plugin-example-harness-entrypoint
uv run plugin-example-harness-code
uv run pytest
```

No example needs a model API key or network access after dependencies are installed.

## How to Use These Examples

1. Read the example README before copying code; it identifies the owning extension point and trust boundary.
2. Run the demo to observe the complete integration path.
3. Read the implementation and focused tests.
4. Copy only the module that matches your extension point and replace example policy, configuration, and persistence choices with application-owned ones.

Examples are executable teaching material, not additional product APIs. Accepted behavior remains owned by [`spec/`](../spec/README.md), while user documentation remains under [`docs/`](../docs/index.md).
