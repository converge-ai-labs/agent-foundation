# Configure Providers

An Environment has separate desired configuration, backend access, runtime collaborators, and current state. They answer different questions and should not be combined into a portable credential-bearing blob.

| Value                                         | Owns                                            | Example                                                  |
| --------------------------------------------- | ----------------------------------------------- | -------------------------------------------------------- |
| Target recipe (`environment_model`)           | What this Environment should expose             | Workspace root, Docker mounts, E2B template              |
| Account configuration (`configuration_model`) | Where the Host reaches the backend              | Docker daemon, E2B domain, HTTP endpoint                 |
| Credential (`credential_model`)               | Current access to that backend                  | E2B API key or HTTP EIP token                            |
| Runtime collaborator                          | Live clients, sessions, and Host allocations    | Acquired only inside the definition's `runtime_factory`  |
| `EnvironmentState`                            | Validated reference to an exact retained target | Container/sandbox identity and configuration fingerprint |

Pass all four to `definition.create(recipe, configuration=..., credential=..., state=...)`. It validates the account configuration, enforces the declared credential rule, validates the recipe, and only then acquires the runtime collaborator, so everything before that acquisition is pure. `definition.describe_environment(recipe)` explains the intended capability without preparing the target; actual readiness still comes from the entered adapter. [Providers and runtime](providers.md) covers catalog selection and runtime construction.

A definition owns exactly one model of each kind. There is no configuration schema version: changing the meaning of an input changes the Provider type.

The [complete generated field reference](configuration-reference.md) covers built-in desired/backend/credential models and nested roots, mounts, and shell profiles. It does not read secrets or evaluate a Host-specific default factory. Cross-field and target validation still apply beyond JSON-schema field bounds.

## Direct Local

`DirectLocalEnvironmentConfiguration` requires an absolute `root.path`; `root.read_only` defaults to false. The basic configuration is file-only: shell profiles, allowed executables, and allowed ports are empty by default.

To enable one executable, follow the [complete command example](commands.md). For shell syntax, configure an absolute shell executable and profile ID, optional fixed arguments, dialect `posix` or `powershell`, and explicit login permission. Profile IDs are unique. A read-only root cannot enable process execution; PowerShell profiles cannot enable login mode.

`inherit_environment` defaults to false. `allowed_environment_keys` defaults to an empty set; choose an explicit allowlist or the supported `None` meaning deliberately. Model-authored environment changes do not bypass this policy. Absolute paths and null/control-character restrictions are validated before target use.

Defaults are a 64 MiB file value bound, 128 concurrent processes, 24-hour maximum wall time, five-second termination grace, 1 MiB buffer, and 64 GiB spool. These are this Provider's limits, not shell-tool presentation defaults. Direct Local is stateless and shares the Host account; no configuration turns it into an OS sandbox.

## Local Envd

`LocalEnvdEnvironmentConfiguration` requires an absolute `workspace.path`. It defaults to Host network mode and empty trusted executable roots/shell profiles. Enabling file access alone does not implicitly enable commands.

A shell profile selects `profile_id`, absolute `executable`, fixed arguments, login permission, and a script-byte bound (1 MiB by default). `trusted_executable_roots` controls eligible executable locations. Choose `execution_network="deny"` when the platform supports and your workload needs that boundary; [Envd isolation](../a13n-envd/isolation.md) owns native prerequisites and fail-closed behavior.

Provider defaults are 16 MiB file values, 64 KiB output previews, 1 GiB per output stream, and 64 GiB spool. Preview cannot exceed a stream bound, and spool must reserve both streams. Do not copy the standalone daemon's smaller default output quotas into a Provider tuning table.

Runtime selection owns daemon binary/bootstrap and process construction. Desired data contains no live child process or token. Local Envd remains a library/Harness UI Provider and is not offered by Service.

## Docker

`DockerEnvironmentConfiguration` uses an Envd-free image and native Docker exec. The default image is `ghcr.io/converge-ai-labs/a13n-docker-environment:dev`; select an immutable digest when exact image identity matters. Docker uses a local image when present and pulls only when absent. Rebuilding or pulling a tag does not recreate an existing Environment container.

The private container filesystem supplies `/workspace`. Optional host mounts have an existing absolute `source`, container `target`, and `read_only` flag (default true). They cannot replace `/workspace` or private command metadata. Named volumes are not a template option; external host data is preserved on destruction.

`cpus` measures CPU cores; `memory_gb` measures decimal GB (1 GB = 1,000,000,000 bytes) with a 6 MiB minimum (0.006291456 GB); `pids_limit` bounds processes. `environment` sets ordinary variables. `init_script` runs only on a new container, and failed initialization is not implicitly replayed. Do not store secrets in these template fields. `disable_network` is off by default and selects Docker's `none` network when enabled.

Advanced options include user, shell, Python executable, stop grace (ten seconds), per-helper request timeout (60 seconds; includes init scripts, not Agent Runs or ordinary shell duration), file values (16 MiB), output previews (64 KiB), captured bytes per stream (16 MiB), aggregate observation/retention budgets (64 MiB each), and concurrent process observations (128). Custom images need Linux, Python 3.10+, the configured shell, writable `/workspace` and `/tmp/a13n`; Git is required only for git-ignore queries.

`DockerConnectionConfiguration.docker_host` selects the Engine socket. Worker restarts must reach that same Engine. Service permits Docker only in `deployment.mode = "single_host"`; the shipped Compose mounts the host socket and grants its non-root Service process access.

Use **Test image** beside the template image field before saving when checking a custom image. The test runs on the selected Worker Engine and returns the exact image ID used by the temporary container plus file, command/output, and process-control checks. It omits the init script and external mounts; leaving the editor or changing the draft sends an explicit cancellation request. The Provider details show Engine connectivity separately from whether the Provider is enabled. Normal Environment creation still checks requirements even if no manual test ran.

## Cloud providers

E2B, Daytona, Modal, Vercel Sandbox, Fly.io Sprites, and Runloop use the same backend/credential/template separation. See the [configuration comparison](providers.md#cloud-providers) and [generated schema reference](configuration-reference.md) for all six peers.

### E2B

`E2BEnvironmentConfiguration` defaults to template `base`, root `/home/user`, user `user`, and Python `/usr/bin/python3`; paths must be absolute without traversal. Internet access defaults to true and read-only defaults to false.

Sandbox timeout defaults to 3,600 seconds (30–86,400); request timeout defaults to 30 seconds (up to 300). Neither is a per-command execution deadline. File values default to 16 MiB, observation bytes to 1 MiB, active observations to 128, total retained output to 128 MiB, and query entries to 10,000. [Runtime limitations](providers.md#e2b-runtime) explains text-based observation and supported command control.

`E2BBackendConfiguration.domain` defaults to `e2b.dev`; `E2BCredential.api_key` is a separate protected value. The Host supplies runtime credentials, maintains keepalive while retention requires it, and persists sandbox state after lifecycle outcomes. A new adapter can reconnect the exact retained sandbox; it does not reconstruct lost output or broaden access.

### Daytona

`DaytonaEnvironmentConfiguration` selects snapshot and resources, defaulting to writable `/home/daytona`. `DaytonaConnectionConfiguration` supplies organization and target region; `TokenCredential` supplies the API key.

### Modal

`ModalEnvironmentConfiguration` selects image, resources and bounded lifetime. `ModalConnectionConfiguration` names the workspace and existing deployed App; `ModalCredential` contains token ID and secret. Filesystem snapshot stop/resume is managed-only.

### Vercel Sandbox

`VercelEnvironmentConfiguration` selects runtime, vCPUs, and session lifetime, with root `/vercel/sandbox`. `VercelConnectionConfiguration` names team and project; `TokenCredential` holds the Vercel access token. Persistent native lifecycle is intrinsic to the adapter.

### Fly.io Sprites

`SpritesEnvironmentConfiguration` selects region and defaults to `/home/sprite`. `SpritesConnectionConfiguration` identifies the organization; `TokenCredential` holds the Sprites token. Disable explicit stop retention because Sprites sleep automatically.

### Runloop

`RunloopEnvironmentConfiguration` selects blueprint, resource size, and idle suspension interval, with root `/home/user`. `RunloopConnectionConfiguration` identifies the organization; `TokenCredential` supplies the API key.

Daytona, Sprites, and Runloop select `python3` on the guest PATH. Modal uses `/usr/local/bin/python3` and Vercel its runtime Python path. Custom guest images must supply their configured root, Python, and shell; these paths do not refer to the Host filesystem. Shared command/file limits are listed in each generated recipe schema.

## Remote HTTP / WebSocket Envd

Both remote Providers use `RemoteEnvdEnvironmentConfiguration(required_methods=())` as their recipe; this requests a bounded method subset, not daemon mounts or target provisioning. Required method names are normalized and deduplicated. `RemoteEnvdStateData` selects the externally owned daemon Environment identity.

HTTP backend configuration supplies `endpoint`, initialization timeout (10 seconds), request timeout (30 seconds), maximum in-flight requests (32), and explicit plaintext-private-link opt-in (false by default). The token is a separate `HttpEnvdCredential`. The remote Provider's request timeout differs from the low-level Python EIP client's default request timeout; select the boundary you are configuring.

WebSocket backend configuration has a ten-second connection timeout. The Host connector/runtime supplies the authenticated connection and protocol settings; a URL or WebSocket transport object in desired configuration is not a supported shortcut.

Remote Envd Providers connect to externally managed targets. They do not claim managed provisioning, stop, destroy, or target keepalive ownership. See [Remote Envd](remote-envd.md) for full transport setup and [Python EIP client](../a13n-envd/python-client.md) for low-level session operations.
