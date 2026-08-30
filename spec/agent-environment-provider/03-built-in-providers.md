# Built-in Environment Providers

## Design Position

`a13n-environment-provider` ships four providers in its main distribution:

| Provider key        | Resource                                     | Runtime attachment | Harness backend |
| ------------------- | -------------------------------------------- | ------------------ | --------------- |
| `a13n.direct-local` | One configured local root and process policy | Direct Local       | Direct Local    |
| `a13n.local-envd`   | One local `agent-envd` process and runtime   | EIP                | EIP             |
| `a13n.docker`       | One Docker container running `agent-envd`    | EIP                | EIP             |
| `a13n.e2b`          | One E2B sandbox running `agent-envd`         | EIP                | EIP             |

The built-ins share the specification, Provider, resource-state, and attachment contracts. They do not share lifecycle implementation. Local Envd owns a Host-launched local daemon process, while Docker and E2B use their SDKs for outer resource lifecycle and bootstrap. After attachment, every Local Envd, Docker, and E2B Harness file, shell, process, output, and port operation uses EIP.

## Shared Configuration Rules

Each built-in owns an exact versioned Pydantic configuration model. Configuration is desired behavior, not runtime state. It contains no API key, Docker socket, sandbox/container ID, resolved endpoint, EIP credential, or session.

The provider factory constructs an inert Provider. Current credentials and provider clients enter through a fresh Host runtime context. No built-in touches the filesystem, Docker daemon, E2B API, network, or `agent-envd` during import, catalog construction, configuration validation, factory construction, or Provider construction.

Each provider validates its own configuration, resource lifecycle, daemon bootstrap, and EIP compatibility before issuing an attachment. The Harness remains the sole owner of operation-family/facet consistency and permission-ceiling intersection against the entered Direct Local or EIP descriptor. Unsupported operations fail explicitly; no built-in emulates them through another backend or vendor API.

## Direct Local

### Configuration

The provider package owns the public Direct Local configuration values. `a13n.direct-local` accepts configuration schema version `1` in the first public contract. Its exact runtime collaborator is an empty frozen `DirectLocalProviderRuntime`; Direct Local needs no credential or client factory, but the explicit value preserves the same inert factory/Provider construction boundary as other providers.

```python
@dataclass(frozen=True, slots=True)
class DirectLocalProviderRuntime(EnvironmentProviderRuntime):
    pass


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

    environment_id: str
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

The provider schema owns these desired values; the Harness Direct Local adapter owns their file/process enforcement and provider-neutral operation semantics. This prevents a Host and Harness from maintaining competing local configuration models.

The schema validates bounded non-blank Environment and shell-profile identities, unique profile IDs, valid ports, positive byte/process ceilings, and positive finite time limits. After user expansion, the configured root, shell executables, and allowed executables are absolute paths; relative process-local working directories cannot change the meaning of a persisted specification. The root must already exist as an accessible directory. A read-only root cannot enable shell profiles or allowed executables because Direct Local cannot prevent an allowed child process from mutating files through the embedding OS account.

### Resource state and Provider behavior

A Direct Local resource is a logical access scope over one Host-selected existing directory, not an allocated or owned filesystem resource. Directory creation, deletion, retention, backup, sharing, and concurrent non-Harness use remain entirely Host concerns. The Provider never writes lifecycle metadata beside or inside the directory and never claims exclusive use.

Direct Local resource state uses `state_version="1"` with this provider-owned data codec:

```python
class DirectLocalProviderStateData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_id: str
    configuration_fingerprint: str
```

The fingerprint covers the exact normalized schema-version-1 configuration, including the canonical root path, but is neither authority nor a filesystem identity. The desired configuration remains the source of the root path; state never retargets a Provider to another directory.

`create()` validates the configured existing directory and returns a pre-entry logical Resource plus current state. `resume()` validates the same configuration, state codec, fingerprint, and existing directory before returning another pre-entry Resource. Neither operation creates, adopts, locks, tags, or mutates the directory. `pause()` is unsupported. `destroy()` validates any supplied state and ends only the Host's logical provider-resource lifecycle; it returns without changing the directory or its contents.

Direct Local advertises `resource_allocation=SINGLE_FROM_SPEC`, `attachment_concurrency=SHARED`, and no pause mode. The same specification denotes the same logical access target, while any number of authorized Agent runs, Resource scopes, attachments, and ordinary Host processes can intentionally share that directory. Each acquisition still produces a fresh single-use Harness binding so run-local processes, retained output, permissions, and cleanup remain separate.

Direct Local lifecycle methods perform no external provider dispatch and do not produce an unknown side-effect outcome. A cancelled validation can be retried with the same operation identity. Reconciliation observes the deterministic configured target: create or resume yields `RUNNING` with fresh validated state when the directory is accessible, `ABSENT` when it authoritatively does not exist, and `UNKNOWN` when access or canonical validation cannot establish either fact. Destroy reconciliation yields `ABSENT` because Direct Local retains no provider-owned resource after logical detach, regardless of whether the shared Host directory still exists. Reconciliation performs no filesystem mutation and needs no filesystem operation marker or resource tag.

Direct Local makes no sandbox or network-isolation claim. Its existing path, process, output, cancellation, and cleanup contracts remain owned by [Harness Environment Integration](../agent-harness/08-environment-integration.md).

## Local Envd

### Configuration and runtime

The `a13n.local-envd` provider is the built-in local sandbox. Schema version `1` is credential-free and has this exact conceptual public shape:

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

    environment_id: str
    workspace: LocalEnvdWorkspaceConfiguration
    execution_network: LocalEnvdNetworkMode = LocalEnvdNetworkMode.HOST
    trusted_executable_roots: tuple[Path, ...] = ()
    shell_profiles: tuple[LocalEnvdShellProfile, ...] = ()
    max_file_bytes: int = 16 * 1024 * 1024
    max_output_preview_bytes: int = 64 * 1024
    max_output_bytes_per_stream: int = 1024 * 1024 * 1024
    max_spool_bytes: int = 64 * 1024 * 1024 * 1024
```

The workspace, trusted executable roots, and shell executables are absolute after user expansion. The workspace must be an existing accessible directory. Identities are bounded, nonblank, and unique where applicable; paths and fixed arguments contain no NUL; byte limits are positive; the output preview fits the daemon response envelope; and the spool ceiling can reserve both output streams for one command. Local Envd always selects required native isolation. The user cannot disable it through provider configuration.

The provider maps this narrow model to strict envd bootstrap configuration: one `workspace` root mount, its read-only or writable policy, required isolation, the selected network ceiling, trusted executable roots, shell profiles, and the bounded file/output/spool limits. Schema version 1 always permits `stat`, `read_text`, `open_reader`, `list`, `find`, and `search`; a writable workspace additionally permits `write_text`, `open_writer`, `remove`, and `move`; command-enabled configuration additionally permits `command_cwd` and `executable_source`. It intentionally does not expose an arbitrary envd operation subset or enable `mkdir`, `patch_text`, or `copy`. It does not expose raw envd JSON, transport selection, native runtime paths, payload identities, or arbitrary environment variables. The desired configuration contains no daemon executable path, ambient `PATH` selector, download location, package URL, PID, private runtime path, or live EIP value.

A fresh typed `LocalEnvdProviderRuntime` supplies exactly one absolute `agent-envd` executable and a Host-owned allocator for a protected private runtime parent. Provider construction records those collaborators but performs no filesystem inspection, path search, download, installation, subprocess launch, probe, or allocation. Once constructed, a Provider never changes or rediscovers its executable.

The package exposes a separate Host convenience `resolve_agent_envd_executable()` boundary. It resolves, in order, an explicit Host path, `A13N_AGENT_ENVD_EXECUTABLE`, then the platform executable name through `shutil.which`. A path value is expanded and made absolute relative to the caller's current directory; a command discovered by `which` is made absolute. The helper rejects a missing path, directory, non-regular file, or non-executable file and returns one exact absolute path suitable for `LocalEnvdProviderRuntime`. It does not read a dotenv file, install or download a binary, execute it, construct a Provider, or become part of the low-level EIP client. A development command may load a repository `.env` before calling this helper, but library import and Provider construction never load `.env` implicitly.

Agent UI's managed-runtime selection contract remains independently defined by [Agent UI Runtime, Subagents, and Surfaces](../agent-ui/05-runtime-subagents-and-surfaces.md#local-sandbox-runtime-resolution). Its package-pinned default continues not to search ambient `PATH`; only a Host integration that explicitly selects the generic convenience helper opts into its environment and `which` precedence.

At lifecycle time, the Provider requires the selected executable to report the client release identity through `agent-envd --version` and to pass the production-equivalent `agent-envd isolation probe --json`. These checks establish executable and isolation compatibility without launching the managed daemon. Resource entry independently establishes the launched daemon generation, Environment identity, required methods, protocol compatibility, and current readiness through a provider-owned EIP readiness Session.

### Resource state and Provider behavior

A Local Envd provider resource is uniquely correlated logical state over a Host-selected existing workspace and desired configuration. An entered Resource additionally owns one local daemon generation, its complete process tree, trusted stdio pipes, one physical carrier, and one private runtime parent allocated for that entry. It does not own, create, delete, retain, back up, or exclusively lock the workspace.

Local Envd state uses `state_version="1"` with this exact provider-owned data codec:

```python
class LocalEnvdResourcePhase(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"


class LocalEnvdProviderStateData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_id: str
    resource_correlation: str
    configuration_fingerprint: str
    phase: LocalEnvdResourcePhase
```

The fingerprint covers the exact normalized schema-version-1 configuration, including canonical Host paths. The state never contains the executable path, PID, process handle, pipes, private runtime path, daemon generation, EIP descriptor, session, or probe output. `resource_correlation` distinguishes independent `MULTIPLE_FROM_SPEC` logical resources even when they use the same specification and workspace.

`create()` validates the configured workspace, executable shape and release, required isolation probe, and operation correlation, then returns a pre-entry Resource with `RUNNING` state. `resume()` accepts valid `RUNNING` or `PAUSED` state, validates its configuration fingerprint, correlation, workspace, executable, release, and isolation probe, normalizes the result to `RUNNING`, and returns a new pre-entry Resource. Accepting `RUNNING` permits ordinary Resource exit and later re-entry without pretending that exit paused the logical resource. Neither method allocates a private runtime or launches envd.

Resource entry asks the Host allocator for a fresh protected runtime parent, writes one strict private envd configuration file, and launches one stdio daemon generation with `AGENT_ENVD_RUNTIME_DIR` set to that parent. It immediately claims the physical carrier for one provider-owned readiness Session, observes process exit concurrently, completes `initialize` plus the mandatory `environment.readiness` operation under finite deadlines, and cleanly calls `session.close`. Entry succeeds only after envd fully writes the close response and rearms the same carrier for a fresh Session. The Provider uses no sleep, filesystem marker, transport-specific health endpoint, or provider-native operation as readiness evidence. Resource exit first fences attachment admission, requires attachment scopes to be closed, terminates the complete daemon process tree, closes its pipes, and releases only that entry's private runtime. Exit does not pause, destroy, or rewrite the logical provider state.

`acquire_attachment()` returns one fresh `EIPEnvironmentAttachment` whose `StdioEIPSessionSource` carries an exclusive single-use lease over the Resource-owned carrier. Attachment entry completes ordinary EIP initialization and its mandatory initial `environment.readiness` operation before the Harness can publish a binding. After envd cleans session-owned state and fully writes a successful `session.close` response, the source detaches its requester and returns the healthy carrier lease so a later sequential attachment can initialize against the same daemon generation. Initialization/readiness failure, a lost or malformed close response, ambiguous request correlation, fatal protocol or framing failure, carrier EOF, or unexpected process exit permanently fences that carrier and makes the Resource unavailable. The Provider never starts a replacement implicitly within the same Resource entry.

Local Envd advertises `resource_allocation=MULTIPLE_FROM_SPEC`, `attachment_concurrency=SINGLE`, and filesystem pause only. `pause(mode=FILESYSTEM)` requires the matching entered Resource after attachment scopes close, fences new attachments, stops its complete daemon process tree, releases its private runtime, leaves workspace files untouched, and returns state with `phase=PAUSED`. Resource exit then completes any idempotent local cleanup. `FULL` is unsupported. A later `resume()` returns a new Resource whose next entry starts a fresh private runtime and daemon generation.

`destroy()` is called only after Resource exit. It validates the exact state/configuration/resource correlation and ends the logical provider lifecycle; the entry-owned daemon and private runtime must already have been removed. It never deletes or mutates the selected workspace. If prior Resource cleanup could not prove process termination or private-runtime release, that cleanup fails and the Host must not treat a later logical destroy as proof that the process-local resource disappeared.

Local Envd reconciliation is bounded and read-only. Destroy reconciliation returns `ABSENT` after valid state correlation because no provider-owned durable object remains after Resource exit. Pause reconciliation returns validated `PAUSED` state when supplied evidence identifies that completed local transition. Create and resume compatibility requires executing the release and isolation checks, so reconciliation validates only supplied state correlation and static filesystem shape, then returns `UNKNOWN`; the Host retries the side-effect-free lifecycle validation with the same operation identity rather than weakening it inside reconciliation. Inaccessible or ambiguous local evidence also returns `UNKNOWN`. Reconciliation never allocates a runtime, launches a process, mutates the workspace, or substitutes another executable.

A missing executable, release mismatch, failed isolation probe, daemon startup/readiness-Session failure, incompatible EIP initialization, unexpected process exit, or inability to prove cleanup makes Local Envd unavailable. The Provider does not fall back to `a13n.direct-local`, disable isolation, or reinterpret the Environment as ordinary Host process access.

## Docker

### Configuration

`a13n.docker` schema version `1` manages one container on a local Docker Engine and uses authenticated Host-dialed HTTP EIP. It defaults to the repository's stable sandbox `latest` image and can pull the selected image through the Host-configured Docker client before resolving one immutable image ID for container creation. It never builds or mutates an image.

The exact conceptual public schema is:

```python
class DockerImagePullPolicy(StrEnum):
    IF_MISSING = "if_missing"
    ALWAYS = "always"
    NEVER = "never"


class DockerBindMountSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["bind"] = "bind"
    path: Path


class DockerVolumeMountSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["volume"] = "volume"
    name: str


DockerMountSource = Annotated[
    DockerBindMountSource | DockerVolumeMountSource,
    Field(discriminator="kind"),
]


class DockerMountConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mount_id: str
    container_path: PurePosixPath
    source: DockerMountSource | None = None
    read_only: bool = False
    allow_command_execution: bool = True


class DockerShellProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str
    executable: PurePosixPath
    fixed_arguments: tuple[str, ...] = ()
    allow_login: bool = False
    max_script_bytes: int = 1024 * 1024


class DockerProviderConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_id: str
    image: str = "ghcr.io/converge-ai-labs/agent-foundation-sandbox:latest"
    pull_policy: DockerImagePullPolicy = DockerImagePullPolicy.IF_MISSING
    root_mount_id: str = "workspace"
    mounts: tuple[DockerMountConfiguration, ...] = (
        DockerMountConfiguration(
            mount_id="workspace",
            container_path=PurePosixPath("/workspace"),
        ),
    )
    trusted_executable_roots: tuple[PurePosixPath, ...] = ()
    shell_profiles: tuple[DockerShellProfile, ...] = (
        DockerShellProfile(
            profile_id="bash",
            executable=PurePosixPath("/bin/bash"),
            fixed_arguments=("-c",),
        ),
    )
    nano_cpus: int | None = None
    memory_bytes: int | None = None
    pids_limit: int | None = None
    stop_grace_seconds: int = 10
    max_file_bytes: int = 16 * 1024 * 1024
    max_output_preview_bytes: int = 64 * 1024
    max_output_bytes_per_stream: int = 1024 * 1024 * 1024
    max_spool_bytes: int = 64 * 1024 * 1024 * 1024
```

With only `environment_id`, the default configuration starts the repository sandbox image, exposes its writable `/workspace` as the root virtual mount, and enables its fixed Bash profile. A mount with `source=None` exposes an existing image path backed by the container writable layer. A bind source is an existing absolute Host path. A volume source names one existing external Docker volume. Schema version 1 never creates or owns a named volume. `mount_id` values and container paths are unique, `root_mount_id` identifies exactly one configured mount, and every container path, trusted executable root, and shell executable is an absolute normalized POSIX path that neither contains nor is contained by the fixed `/run/a13n` bootstrap tree or `/home/sandbox/.local/state/agent-envd` runtime tree. Bind sources are absolute after user expansion. Identities, image references, volume names, paths, and fixed arguments are bounded and contain no control character or NUL. Byte and resource limits are positive when present; the output preview fits one daemon response and the spool ceiling can retain both output streams.

The Provider maps the mount, shell, and byte-limit values to one strict envd configuration. Mount IDs and container paths form the virtual Environment namespace presented through EIP; Host bind paths and Docker volume identities are lifecycle inputs and never become model-facing paths. Every mount permits `stat`, `read_text`, `open_reader`, `list`, `find`, and `search`; a writable mount additionally permits `write_text`, `open_writer`, `remove`, and `move`; a command-enabled mount additionally permits `command_cwd` and `executable_source`. As with Local Envd, schema version 1 does not enable `mkdir`, `patch_text`, or `copy` and does not expose an arbitrary envd operation list. Harness file operations always use this EIP virtual filesystem and never Docker copy or archive APIs.

Docker v1 uses the Engine's ordinary bridge network. The Provider publishes one fixed container EIP port to a Docker-assigned Host port bound only to `127.0.0.1`. It accepts no transport selector, remote-Engine endpoint, private-link route, Docker network mode, arbitrary port publication, container command, entrypoint, environment mapping, user, privileged mode, capability, device, Docker socket, label, raw Docker mount object, or provider-created volume policy. The image runs envd and payload processes under its fixed unprivileged identity. The Host-supplied bootstrap store chooses filesystem ownership and permissions appropriate for that local deployment; the Provider requires only that the fixed container identity can read the mounted bootstrap files and never exposes those files through the EIP mount namespace.

`nano_cpus`, `memory_bytes`, and `pids_limit` are optional Docker-native ceilings. Resource lifetime remains Host lifecycle policy because an ordinary Docker container has no portable durable TTL contract; the Provider owns no expiry scheduler. `stop_grace_seconds` bounds an ordinary graceful stop but is not an execution or Harness operation timeout.

### Runtime and bootstrap boundary

A fresh typed `DockerProviderRuntime` supplies two process-local collaborators:

```python
@dataclass(frozen=True, slots=True)
class DockerProviderRuntime(EnvironmentProviderRuntime):
    engine: DockerEngine
    bootstrap_store: DockerBootstrapStore
```

`DockerEngine` is the package-owned typed async boundary for local-topology validation, local image inspection, image pull, image resolution, container create/start/inspect/stop/remove, exact label queries, and Host-loopback route inspection. The main implementation wraps the Docker SDK for Python. Registry credentials, credential helpers, mirrors, proxies, and daemon policy remain ordinary Host Docker-client configuration. The package root exports the runtime and bootstrap-store contracts, not Docker SDK models or an unscoped client escape hatch. Construction is inert. Every blocking SDK call runs off the event loop through a bounded worker-thread boundary and uses finite client timeouts.

The runtime must prove that its Engine is local and that a `127.0.0.1` published port is reachable from the Provider process. An unprovable or remote topology fails before image resolution, bootstrap allocation, or container dispatch. Supporting remote Engines or another trusted route requires another explicit runtime topology contract.

`DockerBootstrapStore` owns protected Host-local material needed to start and reconnect to one container. The Provider derives the bootstrap correlation as `bootstrap-` plus the first 24 lowercase hexadecimal characters of SHA-256 over the UTF-8 create operation ID. The same logical create retry therefore selects the same allocation without making the correlation a credential.

The store supports four bounded async operations:

- `create(correlation, material)` publishes one new complete allocation and returns the existing allocation only when its complete identity and material digest match exactly;
- `recover(correlation)` returns that exact complete allocation or authoritative absence;
- `replace(correlation, material)` validates every immutable material field and atomically replaces the current credential for a stopped container while preserving the correlation;
- `remove(correlation)` removes that exact allocation and treats authoritative prior absence as success.

Bootstrap material contains the Environment identity, configuration fingerprint, complete strict envd configuration, and current HTTP Bearer credential. A process-local returned allocation additionally contains its bounded non-secret correlation and one absolute Host directory accepted as a local-Engine bind source. Store failures never become absence, and conflicting reuse of a correlation fails without modifying the existing allocation.

Creation publishes one complete allocation atomically. Environment identity, configuration fingerprint, and envd configuration are immutable for that allocation, so stopped-container replacement has one mutable commit point: an atomic credential-file swap followed by complete-material verification. The directory is mounted read-only at `/run/a13n/bootstrap`; the image's fixed non-root envd command reads the configuration and credential from fixed paths in that mount and writes generation state only beneath its image-owned runtime directory. The credential never enters provider configuration, provider state, Docker labels, endpoint URLs, logs, traces, or model-visible values. The Host decides whether its local bootstrap store needs process-private permissions, encryption, or only ordinary application-data protection. A store intended for durable Resource reuse retains allocations across Provider process replacement; an ephemeral Host can supply a temporary store with the same contract.

The Provider uses no Docker archive, copy, exec, logs, or filesystem API for bootstrap, repair, readiness, or Harness operations. Missing or incompatible bootstrap evidence never triggers credential rotation or container replacement while a container is running.

### Resource state and identity

Docker state uses `state_version="1"` with this exact provider-owned codec:

```python
class DockerResourcePhase(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"


class DockerProviderStateData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_id: str
    resource_correlation: str
    container_id: str
    image_id: str
    bootstrap_correlation: str
    configuration_fingerprint: str
    create_operation_id: str
    phase: DockerResourcePhase
```

The fingerprint covers the normalized schema-version-1 configuration. `container_id` and `image_id` are immutable identities returned by the local Engine, not user references. State contains no endpoint, Host port, bind-source path, credential, Docker client, container object, daemon generation, EIP descriptor, or Session.

Before container creation, the Provider applies these exact bounded labels:

| Label                                | Value                              |
| ------------------------------------ | ---------------------------------- |
| `io.a13n.environment-provider`       | `a13n.docker`                      |
| `io.a13n.environment-provider.state` | `1`                                |
| `io.a13n.environment-id`             | configured Environment identity    |
| `io.a13n.resource-correlation`       | Host resource correlation          |
| `io.a13n.bootstrap-correlation`      | Host bootstrap allocation identity |
| `io.a13n.configuration-fingerprint`  | normalized configuration digest    |
| `io.a13n.create-operation-id`        | exact create operation identity    |

The Provider validates container ID, resolved image ID, every label, fixed command and bootstrap mount, resource limits, configured mounts, non-root launch contract, EIP port publication, and bootstrap-store correlation before treating an inspection as its resource. An ID match with incompatible metadata is a conflict, never authority to adopt, start, stop, or remove the container.

### Lifecycle and attachments

Docker advertises `resource_allocation=MULTIPLE_FROM_SPEC`, `attachment_concurrency=SINGLE`, and filesystem pause only.

`create()` validates local topology and mount sources, applies the configured image pull policy, and resolves the selected image to its immutable ID. `IF_MISSING` uses a compatible local image and pulls only after authoritative local absence; `ALWAYS` pulls before resolution; `NEVER` requires a compatible local image. Pull failure occurs before bootstrap or container dispatch and does not require provider-resource reconciliation, even if Docker retains partial image-cache state. The Provider then creates complete bootstrap material and dispatches exactly one labeled container create with all fixed configuration, limits, mounts, the read-only bootstrap mount, and the loopback-only EIP publication present atomically. It starts that exact container and confirms compatible running inspection before returning a pre-entry Resource. It does not open EIP during `create()`. A known failure before container dispatch removes the just-created bootstrap allocation; a failure after create or start may have reached Docker has unknown outcome unless exact follow-up inspection proves a result. A compatible container in Docker's created or exited state is a filesystem-preserved `PAUSED` resource rather than absence; exact create reconciliation can return it for resume.

`resume()` requires valid state for the exact configuration and resource correlation. For a compatible running container it recovers the matching bootstrap allocation and returns a Resource without restarting the container or changing the credential. For a compatible stopped container it atomically replaces the credential under the same bootstrap correlation while preserving exact immutable bootstrap material, starts that exact container, confirms running state, and returns a Resource with `phase=RUNNING`. The new process has a fresh envd generation. Missing state, container, image identity, bootstrap evidence, or compatible metadata fails; resume never creates a replacement container.

Resource entry re-inspects the exact running container, recovers its credential, and resolves only the `127.0.0.1` EIP route from authoritative port inspection. It opens one provider-owned HTTP Session, completes initialization and mandatory readiness under finite deadlines, cleanly closes that Session, and only then admits attachments. Entry failure or a later route, credential, initialization, readiness, or close failure makes that Resource entry unavailable without restarting or replacing the container and without rewriting outer lifecycle state.

Each `acquire_attachment()` returns one fresh single-use `EIPEnvironmentAttachment` with a fresh `HttpEIPSessionSource` for the same running daemon generation. Only one attachment scope can be active. Attachment initialization performs mandatory readiness before the Harness publishes a mount. Resource exit fences admission and requires attachment release but does not stop, pause, destroy, or restart the container.

`pause(mode=FILESYSTEM)` requires the matching entered Resource after attachment release, fences admission, stops only the exact validated container using the configured grace period, confirms stopped inspection, and returns state with `phase=PAUSED`. Its writable layer and external mounts remain. `FULL` and Docker process freeze are unsupported.

`destroy()` runs only after Resource exit. It validates state and current inspection before stopping when necessary and removing only the exact container ID. It never removes bind sources or external named volumes. After authoritative container absence, it removes the exact bootstrap allocation. Container absence with bootstrap cleanup failure is a cleanup failure rather than completed destroy. Repeating destroy is successful only when authoritative inspection and bootstrap-store evidence both prove absence.

### Reconciliation and outcome certainty

Docker reconciliation is bounded, read-only, and action-aware. It inspects the exact container ID from valid state when available and queries the exact provider/resource/create-operation labels needed to recover an uncertain create. It never starts, stops, removes, creates, or repairs a container and never creates, replaces, or removes bootstrap material.

| Evidence                                                                                                  | Result                                 |
| --------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| One exact compatible running container and complete matching bootstrap allocation                         | `RUNNING` with validated current state |
| One exact compatible created or exited container and complete matching bootstrap allocation               | `PAUSED` with validated current state  |
| No matching container and no matching bootstrap allocation                                                | `ABSENT`                               |
| Create reconciliation finds no matching container and one complete matching reusable bootstrap allocation | `ABSENT`                               |
| Non-create reconciliation finds container absence while matching bootstrap material remains               | `UNKNOWN`                              |
| Container present while bootstrap material is missing, stale, inaccessible, or mismatched                 | `UNKNOWN`                              |
| Container is removing, dead, or otherwise not authoritatively running or filesystem-preserved             | `UNKNOWN`                              |
| Duplicate label matches, ID/label/image/configuration mismatch, or inaccessible Engine or bootstrap store | `UNKNOWN`                              |

Create reconciliation uses the create operation ID and resource correlation because no returned state may exist. When authoritative Engine inspection proves that no matching container exists, one complete matching bootstrap allocation is reusable only by the same create operation and therefore does not prevent `ABSENT` or one same-operation create retry. Resume, pause, and destroy reconciliation require valid last-known state and preserve its exact container identity. A destroy operation reaches `ABSENT` only when both the container and bootstrap allocation are authoritatively absent. A timeout or cancellation before an SDK effect is dispatched is `NOT_DISPATCHED`; interruption after create, start, stop, or remove may have been dispatched is `UNKNOWN` and requires exact reconciliation before retry. Read-only inspection failures do not turn unknown evidence into absence.

## E2B

### Configuration

The E2B provider accepts a bounded versioned configuration including:

- an exact E2B template name or ID that contains a compatible `agent-envd` binary and startup contract;
- the sandbox's logical Environment identity;
- a finite sandbox timeout;
- timeout behavior of `kill` or `pause`;
- for pause, `full` or `filesystem` snapshot mode;
- explicit outbound network policy supported by E2B;
- bounded non-secret metadata;
- the dedicated EIP HTTP listener port and required EIP compatibility.

The E2B template starts `agent-envd` as part of its trusted boot contract. The E2B SDK is not used to execute model commands or implement Harness file operations. E2B's own in-sandbox service and `agent-envd` are distinct components; the provider validates the selected template's Agent Foundation daemon readiness rather than inferring it from generic sandbox availability.

Automatic resume from inbound traffic is disabled for managed EIP resources. Resume remains an explicit Provider action, which keeps lifecycle observations and fresh binding construction in one path. Timeout can still pause the sandbox under the selected full or filesystem mode.

E2B advertises `resource_allocation=MULTIPLE_FROM_SPEC` and `attachment_concurrency=SINGLE`. Each successful create returns one independent sandbox state. Its pause modes follow the selected provider configuration and account capabilities.

### Provider behavior

`create()` calls the E2B SDK to create one sandbox from the selected template and lifecycle configuration, including bounded metadata for the Host operation ID, resource correlation, and provider/configuration fingerprint. It waits for provider running state and records the sandbox identity without treating generic provider availability as envd readiness. Resource entry resolves the provider-routed HTTPS host for the dedicated EIP port, completes a provider-owned initialize/readiness/close Session against that fresh routing, and only then issues attachment sources. Every acquired attachment independently initializes EIP, completes its mandatory initial readiness operation, and must match the expected Environment identity, generation, protocol, required methods, and limits before the Harness publishes a mount.

`resume()` uses the sandbox ID from validated provider resource state. E2B's connect operation attaches to a running sandbox or resumes a paused sandbox; it never creates a replacement for a missing or killed sandbox. After connect, Resource entry resolves fresh routing and performs the same provider-owned readiness Session before issuing fresh attachment sources.

`pause(mode=FULL)` preserves the sandbox filesystem, memory, and running processes. External network connections still close. On resume, the in-sandbox `agent-envd` process and daemon generation can remain, but the Host obtains a fresh endpoint and establishes a fresh readiness-confirmed Session. No HTTP request, transfer, or EIP Session survives pause.

`pause(mode=FILESYSTEM)` preserves the sandbox filesystem while discarding memory and processes. Resume reboots from disk, the template startup contract launches a new `agent-envd` process, and the daemon generation changes. Every previous operation receipt, process handle, output reference, transfer, and session is fenced. The Harness receives a fresh attachment and mount incarnation before operations resume.

`destroy()` kills the exact sandbox ID. A killed or expired sandbox is successful absence only when E2B reports that state authoritatively. Sandbox timeout and connect behavior follow the SDK contract: connect can extend an expiring running sandbox but does not silently shorten a longer current timeout; exact timeout changes use the SDK's explicit timeout operation.

E2B reconciliation uses exact sandbox ID when state exists and exact bounded operation/resource metadata when an uncertain create returned no state. One matching running or paused sandbox yields validated `RUNNING` or `PAUSED` state; authoritative killed/expired/absent evidence yields `ABSENT`; duplicate, inaccessible, or insufficient evidence yields `UNKNOWN`. Reconciliation does not connect, resume, pause, extend, or kill the sandbox.

The E2B Provider uses the SDK's async lifecycle surface. A provider call lacking an async SDK operation is isolated through a bounded worker-thread boundary only when required and preserves cancellation and unknown-outcome semantics. E2B API credentials remain with the Provider. Provider-routed HTTPS, E2B access controls, and EIP bootstrap credentials are separate layers; EIP authentication remains mandatory.

## E2B and Harness Environment Semantics

E2B persistence does not replace Harness Environment state:

| E2B event                   | Provider resource             | `agent-envd` generation           | Harness requirement                                                      |
| --------------------------- | ----------------------------- | --------------------------------- | ------------------------------------------------------------------------ |
| Connect to running sandbox  | Same sandbox                  | Same when daemon remained running | Fresh attachment, mount incarnation, and EIP session                     |
| Full pause and resume       | Same sandbox, memory restored | Can remain the same               | Fresh external connection/session; no transfer resume                    |
| Filesystem pause and resume | Same sandbox, rebooted        | Changes                           | Fresh attachment and mount incarnation; every daemon selector fenced     |
| Kill or expiry              | Resource absent               | Absent                            | Resume fails; Host explicitly decides whether to create another resource |

Files written in the sandbox can survive both pause modes as provider-native resource state. EIP 1.0 still exports no `EnvironmentMountState`. If the Host also selects a portable Harness workspace codec, that codec remains a separate explicit import/export mechanism and is not inferred from E2B pause.

## Dependencies and Public Surface

The main provider distribution depends on compatible Docker and E2B SDK versions. There are no provider extras. The package root exports:

- provider specification, catalog, factory, Provider, operation identity, reconciliation, state, lifecycle capability, and attachment contracts;
- the four built-in provider keys, typed configuration models, and exact runtime collaborator types;
- `EIPSessionSource` plus stdio, HTTP, and accepted reverse-WebSocket source configuration;
- stable provider error and outcome types.

It does not export vendor clients, Docker models, E2B SDK objects, raw EIP transfer handles, or a generic vendor API escape hatch.

## Failure Semantics

| Provider         | Material failure                                 | Outcome                                                                 |
| ---------------- | ------------------------------------------------ | ----------------------------------------------------------------------- |
| Direct Local     | Configured shared root validation fails          | No attachment and no filesystem mutation                                |
| Local Envd       | Runtime resolution or required isolation fails   | No daemon attachment and no Direct Local fallback                       |
| Local Envd       | Daemon stop or private-runtime cleanup uncertain | Preserve explicit cleanup failure; never delete the workspace           |
| Docker           | Container create/start outcome is uncertain      | Reconcile exact container labels/ID before another create               |
| Docker           | Stopped container resumes                        | New envd generation and fresh binding/session                           |
| E2B              | Sandbox create response is lost                  | Reconcile provider metadata before another create                       |
| E2B              | Sandbox is paused                                | Explicit resume/connect, fresh routing, and fresh EIP readiness Session |
| E2B              | Filesystem-only resume fails to start envd       | Resource remains unavailable; no attachment is issued                   |
| E2B              | Sandbox is killed or expired                     | Resume fails as absent; no implicit replacement                         |
| Any EIP provider | EIP operation disconnect after possible dispatch | EIP outcome remains unknown under EIP operation-ID reconciliation       |
| Any provider     | Lifecycle API response is lost                   | Reconcile the exact Host operation/resource before another transition   |
| Any provider     | Lifecycle cleanup fails after a Harness result   | Report cleanup separately and preserve the Harness result candidate     |

Raw vendor exceptions remain protected causes. Safe errors expose only bounded provider key, lifecycle action, resource correlation, and stable failure class.

## Compatibility

Built-in key, configuration schema, provider resource-state codec, lifecycle allocation/concurrency behavior, Host-resolved local daemon contract, template/image bootstrap contract, vendor SDK range, EIP version, and Harness adapter evolve independently. A local executable, template, or image is compatible only when its `agent-envd` and carrier profile satisfy the configured protocol requirements.

Searching ambient `PATH` for Local Envd, silently falling back from Local Envd to Direct Local, changing E2B full pause into filesystem pause, enabling traffic-triggered resume implicitly, using Docker/E2B native operations as Harness fallbacks, or preserving a daemon generation claim across reboot is incompatible.

## Trade-offs

### Shared main-distribution providers

Shipping Local Envd plus Docker and E2B SDKs increases installation size. It gives Hosts one typed catalog and avoids extras or thin adapter packages. Inert imports and lazy clients keep unused providers effect-free. The Local Envd binary remains Host-selected runtime material rather than Python package data.

### Local sandbox as a separate provider

Local Envd adds a subprocess, private-runtime, native-isolation, and EIP lifecycle where Direct Local needs none. Keeping it as a distinct provider makes the sandbox guarantee and failure boundary explicit and prevents a convenience fallback from silently changing command authority.

### Explicit resume

Disabling E2B traffic-triggered auto-resume adds one explicit Provider call. It makes resumed routing, daemon readiness, generation validation, and fresh attachment issuance deterministic and easy to test.

### EIP-only sandbox operations

Baking and starting `agent-envd` adds a template/image requirement. It avoids divergent Docker, E2B, and local command/file semantics and lets the same Harness conformance suite validate every sandbox backend.

## Invariants

01. The four exact built-in keys are available without entry-point discovery or extras.
02. Direct Local and EIP are the only Harness Environment operation backends.
03. Local Envd launches only the exact Host-resolved executable and owns its process/private runtime; it never searches, downloads, installs, or falls back to Direct Local.
04. Docker and E2B SDKs manage resource lifecycle and bootstrap only; Harness operations always use EIP.
05. Provider configuration and resource state contain no credential or live process, SDK, or EIP object.
06. Local Envd, Docker filesystem pause, and E2B filesystem pause discard process memory and create a fresh `agent-envd` generation on resume.
07. Local Envd required isolation never degrades to disabled mode; E2B full pause can preserve the daemon process but never preserves external connections, transfers, or EIP sessions.
08. A missing, expired, killed, or incompatible resource is never replaced implicitly by `resume()`.
09. E2B automatic traffic-triggered resume is disabled; the Provider owns explicit resume and readiness validation.
10. Every resumed EIP-backed resource yields a fresh attachment, Harness binding, and readiness-confirmed EIP Session.
11. Docker SDK calls and any unavoidable synchronous E2B SDK call never block the async event loop; native E2B async lifecycle operations remain async.
12. Provider-native filesystem persistence and Harness portable Environment state remain separate mechanisms.
