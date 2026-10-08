# Environment Provider Example

This standalone project demonstrates how a Host selects and drives the built-in `a13n_harness.providers.environment` backends without involving Agent Harness or a model.

It covers the common Provider lifecycle:

1. create a credential-free `EnvironmentProviderSpec`;
2. build an explicit allowlisted Provider catalog;
3. validate the recipe against the Provider's declared model;
4. supply fresh process-local runtime collaborators when required;
5. construct one fresh, inert `Environment` adapter;
6. enter it, require file readiness, and use provider-neutral operations;
7. read `dump_state()` and close the adapter non-destructively;
8. for Docker, give the state to a fresh adapter and destroy the target explicitly.

## Demonstrated routes

| Route  | Provider                 | Use it for                               | Operation and ownership boundary                            |
| ------ | ------------------------ | ---------------------------------------- | ----------------------------------------------------------- |
| Native | `direct_local`           | Trusted local automation                 | Host OS operations; existing directory, no sandbox claim    |
| Native | `e2b`                    | Native managed cloud sandbox             | E2B SDK; sandbox create/pause/resume/renew/destroy          |
| Envd   | `local_envd`             | CLI and local Agents                     | Private stdio daemon; close preserves workspace             |
| Native | `docker` (Native Docker) | Small single-node self-hosted services   | Docker lifecycle and native exec; close preserves container |
| Envd   | `http_envd`              | Network-reachable external environments  | HTTP(S) EIP; connect-only                                   |
| Envd   | `websocket_envd`         | Environments that connect back to a Host | Reverse WebSocket EIP; Host-integrated SDK, connect-only    |

The cloud demos here exercise E2B only. The complete [cloud-provider catalog](../../docs/environments/providers.md#cloud-providers) also includes the peer Daytona, Modal, Vercel Sandbox, Fly.io Sprites, and Runloop implementations; their opt-in checks are documented there.

## Try remote providers in one command

Build the daemon once, then try both transports without credentials, Docker, or a model:

```bash
# Repository root
cargo build --locked --package a13n-envd
cd examples/environment-provider
uv sync --locked
uv run environment-provider-example remote_envd_demo \
  --transport http --executable ../../target/debug/a13n-envd
uv run environment-provider-example remote_envd_demo \
  --transport websocket --executable ../../target/debug/a13n-envd
```

Both write a file, close its Session, and read it through a fresh adapter with a different Session on the same Device. The output confirms that Provider close preserves the remote daemon/workspace. Demo operator code then removes its own temporary resources. The demo configures no command executables or shell profiles; file operations use Device-absolute paths.

**Start reading [`remote.py`](src/a13n_environment_example/remote.py).** `run_http()` shows the minimal connection setup. `run_websocket()` shows a Host-owned authenticated listener calling `connections.attach()`; the SDK does not open a listener. `use_remote()` demonstrates the common Provider/Environment lifecycle without infrastructure details. [`remote_demo.py`](src/a13n_environment_example/remote_demo.py) is separate local operator scaffolding, not something the remote Provider needs in production.

To use an existing HTTP daemon instead:

```bash
uv run environment-provider-example http_envd \
  --endpoint https://envd.example.com \
  --device-id device-remote-machine \
  --credential-file /private/envd-token
```

For reverse WebSocket, start the example Host first and point your daemon at `ws://127.0.0.1:8788` with the matching identity and token:

```bash
uv run environment-provider-example websocket_envd \
  --device-id device-remote-machine \
  --credential-file /private/envd-token
```

The external daemon must permit `file.read_text` and `file.write_text`; these examples resolve the Device default working directory and write `provider-example.txt` there. Token contents never appear in URLs or command-line arguments. The standalone listener binds loopback and waits up to 60 seconds; your production Host supplies its own TLS, authentication, routing and lifespan. Other frameworks adapt the public `WebSocketConnection` message protocol.

See the [remote guide](../../docs/environments/remote-envd.md) for identities, deployment boundaries and recovery. One Device supports multiple independent Sessions. Opening a new Session does not take over an abandoned Session or recover its process handles.

## Run Direct Local

Direct Local is the default offline path and needs no daemon, container engine, or model credentials:

```bash
cd examples/environment-provider
uv sync --locked
uv run environment-provider-example direct_local
```

The Host creates `.environment-provider-example/direct-local-workspace`, gives its absolute path to the Provider, writes and reads `/provider-example.txt`, and closes the adapter. The workspace remains because Direct Local never owns or deletes it.

Select another Host-owned directory with:

```bash
uv run environment-provider-example direct_local --workspace /absolute/path/to/workspace
```

Direct Local shares the embedding Host account. Its operation policy is not an operating-system sandbox.

## Run Local Envd

The Host's Local Envd runtime starts one shared Device lazily; the fresh adapter opens a fixed-cwd Session over EIP:

```bash
uv run environment-provider-example local_envd \
  --executable /absolute/path/to/a13n-envd
```

If `--executable` is omitted, resolution checks `A13N_ENVD_EXECUTABLE` and then `PATH`. Use `--workspace` to select another directory. The example closes the adapter's Session, then explicitly closes the Host runtime and its daemon. The selected directory remains intact.

The executable and `a13n-envd-client` must have identical release versions. Envd shares its daemon account's authority; the working directory is not containment. See the [`a13n-envd` guide](../../docs/a13n-envd/index.md) for build and platform prerequisites.

## Run Docker

Docker needs a local Docker Engine. Build the repository sandbox image, then run the example:

```bash
# From the repository root
make image-docker-environment

cd examples/environment-provider
uv run environment-provider-example docker
```

The example defaults to `a13n-docker-environment:local`, the image built by that Make target. Use `--image IMAGE` to select another compatible sandbox image; ordinary Docker authentication and pull behavior apply.

The example intentionally exercises the complete stateful lifecycle:

1. create and enter one container-backed Environment;
2. write `/provider-example.txt` and capture `EnvironmentState`;
3. close the first adapter without stopping or deleting the container;
4. construct a fresh adapter from the captured state and read the same file;
5. close the re-entry adapter;
6. construct a third fresh adapter and call `destroy()` explicitly.

If the process is interrupted after target creation, inspect the retained Docker container before retrying or removing them. State is the exact soft reference needed for safe re-entry or destruction; do not guess a replacement target from a name.

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

Only Direct Local runs in the offline smoke gate. The EIP integration gate builds envd and runs the HTTP/WebSocket demos as tests. Docker and E2B require their own external runtimes.

Read [`application.py`](src/a13n_environment_example/application.py) for the complete Host-side code. For third-party Provider packaging and entry-point discovery, see the separate [Provider plugin example](../plugins/README.md#environment-provider).

## Boundaries

- Provider configuration contains desired behavior, not credentials or current target identity.
- Runtime collaborators such as the daemon path, Docker client, and Engine endpoint are fresh process-local values.
- Every independent Run or Host lifecycle action uses a fresh adapter.
- `dump_state()` performs no I/O and may be read during unconditional finalization.
- `close()` releases process-local resources without destroying a backing target.
- Only explicit Host retention policy should construct a fresh adapter and call `destroy()`.
- Harness owns multi-mount routing and model-facing tools; this example stays at the lower single-Environment Provider boundary.

## Native E2B

With `E2B_API_KEY` set in the Host environment, run:

```bash
uv run python -m a13n_environment_example.e2b
```

This creates a default E2B sandbox, writes and reads a file, closes the adapter and explicitly destroys the sandbox in `finally`. It requires no envd installation or custom E2B template. The library does not load `.env` files.
