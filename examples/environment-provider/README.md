# Environment Provider Example

This standalone project demonstrates how a Host selects and drives the built-in `a13n-environment-provider` backends without involving Agent Harness or a model.

It covers the common Provider lifecycle:

1. create a credential-free `EnvironmentProviderSpec`;
2. build an explicit allowlisted Provider catalog;
3. validate the exact configuration version;
4. supply fresh process-local runtime collaborators when required;
5. construct one fresh, inert `Environment` adapter;
6. enter it, require file readiness, and use provider-neutral operations;
7. read `dump_state()` and close the adapter non-destructively;
8. for Docker, give the state to a fresh adapter and destroy the target explicitly.

## Run Direct Local

Direct Local is the default offline path and needs no daemon, container engine, or model credentials:

```bash
cd examples/environment-provider
uv sync --locked
uv run environment-provider-example direct-local
```

The Host creates `.environment-provider-example/direct-local-workspace`, gives its absolute path to the Provider, writes and reads `/provider-example.txt`, and closes the adapter. The workspace remains because Direct Local never owns or deletes it.

Select another Host-owned directory with:

```bash
uv run environment-provider-example direct-local --workspace /absolute/path/to/workspace
```

Direct Local shares the embedding Host account. Its operation policy is not an operating-system sandbox.

## Run Local Envd

Local Envd starts one private `agent-envd` generation for the fresh adapter and exposes the Host-owned workspace through EIP:

```bash
uv run environment-provider-example local-envd \
  --executable /absolute/path/to/agent-envd
```

If `--executable` is omitted, resolution checks `A13N_AGENT_ENVD_EXECUTABLE` and then `PATH`. Use `--workspace` to select another directory. The example requests denied execution networking, closes the private daemon and runtime data, and preserves the workspace.

The executable and `a13n-envd-client` must have compatible release versions, and the current platform must pass the native-isolation probe. See the [`agent-envd` guide](../../docs/agent-envd/index.md) for build and platform prerequisites.

## Run Docker

Docker needs a local Docker Engine. Build the repository sandbox image, then run the example:

```bash
# From the repository root
make image-sandbox

cd examples/environment-provider
uv run environment-provider-example docker
```

The example defaults to `agent-foundation-sandbox:local`, the image built by that Make target. Use `--image IMAGE` to select another compatible sandbox image; ordinary Docker authentication and pull behavior apply.

The example intentionally exercises the complete stateful lifecycle:

1. create and enter one container-backed Environment;
2. write `/provider-example.txt` and capture `EnvironmentState`;
3. close the first adapter without stopping or deleting the container;
4. construct a fresh adapter from the captured state and read the same file;
5. close the re-entry adapter;
6. construct a third fresh adapter and call `destroy()` explicitly.

Bootstrap material defaults to `.environment-provider-example/docker-bootstrap`. Override it with `--bootstrap-root`. The directory is Host state: keep it durable and private while a container is retained. The example removes its Provider-owned allocation when destruction succeeds.

If the process is interrupted after target creation, inspect the retained Docker container and bootstrap directory before retrying or removing them. State is the exact soft reference needed for safe re-entry or destruction; do not guess a replacement target from a name.

## Test and inspect

Run this project's offline checks directly:

```bash
uv run pytest
uv run ruff check .
uv run pyright
```

Or run the repository-wide examples gate:

```bash
make examples-check-all
```

Only Direct Local runs in the repository's offline smoke gate. Local Envd and Docker remain explicit because they require external runtimes.

Read [`application.py`](src/a13n_environment_provider_example/application.py) for the complete Host-side code. For third-party Provider packaging and entry-point discovery, see the separate [Provider plugin example](../plugins/README.md#environment-provider).

## Boundaries

- Provider configuration contains desired behavior, not credentials or current target identity.
- Runtime collaborators such as the daemon path, Docker client, and bootstrap store are fresh process-local values.
- Every independent Run or Host lifecycle action uses a fresh adapter.
- `dump_state()` performs no I/O and may be read during unconditional finalization.
- `close()` releases process-local resources without destroying a backing target.
- Only explicit Host retention policy should construct a fresh adapter and call `destroy()`.
- Harness owns multi-mount routing and model-facing tools; this example stays at the lower single-Environment Provider boundary.

## Native E2B

With `E2B_API_KEY` set in the Host environment, run:

```bash
uv run python -m a13n_environment_provider_example.e2b
```

This creates a default E2B sandbox, writes and reads a file, closes the adapter and explicitly destroys the sandbox in `finally`. It requires no envd installation or custom E2B template. The library does not load `.env` files.
