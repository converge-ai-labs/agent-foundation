---
title: Connect to Remote Envd
sidebarTitle: Remote Envd
description: Connect existing physical machines, VMs, or containers running Envd to your Host over direct HTTP(S) or reverse WebSocket tunnels.
---

Connect to an operator-managed machine running `a13n-envd` in either direction:

- **`http_envd`:** Your Host dials an existing HTTP(S) Envd daemon.
- **`websocket_envd`:** Envd dials outbound to your Host's WebSocket listener (works behind NAT/firewalls).

Both providers are connect-only—closing an execution detaches the Session while leaving the remote machine and workspace files intact.

## Connect to an existing HTTP daemon

Pass the daemon's base HTTPS origin (or loopback HTTP), expected `device_id`, and bearer token to the built-in `http_envd` provider, then wrap it in [`PreparedSource`](../a13n-harness/environments.md#supply-a-source):

```python
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers

(http_envd,) = select_builtin_environment_providers(("http_envd",))
connector = http_envd.execution_connector(
    {"working_directory": "/work/project"},
    configuration={"endpoint": "https://envd.example.com"},
    credential={"token": token_from_your_secret_store},
    environment_id="env-my-project",
    state=EnvironmentState(
        provider_key=http_envd.type,
        state_version="1",
        state={"device_id": "env-remote-machine"},
    ),
)
result = await executable.run("Inspect the workspace", environment=PreparedSource(connector))
```

Keep TLS verification enabled for remote endpoints (configure custom CAs via `HttpEnvdProviderRuntime.verify`).

## Integrate a reverse WebSocket Host

Own a `WebSocketEnvdConnections` registry in your server process, authenticate incoming `eip.v1` upgrades, and construct a `websocket_envd` connector:

```python
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.environment.remote_envd.configuration import WebSocketEnvdConnectionConfiguration
from a13n_harness.providers.environment.remote_envd.websocket import WebSocketEnvdProviderRuntime

# 1. Inside your authenticated WebSocket route handler:
# await connections.attach(trusted_registration.device_id, connection)

# 2. Build a connector targeting the attached device_id:
(websocket_envd,) = select_builtin_environment_providers(("websocket_envd",))
connector = websocket_envd.execution_connector(
    {"working_directory": "/work/project"},
    environment_id="env-my-project",
    state=EnvironmentState(
        provider_key=websocket_envd.type,
        state_version="1",
        state={"device_id": "env-remote-machine"},
    ),
    runtime=WebSocketEnvdProviderRuntime(
        connections, WebSocketEnvdConnectionConfiguration(connection_timeout=30)
    ),
)
```

## Session egress and credential references

Pass `session` policy in the connector recipe to enforce controlled egress and inject Host-side secrets as placeholders on a compatible Linux Envd (or select guest VM isolation via `session.isolation`):

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
}
```

See [Controlled Session egress](../a13n-envd/egress.md) and [Execution isolation](../a13n-envd/isolation.md) for daemon prerequisites.

## State, concurrency, and recovery

- **`EnvironmentState`** stores the portable `device_id` (no URLs or secrets).
- **`EnvdSessionRecovery`** (`a13n_harness.environment.session_recovery`) stores a Host-private `EnvdSessionState` checkpoint so consecutive Runs can reattach to the same retained Session within the same daemon generation without replaying uncertain commands.

## Connect to the Service

- **Harness UI:** Pair with `a13n-envd connect` ([Connect and approve Envd](../a13n-harness-ui/environments-and-projects.md#connect-and-approve-envd)), attach via [Add Device bindings](../a13n-harness-ui/environments-and-projects.md#add-device-bindings), [enable commands](../a13n-envd/configuration.md#enable-commands), keep [saved instances and Hosts](../a13n-envd/configuration.md#saved-instances-and-hosts), or [revoke a Device](../a13n-harness-ui/environments-and-projects.md#revoke-a-device).
- **Service:** Pair outbound via [Connect a client computer](../a13n-service/environments.md#connect-a-client-computer) or register an HTTP daemon via [Register an external target](../a13n-service/environments.md#register-an-external-target).
