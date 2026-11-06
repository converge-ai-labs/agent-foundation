---
title: Connect to Remote Envd
sidebarTitle: Remote Envd
description: Connect an existing Device to your Host, open independent Sessions, and recover without replaying work.
---

Choose the transport by connection direction: `http_envd` lets your Host dial an existing HTTP(S) daemon; `websocket_envd` lets the daemon dial back to your Host, including from behind NAT. Both expose EIP-backed file, shell, process, output, and port operations.

The operator deploys and maintains the remote machine and daemon. Your Host authenticates the Device and selects when and where to use it. These Providers are connect-only: opening or closing an execution leaves target creation, renewal, shutdown, and deletion with the operator.

## Try both locally first

Prepare a Rust toolchain, Python 3.13 or later, and `uv`. From the repository root, build Envd, install the independent example project, then run each transport demo:

```bash
cargo build --locked --package a13n-envd
cd examples/environment-provider
uv sync --locked

uv run environment-provider-example remote_envd_demo \
  --transport http --executable ../../target/debug/a13n-envd

uv run environment-provider-example remote_envd_demo \
  --transport websocket --executable ../../target/debug/a13n-envd
```

Check the output for:

```text
re-entry read: hello from remote envd
independent Sessions: True
provider close preserved remote daemon and workspace
```

Follow the demo's sequence: start a temporary daemon, write through one Session, close that execution, then read through a new Session on the same connector. The program checks that the daemon and file survive execution close. Its operator code finally stops the daemon and removes the temporary workspace.

This local exercise uses loopback, a temporary credential, and file operations with command execution disabled. It requires no model, Docker, or cloud account. For your application, copy the [Host integration](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/src/a13n_environment_example/remote.py); use the separate [demo scaffolding](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/src/a13n_environment_example/remote_demo.py) to understand local launch and cleanup.

## Connect to an existing HTTP daemon

Prepare four inputs before opening a connection:

1. Obtain the operator's HTTP(S) origin, such as `https://envd.example.com`. Leave off `/eip/control`; the client builds resource paths.
2. Obtain the configured `A13N_ENVD_DEVICE_ID` and credential through a protected channel.
3. Choose an existing Device-absolute working directory, such as `/work/project`.
4. Build your Harness executable and the Host-owned [`PreparedSource` helper](../a13n-harness/environments.md#supply-a-source).

The following integration fragment assumes `executable`, `PreparedSource`, and `token_from_your_secret_store` already exist. Replace the sample endpoint, Device ID, and directory:

```python
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.environment.models import EnvironmentState

(http_envd,) = select_builtin_environment_providers(("http_envd",))
state = EnvironmentState(
    provider_key=http_envd.type,
    state_version="1",
    state={"device_id": "env-remote-machine"},
)
connector = http_envd.execution_connector(
    {"working_directory": "/work/project"},
    configuration={"endpoint": "https://envd.example.com"},
    credential={"token": token_from_your_secret_store},
    environment_id="env-my-project",  # Your Host's logical identity.
    state=state,
)

# PreparedSource comes from the Host integration above.
# Harness opens and closes a fresh execution for this Run.
result = await executable.run("Inspect the workspace", environment=PreparedSource(connector))
```

`environment_id` names the Environment in your Host; `device_id` in the state payload selects the expected remote Device. Outside Harness, open the same connector with `async with await connector.open() as execution` and call its operations. If you supply a shared `HttpEnvdProviderRuntime`, keep it open for its users and close it during Host shutdown.

For a file round trip using the example CLI, run this from `examples/environment-provider` after installing the project:

```bash
uv run environment-provider-example http_envd \
  --endpoint https://envd.example.com \
  --device-id env-remote-machine \
  --credential-file /private/envd-token
```

The CLI writes or replaces `provider-example.txt` under the Device's default cwd, then reads it through a fresh Session. The daemon must allow `file.write_text` and `file.read_text`. Store the token in the protected file, not in the URL or command line.

Use HTTPS for public endpoints. Plain HTTP is supported on loopback, or on a trusted provider-private link with `allow_plaintext_private_link=True`. HTTPS uses environment proxies; plaintext connects directly. For a private CA, supply an SSL context or CA file through `HttpEnvdProviderRuntime.verify` before constructing the runtime. Verification is enabled by default; `verify=False` explicitly disables certificate and hostname checks. See [Host outbound connections](configuration.md#host-outbound-connections) for process defaults.

## Session egress and credential references

Select immutable startup policy under `session`, within the Device's deployment ceiling. A controlled Session requires explicit destinations; inherit and deny Sessions reject destination policy. Local, HTTP, and WebSocket Envd use the same reference-only recipe.

For a Device launched with disabled Sandbox and controlled egress, this recipe allows GitHub API traffic and names a Host-side credential source:

```python
recipe = {
    "working_directory": "/work/project",
    "session": {
        "egress_mode": "controlled",
        "egress": {
            "destinations": {"mode": "allowlist", "hosts": ["api.github.com"]},
            "secrets": [{
                "env": "GH_TOKEN",
                "source": {"kind": "environment", "name": "HOST_GITHUB_TOKEN"},
                "inject_hosts": ["api.github.com"],
            }],
        },
    },
    "expected_boundary": {
        "sandbox": {"mode": "disabled"},
        "egress": "controlled",
    },
}
```

Set `HOST_GITHUB_TOKEN` in the Host environment, then pass `recipe` as the first argument to `execution_connector`. The runtime resolves the source before opening each Session; missing or empty values fail preparation. Saved recipes contain references, while commands receive sentinels instead of real credentials.

Set `expected_boundary` to the effective Session boundary you require. For a restricted Session, specify its exact grants. Continue to [controlled egress](../a13n-envd/egress.md) for credential injection and policy updates.

## Integrate your own WebSocket Host

Your application owns the listener and a `WebSocketEnvdConnections` instance for its lifespan. Envd connects to that listener; the Host then sends EIP requests over the accepted connection.

1. Create the connection registry, preferably with `async with WebSocketEnvdConnections() as connections`.
2. Authenticate the upgrade, negotiate `eip.v1`, and resolve the expected Device ID from trusted registration.
3. In the accepted connection handler, await `attach()` until the connection ends:

```python
async def authenticated_envd_handler(connection):
    # Resolve this from your authenticated registration, not untrusted EIP input.
    native_id = trusted_registration.device_id
    await connections.attach(native_id, connection)
```

This handler fragment assumes your listener supplies `connection`, and your application supplies `connections` and `trusted_registration`. Attachment immediately performs the Device handshake with zero Sessions. Initialize at acceptance rather than waiting for a Run; Envd has a finite initialization deadline.

Next, construct a connector using that registry. This fragment assumes `connections` is still open and the registered Device ID is `env-remote-machine`:

```python
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.remote_envd.configuration import (
    WebSocketEnvdConnectionConfiguration,
)
from a13n_harness.providers.environment.remote_envd.websocket import WebSocketEnvdProviderRuntime

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

Open the returned connector as `async with await environment.open() as execution`, or supply it through a Host source. Without a Host Session owner, each execution owns and closes an independent Session on the shared Device connection. To share a Session across directory views and Runs, supply the same `EnvdSessionRecovery` owner as described below.

Close the registry at Host shutdown. Its capacity is finite, duplicate active Device connections are rejected, and connection acquisition has a finite timeout. A WebSocket Provider without a supplied connection runtime fails explicitly.

### Framework integration

Use `websockets.asyncio.server.ServerConnection` directly, or adapt your framework to `a13n_envd_client.WebSocketConnection`:

| Member                        | Required behavior                              |
| ----------------------------- | ---------------------------------------------- |
| `subprotocol`                 | Report negotiated `eip.v1`                     |
| `recv()`                      | Return one complete `str` or `bytes` message   |
| `send(message)`               | Preserve text versus binary messages           |
| `close(code=1000, reason="")` | End the accepted connection                    |
| `wait_closed()`               | Observe closure without consuming EIP messages |

Translate framework disconnects into `EOFError` or `OSError`. Set finite frame and queue limits at the listener, and let EIP exclusively read accepted messages. Authenticate before handing the connection to the SDK; neither a subprotocol nor a claimed Device ID authenticates the caller.

To try the supplied `run_websocket()` listener with a manually operated daemon, start the example Host first:

```bash
# Start the example Host first; it waits up to 60 seconds for the daemon.
uv run environment-provider-example websocket_envd \
  --port 8788 --device-id env-remote-machine \
  --credential-file /private/envd-token
```

The example checks a Bearer credential on loopback and waits up to 60 seconds. Configure the daemon with `reverse_websocket`, the matching credential file and Device ID, and `A13N_ENVD_REVERSE_WS_URL=ws://127.0.0.1:8788`. The [Envd operations guide](../a13n-envd/index.md) covers operator configuration. Replace this example listener with your application's TLS, authentication, and routing policy for deployment.

## State, concurrency and recovery

Keep target identity and Session recovery in separate Host records:

| Record                                  | Use                                      | Lifetime                                                                                                |
| --------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `EnvironmentState`                      | Select the Device by stable `device_id`  | Portable target reference; excludes endpoint, credentials, connections, Sessions, and daemon generation |
| `EnvdSessionRecovery.state`             | Attempt attachment to a retained Session | Host-private checkpoint for its original logical execution scope                                        |
| Process handles and output observations | Observe work through an execution        | Local to that execution; reopening does not restore them                                                |

Resolve an omitted cwd from authenticated Device info before accepting a Run that needs immutable directory selection. Device info and bounded directory discovery require no Session. Multiple Sessions can use one Device, but they are resource owners, not tenant isolation boundaries.

For recovery after an interrupted Host execution:

1. Create `EnvdSessionRecovery` from `a13n_harness.environment.session_recovery`. Restore an optional `EnvdSessionState` only for the original logical scope.
2. Pass `session_recovery=recovery` to `execution_connector(...)`. While active, save the observed `recovery.state` separately from `EnvironmentState` and Harness state.
3. Reconnect with current credentials. Attachment verifies the logical target, Device generation, selected Session configuration and effective descriptor. Ordinary new work may open a new Session after confirmed loss; authentication, transport and protocol errors propagate. Changed startup configuration requires explicit retirement before a successor opens. Persist retirement before closing the old Session, and retain its descriptor if close is uncertain.
4. Pass the same owner to all directory views of that binding. Closing the final execution detaches the connection and preserves the checkpoint for the next Run. The view's working directory does not partition Session identity.
5. For retained native work, use `recovery.exact(original_session_id)`. This borrower requires that exact original Session and never opens a replacement. Retire a binding only after its admitted work has settled; a previously retired configuration is never resurrected.

A lost framed carrier detaches Sessions without extending their existing idle or maximum-lifetime deadlines. Recovery requires the daemon to retain the same Session and generation. Reattachment advances the owner epoch and fences requests from the previous owner; it does not create an authorization boundary between authenticated clients. Inspect uncertain outcomes before continuing. Never replay a possibly dispatched command or mutation because the connection dropped, and use a fresh execution attempt after failed preparation.

Keep the WebSocket listener and execution in the same process, or explicitly route execution to the connection owner. The SDK's process-local registry supplies no cross-process relay.

## Connect to Harness UI

Follow the Harness UI Device binding workflow to pair a Device, select its working directory, and grant a Thread access. Registration alone grants no Run access. [Follow the steps](../a13n-harness-ui/environments-and-projects.md#add-device-bindings).

### Enable shell execution

The default daemon connection is file-only. Select explicit shell profiles or Full Control using daemon configuration. Full Control runs under the selected account and provides no tenant isolation. [Follow the steps](../a13n-envd/configuration.md#enable-commands).

### Restart or connect another Host

Preserve the instance, Host alias, state directory, and launch flags when restarting. Run a separate process and instance for another Host. [Follow the steps](../a13n-envd/configuration.md#saved-instances-and-hosts).

### Manage and revoke

Use Revoke to withdraw trust; forgetting local configuration is a different operation. Remote files and already dispatched effects remain. [Follow the steps](../a13n-harness-ui/environments-and-projects.md#revoke-a-device).

### Troubleshooting

Check the reachable HTTPS origin, Device approval, and saved credential. A revoked credential is not replaced automatically. [Follow the steps](../a13n-harness-ui/environments-and-projects.md#connect-and-approve-envd).

## Connect to the Service

For Service, operate an HTTP daemon and [register it as an external target](../a13n-service/environments.md#register-an-external-target) with its endpoint and token. Service connects to the target while the operator retains lifecycle ownership. Service does not accept self-registration.
