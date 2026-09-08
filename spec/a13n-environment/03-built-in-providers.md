# Built-in Environment Providers

## Design Position

`a13n-environment` supplies two operation routes and six Provider choices:

| Route  | Provider key          | Backing target                              | Operation backend            | Lifecycle boundary                              |
| ------ | --------------------- | ------------------------------------------- | ---------------------------- | ----------------------------------------------- |
| Native | `a13n.direct-local`   | Existing Host directory                     | Local OS file/process APIs   | Caller-owned directory                          |
| Native | `a13n.e2b`            | Native E2B sandbox                          | E2B SDK and bounded commands | Sandbox create, pause/resume, renew and destroy |
| Envd   | `a13n.local-envd`     | Local workspace and private daemon          | EIP over stdio               | Adapter-owned daemon; caller-owned workspace    |
| Envd   | `a13n.docker`         | Local Docker container running envd         | EIP over HTTP                | Managed container; close preserves target       |
| Envd   | `a13n.http-envd`      | External daemon at a configured origin      | EIP over HTTP(S)             | Connect-only                                    |
| Envd   | `a13n.websocket-envd` | External daemon reverse-connected to a Host | EIP over accepted WebSocket  | Connect-only; Host-integrated SDK               |

Docker Envd retains the serialized key `a13n.docker`. Local Envd serves CLI and local Agent use; Docker Envd supplies container-backed execution for single-node self-hosting. Multi-tenant authorization and allocation remain Host responsibilities. Remote Envd supports network-reachable environments through HTTP or outbound-only environments through reverse WebSocket. A connection Session is not a tenant boundary.

Every built-in follows the same three-entity model: an inert `EnvironmentProvider`, a fresh process-local `Environment`, and optional `EnvironmentState`. Envd-backed Providers use EIP for Agent file, shell, process, output and port operations. Native Providers use their native backends without requiring envd. No Provider emulates an unsupported operation through a different backend.

This document owns native and provider-launched target behavior. [Remote Envd Providers and Host Integration](04-remote-envd.md) owns the external state/configuration codecs, connection lifecycle, and WebSocket SDK.

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
    dialect: Literal["posix", "powershell"] = "posix"
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

### Native command execution

Direct Local runs on POSIX hosts and native Windows without WSL or a daemon. Each command owns a POSIX process group or Windows Job Object. On Windows, assignment to the Job precedes execution so descendants cannot escape ownership during startup. Root exit and complete tree cleanup are separate observations; inherited output pipes finish only after owned descendants are cleaned up. Timeout, cancellation, startup failure, and adapter close clean up owned processes and output resources.

The default `posix` shell dialect retains `-c` and optional login semantics. A `powershell` profile disallows login, passes the script as an encoded command, and selects UTF-8 for redirected text input/output; argv execution passes arguments without shell interpolation. Hosts explicitly select executables, dialects, and environment keys. The Provider does not inherit ambient environment variables. Binary process output remains bytes.

POSIX supports interrupt and terminate signals. Windows supports tree termination and kill; an interrupt request returns `environment_unsupported` rather than pretending a forced termination is a graceful interrupt. This does not limit cancellation, which uses owned-tree termination. Native execution does not enforce memory, CPU, process-count, or network-denial limits and rejects requests requiring them.

### State and lifecycle

Direct Local denotes access to an existing Host-controlled directory. It does not allocate, own, lock, retain, back up, or delete that directory.

Direct Local is stateless for re-entry and `dump_state()` returns `None`. The selected configuration remains the deterministic target. The Provider can compute an internal configuration fingerprint for validation and observation, but it does not require an `EnvironmentState` merely to repeat access to the same configured root.

`prepare()` validates that the configured root exists and is accessible, constructs fresh local file/process/output facets, and establishes Run-local process ownership. Inaccessibility is `unknown`/unavailable rather than authoritative target absence; Direct Local never creates the root automatically.

`close()` terminates and releases only processes, streams, retained output, and local handles owned by that adapter. It does not change or delete the root. `stop()` and `destroy()` are unsupported for the caller-owned backing directory. Direct Local declares no keepalive requirement; retention policies must disable target stop/delete.

Prepared Direct Local descriptors expose bounded `backing_identity` evidence for Host policy and backing observation across fresh operation Sessions. The evidence binds the Provider, Host filesystem namespace, resolved root file identity, and configured operation policy. An ordinary workspace content edit preserves it; replacing the root or changing the policy invalidates it. Discovery, validation, construction, and entry do not inspect the filesystem or advertise verified backing identity. If the filesystem cannot supply usable identity evidence, the field remains absent; this does not make Harness approvals connection-local. This evidence is neither a content digest nor a filesystem lock, and makes no guarantee against file-ID reuse or hostile concurrent namespace changes.

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

The package exposes a separate Host convenience `resolve_a13n_envd_executable()` helper. It can resolve an explicit path, `A13N_ENVD_EXECUTABLE`, then `shutil.which`. The helper validates the result and performs no download or installation. Low-level Provider construction never invokes it or loads `.env`.

The package also exposes `local_envd.validate_local_envd_runtime(executable, configuration)` for explicit Host preflight. It uses the same exact-release and production isolation checks as `prepare()`, including denied-network verification when selected, without allocating a private runtime or starting an EIP daemon. It performs no executable discovery, downloads, installation, or system-policy changes. Success is a point-in-time observation; actual preparation retains its normal checks. Cancellation and timeout terminate the validation subprocess.

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

Because every independent Run creates a fresh daemon, Local Envd state does not preserve daemon identity across Runs. Filesystem continuity comes from the configured Host workspace. After preparation, `backing_identity` binds the same Host filesystem evidence described for Direct Local, including the workspace, trusted executable roots, and canonical execution policy. Recreating a private daemon or its runtime directory preserves this evidence; replacing a backing root or changing the policy changes it. Harness does not automatically bind deferred approval to this evidence. The fresh daemon `generation` still fences process, output, and other Session-local handles. Missing filesystem evidence means backing continuity is unknown, not that a new Session must invalidate approval.

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

    image: str = "ghcr.io/converge-ai-labs/a13n-sandbox:latest"
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

`a13n.e2b` configuration schema version `1` selects `template` (default `base`), logical filesystem `root` (default `/home/user`), sandbox `user`, the Python executable used by file/port helpers, sandbox and request timeouts, sandbox-wide internet access, read-only access, finite file/traversal bounds and local observation limits. `timeout_seconds` defaults to 3600 seconds for sandbox TTL; `request_timeout_seconds` defaults to 30 seconds for native requests. `max_active_observations` defaults to 128 concurrent native attachments, `max_observation_bytes` to 1 MiB cumulative combined stdout/stderr per observed command, and `max_retained_output_bytes` to 128 MiB retained text across the adapter. Configuration contains no credential, sandbox ID, endpoint or live SDK object.

`E2BProviderRuntime` supplies an explicit secret API key, backend domain, managed/external selection and optional Host operation correlation. Generic Provider Backend configuration contains the domain; its credential contains `api_key`. The library does not load `.env` or acquire credentials. Construction, discovery of Provider definitions and scope entry are inert.

Commands use the official asynchronous E2B SDK's public `run`, `list`, `connect`, `kill`, `send_stdin` and `close_stdin` operations. File and port helpers use bounded Python standard-library commands. No guest command supervisor, PID registry, capture files, pidfd support, `a13n-envd`, executable upload or template build is required. The selected template provides Linux, Python 3.11 or later, Bash, the selected account and existing root. Git-ignore queries additionally require Git.

### State and lifecycle

State version `1` contains `sandbox_id`, owning `environment_id` and `configuration_fingerprint`. The fingerprint covers target-compatible template, root, user and internet-access configuration, not local observation budgets. Target metadata carries the same ownership and fingerprint, plus optional create correlation. No command inventory, SDK handle, text buffer, model reference or cursor is persisted.

`prepare()` validates the exact state target, or searches ownership metadata if state has not yet been received. Multiple matches and incompatible metadata are conflicts. It resumes a matching paused sandbox or reconnects to the same running sandbox. Only managed selection with authoritative absence permits creation or replacement. Authorization, transport and rate-limit failures do not permit speculative creation. Host lifecycle serialization remains required; metadata search is not an atomic create-if-absent primitive.

A successful create is cached before operation readiness. Generation derives from the exact sandbox identity. A transport recovery to that same target preserves current-scope observations and offsets. A replacement sandbox invalidates previous identities. Native PIDs are best-effort selectors; the adapter claims no stronger anti-reuse guarantee than the native API.

`close()` disconnects SDK observations and fences local facets without killing commands or the sandbox. A fresh adapter restored from state can discover running commands that the sandbox still exposes. Unchanged sandbox identity does not prove command survival; an exited command absent from the native inventory has no invented exit result. `stop()` pauses the exact validated sandbox with memory preservation. `destroy()` kills only the validated sandbox. Neither action prepares or resumes the target. Keepalive rejects paused or missing targets, never shortens an existing deadline and reports observed expiry. Creation uses finite kill-on-timeout behavior with automatic resume disabled. Host delete deadlines remain effective while paused.

### Operation guarantees and limits

Files support bounded raw reads, streamed SDK transfers, UTF-8 text, shared unified-diff semantics, traversal, search, atomic staged publication, copy, move and deletion. Logical paths resolve beneath the configured root; that is a file-API boundary, not shell confinement. Append and patch are read/modify/write operations, not concurrent compare-and-swap transactions. Port operations inspect or wait for loopback TCP listeners only.

The `default` Shell profile uses native login Bash. An unspecified login setting selects this native behavior; explicit non-login mode, direct argv execution, environment unsets and unknown profiles are unsupported. Explicit environment sets are passed to the SDK. Native stdin is bounded per request to 1 MiB, not by a cross-adapter cumulative quota. Native kill is SIGKILL; interrupt/terminate and verified process-tree cleanup are unsupported.

E2B does not enforce a per-command hard wall-time limit. Explicit execution deadlines, cumulative stdin quotas, CPU/memory/process-count limits and per-command network denial on an internet-enabled sandbox fail before command dispatch. The SDK command-stream timeout is disabled; request timeouts only bound native requests. Neither tool waiting nor connection timeout is represented as a process deadline. Whole-sandbox expiry and configured internet denial remain native target policies.

Output follows the [shared observation contract](02-environment-lifecycle.md#process-and-output-observations) with `origin="sdk_text"`. Producer totals and producer completion are unknown even when the SDK supplies terminal status. Only active native attachments, including in-flight start/connect reservations, count against observation concurrency. Finished and disconnected observations immediately release that slot; discovery-only references consume none. Active admission fails before a new start or attachment when that pool is full. This does not limit native sandbox jobs or cumulative completed commands. Status/reference metadata is separate from output retention: an adapter admits at most 100,000 unreleased references, including discovery records, and requires explicit release before admitting more. This generous metadata guard bounds silent-command histories without consuming active slots or silently invalidating old handles.

The aggregate retained-output budget evicts the oldest closed observations' output first. It preserves reference metadata, known terminal status and cumulative per-stream end offsets; reads report `observation_evicted`, partial coverage and advanced `available_start` rather than silently rebasing a log to zero. Finished logs retain neither SDK handles nor pending SDK waiters. Active buffers are not evicted. If only active logs remain and there is insufficient space, or a command reaches its cumulative output limit, the adapter stops that observation and disconnects its SDK stream, not the command. Un-evicted output remains readable until local release or scope close.

E2B waits use one monotonic budget that includes native status queries and any stream attachment. Zero timeout performs one status poll bounded by the native request timeout; it does not promise zero network latency. Output events only trigger condition rechecks. A capped observation still permits bounded periodic native status polling, with a minimum interval independent of stdout frequency. Timeout returns the latest observed status without inventing completion. Foreground exec continues while that status is running even after output closure; missing or unknown status returns uncertainty without a fabricated successful exit. Cancellation releases local observation, without killing or replaying the native command.

Repeated waits and status queries do not reset either budget or reconnect a capped observation. Eviction never replenishes the per-command cumulative allowance. Native status remains independently queryable; loss of final exit evidence remains unknown. SDK accumulation stops at detachment, but in-flight events, SDK text copies and bounded metadata mean retained payload budgets are not exact resident-memory ceilings.

A transient connection loss before the cap permits explicit output/wait reattachment to the same identity, appending SDK text to the existing log and marking partial coverage. Status-only queries and background readiness watchers do not reconnect. One adapter-owned consumer observes each SDK handle; cancellation of a caller's wait does not cancel the shared SDK event task. A query has no output reset behavior.

Native listing is not paginated by the locked SDK. The adapter bounds its returned projection and reports `has_more` without claiming a server-side cursor. It projects no native environment variables or arbitrary argv and does not attach every listed command. Complete durable logs require the application to write files deliberately.

## Harness Semantics

Harness receives already constructed native or Envd-backed Environment instances. It never receives a Provider, discovers the catalog, acquires an attachment, or owns an ephemeral target lifetime.

A singular Environment becomes mount `workspace`. Named mounts can combine built-ins. Harness supplies Run-local access ceilings and working directories, binds local scopes atomically without target I/O, routes operations through ready or lazy objects, snapshots each non-`None` state directly into `HarnessState.environment_states`, and closes adapters non-destructively.

Direct Local and Local Envd normally contribute no state entry. Docker, E2B and Remote Envd contribute their provider envelope under the selected mount name. Async child Runs receive fresh adapters selected from Host state; inline children borrow the current entered Harness facade.

## Dependencies and Public Surface

The package root exports:

- `EnvironmentProvider`, `Environment`, and `EnvironmentState`;
- provider-neutral single-Environment operation models and errors;
- `EnvironmentProviderSpec` and the catalog;
- built-in configuration and runtime collaborator contracts;
- built-in Provider constructors or catalog instances;
- Host-owned reverse WebSocket connection SDK and typed runtime collaborators;
- explicit Host convenience resolvers such as `resolve_a13n_envd_executable()`;
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

Docker reconciliation searches the frozen logical target's ownership labels, validates exact configuration and bootstrap evidence, and caches any recovered container ID without starting the container. External registration preserves the native identity in Provider state while exposing its independently allocated Service identity to Harness. An external stopped target is unavailable until its external owner starts it.
