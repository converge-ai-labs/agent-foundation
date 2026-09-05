# Built-in Environment Providers

## Design Position

`a13n-environment-provider` ships four Providers:

| Provider key        | Backing target                                   | Operation backend            |
| ------------------- | ------------------------------------------------ | ---------------------------- |
| `a13n.direct-local` | One configured existing local root               | Direct Local                 |
| `a13n.local-envd`   | One configured existing workspace and local envd | EIP                          |
| `a13n.docker`       | One Docker container running envd                | EIP                          |
| `a13n.e2b`          | One native E2B sandbox                           | E2B SDK and bounded commands |

Every built-in follows the same three-entity model: an inert `EnvironmentProvider`, a fresh process-local `Environment`, and optional `EnvironmentState`. Local Envd and Docker use EIP for Agent file, shell, process, output, and port operations after preparation. They do not bypass EIP through vendor filesystem, exec, log, or copy APIs.

## Shared Configuration Rules

Each Provider owns an exact versioned Pydantic configuration model. Configuration is desired behavior and contains no API key, Docker socket, container/sandbox ID, PID, resolved endpoint, EIP credential, live client, or session.

Provider discovery, configuration validation, Provider construction, `create_environment()` and scope `enter()` are inert. They do not inspect the filesystem, invoke a subprocess, connect to Docker or E2B, allocate bootstrap material, or open EIP.

Fresh Host runtime collaborators supply stable per-Environment creation/ownership correlation, current credentials, SDK boundaries, executable selection, bootstrap storage, and private runtime allocation. Providers never discover ambient credentials or executables unless the Host explicitly invokes a separate convenience resolver and passes its result.

Each scoped Environment exposes a configured descriptor without target I/O; preparation validates the live descriptor before operations. Harness intersects its operation families with the Run-local access ceiling. Unsupported operations fail; no built-in emulates an operation through another backend.

Stable environment identity is allocated by the Host per Environment and supplied through runtime collaborators, never frozen into a reusable template recipe. Providers may retain its non-secret target-ownership correlation in state/labels when needed to recover a dispatched creation.

## Direct Local

### Configuration

`a13n.direct-local` configuration schema version `1` has this conceptual public shape:

```python
class DirectLocalRootConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    read_only: bool = False


class DirectLocalShellProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str
    executable: Path
    fixed_arguments: tuple[str, ...] = ()
    allow_login: bool = False


class DirectLocalProviderConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    root: DirectLocalRootConfiguration
    shell_profiles: tuple[DirectLocalShellProfile, ...] = ()
    allowed_executables: frozenset[Path] = frozenset()
    allowed_environment_keys: frozenset[str] = frozenset()
    allowed_ports: frozenset[int] = frozenset()
    max_value_bytes: int = 16 * 1024 * 1024
    max_concurrent_processes: int = 128
    max_wall_time_seconds: float = 24 * 60 * 60
    terminate_grace_seconds: float = 5.0
    max_buffer_bytes: int = 1024 * 1024
    max_spool_bytes: int = 64 * 1024 * 1024 * 1024
```

The root, shell executables, and allowed executables are absolute after user expansion. Host-supplied runtime identities are bounded and nonblank; profile IDs are unique; ports and limits are valid and positive. A read-only root cannot enable shell profiles or allowed executables because an allowed native process could mutate files through the embedding OS account.

### State and lifecycle

Direct Local denotes access to an existing Host-controlled directory. It does not allocate, own, lock, retain, back up, or delete that directory.

Direct Local is stateless for re-entry and `dump_state()` returns `None`. The selected configuration remains the deterministic target. The Provider can compute an internal configuration fingerprint for validation and observation, but it does not require an `EnvironmentState` merely to repeat access to the same configured root.

`prepare()` validates that the configured root exists and is accessible, constructs fresh local file/process/output facets, and establishes Run-local process ownership. Inaccessibility is `unknown`/unavailable rather than authoritative target absence; Direct Local never creates the root automatically.

`close()` terminates and releases only processes, streams, retained output, and local handles owned by that adapter. It does not change or delete the root. `stop()` and `destroy()` are unsupported for the caller-owned backing directory. Direct Local declares no keepalive requirement; retention policies must disable target stop/delete.

Direct Local makes no sandbox, account isolation, network isolation, or race-free filesystem-broker claim. Its confinement is a provider operation policy over one Host-selected root. A hostile same-account process can race native filesystem changes.

## Local Envd

### Configuration and runtime

`a13n.local-envd` is the built-in local isolated execution provider. Configuration schema version `1` is credential-free:

```python
class LocalEnvdNetworkMode(StrEnum):
    HOST = "host"
    DENY = "deny"


class LocalEnvdWorkspaceConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    read_only: bool = False


class LocalEnvdShellProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str
    executable: Path
    fixed_arguments: tuple[str, ...] = ()
    allow_login: bool = False
    max_script_bytes: int = 1024 * 1024


class LocalEnvdProviderConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace: LocalEnvdWorkspaceConfiguration
    execution_network: LocalEnvdNetworkMode = LocalEnvdNetworkMode.HOST
    trusted_executable_roots: tuple[Path, ...] = ()
    shell_profiles: tuple[LocalEnvdShellProfile, ...] = ()
    max_file_bytes: int = 16 * 1024 * 1024
    max_output_preview_bytes: int = 64 * 1024
    max_output_bytes_per_stream: int = 1024 * 1024 * 1024
    max_spool_bytes: int = 64 * 1024 * 1024 * 1024
```

Paths are absolute after user expansion. The workspace must already exist. Local Envd always selects required native isolation; configuration cannot disable it. The Provider maps this model to one strict envd configuration and does not expose arbitrary envd JSON, transport selection, native runtime paths, payload identities, or arbitrary environment variables.

A fresh `LocalEnvdProviderRuntime` supplies one exact absolute envd executable and a Host-owned allocator for a protected private runtime parent. Provider construction records these collaborators without inspection or launch.

The package exposes a separate Host convenience `resolve_agent_envd_executable()` helper. It can resolve an explicit path, `A13N_AGENT_ENVD_EXECUTABLE`, then `shutil.which`. The helper validates the result and performs no download or installation. Low-level Provider construction never invokes it or loads `.env`.

### State and lifecycle

Local Envd owns no durable provider target beyond the Host-selected workspace. Each fresh adapter launches a new process-local daemon generation and closes it with the adapter. `dump_state()` therefore returns `None`.

Raw PID, subprocess handle, process tree, pipes, private runtime path, EIP credential, endpoint, daemon generation, descriptor, and Session are process-local runtime data. They never enter `EnvironmentState` or Harness continuation.

`prepare()`:

1. validates the workspace and exact executable shape/version;
2. runs the production-equivalent isolation probe;
3. allocates a protected private runtime;
4. starts one daemon generation over trusted stdio or another fixed local carrier;
5. establishes EIP initialization, Environment identity, method compatibility, and readiness;
6. exposes fresh EIP operation facets.

A failure after process launch unconditionally terminates the owned process tree, closes carriers, and removes private runtime data. Cancellation does not leave a reusable daemon reference.

`close()` fences new operations, closes EIP, terminates the complete owned daemon process tree under bounded grace, closes pipes, and removes its private runtime. It never deletes or mutates the shared workspace merely because the adapter closes. There is no durable daemon target to stop or delete; these target actions are unsupported. Local Envd requires no target keepalive, and template stop/delete policies must be disabled for its caller-owned workspace.

Because every independent Run creates a fresh daemon, Local Envd state does not preserve daemon identity across Runs. Filesystem continuity comes from the configured Host workspace.

## Docker

### Configuration

`a13n.docker` configuration schema version `1` declares one envd container. Its conceptual shape includes:

```python
class DockerImagePullPolicy(StrEnum):
    IF_MISSING = "if_missing"
    ALWAYS = "always"
    NEVER = "never"


class DockerMountKind(StrEnum):
    LAYER = "layer"
    BIND = "bind"
    VOLUME = "volume"


class DockerMountConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mount_id: str
    container_path: PurePosixPath
    kind: DockerMountKind = DockerMountKind.LAYER
    source: str | Path | None = None
    read_only: bool = False


class DockerProviderConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    image: str = "ghcr.io/converge-ai-labs/agent-foundation-sandbox:latest"
    pull_policy: DockerImagePullPolicy = DockerImagePullPolicy.IF_MISSING
    mounts: tuple[DockerMountConfiguration, ...]
    root_mount_id: str = "workspace"
    trusted_executable_roots: tuple[PurePosixPath, ...] = ()
    shell_profiles: tuple[EnvdShellProfile, ...]
    nano_cpus: int | None = None
    memory_bytes: int | None = None
    pids_limit: int | None = None
    stop_grace_seconds: int = 10
    max_file_bytes: int = 16 * 1024 * 1024
    max_output_preview_bytes: int = 64 * 1024
    max_output_bytes_per_stream: int = 1024 * 1024 * 1024
    max_spool_bytes: int = 64 * 1024 * 1024 * 1024
```

The default configuration exposes writable `/workspace` from the container layer and one fixed Bash profile. Bind sources are existing absolute Host paths. Volume sources name existing external Docker volumes; schema version `1` does not create or own named volumes.

Container paths are absolute normalized POSIX paths and cannot overlap the fixed bootstrap or envd runtime trees. IDs, image references, paths, fixed arguments, and limits are bounded and validated. Docker v1 uses the ordinary bridge network and publishes one fixed EIP port to a Docker-assigned Host port bound only to `127.0.0.1`.

The schema accepts no remote Engine endpoint, Docker socket, raw Docker mount, arbitrary command/entrypoint/environment, privileged mode, device, capability, network mode, public port publication, user override, or provider-created volume policy.

### Runtime and bootstrap

A fresh `DockerProviderRuntime` supplies:

```python
@dataclass(frozen=True, slots=True)
class DockerProviderRuntime:
    engine: DockerEngine
    bootstrap_store: DockerBootstrapStore
```

`DockerEngine` is the typed async boundary for local-topology validation, image inspection/pull/resolution, container create/start/inspect/stop/remove, exact label queries, and loopback route inspection. Blocking Docker SDK work runs off the event loop through bounded worker threads and finite client timeouts.

The runtime must prove that the Engine is local and that the Provider process can reach a `127.0.0.1` published port. An unprovable or remote topology fails before image resolution, bootstrap allocation, or container mutation.

`DockerBootstrapStore` publishes protected Host-local bootstrap material atomically and supports exact create/recover/replace/remove by a bounded non-secret correlation. Material contains the Environment identity, configuration fingerprint, strict envd configuration, and current HTTP bearer credential. The built-in directory store keeps its Host root private to the owning account; individual allocation files can remain readable by the fixed non-root container user only because Docker bind-mounts the allocation below that private Host root. Returned process-local values can include an absolute Host directory accepted as a local-Engine bind source. Credentials never enter configuration, state, Docker labels, endpoint URLs, logs, traces, or model-visible values.

The Provider uses no Docker archive, copy, exec, or logs API for bootstrap, repair, readiness, or Agent operations.

### State

Docker `EnvironmentState` uses `state_version="1"` with this provider payload:

```python
class DockerEnvironmentStateData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_id: str
    container_id: str
    image_id: str
    bootstrap_correlation: str
    configuration_fingerprint: str
    create_correlation: str
```

`container_id` and `image_id` are immutable identities returned by the selected local Engine. State contains no endpoint, Host port, bind path, bearer credential, Docker client/container object, envd generation, descriptor, or Session.

The Provider applies bounded labels for provider key, state version, Environment identity, configuration fingerprint, bootstrap correlation, and create correlation. Before adopting, starting, stopping, or removing a container, it validates the exact ID, resolved image, all labels, fixed command/bootstrap mount, configured mounts and limits, non-root launch contract, EIP publication, and bootstrap evidence. An ID match with incompatible metadata is a conflict.

### Preparation and replacement

With no state, Host-authorized `prepare()`:

1. validates local topology and mount sources;
2. applies image pull policy and resolves an immutable image ID;
3. creates or recovers one exact bootstrap allocation by create correlation;
4. dispatches one completely configured labeled container create;
5. starts and validates that exact container;
6. records changed state immediately;
7. resolves only the authoritative loopback EIP route;
8. opens a fresh EIP Session and completes initialization and readiness.

With state, `prepare()` inspects and validates the exact container:

- a compatible running container is re-entered without replacement;
- a compatible created or exited container can receive an atomic credential replacement and be started;
- authoritative container absence permits creation of one replacement under a fresh exact create correlation and updates state;
- container or bootstrap unavailability, uncertain inspection, incompatible metadata, or an ambiguous label query fails without creating another container.

A failure after a create or replacement preserves the new state through `dump_state()` even if EIP readiness later fails. A known failure before container dispatch cleans newly allocated bootstrap material. Cancellation after dispatch is an unknown outcome unless exact follow-up evidence proves the result.

`close()` fences operations and closes EIP/SDK sessions, readiness work, and process-local handles. It does not stop or remove the container and does not delete bootstrap material.

`destroy()` validates the exact represented target, stops it when necessary, removes only that container, confirms absence, then removes its exact bootstrap allocation. It never removes bind sources, external named volumes, or unrelated containers. Container absence with matching bootstrap cleanup failure is still cleanup failure. Unknown inspection or mutation outcome preserves state.

`stop()` stops the exact validated container and retains its state and writable filesystem for later start. It does not promise to preserve process memory. Docker declares stop and destroy support and no Provider timeout-renewal requirement. Retention acts on the durable container independently of local scope close.

### Prune discovery

Docker exposes bounded provider-specific discovery sufficient for an authorized Host to find targets carrying the exact provider labels and correlations. Discovery returns candidates or unknown evidence; it never adopts, starts, stops, removes, or repairs a container.

Prune policy, candidate persistence, grace periods, sharing checks, and destroy authorization remain Host-private behavior. The shared API does not define a prune-record class or database schema.

## E2B

### Configuration and runtime

`a13n.e2b` configuration schema version `1` selects `template` (default `base`), logical filesystem `root` (default `/home/user`), sandbox `user`, Python and Bash executable paths, sandbox and request timeouts, sandbox-wide internet access, read-only access, and finite file, output, process-record, command-duration and traversal limits. It contains no credential, sandbox ID, endpoint or live SDK object.

`E2BProviderRuntime` supplies an explicit secret API key, backend domain, managed/external selection and optional Host operation correlation. Generic Provider Backend configuration contains the domain; its credential contains `api_key`. The library does not load `.env` or acquire credentials. Provider construction, discovery and scope entry are inert.

Operations use the official asynchronous E2B SDK. Bounded Python standard-library commands run through the native command API; no daemon, executable upload, template build, EIP endpoint or daemon version negotiation is required. The selected template must provide Linux, Python 3.11 or later with pidfd support, Bash, the selected account and existing root. Git ignore queries additionally require Git. The default E2B base template satisfies these prerequisites.

### State and lifecycle

State version `1` contains `sandbox_id`, owning `environment_id` and `configuration_fingerprint`. Target metadata carries the same ownership and fingerprint, plus the optional create operation correlation. State never contains credentials, command handles, endpoints or output bytes.

`prepare()` validates the exact state target, or searches ownership metadata if state has not yet been received. Multiple matches and incompatible metadata are conflicts. It resumes a matching paused sandbox or reconnects to the same running sandbox. Only managed selection with authoritative absence permits creation or replacement. Authorization, transport and rate-limit failures do not permit speculative creation. Host lifecycle serialization remains required; E2B metadata search is not an atomic create-if-absent primitive.

A successful create is cached before operation readiness, so subsequent readiness failure preserves its state. Generation combines sandbox identity and the guest boot identity. Fresh adapters bind fresh mount handles; explicit process rebind validates durable process identity. A different boot fences previous handles and clears obsolete capture records.

`close()` disconnects native command handles, stops commands started by this adapter, releases its capture records and fences its operation facets. It preserves the sandbox and user files. Rebinding a process borrows it; closing the borrowing adapter does not kill it. `stop()` pauses the exact validated sandbox with memory preservation. `destroy()` kills only the validated sandbox. Neither action prepares or resumes the target. Keepalive rejects paused or missing targets, never shortens an existing deadline and reports the observed expiry. Creation uses finite kill-on-timeout behavior with automatic resume disabled. Host delete deadlines remain effective while paused.

### Operation guarantees and limits

Files support bounded raw reads, streamed SDK transfers, UTF-8 text, shared unified-diff semantics, traversal, search, atomic staged publication, copy, move and deletion. Logical paths resolve beneath the configured root; the root is a file-API boundary, not a confinement boundary for an allowed shell. Copy uses the same streamed publication path as writes. Append and patch are read/modify/write operations, not concurrent compare-and-swap transactions.

Structured argv is passed directly to the command-local child process. Shell requests use the `default` Bash profile. Each command receives the runtime's small inherited environment allowlist plus explicit set/unset changes. A command-local runner retains raw stdout/stderr bytes in private sandbox files rather than relying on SDK-decoded output. Each stream has a finite cap; live and retained records share a bounded admission pool. Output supports offsets, cursors, explicit release and generation/mount fencing. Truncation drains excess bytes and reports loss; retain/fail overflow terminates execution at capacity. Stdin quota reservations are serialized inside the sandbox across adapters and remain consumed after uncertain delivery.

Wall-time limits survive Host disconnection. Signals address a verified runner through Linux pidfds and the runner signals its process group. Completion reports `residual_confined`, because descendants may escape that group while remaining inside the E2B sandbox; it never claims namespace-level tree cleanup. A `tree_cleaned` wait resolves at the final cleanup disposition, including this explicit residual result. Per-command network denial on an internet-enabled sandbox, per-command CPU/memory/process-count limits, unknown shell profiles and non-loopback port targets fail as unsupported before their requested action. Whole-sandbox internet denial remains available through configuration. Port operations only inspect or wait for loopback TCP listeners.

## Harness Semantics

Harness receives already constructed Direct Local, Local Envd, Docker, or E2B Environment instances. It never receives a Provider, discovers the catalog, acquires an attachment, or owns an ephemeral target lifetime.

A singular Environment becomes mount `workspace`. Named mounts can combine built-ins. Harness supplies Run-local access ceilings and working directories, binds local scopes atomically without target I/O, routes operations through ready or lazy objects, snapshots each non-`None` state directly into `HarnessState.environment_states`, and closes adapters non-destructively.

Direct Local and Local Envd normally contribute no state entry. Docker and E2B contribute their provider envelope under the selected mount name. Async child Runs receive fresh adapters selected from Host state; inline children borrow the current entered Harness facade.

## Dependencies and Public Surface

The package root exports:

- `EnvironmentProvider`, `Environment`, and `EnvironmentState`;
- provider-neutral single-Environment operation models and errors;
- `EnvironmentProviderSpec` and the catalog;
- built-in configuration and runtime collaborator contracts;
- built-in Provider constructors or catalog instances;
- explicit Host convenience resolvers such as `resolve_agent_envd_executable()`;
- bounded provider-specific discovery needed for Host-authorized prune where supported.

It does not export Docker SDK models, E2B SDK models, EIP native sessions, Resource/attachment/binding types, Harness mount types, Host persistence models, or unscoped native-client escape hatches.

## Failure Semantics

| Failure                                              | Required behavior                                              |
| ---------------------------------------------------- | -------------------------------------------------------------- |
| Invalid configuration or state                       | Fail before provider effects                                   |
| Direct Local root/workspace inaccessible             | Fail unavailable; do not create or retarget                    |
| Local Envd executable/probe incompatible             | Fail before daemon launch where possible                       |
| Local Envd startup/readiness fails                   | Terminate owned process tree and remove private runtime        |
| Docker/E2B state target compatible                   | Re-enter exact target                                          |
| Docker/E2B target authoritatively absent             | Create replacement only under documented policy                |
| Docker/E2B target unavailable, ambiguous, or unknown | Fail without speculative replacement                           |
| Docker/E2B metadata incompatible                     | Fail conflict; do not adopt, mutate, or destroy                |
| Create/replacement succeeds then readiness fails     | Preserve changed state                                         |
| Close fails                                          | Report local cleanup failure without destroying backing target |
| Destroy outcome unknown                              | Preserve state for Host retry or prune                         |

## Compatibility

Provider keys, configuration schema versions, and state codec versions are stable explicit boundaries. Providers reject unsupported versions and incompatible fingerprints.

Hosts construct fresh operation objects from authoritative state and choose eager or lazy preparation under the shared lifecycle contract. Backing resume/rebuild semantics remain separate from local scope entry and close.

## Invariants

01. All built-in Provider and Environment construction is inert.
02. Direct Local and Local Envd can omit portable state.
03. Local Envd state never contains a PID or private runtime path.
04. Docker state contains the exact container ID and compatibility evidence.
05. E2B state contains the exact sandbox identity and compatibility evidence.
06. EIP-backed providers use EIP for Agent operations.
07. Confirmed absence and unknown evidence are distinct.
08. Known state is retained before later readiness failure.
09. Close never removes a Docker container or terminates an E2B sandbox.
10. Destroy validates and removes only the exact represented target.
11. External workspaces, bind sources, and named volumes are not provider-owned cleanup targets.
12. Host prune policy and bookkeeping remain outside the shared model.

## Host-local Backend Scope

Direct Local, Local Envd and Docker backend configuration includes a fixed `host_id`. Docker also records its exact local `docker_host` endpoint. Hosts enforce this affinity before preparation and maintenance; equal filesystem paths on different hosts are different targets. Direct Local and Local Envd identify their selected workspace path without including access or connection state. Docker's canonical native identity is the exact container ID, independent of bootstrap credential rotation.

Docker reconciliation searches the frozen logical target's ownership labels, validates exact configuration and bootstrap evidence, and caches any recovered container ID without starting the container. External registration preserves the native identity in Provider state while exposing its independently allocated Foundation identity to Harness. An external stopped target is unavailable until its external owner starts it.
