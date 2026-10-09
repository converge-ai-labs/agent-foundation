---
title: Built-in Provider examples
sidebarTitle: Built-in examples
description: Runnable examples that use Direct Local, Local Envd, Docker, and remote Envd Providers directly.
---

The runnable [`examples/environment-provider`](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/environment-provider) project uses `a13n-environment` directly, without Harness or model credentials. See [Cloud Providers](providers.md#cloud-providers) for the cloud backends.

## Install

```bash
cd examples/environment-provider
uv sync --locked
```

External applications can install the released `a13n-environment` distribution.

## Direct Local

```bash
uv run environment-provider-example direct_local --workspace /absolute/path/to/workspace
```

The Host creates the directory, constructs a connector, opens an execution, writes and reads `/provider-example.txt`, then closes the execution. The directory remains. Direct Local shares the Host account and does not provide operating-system isolation.

## Local Envd

```bash
uv run environment-provider-example local_envd --executable /absolute/path/to/a13n-envd
```

Without `--executable`, resolution checks `A13N_ENVD_EXECUTABLE` and then `PATH`. The Host owns a `LocalEnvdProviderRuntime`. Each connector opening creates an independent fixed-cwd Session; execution close releases only that Session. The example closes the Host runtime afterward and preserves the workspace. See [Operate `a13n-envd`](../a13n-envd/index.md).

## Docker

```bash
# Repository root
make image-sandbox
cd examples/environment-provider
uv run environment-provider-example docker
```

The default image is `a13n-sandbox:local`; use `--image IMAGE` to select another compatible image. The Host explicitly creates a container through a management provider and keeps the returned state. A connector built from that state opens two independent executions: one writes a file, the other reads it. Closing either execution preserves the container. The Host finally calls provider `destroy()` explicitly.

A production Host publishes management state before opening execution. If a management operation fails or is cancelled after allocation, use `observed_environment_state(error, fallback)` to recover and publish the observed reference. Execution has no management-state writeback. A stopped or missing target fails opening without resume or replacement.

## Use with Harness

Pass the connector to an executable:

```python
result = await executable.run("Inspect the workspace", environment=connector)
```

Harness opens and closes a fresh execution per mount. Add `DynamicEnvironmentCapability` when the model should receive Environment tools. For a complete offline application, see the [Agent application example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app).

## HTTP and WebSocket Envd

The [Remote Envd guide](remote-envd.md) covers existing daemons and a Host WebSocket handler. The demo explicitly starts and cleans up its own daemon:

```bash
uv run environment-provider-example remote_envd_demo \
  --transport http --executable ../../target/debug/a13n-envd
uv run environment-provider-example remote_envd_demo \
  --transport websocket --executable ../../target/debug/a13n-envd
```

Both open separate Sessions through the same connector. Execution close preserves the daemon; only the demo's operator code stops it.

## Validate

From the repository root, `make examples-check-all` lints, type-checks, tests, and builds the independent projects. The offline smoke path uses Direct Local; Envd and Docker require their respective runtimes.
