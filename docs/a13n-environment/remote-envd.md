# Connect to Remote Envd

Use `a13n.http-envd` when your Host can reach an existing daemon over HTTP(S). Use `a13n.websocket-envd` when the daemon must connect back to your Host, for example from a machine behind NAT.

Both are **connect-only**: they do not create remote machines, start daemons, renew infrastructure timeouts, stop daemons or delete their workspaces. The operator owns deployment; your Host owns authentication, environment selection and scheduling. Files, shell, processes, output and ports use the same EIP-backed Provider operations as Local Envd.

## Try both locally first

No model, Docker or cloud account is needed. From the repository root:

```bash
cargo build --locked --package a13n-envd
cd examples/environment-provider
uv sync --locked

uv run environment-provider-example remote-envd-demo \
  --transport http --executable ../../target/debug/a13n-envd

uv run environment-provider-example remote-envd-demo \
  --transport websocket --executable ../../target/debug/a13n-envd
```

Each demo starts a temporary local daemon, creates an external Provider adapter, writes a file, closes the adapter, then reads the file through a fresh adapter. It verifies that the daemon generation and workspace survived Provider close. Finally, the **demo operator code** stops its daemon and removes its temporary files.

Expected results include:

```text
re-entry read: hello from remote envd
independent Sessions: True
provider close preserved remote daemon and workspace
```

The demo leaves command execution disabled, uses loopback and a temporary credential, and writes under its temporary Device cwd. It is a learning setup, not a production sandbox configuration.

Read the small, copyable [Host integration](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/src/a13n_environment_example/remote.py) separately from the [local demo scaffolding](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/src/a13n_environment_example/remote_demo.py). Your production application normally needs the former, not the latter.

## Connect to an existing HTTP daemon

The operator supplies an HTTP(S) origin, the configured `A13N_ENVD_DEVICE_ID`, and a credential through a protected channel. The origin has no `/eip/control` suffix: the client constructs EIP resource paths.

```python
from a13n_environment import (
    EnvironmentState,
    HttpEnvdBackendConfiguration,
    HttpEnvdCredential,
    HttpEnvdEnvironmentProvider,
    HttpEnvdProviderRuntime,
    RemoteEnvdProviderConfiguration,
)
from pydantic import SecretStr

provider = HttpEnvdEnvironmentProvider()
runtime = HttpEnvdProviderRuntime(
    configuration=HttpEnvdBackendConfiguration(endpoint="https://envd.example.com"),
    credential=HttpEnvdCredential(token=SecretStr(token_from_your_secret_store)),
)
state = EnvironmentState(
    provider_key=provider.key,
    state_version="1",
    state={"device_id": "env-remote-machine"},
)
environment = provider.create_environment(
    configuration=RemoteEnvdProviderConfiguration(working_directory="/work/project"),
    environment_id="env-my-project",  # Your Host's logical identity.
    state=state,
    runtime=runtime,
)

# Harness binds, uses and closes this fresh adapter for the Run.
result = await executable.run("Inspect the workspace", environment=environment)
```

Without Harness, use `enter()`, `ensure_ready()` and `EnvironmentOperations` as shown in `remote.py`. Construction and `enter()` are inert; preparation connects. Always close the adapter in `finally` and close the shared HTTP runtime during Host shutdown. One runtime can serve many independent adapters.

The example CLI can connect to an existing daemon too:

```bash
uv run environment-provider-example http-envd \
  --endpoint https://envd.example.com \
  --device-id env-remote-machine \
  --credential-file /private/envd-token
```

This writes `provider-example.txt` under the selected Device cwd and reads it through a fresh Session. Your daemon must permit `file.write_text` and `file.read_text`. Never supply a credential in a URL or command-line argument.

Public network endpoints require verified HTTPS. HTTP is accepted on loopback; a trusted provider-private link needs an explicit `allow_plaintext_private_link=True`. The runtime also accepts an SSL context or CA file through `verify`; disabling TLS verification is rejected.

## Integrate your own WebSocket Host

The connection direction is **envd to Host**; your Host still sends every EIP request. The library does not open a listener. It provides a process-local `WebSocketEnvdConnections` instance that you own during application lifespan.

After authenticating the upgrade and selecting the expected native identity in your Host:

```python
async def authenticated_envd_handler(connection):
    # Resolve this from your authenticated registration, not untrusted EIP input.
    native_id = trusted_registration.device_id
    await connections.attach(native_id, connection)
```

Await `attach()` for the handler's entire lifetime. The SDK immediately performs the Device handshake with zero Sessions, even before a Run exists. Do not queue an uninitialized connection until the next Run: envd has a finite initialization deadline.

To use one of those connections:

```python
from a13n_environment import (
    WebSocketEnvdBackendConfiguration,
    WebSocketEnvdEnvironmentProvider,
    WebSocketEnvdProviderRuntime,
)

provider = WebSocketEnvdEnvironmentProvider()
environment = provider.create_environment(
    configuration=RemoteEnvdProviderConfiguration(working_directory="/work/project"),
    environment_id="env-my-project",
    state=EnvironmentState(
        provider_key=provider.key,
        state_version="1",
        state={"device_id": "env-remote-machine"},
    ),
    runtime=WebSocketEnvdProviderRuntime(
        connections,
        WebSocketEnvdBackendConfiguration(connection_timeout=30),
    ),
)
```

Close `connections` during Host shutdown, or use `async with WebSocketEnvdConnections() as connections`. Each instance has a finite connection capacity, rejects duplicate active daemon connections, and shares one Device connection across independent adapter-owned Sessions. A waiting acquisition has a finite timeout. Run completion or cancellation does not close that shared connection.

### Framework integration

A `websockets.asyncio.server.ServerConnection` works directly. Other web frameworks implement the public `a13n_envd_client.WebSocketConnection` protocol:

- `subprotocol` reports the negotiated `eip.v1`;
- `recv()` returns one complete `str` or `bytes` message;
- `send(message)` preserves text versus binary messages;
- `close(code=1000, reason="")` ends the accepted connection;
- `wait_closed()` observes closure without consuming EIP messages.

Translate framework disconnects into `EOFError` or `OSError`, keep frame and queue limits finite at your listener, and let EIP exclusively read accepted messages. The Host authenticates **before** handing a connection to the SDK. A negotiated subprotocol or a daemon ID is not authentication.

The runnable `run_websocket()` example includes a small loopback Host listener, Bearer-token check and `eip.v1` negotiation. That listener is application example code, not a server started by the SDK. For a manually operated daemon:

```bash
# Start the example Host first; it waits up to 60 seconds for the daemon.
uv run environment-provider-example websocket-envd \
  --port 8788 --device-id env-remote-machine \
  --credential-file /private/envd-token
```

Configure the daemon to use `reverse_websocket`, the matching credential file, native identity, and `A13N_ENVD_REVERSE_WS_URL=ws://127.0.0.1:8788`. See the [envd operations guide](../a13n-envd/index.md) for the complete operator configuration. Production Hosts provide their own TLS listener, authentication and routing policy.

## State, concurrency and recovery

- The Host's logical Environment ID and envd's native ID may differ. Initialization validates the native ID; operation references belong to the logical Environment and current generation.
- Persist only the Provider state envelope. It contains no credential, endpoint, connection, Session or daemon generation.
- One Device admits multiple independent Sessions. Each fresh adapter opens its own Session with the captured cwd and owns its resources. Sessions are not tenant isolation boundaries.
- Closing an adapter closes its Session, not remote infrastructure or a borrowed connection. Both HTTP and WebSocket Device connections remain Host-owned. File continuity survives; restarting the daemon invalidates native handles.
- Device info and bounded directory listing require no adapter or Session. Resolve an omitted cwd from Device info before immutable Run acceptance.
- A lost framed carrier detaches Sessions for disconnect grace. An existing owner can explicitly attach the exact same Session in the same generation; no operation or transfer is replayed. A fresh adapter always opens a new Session.
- Never replay a possibly dispatched command or mutation merely because the connection dropped. A failed preparation requires a fresh adapter for the next attempt.

The WebSocket SDK is process-local. If the listener and executing worker live in different processes, your Host must route execution to the connection owner or provide an explicit integration. No automatic relay, distributed registry or global connection pool is implied.

## Hosted use

a13n Service includes HTTP Envd in its default Provider catalog. Create a Provider with `HttpEnvdBackendConfiguration` and a separate write-only `{"token": "..."}` credential, then externally register an Environment with the recipe and state shown above. Remote Providers report `supports_managed=False`, so managed templates are rejected before target I/O.

WebSocket Envd is available as a library Provider but is not enabled by default in a13n Service: the Host must wire its SDK and connection routing. Enabling a key alone does not supply a listener or runtime. Harness UI recognizes both keys in its catalog; project integration still requires a Host-approved adapter supplying remote state and runtime. Neither becomes a default local execution mode.
