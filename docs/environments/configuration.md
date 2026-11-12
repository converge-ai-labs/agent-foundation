---
title: Configure Providers
description: Choose a target, supply its connection settings, and open it with current credentials.
---

Start by choosing a [backend](index.md#choose-a-backend). Then prepare its target inputs, account settings, and credentials separately:

| Input                                         | What to put there                        | Example                                            |
| --------------------------------------------- | ---------------------------------------- | -------------------------------------------------- |
| Target configuration (`environment_model`)    | Workspace and execution settings         | Root directory, container mounts, required methods |
| Account configuration (`configuration_model`) | Backend location and account selection   | Docker Engine, E2B domain, HTTP endpoint           |
| Credential (`credential_model`)               | Current access secret                    | API key or HTTP EIP token                          |
| Runtime collaborator                          | A Host-owned live connection or runtime  | Shared Local Envd Device                           |
| `EnvironmentState`                            | A saved reference to the selected target | Container or sandbox identity                      |

Call `definition.execution_connector(recipe, configuration=..., credential=..., state=...)` to validate these inputs and construct a connector. Call `open()` when you are ready to acquire execution resources. To borrow a Host-owned connection, pass `runtime=...` with account settings and omit `credential`.

Creating or starting managed targets is a separate step: `management.open_provider(configuration=..., credential=...)` acquires the management client. Follow [Lifecycle and state](lifecycle.md) for that workflow. Save target state for later connections; keep live clients in memory. To continue an existing Envd Session, use a separate [Session recovery hint](remote-envd.md#state-concurrency-and-recovery).

The sections below explain the decisions for each backend. Use the [generated field reference](configuration-reference.md) for complete schemas, defaults, and numeric limits.

## Host outbound connections

For an HTTP proxy, set `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, or `NO_PROXY` in the Host process environment; lowercase forms are also supported. These settings route Host-owned HTTP connections. Configure [Session egress](../a13n-envd/egress.md) separately for commands running on the Device.

Keep TLS verification enabled and use a trusted CA for private HTTPS endpoints. Setting `A13N_OUTBOUND_TLS_VERIFY=false` before constructing an owned HTTP client disables certificate and hostname checks. Existing or supplied clients, vendor SDKs, and WebSocket transports retain their own TLS settings. See [Remote Envd](remote-envd.md#connect-to-an-existing-http-daemon) for endpoint and CA configuration.

## Direct Local

1. Set `root.path` to an existing absolute Host directory, as in the [quickstart](getting-started.md).
2. Keep the default file-only configuration, or add allowed executables using the [command example](commands.md).
3. For shell syntax, add a shell profile with a unique ID, absolute executable, fixed arguments if needed, and `posix` or `powershell` dialect. Enable login explicitly for a supported POSIX shell; PowerShell profiles do not support login mode.
4. Select environment-variable access. `inherit_environment` defaults to false. An empty `allowed_environment_keys` rejects all explicit set/unset keys; list allowed keys or use `None` to allow any key.

The root configuration permits writes. To expose it as a reference-only Harness mount, withhold write actions in the mount's permission ceiling. Direct Local runs under the Host account and provides no OS sandbox. It is stateless, so its connector uses `state=None`.

The Provider's resource limits govern file values, concurrent processes, command wall time, and retained output. They are separate from the tools' display limits; look up their defaults in the [field reference](configuration-reference.md).

## Local Envd

Configure Local Envd at two levels:

1. **Launch the Device.** Give the Host runtime a `LocalEnvdLaunchConfiguration`: execution identity, filesystem sandbox, network mode, default directory, executable roots, shell profiles, and resource limits. Executable roots and shell profiles start empty. The [Host boundary](../a13n-envd/isolation.md) determines which isolation features are available.
2. **Select each Session and directory view.** Put startup policy in `LocalEnvdEnvironmentConfiguration.session`: working directory, sandbox, `egress_mode`, `egress`, lifecycle and command selection. The outer `working_directory` selects the directory view; `required_methods` and `expected_boundary` validate the resulting execution. A shared Host Session owner lets multiple views borrow one Session. Filesystem policy selects what the worker may access; view cwd only selects where work begins.

Omitting the working directory uses the Device default. A Host that persists Run selections resolves an explicit directory before accepting work. Output limits must fit together: previews fit within each stream, each Session reserves both streams, and Session spools fit within the Device quota.

Keep the shared `LocalEnvdProviderRuntime` alive until all borrowing executions close, then close it separately or leave its async context. Execution close releases its borrower; a shared Session owner detaches on final release and retains recovery state. Local Envd is available to library callers and Harness UI; Service does not offer this provider.

## Docker

Use [Docker runtime](providers.md#docker-runtime) for target settings, image requirements, and shared Engine clients. In the field reference, `DockerRecipe` manages a target and `DockerEnvironmentConfiguration` connects to the selected target.

## Cloud Providers

Use [Cloud Providers](cloud-providers.md) for account settings, credentials, runtime differences, and desktop preparation. The [field reference](configuration-reference.md#cloud-providers) separates fixed-target Harness inputs from Service management recipes, which can prepare automatic images or snapshots. Constructing a connector does not create a billable target.

### E2B

For setup and backend limits, see [E2B](cloud-providers.md#e2b-runtime).

### Daytona

For setup and backend limits, see [Daytona](cloud-providers.md#daytona).

### Modal

For setup and backend limits, see [Modal](cloud-providers.md#modal).

### Vercel Sandbox

For setup and backend limits, see [Vercel Sandbox](cloud-providers.md#vercel-sandbox).

### Fly.io Sprites

For setup and backend limits, see [Fly.io Sprites](cloud-providers.md#flyio-sprites).

### Runloop

For setup and backend limits, see [Runloop](cloud-providers.md#runloop).

### boxd

For setup and backend limits, see [boxd](cloud-providers.md#boxd).

## Remote HTTP / WebSocket Envd

1. Register or select the externally managed Device. `RemoteEnvdStateData.device_id` identifies it.
2. Set startup policy under `RemoteEnvdEnvironmentConfiguration.session`, including optional `egress_mode` and reference-only `egress`. Set outer `working_directory` for the directory view, plus `required_methods` and optional `expected_boundary`. Session grants must fit the daemon's deployment ceiling.
3. Supply transport settings. HTTP uses an `endpoint` and separate `HttpEnvdCredential`; WebSocket uses the Host's authenticated connector/runtime.
4. Open an execution to acquire the Session, and close it after use. Target provisioning, stop, destruction, and keepalive remain with the external owner.

HTTP defaults to a 10-second initialization timeout, 30-second request timeout, and 32 in-flight requests. Plaintext private links require explicit opt-in. WebSocket defaults to a 10-second connection timeout; its Host runtime owns the connection and protocol settings. These Provider timeouts are distinct from the low-level client's defaults.

Continue to [Remote Envd](remote-envd.md) for transport setup or the [Python EIP client](../a13n-envd/python-client.md) for direct Session operations.
