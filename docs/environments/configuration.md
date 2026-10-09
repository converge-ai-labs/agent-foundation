---
title: Configure Providers
description: Configure a Provider's target, backend connection, and credentials separately.
---

Configure the target, backend connection, and credentials separately. Keep runtime clients in memory and save `EnvironmentState` to reconnect later.

| Value                                         | Owns                                            | Example                                                  |
| --------------------------------------------- | ----------------------------------------------- | -------------------------------------------------------- |
| Target recipe (`environment_model`)           | What this Environment should expose             | Root directory, Docker mounts, E2B template              |
| Account configuration (`configuration_model`) | Where the Host reaches the backend              | Docker daemon, E2B domain, HTTP endpoint                 |
| Credential (`credential_model`)               | Current access to that backend                  | E2B API key or HTTP EIP token                            |
| Runtime collaborator                          | Live clients, sessions, and Host allocations    | Supplied by the Host or acquired by `runtime_factory`    |
| `EnvironmentState`                            | Validated reference to an exact retained target | Container/sandbox identity and configuration fingerprint |

Call `definition.create(recipe, configuration=..., credential=..., state=...)` to acquire a runtime, or pass `runtime=...` to borrow one you already manage. With a borrowed runtime, omit account configuration and credentials. Creation validates the recipe before account inputs and acquires the runtime after validation. See [Providers and runtime](providers.md) for catalog selection and construction.

A definition owns exactly one model of each kind. There is no configuration schema version: changing the meaning of an input changes the Provider type.

The [generated field reference](configuration-reference.md) lists built-in recipes, account settings, credentials, roots, mounts, and shell profiles.

## Host outbound connections

Set `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, or their lowercase forms in the Host process environment. These trusted settings route Host-owned HTTP connections, not traffic from Device commands.

TLS verification defaults to enabled. Set `A13N_OUTBOUND_TLS_VERIFY=false` before constructing an owned HTTP client to disable certificate and hostname checks. Prefer a trusted CA for private HTTPS endpoints. Existing clients, supplied clients, and vendor SDK/WebSocket transports keep their own TLS settings.

See [Remote Envd](remote-envd.md#connect-to-an-existing-http-daemon) for endpoint and CA options, and [Session egress](../a13n-envd/egress.md) for Device command destinations.

## Direct Local

`DirectLocalEnvironmentConfiguration` requires an absolute `root.path`. The root is always writable; a reference-only mount withholds write actions through the Harness permission ceiling instead. The basic configuration is file-only: shell profiles, allowed executables, and allowed ports are empty by default.

To enable one executable, follow the [complete command example](commands.md). For shell syntax, configure an absolute shell executable and profile ID, optional fixed arguments, dialect `posix` or `powershell`, and explicit login permission. Profile IDs are unique. PowerShell profiles cannot enable login mode.

`inherit_environment` defaults to false. `allowed_environment_keys` defaults to an empty set, which rejects every explicit set or unset key; `None` allows any key. Choose deliberately. Model-authored environment changes do not bypass this policy. Absolute paths and null/control-character restrictions are validated before target use.

Defaults are a 64 MiB file value bound, 128 concurrent processes, 24-hour maximum wall time, five-second termination grace, 1 MiB buffer, and 64 GiB spool. These are this Provider's limits, not shell-tool presentation defaults. Direct Local is stateless and shares the Host account; no configuration turns it into an OS sandbox.

## Local Envd

`LocalEnvdEnvironmentConfiguration` selects an optional Device-absolute `working_directory`, `required_methods`, optional Session `egress`, and an optional `expected_boundary`. Each adapter opens an independent Session. An omitted directory uses the Device default; Hosts that persist Run selections resolve and freeze an explicit directory before accepting work. The directory is not a filesystem access root.

`LocalEnvdLaunchConfiguration` belongs to the Host runtime, not each adapter. It selects the execution identity, filesystem Sandbox, egress mode, native default directory, directory discovery, trusted executable roots, shell profiles, and resource limits. A shell profile selects `profile_id`, absolute `executable`, fixed arguments, login permission, and a script-byte bound (1 MiB by default). Executable roots and shell profiles default to empty. `sandbox` and `egress` select the Device's filesystem Sandbox and network mode, which Envd enforces for every Session; the [outer Host boundary](../a13n-envd/isolation.md) still sets available authority.

Launch defaults are 16 MiB file values, 64 KiB previews, 256 MiB per output stream, 1 GiB per Session spool, and 4 GiB aggregate Device spool. Preview cannot exceed a stream bound; each Session spool must reserve both streams and fit within the Device quota.

The Host owns one shared `LocalEnvdProviderRuntime`, its binary/bootstrap and shutdown. Adapter `close()` closes only that adapter's Session. Close the runtime separately or use its async context manager. Local Envd remains a library/Harness UI Provider and is not offered by Service.

## Docker

When `user` is omitted, the provider uses the image label `ai.a13n.environment.user`, falling back to the image USER for unlabeled custom images. `a13n-sandbox` selects `sandbox` (UID/GID 1000); an explicit recipe `user` takes precedence.

`DockerEnvironmentConfiguration` uses native Docker exec without starting the bundled Envd daemon. The default image is `ghcr.io/converge-ai-labs/a13n-sandbox:dev`; select an immutable digest when exact image identity matters. Docker uses a local image when present. With the default `pull_policy="if_missing"` it pulls an absent image; with `"never"` it fails with `environment_image_missing`. Rebuilding or pulling a tag does not recreate an existing Environment container.

The private container filesystem supplies `/workspace`. Optional bind mounts have an existing absolute `source` on the Docker Engine's machine, a container `target`, and a `read_only` flag (default true). They cannot replace `/workspace` or private command metadata. Named volumes are not a recipe option; external data is preserved on destruction.

`cpus` measures CPU cores; `memory_gb` measures decimal GB (1 GB = 1,000,000,000 bytes) with a 6 MiB minimum (0.006291456 GB); `pids_limit` bounds processes. `environment` sets ordinary variables. `init_script` runs only on a new container, and failed initialization is not implicitly replayed. Do not store secrets in these recipe fields. `disable_network` is off by default and selects Docker's `none` network when enabled.

Advanced options include user, shell, Python executable, stop grace (ten seconds), per-helper request timeout (60 seconds; includes init scripts, not Agent Runs or ordinary shell duration), file values (16 MiB), output previews (64 KiB), captured bytes per stream (16 MiB), aggregate observation/retention budgets (64 MiB each), and concurrent process observations (128). Custom images need Linux, Python 3.10+, the configured shell, writable `/workspace` and `/tmp/a13n`; Git is required only for git-ignore queries.

`DockerConnectionConfiguration.docker_host` selects the Engine socket. After a restart, the Host process must reach the same Engine to re-enter the container. The Service's [Compose deployment](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose) mounts the Engine socket and grants its non-root Service process access.

## Cloud Providers

E2B, Daytona, Modal, Vercel Sandbox, Fly.io Sprites, and Runloop use the same backend/credential/recipe separation. See the [configuration comparison](providers.md#cloud-providers) and [generated schema reference](configuration-reference.md) for all six peers.

### E2B

`E2BEnvironmentConfiguration` defaults to template `base`, root `/home/user`, user `user`, and Python `/usr/bin/python3`; paths must be absolute without traversal. Internet access defaults to true.

Sandbox timeout defaults to 3,600 seconds (30–86,400); request timeout defaults to 30 seconds (up to 300). Neither is a per-command execution deadline. File values default to 16 MiB, observation bytes to 1 MiB, active observations to 128, total retained output to 128 MiB, and query entries to 10,000. [Runtime limitations](providers.md#e2b-runtime) explains text-based observation and supported command control.

`E2BConnectionConfiguration.domain` defaults to `e2b.dev`; `E2BCredential.api_key` is a separate protected value. The Host supplies runtime credentials, maintains keepalive while retention requires it, and persists sandbox state after lifecycle outcomes. A new adapter can reconnect the exact retained sandbox; it does not reconstruct lost output or broaden access.

### Daytona

`DaytonaEnvironmentConfiguration` selects a snapshot, which sets the sandbox's resources. The root defaults to the writable `/home/daytona`. `DaytonaConnectionConfiguration` supplies organization and target region; `TokenCredential` supplies the API key.

### Modal

`ModalEnvironmentConfiguration` selects image, resources and bounded lifetime. `ModalConnectionConfiguration` names the Modal `workspace` and existing deployed App; `ModalCredential` contains token ID and secret. Filesystem snapshot stop/resume is managed-only.

### Vercel Sandbox

`VercelEnvironmentConfiguration` selects runtime, vCPUs, and session lifetime, with root `/vercel/sandbox`. `VercelConnectionConfiguration` names team and project; `TokenCredential` holds the Vercel access token. The adapter uses a named persistent Vercel sandbox with native stop and resume.

### Fly.io Sprites

`SpritesEnvironmentConfiguration` selects region and defaults to `/home/sprite`. `SpritesConnectionConfiguration` identifies the organization; `TokenCredential` holds the Sprites token. Do not schedule an explicit stop: the Sprites Provider does not support `stop()`, and Sprites sleep automatically.

### Runloop

`RunloopEnvironmentConfiguration` selects blueprint, resource size, and idle suspension interval, with root `/home/user`. `RunloopConnectionConfiguration` identifies the organization; `TokenCredential` supplies the API key.

Daytona, Sprites, and Runloop select `python3` on the guest PATH. Modal uses `/usr/local/bin/python3` and Vercel its runtime Python path. Custom guest images must supply their configured root, Python, and shell; these paths do not refer to the Host filesystem. Shared command/file limits are listed in each generated recipe schema.

## Remote HTTP / WebSocket Envd

Both remote Providers use `RemoteEnvdEnvironmentConfiguration` as their recipe; it selects a fixed Session working directory, required methods, optional Session `egress`, and an optional `expected_boundary`, not daemon mounts or target provisioning. Required method names are normalized and deduplicated. `RemoteEnvdStateData.device_id` selects the externally owned Device.

HTTP backend configuration supplies `endpoint`, initialization timeout (10 seconds), request timeout (30 seconds), maximum in-flight requests (32), and explicit plaintext-private-link opt-in (false by default). The token is a separate `HttpEnvdCredential`. The remote Provider's request timeout differs from the low-level Python EIP client's default request timeout; select the boundary you are configuring.

WebSocket backend configuration has a ten-second connection timeout. The Host connector/runtime supplies the authenticated connection and protocol settings; a URL or WebSocket transport object in the recipe or account configuration is not a supported shortcut.

Remote Envd Providers connect to externally managed targets. They do not claim managed provisioning, stop, destroy, or target keepalive ownership. See [Remote Envd](remote-envd.md) for full transport setup and [Python EIP client](../a13n-envd/python-client.md) for low-level Session operations.
