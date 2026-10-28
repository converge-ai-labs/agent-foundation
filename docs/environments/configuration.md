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
2. **Select each Session.** Set `LocalEnvdEnvironmentConfiguration.working_directory`, `required_methods`, and optional `egress` and `expected_boundary`. Each execution opens an independent Session. The working directory selects where work begins; filesystem policy selects what it may access.

Omitting the working directory uses the Device default. A Host that persists Run selections resolves an explicit directory before accepting work. Output limits must fit together: previews fit within each stream, each Session reserves both streams, and Session spools fit within the Device quota.

Keep the shared `LocalEnvdProviderRuntime` alive until all borrowing executions close, then close it separately or leave its async context. Execution close releases its Session. Local Envd is available to library callers and Harness UI; Service does not offer this provider.

## Docker

For a managed Docker target:

1. Set `DockerConnectionConfiguration.docker_host` to the Engine the Host can reach. Reconnecting later requires access to the same Engine. Service's [Compose deployment](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose) shows socket access for its non-root process.
2. Choose an image and execution route. Service's `DockerRecipe` defaults to `execution="native"` and `ghcr.io/converge-ai-labs/a13n-sandbox:dev`. Native execution uses Docker exec; `execution="envd_http"` uses the image's Envd daemon.
3. Add resource limits and optional bind mounts. Mount `source` paths must already exist on the Engine's machine; `read_only` defaults to true. Keep `/workspace` and private command metadata under the container's own filesystem. Named volumes are not a recipe option.
4. Create the target, save its state, and open a connector. Pulling or rebuilding an image tag does not replace an existing container.

The default `pull_policy="if_missing"` pulls only absent images. `"never"` reports `environment_image_missing` when the image is absent. Use an immutable digest when you need exact image identity. Native user selection uses an explicit recipe `user`, then `ai.a13n.environment.user`, then the image USER. The standard sandbox image selects UID/GID 1000.

Custom native images need Linux, Python 3.10+, the configured shell, and writable `/workspace` and `/tmp/a13n`. Git is needed for git-ignore queries. `init_script` runs on new containers; failed initialization is not implicitly replayed. Its helper request timeout is separate from ordinary shell duration. Put ordinary variables in `environment`, and keep secrets out of recipe fields. `memory_gb` uses decimal GB, while `cpus` measures cores. `disable_network=true` selects Docker's `none` network. Destruction preserves external bind-mounted data.

For HTTP EIP, configure the connection's `envd_http` with a Worker-reachable host and bind address. Service preserves the image entrypoint and publishes container port 8888 to a Docker-assigned host port. Use a compatible Envd image and keep networking enabled. The image owns execution identities, so this route rejects recipe `user` overrides. Add `desktop: true` to include observable, input-ready desktop readiness. Plain HTTP requires loopback or an explicitly trusted private link. Follow [Create a Docker desktop](../a13n-service/environments.md#create-a-docker-desktop) for the Console steps.

## Cloud Providers

Choose the account and target settings below, then compare runtime behavior in [Cloud Providers](providers.md#cloud-providers). The [field reference](configuration-reference.md) lists all six providers' schemas and limits. Creation and retention need Host lifecycle management; constructing an execution connector does not allocate a cloud target.

### E2B

Set `E2BConnectionConfiguration.domain` (default `e2b.dev`) and supply a separate `E2BCredential.api_key`. Service's `E2BRecipe` defaults to template `base`, root `/home/user`, user `user`, and Python `/usr/bin/python3`, with internet access enabled.

Choose the sandbox lifetime separately from request timeouts. Defaults are 3,600 seconds for the sandbox and 30 seconds for a request; neither sets a per-command deadline. The Host maintains keepalive when retention needs it and saves state after lifecycle operations. Reconnection selects that exact sandbox; retained output follows the [E2B runtime limits](providers.md#e2b-runtime).

For a Service desktop without a custom template, use automatic private preparation: `template="auto"`, `execution="envd_http"`, and `desktop_assistance=true`. First creation builds the template; later creations reuse it within the same connection and credential scope. Track the actual stage in the environment inspector and open **View desktop** once ready. Saving configuration alone does not build the template. See [desktop preparation](providers.md#e2b-desktop-preparation).

### Daytona

Set the region and optional organization constraint in `DaytonaConnectionConfiguration`; supply the API key through `TokenCredential`. An omitted organization is discovered from the key. Select a snapshot in `DaytonaEnvironmentConfiguration`; it determines sandbox resources, with working directory `/workspace`. Harness needs a concrete snapshot. Service desktop recipes can prepare one on first creation and reuse it afterward.

### Modal

Choose the image, resources, and finite lifetime in `ModalEnvironmentConfiguration`. In `ModalConnectionConfiguration`, optionally identify the workspace and select an App (default `a13n-sandboxes`). Supply token ID and secret through `ModalCredential`. Service automatic desktops prepare the App and image on first creation. Filesystem-snapshot stop/resume belongs to managed targets.

### Vercel Sandbox

Set team and project in `VercelConnectionConfiguration`, and put the access token in `TokenCredential`. Native execution uses `/vercel/sandbox`. For EIP, select `execution="envd_http"` and an operator-built `snapshot`; this route uses `/workspace` and is required for `desktop=true`. Service adds `desktop_assistance=true` for human access. The provider retains a named persistent sandbox with native stop/resume and a bounded vendor session lifetime. Follow [snapshot preparation](providers.md#vercel-sandbox).

### Fly.io Sprites

Set the organization in `SpritesConnectionConfiguration` and supply a `TokenCredential`. The target configuration selects the region and defaults to `/home/sprite`. Sprites sleep automatically; the provider has no explicit `stop()` operation to schedule.

For Service desktops, `desktop=true` selects `execution="envd_http"` and installs the desktop in each new Sprite. This route uses a private TCP tunnel and working directory `/workspace`; Service enables human control by default. See [automatic Sprites desktops](../a13n-service/environments.md#automatic-sprites-desktops).

### Runloop

Set the organization in `RunloopConnectionConfiguration` and supply the API key through `TokenCredential`. Choose the blueprint, resource size, and idle suspension interval in `RunloopEnvironmentConfiguration`; the root is `/home/user`.

For custom guest images, provide the configured root, Python, and shell inside the guest. Daytona, Sprites, and Runloop use guest-PATH `python3`. Modal requires an Envd image and uses guest-PATH `python3` for management helpers; Vercel uses its runtime Python path.

## Remote HTTP / WebSocket Envd

1. Register or select the externally managed Device. `RemoteEnvdStateData.device_id` identifies it.
2. Set the Session working directory, required methods, optional `egress`, and optional `expected_boundary` in `RemoteEnvdEnvironmentConfiguration`. Configure daemon mounts where the daemon runs.
3. Supply transport settings. HTTP uses an `endpoint` and separate `HttpEnvdCredential`; WebSocket uses the Host's authenticated connector/runtime.
4. Open an execution to acquire the Session, and close it after use. Target provisioning, stop, destruction, and keepalive remain with the external owner.

HTTP defaults to a 10-second initialization timeout, 30-second request timeout, and 32 in-flight requests. Plaintext private links require explicit opt-in. WebSocket defaults to a 10-second connection timeout; its Host runtime owns the connection and protocol settings. These Provider timeouts are distinct from the low-level client's defaults.

Continue to [Remote Envd](remote-envd.md) for transport setup or the [Python EIP client](../a13n-envd/python-client.md) for direct Session operations.
