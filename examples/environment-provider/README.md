# Environment Provider Example

This standalone project demonstrates how a Host uses Harness execution definitions and Service management drivers without an Agent or a model.

The Host explicitly manages backing targets through `EnvironmentProvider`, records the returned state, and builds a reusable `EnvironmentConnector`. Each `open()` returns an independent `EnvironmentExecution` with ready operations. Execution close releases its connection and local resources; target deletion requires an explicit management call. Each managed vendor retains its own resource management subclass. Native Local/Docker/E2B provide Files & Shell for quick setup; Envd is the general execution path for OS sandbox, Egress, Computer use, and Device metrics.

## Demonstrated routes

| Execution path | Provider         | Execution scope                              | Resource management             |
| -------------- | ---------------- | -------------------------------------------- | ------------------------------- |
| Native         | `direct_local`   | Local Files & Shell                          | Host-owned directory            |
| Native         | `docker`         | Container Files & Shell                      | Docker provider                 |
| Native         | `e2b`            | Remote sandbox Files & Shell                 | E2B provider                    |
| Envd           | `local_envd`     | General execution over stdio EIP             | Host-owned daemon               |
| Envd           | `http_envd`      | General execution over HTTP(S) EIP           | Connect-only                    |
| Envd           | `websocket_envd` | General execution over reverse WebSocket EIP | Connect-only; Host owns ingress |

The cloud demos here exercise E2B only. The complete [cloud-provider catalog](../../docs/environments/providers.md#cloud-providers) also includes the peer Daytona, Modal, Vercel Sandbox, Fly.io Sprites, and Runloop implementations; their opt-in checks are documented there.

For Chinese desktop applications, [desktop_template.py](desktop_template.py) builds an operator-owned template derived from E2B's `desktop` with Noto CJK fonts. Set `E2B_API_KEY` securely, run `uv sync --locked` in this directory, then `uv run python desktop_template.py my-desktop-cjk`. The build creates or updates that named template in your E2B account. Select its ID or name under Console's **Desktop environment → Custom template** when creating a new environment. Font installation runs during template build only. See [E2B desktop preparation](../../docs/environments/providers.md#e2b-desktop-preparation). This script prepares fonts; provision and connect Envd separately for Agent Computer use.

### Desktop access endpoint development

[desktop_access_template.py](desktop_access_template.py) builds an opt-in E2B template with the internal desktop-access supervisor and CJK fonts. Run `uv run python desktop_access_template.py my-desktop-access` with `E2B_API_KEY` configured securely. The build creates or updates that template in your account. It owns endpoint startup; the official Desktop SDK initializes the desktop resources. Agent Computer operations use Envd. The supervisor uses Python 3.10+, x11vnc and websockify, installed at build time. No dependency installation happens when issuing a grant.

The Docker desktop image includes the same supervisor. For isolated endpoint development, start it with `A13N_DESKTOP_ACCESS=true` and publish container port 6081 to loopback. The management socket at `/run/a13n-desktop-access/control.sock` is root-private. A remote deployment needs a separate HTTPS/WSS viewer route; the Docker Engine connection is not a browser route. Do not publish the plaintext listener directly to the internet.

Enable `desktop_assistance: true` in a Service desktop recipe to use the Agent-requested assistance flow. E2B needs this compatible custom template. Docker also needs `desktop_viewer_url`, an operator-provided WSS route containing `{container_id}` and reaching container port 6081. Service authorizes the human, reserves the native target and drains managed execution before admitting control. Console displays the assistance card only when the Agent requests it; arbitrary takeover is not supported. A controller grant expires within 60 seconds unless renewed, with an absolute 15-minute lifetime. Observe grants reject input at the server. Revocation disconnects that grant and rejects its old credentials without revoking other observers. Envd Agent input is a separate authority; the Host must keep it excluded until revocation and input cleanup are confirmed. See the [management contract](../../spec/a13n-service/06a-environment-management.md#desktop-access-enforcement).

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
make image-sandbox

cd examples/environment-provider
uv run environment-provider-example docker
```

The example defaults to `a13n-sandbox:local`, the image built by that Make target. Use `--image IMAGE` to select another compatible sandbox image; ordinary Docker authentication and pull behavior apply.

The example intentionally exercises the complete stateful lifecycle:

1. explicitly create a container and retain the returned `EnvironmentState`;
2. construct a connector for that exact target;
3. open an execution, write a file, and close the execution;
4. open another execution from the same connector and read the file;
5. close the second execution;
6. explicitly destroy the container through the management provider.

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
- Every `open()` creates an independently owned execution; the connector is reusable.
- Management results and `observed_environment_state()` expose references to persist before execution.
- `close()` releases process-local resources without destroying a backing target.
- Only explicit Host retention policy should call provider `destroy()`.
- Harness owns multi-mount routing and model-facing tools; this example stays at the lower single-Environment Provider boundary.

## Native E2B

With `E2B_API_KEY` set in the Host environment, run:

```bash
uv run python -m a13n_environment_example.e2b
```

This creates a default E2B sandbox, writes and reads a file, closes the adapter and explicitly destroys the sandbox in `finally`. It requires no envd installation or custom E2B template. The library does not load `.env` files.
