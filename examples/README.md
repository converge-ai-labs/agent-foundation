# Examples

This directory contains runnable, tested examples of public Agent Foundation extension points. Each example is deliberately small enough to read end to end, but it uses the same packaging and runtime boundaries that an application or integration package uses.

## Start Here

| Goal                                         | Example                                                                        | What it demonstrates                                                                                                          |
| -------------------------------------------- | ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| Embed Harness in a normal Python application | [General Agent example](general-agent/README.md)                               | Minimal build, fresh local bindings, one run, normalized result, and an injected offline model                                |
| Run a local Agent through first-party tools  | [Local Agent example](local-agent/README.md)                                   | Offline model, Direct Local files, working state, structured suspension, fresh bindings, and resume                           |
| Persist and resume a Harness run             | [Host persistence example](hosting/README.md)                                  | Host-owned Execution/ExecutionAttempt fencing, selected `HarnessState`, fresh bindings, replacement runs, and terminal commit |
| Wrap the complete Harness run                | [Integration package examples](plugins/README.md#harness-plugin)               | Preferred YAML/JSON configuration, runtime directory discovery, direct objects, parameters, and per-run isolation             |
| Publish and compose an Environment provider  | [Integration package examples](plugins/README.md#environment-provider-factory) | Selected entry-point and explicit factory modes, Host JSON configuration, provider bindings, and multi-environment routing    |

Run every integration package example and its focused checks from the repository root:

```bash
make examples-check-all
```

Or enter the project and run one path at a time:

```bash
cd examples/general-agent
uv sync --locked
uv run general-agent-example
uv run pytest

cd ../plugins
uv sync --locked
uv run plugin-example-environment-entrypoint
uv run plugin-example-environment-code
uv run plugin-example-harness-entrypoint
uv run plugin-example-harness-code
uv run pytest

cd ../hosting
uv sync --locked
uv run host-persistence-example
uv run pytest

cd ../local-agent
uv sync --locked
uv run local-agent-example
uv run pytest
```

No example needs a model API key or network access after dependencies are installed.

## How to Use These Examples

1. Read the example README before copying code; it identifies the owning extension point and its trust boundary.
2. Run the demo to observe the complete integration path.
3. Read the implementation and then its focused test.
4. Copy only the module that matches your extension point and replace the example policy, configuration, and persistence choices with application-owned ones.

Examples are executable teaching material, not additional product APIs. Accepted behavior remains owned by [`spec/`](../spec/README.md), while user documentation remains under [`docs/`](../docs/index.md).
