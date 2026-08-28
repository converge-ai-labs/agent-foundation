# Examples

This directory contains runnable, tested examples of public Agent Foundation extension points. Each independent project is small enough to read end to end while using the same packaging and runtime boundaries as an application or integration package.

## Start Here

| Goal                                        | Example                                                                        | What it demonstrates                                                                                              |
| ------------------------------------------- | ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| Build a recoverable Agent application       | [Agent application example](agent-app/README.md)                               | Offline streaming turns, successful-turn state persistence, restart recovery, and temporary Environment cleanup   |
| Wrap the complete Harness run               | [Integration package examples](plugins/README.md#harness-plugin)               | YAML/JSON configuration, runtime directory discovery, direct objects, parameters, and per-run isolation           |
| Publish and compose an Environment provider | [Integration package examples](plugins/README.md#environment-provider-factory) | Entry-point and explicit factory modes, Host JSON configuration, provider bindings, and multi-environment routing |

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
