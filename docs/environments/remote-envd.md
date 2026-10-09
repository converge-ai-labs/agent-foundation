---
title: Connect to Remote Envd
sidebarTitle: Remote Envd
description: Connect to an existing Envd daemon over HTTP, or let it dial back to your Host over WebSocket.
---

Use `http_envd` when your Host can reach an existing daemon over HTTP(S). Use `websocket_envd` when the daemon must connect back to your Host, for example from a machine behind NAT.

Both are **connect-only**: they do not create remote machines, start daemons, renew infrastructure timeouts, stop daemons or delete their files. The operator owns deployment; your Host owns authentication, Environment selection and scheduling. Files, shell, processes, output and ports use the same EIP-backed Provider operations as Local Envd.

## Try both locally first

No model, Docker or cloud account is needed. From the repository root:

```bash
cargo build --locked --package a13n-envd
cd examples/environment-provider
uv sync --locked

uv run environment-provider-example remote_envd_demo \
  --transport http --executable ../../target/debug/a13n-envd

uv run environment-provider-example remote_envd_demo \
  --transport websocket --executable ../../target/debug/a13n-envd
```

Each demo starts a temporary local daemon, creates an external Provider adapter, writes a file, closes the adapter, then reads the file through a fresh adapter. It verifies that the daemon generation and files survived adapter close. Finally, the **demo operator code** stops its daemon and removes its temporary files.

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
from a13n_environment.builtins import select_builtin_environment_providers
from a13n_environment.models import EnvironmentState

(http_envd,) = select_builtin_environment_providers(("http_envd",))
state = EnvironmentState(
    provider_key=http_envd.type,
    state_version="1",
    state={"device_id": "env-remote-machine"},
)
environment = http_envd.execution_connector(
    {"working_directory": "/work/project"},
    configuration={"endpoint": "https://envd.example.com"},
    credential={"token": token_from_your_secret_store},
    environment_id="env-my-project",  # Your Host's logical identity.
    state=state,
)

# Harness binds, uses and closes this fresh adapter for the Run.
result = await executable.run("Inspect the workspace", environment=environment)
```

HTTP and WebSocket Envd both declare `supports_managed=False`. They provide fixed-device connectors only and reject `open_provider()` before any external request.

Outside Harness, use `async with await environment.open() as execution` and call the execution's operations. A shared `HttpEnvdProviderRuntime` stays open until Host shutdown.

The example CLI can connect to an existing daemon too:

```bash
uv run environment-provider-example http_envd \
  --endpoint https://envd.example.com \
  --device-id env-remote-machine \
  --credential-file /private/envd-token
```

This writes `provider-example.txt` under the selected Device cwd and reads it through a fresh Session. Your daemon must permit `file.write_text` and `file.read_text`. Never supply a credential in a URL or command-line argument.

Use HTTPS for public endpoints. HTTP is allowed on loopback or, with `allow_plaintext_private_link=True`, a trusted provider-private link. HTTPS uses environment proxies; plaintext stays direct.

For a private CA, pass an SSL context or CA file as `HttpEnvdProviderRuntime.verify`. Verification is enabled by default; explicit `verify=False` disables certificate and hostname checks. Set TLS policy before constructing the runtime. Process defaults are described in [Host outbound connections](configuration.md#host-outbound-connections).

## Session egress and credential references

Local, HTTP and WebSocket Envd share the same reference-only Session recipe. The operator selects the Device's Sandbox and network mode at launch. A controlled Device requires explicit destinations on every Session; inherit and deny Devices reject Session policy.

For example, pass this recipe to the Provider rather than placing a token value in configuration:

```python
recipe = {
    "working_directory": "/work/project",
    "egress": {
        "destinations": {"mode": "allowlist", "hosts": ["api.github.com"]},
        "secrets": [{
            "env": "GH_TOKEN",
            "source": {"kind": "environment", "name": "HOST_GITHUB_TOKEN"},
            "inject_hosts": ["api.github.com"],
        }],
    },
    "expected_boundary": {
        "sandbox": {"mode": "disabled"},
        "egress": "controlled",
    },
}
```

Set `HOST_GITHUB_TOKEN` in the Host environment. The runtime resolves it before opening each Session; a missing or empty value fails preparation. Saved recipes retain references, and commands receive sentinels rather than real credentials.

`expected_boundary` checks the Device's launch settings. For restricted Devices, list the exact grants. See [controlled egress](../a13n-envd/egress.md) for credential injection and policy updates.

## Integrate your own WebSocket Host

The connection direction is **Envd to Host**; your Host still sends every EIP request. The library does not open a listener. It provides a process-local `WebSocketEnvdConnections` instance that you own during application lifespan.

After authenticating the upgrade and selecting the expected Device ID in your Host:

```python
async def authenticated_envd_handler(connection):
    # Resolve this from your authenticated registration, not untrusted EIP input.
    native_id = trusted_registration.device_id
    await connections.attach(native_id, connection)
```

Await `attach()` for the handler's entire lifetime. The SDK immediately performs the Device handshake with zero Sessions, even before a Run exists. Do not queue an uninitialized connection until the next Run: Envd has a finite initialization deadline.

To use one of those connections:

```python
from a13n_environment.builtins import select_builtin_environment_providers
from a13n_environment.models import EnvironmentState
from a13n_environment.remote_envd.configuration import (
    WebSocketEnvdConnectionConfiguration,
)
from a13n_environment.remote_envd.websocket import WebSocketEnvdProviderRuntime

(websocket_envd,) = select_builtin_environment_providers(("websocket_envd",))
environment = websocket_envd.execution_connector(
    {"working_directory": "/work/project"},
    environment_id="env-my-project",
    state=EnvironmentState(
        provider_key=websocket_envd.type,
        state_version="1",
        state={"device_id": "env-remote-machine"},
    ),
    runtime=WebSocketEnvdProviderRuntime(
        connections,
        WebSocketEnvdConnectionConfiguration(connection_timeout=30),
    ),
)
```

The Host supplies the connection SDK directly as the runtime collaborator, because it owns the accepted connections. An unwired WebSocket Provider stays inert and fails explicitly.

Close `connections` during Host shutdown, or use `async with WebSocketEnvdConnections() as connections`. Each instance has a finite connection capacity, rejects duplicate active daemon connections, and shares one Device connection across independent adapter-owned Sessions. A waiting acquisition has a finite timeout. Run completion or cancellation does not close that shared connection.

### Framework integration

A `websockets.asyncio.server.ServerConnection` works directly. Other web frameworks implement the public `a13n_envd_client.WebSocketConnection` protocol:

- `subprotocol` reports the negotiated `eip.v1`;
- `recv()` returns one complete `str` or `bytes` message;
- `send(message)` preserves text versus binary messages;
- `close(code=1000, reason="")` ends the accepted connection;
- `wait_closed()` observes closure without consuming EIP messages.

Translate framework disconnects into `EOFError` or `OSError`, keep frame and queue limits finite at your listener, and let EIP exclusively read accepted messages. The Host authenticates **before** handing a connection to the SDK. A negotiated subprotocol or a Device ID is not authentication.

The runnable `run_websocket()` example includes a small loopback Host listener, Bearer-token check and `eip.v1` negotiation. That listener is application example code, not a server started by the SDK. For a manually operated daemon:

```bash
# Start the example Host first; it waits up to 60 seconds for the daemon.
uv run environment-provider-example websocket_envd \
  --port 8788 --device-id env-remote-machine \
  --credential-file /private/envd-token
```

Configure the daemon to use `reverse_websocket`, the matching credential file, Device ID, and `A13N_ENVD_REVERSE_WS_URL=ws://127.0.0.1:8788`. See the [Envd operations guide](../a13n-envd/index.md) for the complete operator configuration. Production Hosts provide their own TLS listener, authentication and routing policy.

## State, concurrency and recovery

- The Host's logical Environment ID and the Envd Device ID may differ. Initialization validates the Device ID; operation references belong to the logical Environment and current generation.
- Persist only the Provider state envelope. It contains no credential, endpoint, connection, Session or daemon generation.
- One Device admits multiple independent Sessions. Each fresh adapter opens its own Session with the captured cwd and owns its resources. Sessions are not tenant isolation boundaries.
- Closing an adapter closes its Session, not remote infrastructure or a borrowed connection. Both HTTP and WebSocket Device connections remain Host-owned. File continuity survives; restarting the daemon invalidates native handles.
- Device info and bounded directory listing require no adapter or Session. Resolve an omitted cwd from Device info before immutable Run acceptance.
- A lost framed carrier detaches Sessions for disconnect grace. An existing owner can explicitly attach the exact same Session in the same generation; no operation or transfer is replayed. A fresh adapter always opens a new Session.
- Never replay a possibly dispatched command or mutation merely because the connection dropped. A failed preparation requires a fresh adapter for the next attempt.

The WebSocket SDK is process-local. If the listener and executing worker live in different processes, your Host must route execution to the connection owner or provide an explicit integration. No automatic relay, distributed registry or global connection pool is implied.

## Connect to Harness UI

Harness UI supports self-registration. Install `a13n-envd` on the computer whose files and tools you want to use, then copy the command from **Settings → Environments → Connect Device** in Harness UI:

```bash
a13n-envd connect https://your-host.example --host work --instance work
```

Keep the process running. Open the approval link printed in the terminal, sign in to the Host and compare the verification code before approving. Do not approve an unfamiliar Device or mismatched code. Registration alone does not start a conversation or grant a Run access to the Device.

Once the Device is online, choose it and a working directory when you add an environment to a conversation or Project. Directory discovery works before a Run exists. A working directory is the Session's starting directory, **not** a filesystem sandbox.

### Enable shell execution

By default, the connection allows file operations only; shell execution needs Full Control or manually configured shell profiles. To use the current operating-system account's full authority:

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://your-host.example --host work --instance work
```

PowerShell:

```powershell
$env:A13N_ENVD_FULL_CONTROL = "1"
a13n-envd connect https://your-host.example --host work --instance work
```

Alternatively, provide an explicit Envd configuration for the execution policy you need. See [Envd configuration](../a13n-envd/configuration.md). Full Control is not tenant isolation; only connect to a Host you trust.

### Restart or connect another Host

After approval, Envd saves the Host endpoint and a protected, narrow credential locally. Restart with the saved alias and the same instance:

```bash
a13n-envd connect work --instance work
```

Keep the same `--state-dir` too if you supplied one. Reconnection does not require another approval. The secret is never a Host login or general API credential, and the Host retains only its digest.

One process connects to **one Host**. For a second Host on the same physical computer, run another process with a different instance:

```bash
a13n-envd connect https://other-host.example --host personal --instance personal
```

Instances have independent daemon identities, state and credentials. There is no multi-Host scheduler inside one daemon. Stopping a process takes its connection offline without deleting its files or Host registration.

### Manage and revoke

Harness UI lists approved Devices in **Settings → Environments**; choose **Revoke**, then **Revoke connection**, to permanently revoke a Device's credential. Revocation prevents new access and fences the existing connection within its bounded authority window; it does not undo already dispatched effects, delete files/history, or remotely manage the daemon process. The revoked record remains visible.

A revoked credential is not silently replaced. If you intentionally want to enroll again, use a new `--instance` and explicitly approve it. Deleting local credentials is not a way to take over an existing registration.

### Troubleshooting

- HTTPS is required except on loopback. `localhost` means the computer running Envd, not a remote Host computer. Use the public reachable hostname for another computer. Use `--ca-file` for a private certificate authority rather than disabling verification.
- If a pending approval expires, rerun the same command to request approval again. If authentication is denied after revocation, Envd exits instead of generating a replacement credential.

## Connect to the Service

The Service does not accept self-registration. Run an HTTP daemon, then [register it as an external target](../a13n-service/environments.md#register-an-external-target) with its endpoint and token. The Service only connects to it and never manages its lifecycle.
