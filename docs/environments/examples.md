---
title: Built-in Provider examples
sidebarTitle: Built-in examples
description: Run the local file example, then explore Envd Sessions and Docker target ownership.
---

Run [`examples/environment-provider`](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/environment-provider) to see who creates a target, who opens an execution, and what remains after close. Start with Direct Local for an offline file round trip. The other examples add a local daemon, a managed Docker container, or a remote connection. They use Harness for execution and Service for Docker management, without an Agent or model credentials. Cloud backends are covered in [Cloud Providers](providers.md#cloud-providers).

## Install

From a repository checkout with Python 3.13 or later and `uv`, install the independent example project:

```bash
cd examples/environment-provider
uv sync --locked
```

Run the commands below from this directory unless stated otherwise. The project installs `a13n-harness` for execution and `a13n-service` for Docker creation and destruction. Keep both when copying the complete application; execution alone needs Harness.

## Direct Local

Choose a workspace directory for the example. It creates the directory if needed and writes `provider-example.txt` there, replacing that file if it already exists:

```bash
uv run environment-provider-example direct_local --workspace /absolute/path/to/workspace
```

Expect output including `read: hello from direct_local`, `state: none`, and `workspace preserved: True`. Inspect `provider-example.txt` in your workspace after the command exits.

The program creates a connector, opens one execution, writes and reads `/provider-example.txt`, then closes the execution. The file and directory remain owned by the Host. Direct Local uses the Host account and provides no operating-system isolation.

## Local Envd

Install or build the native executable as described in [Operate Envd](../a13n-envd/index.md), then pass its absolute path:

```bash
uv run environment-provider-example local_envd --executable /absolute/path/to/a13n-envd
```

Expect `read: hello from local_envd` and a preserved workspace. Add `--workspace /absolute/path/to/workspace` to choose the directory; the default is `.environment-provider-example/local-envd-workspace` beneath the current directory. If you omit `--executable`, resolution checks `A13N_ENVD_EXECUTABLE` and then `PATH`.

Follow the two nested lifetimes in the application: `LocalEnvdProviderRuntime` owns the local Device, while each connector opening owns an independent fixed-cwd Session. Execution close releases the Session. The example then closes the Host runtime, leaving the workspace in place.

## Docker

Start a Docker daemon available to your account. From the repository root, build the example's default image and run the Docker path:

```bash
make image-sandbox
cd examples/environment-provider
uv run environment-provider-example docker
```

The image is `a13n-sandbox:local`; use `--image IMAGE` to select another compatible image. Expect matching `first read` and `re-entry read` values, followed by `target destroyed: True`.

Trace the lifecycle in the example:

1. Create a container through the management provider and retain its returned state.
2. Build a connector from that state. Open one execution to write a file, then another to read it.
3. Close each execution while preserving the container.
4. Call the management provider's `destroy()` explicitly to remove the target.

In a production Host, publish management state before opening execution. If allocation fails or is cancelled, recover the observed reference with `observed_environment_state(error, fallback)` and publish it. Execution does not write back management state, resume a stopped target, or replace a missing one. See [Lifecycle](lifecycle.md) for the Host-owned publication workflow.

## Use with Harness

Once your Host has prepared a connector and built a Harness executable, wrap the connector in the `PreparedSource` implementation from the [Harness environment guide](../a13n-harness/environments.md#supply-a-source). The following integration fragment assumes those objects already exist:

```python
result = await executable.run("Inspect the workspace", environment=PreparedSource(connector))
```

Harness opens and closes a fresh execution for each used mount. Add `DynamicEnvironmentCapability` to give the model Environment tools. For the complete offline agent workflow, use the [Agent application example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/agent-app).

## HTTP and WebSocket Envd

With a native executable available, run either transport demo from the example directory. Replace the executable path if your binary is elsewhere:

```bash
uv run environment-provider-example remote_envd_demo \
  --transport http --executable ../../target/debug/a13n-envd
uv run environment-provider-example remote_envd_demo \
  --transport websocket --executable ../../target/debug/a13n-envd
```

Each demo starts its own temporary daemon, writes and reads through separate Sessions on one connector, and reports `independent Sessions: True`. Execution close preserves the daemon; the demo's operator code then stops it and removes the temporary workspace.

To connect to an existing daemon or implement your own Host WebSocket handler, continue to [Remote Envd](remote-envd.md).

## Validate

Use the Direct Local command above for the offline smoke path. Envd and Docker examples need their respective runtimes. When changing the independent example projects, run `make examples-check-all` from the repository root for their lint, type checks, tests, and builds.
