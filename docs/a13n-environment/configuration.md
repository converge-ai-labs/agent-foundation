# Configure Providers

An Environment has separate desired configuration, backend access, runtime collaborators, and current state. They answer different questions and should not be combined into a portable credential-bearing blob.

| Value                 | Owns                                                        | Example                                                      |
| --------------------- | ----------------------------------------------------------- | ------------------------------------------------------------ |
| Desired configuration | What this Environment should expose                         | Workspace root, Docker mounts, E2B template                  |
| Backend configuration | Where the Host accesses the Provider                        | Docker daemon, E2B domain, HTTP endpoint                     |
| Credential            | Current access to that backend                              | E2B API key or HTTP EIP token                                |
| Runtime               | Live clients, connector, process factory, bootstrap storage | `ProviderRuntimeContext` and Provider-specific collaborators |
| `EnvironmentState`    | Validated reference to an exact retained target             | Container/sandbox identity and configuration fingerprint     |

Use `provider.validate_configuration(schema_version="1", value=...)` for desired data, then construct a fresh adapter with authoritative state and runtime. `describe_configuration()` can explain intended capability without preparing the target; actual readiness still comes from the entered adapter. [Providers and runtime](providers.md) covers catalog selection and runtime creation.

The [complete generated field reference](configuration-reference.md) covers built-in desired/backend/credential models and nested roots, mounts, and shell profiles. It does not read secrets or evaluate a Host-specific default factory. Cross-field and target validation still apply beyond JSON-schema field bounds.

## Direct Local

`DirectLocalProviderConfiguration` requires an absolute `root.path`; `root.read_only` defaults to false. The basic configuration is file-only: shell profiles, allowed executables, and allowed ports are empty by default.

To enable one executable, follow the [complete command example](commands.md). For shell syntax, configure an absolute shell executable and profile ID, optional fixed arguments, dialect `posix` or `powershell`, and explicit login permission. Profile IDs are unique. A read-only root cannot enable process execution; PowerShell profiles cannot enable login mode.

`inherit_environment` defaults to false. `allowed_environment_keys` defaults to an empty set; choose an explicit allowlist or the supported `None` meaning deliberately. Model-authored environment changes do not bypass this policy. Absolute paths and null/control-character restrictions are validated before target use.

Defaults are a 64 MiB file value bound, 128 concurrent processes, 24-hour maximum wall time, five-second termination grace, 1 MiB buffer, and 64 GiB spool. These are this Provider's limits, not shell-tool presentation defaults. Direct Local is stateless and shares the Host account; no configuration turns it into an OS sandbox.

## Local Envd

`LocalEnvdProviderConfiguration` requires an absolute `workspace.path`. It defaults to Host network mode and empty trusted executable roots/shell profiles. Enabling file access alone does not implicitly enable commands.

A shell profile selects `profile_id`, absolute `executable`, fixed arguments, login permission, and a script-byte bound (1 MiB by default). `trusted_executable_roots` controls eligible executable locations. Choose `execution_network="deny"` when the platform supports and your workload needs that boundary; [Envd isolation](../a13n-envd/isolation.md) owns native prerequisites and fail-closed behavior.

Provider defaults are 16 MiB file values, 64 KiB output previews, 1 GiB per output stream, and 64 GiB spool. Preview cannot exceed a stream bound, and spool must reserve both streams. Do not copy the standalone daemon's smaller default output quotas into a Provider tuning table.

Runtime selection owns daemon binary/bootstrap and process construction. Desired data contains no live child process or token. Local Envd remains a library/Harness UI Provider and is not offered by Service.

## Docker

`DockerProviderConfiguration` uses an Envd-free image and native Docker exec. The default image is `ghcr.io/converge-ai-labs/a13n-docker-environment:latest`; select a deployment-owned digest for exact image identity. `pull_policy` is `if_missing`, `always`, or `never`.

The private container filesystem supplies `/workspace`. Optional host mounts have an existing absolute `source`, container `target`, and `read_only` flag (default true). They cannot replace `/workspace` or private command metadata. Named volumes are not a template option; external host data is preserved on destruction.

`cpus` measures CPU cores; `memory_mib` measures MiB; `pids_limit` bounds processes. `environment` sets ordinary variables. `init_script` runs only on a new container, and failed initialization is not implicitly replayed. Do not store secrets in these template fields. `disable_network` is off by default and selects Docker's `none` network when enabled.

Advanced options include user, shell, Python executable, stop grace (ten seconds), request timeout (60 seconds), file values (16 MiB), output previews (64 KiB), captured bytes per stream (16 MiB), aggregate observation/retention budgets (64 MiB each), and concurrent process observations (128). Custom images need Linux, Python 3.11+, the configured shell, writable `/workspace` and `/tmp`, and Git for git-ignore queries.

`DockerBackendConfiguration.docker_host` selects the Engine socket. Worker restarts must reach that same Engine. Service permits Docker only in `deployment.mode = "single_host"`; its shipped Compose uses a dedicated DinD Engine, without mounting the host socket.

## E2B

`E2BProviderConfiguration` defaults to template `base`, root `/home/user`, user `user`, and Python `/usr/bin/python3`; paths must be absolute without traversal. Internet access defaults to true and read-only defaults to false.

Sandbox timeout defaults to 3,600 seconds (30–86,400); request timeout defaults to 30 seconds (up to 300). Neither is a per-command execution deadline. File values default to 16 MiB, observation bytes to 1 MiB, active observations to 128, total retained output to 128 MiB, and query entries to 10,000. [Runtime limitations](providers.md#e2b-runtime) explains text-based observation and supported command control.

`E2BBackendConfiguration.domain` defaults to `e2b.dev`; `E2BCredential.api_key` is a separate protected value. The Host supplies runtime credentials, maintains keepalive while retention requires it, and persists sandbox state after lifecycle outcomes. A new adapter can reconnect the exact retained sandbox; it does not reconstruct lost output or broaden access.

## Remote HTTP / WebSocket Envd

Both remote Providers use `RemoteEnvdProviderConfiguration(required_methods=())`; this requests a bounded method subset, not daemon mounts or target provisioning. Required method names are normalized and deduplicated. `RemoteEnvdStateData` selects the externally owned daemon Environment identity.

HTTP backend configuration supplies `endpoint`, initialization timeout (10 seconds), request timeout (30 seconds), maximum in-flight requests (32), and explicit plaintext-private-link opt-in (false by default). The token is a separate `HttpEnvdCredential`. The remote Provider's request timeout differs from the low-level Python EIP client's default request timeout; select the boundary you are configuring.

WebSocket backend configuration has a ten-second connection timeout. The Host connector/runtime supplies the authenticated connection and protocol settings; a URL or WebSocket transport object in desired configuration is not a supported shortcut.

Remote Envd Providers connect to externally managed targets. They do not claim managed provisioning, stop, destroy, or target keepalive ownership. See [Remote Envd](remote-envd.md) for full transport setup and [Python EIP client](../a13n-envd/python-client.md) for low-level session operations.
