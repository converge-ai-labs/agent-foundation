# Built-in Environment Providers

## Design Position

`converge-agent-environment-provider` ships four providers in its main distribution:

| Provider key            | Managed resource                             | Runtime attachment | Harness backend |
| ----------------------- | -------------------------------------------- | ------------------ | --------------- |
| `converge.direct-local` | One configured local root and process policy | Direct Local       | Direct Local    |
| `converge.local-envd`   | One local `agent-envd` process and runtime   | EIP                | EIP             |
| `converge.docker`       | One Docker container running `agent-envd`    | EIP                | EIP             |
| `converge.e2b`          | One E2B sandbox running `agent-envd`         | EIP                | EIP             |

The built-ins share the specification, Manager, resource-state, and attachment contracts. They do not share lifecycle implementation. Local Envd owns a Host-launched local daemon process, while Docker and E2B use their SDKs for outer resource lifecycle and bootstrap. After attachment, every Local Envd, Docker, and E2B Harness file, shell, process, output, and port operation uses EIP.

## Shared Configuration Rules

Each built-in owns an exact versioned Pydantic configuration model. Configuration is desired behavior, not runtime state. It contains no API key, Docker socket, sandbox/container ID, resolved endpoint, EIP credential, or session.

The provider factory constructs an inert Manager. Current credentials and provider clients enter through a fresh Host runtime context. No built-in touches the filesystem, Docker daemon, E2B API, network, or `agent-envd` during import, catalog construction, configuration validation, factory construction, or Manager construction.

Each provider validates its own configuration, resource lifecycle, daemon bootstrap, and EIP compatibility before issuing an attachment. The Harness remains the sole owner of operation-family/facet consistency and permission-ceiling intersection against the entered Direct Local or EIP descriptor. Unsupported operations fail explicitly; no built-in emulates them through another backend or vendor API.

## Direct Local

### Configuration

The provider package owns the public Direct Local configuration values. `converge.direct-local` accepts configuration schema version `1` in the first public contract. Its exact runtime collaborator is an empty frozen `DirectLocalProviderRuntime`; Direct Local needs no credential or client factory, but the explicit value preserves the same inert factory/Manager construction boundary as other providers.

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

### Resource state and Manager behavior

A Direct Local resource is a logical access scope over one Host-selected existing directory, not an allocated or owned filesystem resource. Directory creation, deletion, retention, backup, sharing, and concurrent non-Harness use remain entirely Host concerns. The Provider never writes lifecycle metadata beside or inside the directory and never claims exclusive use.

Direct Local resource state uses `state_version="1"` with this provider-owned data codec:

```python
class DirectLocalProviderStateData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_id: str
    configuration_fingerprint: str
```

The fingerprint covers the exact normalized schema-version-1 configuration, including the canonical root path, but is neither authority nor a filesystem identity. The desired configuration remains the source of the root path; state never retargets a Manager to another directory.

`create()` validates the configured existing directory and returns a pre-entry logical managed resource plus current state. `resume()` validates the same configuration, state codec, fingerprint, and existing directory before returning another pre-entry managed resource. Neither operation creates, adopts, locks, tags, or mutates the directory. `pause()` is unsupported. `destroy()` validates any supplied state and ends only the Host's logical provider-resource lifecycle; it returns without changing the directory or its contents.

Direct Local advertises `resource_allocation=SINGLE_FROM_SPEC`, `attachment_concurrency=SHARED`, and no pause mode. The same specification denotes the same logical access target, while any number of authorized Agent runs, managed-resource scopes, attachments, and ordinary Host processes can intentionally share that directory. Each acquisition still produces a fresh single-use Harness binding so run-local processes, retained output, permissions, and cleanup remain separate.

Direct Local lifecycle methods perform no external provider dispatch and do not produce an unknown side-effect outcome. A cancelled validation can be retried with the same operation identity. Reconciliation observes the deterministic configured target: create or resume yields `RUNNING` with fresh validated state when the directory is accessible, `ABSENT` when it authoritatively does not exist, and `UNKNOWN` when access or canonical validation cannot establish either fact. Destroy reconciliation yields `ABSENT` because Direct Local retains no provider-owned resource after logical detach, regardless of whether the shared Host directory still exists. Reconciliation performs no filesystem mutation and needs no filesystem operation marker or resource tag.

Direct Local makes no sandbox or network-isolation claim. Its existing path, process, output, cancellation, and cleanup contracts remain owned by [Harness Environment Integration](../agent-harness/08-environment-integration.md).

## Local Envd

### Configuration and runtime

The `converge.local-envd` provider is the built-in local sandbox. Its versioned credential-free configuration contains one Environment identity, one Host-selected existing workspace root and access policy, bounded command and resource limits, a required native-isolation policy, an explicit network posture, and the required EIP compatibility. It contains no daemon executable path, ambient `PATH` selector, download location, package URL, process ID, private runtime path, or live EIP value.

A fresh typed `LocalEnvdProviderRuntime` supplies the absolute `agent-envd` executable and private-runtime allocator already resolved and validated by the Host. Manager construction records that collaborator but performs no path search, download, installation, subprocess launch, probe, or filesystem mutation. The provider never consults ambient `PATH` and never substitutes another executable. Agent UI's managed-runtime selection contract is defined by [Agent UI Runtime, Subagents, and Surfaces](../agent-ui/05-runtime-subagents-and-surfaces.md#local-sandbox-runtime-resolution); another Host must provide an equivalent resolved runtime explicitly.

The selected daemon must report a compatible release identity through `agent-envd --version` and pass the production-equivalent `agent-envd isolation probe --json` in required mode before provider creation. An explicit outer-Host `disabled` isolation posture is invalid for this built-in. These checks establish local runtime availability; successful EIP initialization against the launched process independently establishes the actual daemon generation, Environment identity, required methods, and protocol compatibility.

### Resource state and Manager behavior

A Local Envd resource owns one local daemon process and its private runtime tree over a Host-selected existing workspace. It does not own, create, delete, retain, back up, or exclusively lock that workspace. Provider state records only bounded Environment/configuration correlation and lifecycle evidence; the executable path, process object, PID, pipes, runtime tree, EIP session, and isolation-probe details remain process-local.

`create()` and `resume()` validate the exact configuration and Host-resolved runtime, allocate a fresh private runtime, and start a fresh `agent-envd` process generation with trusted stdio. They issue no attachment until daemon readiness and required isolation succeed. The entered managed resource remains the sole owner of that process, complete process tree, private runtime, and pipes. `acquire_attachment()` returns one fresh `EIPEnvironmentAttachment` whose `StdioEIPSessionSource` carries an exclusive one-shot lease over the resource-owned carrier; attachment entry performs ordinary EIP initialization before the Harness can publish a binding. A clean session close returns the carrier lease so a later sequential attachment can initialize against the same daemon generation. Fatal carrier failure or unexpected process exit makes the resource unavailable and never starts a replacement implicitly.

Local Envd advertises `resource_allocation=MULTIPLE_FROM_SPEC`, `attachment_concurrency=SINGLE`, and filesystem pause only. Each create owns an independent daemon process/private runtime even when several specifications intentionally select the same Host workspace. `pause(mode=FILESYSTEM)` closes the active attachment, stops the complete daemon process tree, and removes only the provider-owned private runtime while leaving workspace files untouched. `resume()` starts another private runtime and daemon generation. `FULL` is unsupported.

`destroy()` stops the exact owned daemon process, closes its private pipes, and removes its private runtime. It never deletes or mutates the selected workspace merely because the provider resource is destroyed. Process termination and private-runtime cleanup must be proven before success; uncertain cleanup remains explicit rather than being reported as absence. Reconciliation uses only exact local process-owner and configuration evidence and never launches a replacement as an observation.

A missing executable, release mismatch, failed isolation probe, daemon startup/readiness failure, incompatible EIP initialization, or inability to prove cleanup makes Local Envd unavailable. The Manager does not fall back to `converge.direct-local`, disable isolation, or reinterpret the Environment as ordinary Host process access.

## Docker

### Configuration

The Docker provider accepts a bounded versioned configuration including:

- an exact image reference selected by the Host;
- the container's logical Environment identity;
- explicit workspace mounts and read-only flags;
- finite CPU, memory, process, and lifetime limits supported by the selected Docker deployment;
- an `agent-envd` bootstrap profile and compatible EIP requirement;
- either private stdio or Host-dialed HTTP as the EIP session-source profile;
- explicit cleanup behavior for provider-created writable volumes when the Host selects destroy.

The provider does not accept arbitrary Docker API objects, callbacks, socket paths from model input, or a second command-execution configuration. A Host can expose a narrower authoring schema while still resolving to this typed configuration.

### Manager behavior

The Docker Manager uses the Docker SDK for Python. Blocking SDK calls run through `anyio.to_thread.run_sync` or an equivalent bounded worker-thread boundary.

`create()` creates and starts one container whose image includes compatible `agent-envd` bootstrap. Before possible visibility loss, it applies bounded labels for the Host operation ID, resource correlation, and provider/configuration fingerprint. `resume()` inspects the exact container ID from provider resource state and starts it when stopped or reconnects when already running. A missing container fails rather than creating another one.

Docker advertises `resource_allocation=MULTIPLE_FROM_SPEC`, `attachment_concurrency=SINGLE`, and filesystem pause only. Each successful create receives an independent container ID and destroy target. `pause(mode=FILESYSTEM)` stops the container after closing the active attachment; the writable container filesystem or selected volumes remain, but process memory and `agent-envd` generation do not. Resume starts a fresh daemon generation and issues a fresh EIP attachment. `FULL` is unsupported rather than being mapped to Docker's process-freeze operation, because a frozen container is not a portable retained sandbox lifecycle.

`destroy()` stops and removes the exact container identified by validated provider state and applies the configured cleanup behavior only to provider-created writable volumes. Host-mounted paths and externally supplied volumes are never inferred as destroy targets. A not-found response is successful absence only after the Docker daemon authoritatively reports it.

Docker reconciliation performs an exact label/ID inspection scoped to the operation and resource correlation. One matching running or stopped container yields validated `RUNNING` or `PAUSED` state as appropriate; authoritative zero matches yields `ABSENT`; ambiguous duplicates, inaccessible daemon state, or mismatched operation/resource/configuration correlation yields `UNKNOWN`. Reconciliation never starts, stops, or removes the container.

For stdio, the Manager owns private daemon pipes. For HTTP, it publishes the dedicated EIP port only to loopback or an explicitly trusted private provider link and supplies mandatory EIP bootstrap authentication. Docker port routing does not itself grant Environment authority.

The Docker SDK is used only for container lifecycle, inspection, and envd bootstrap plumbing. Harness operations never fall back to Docker exec, archive, copy, logs, or filesystem APIs.

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

Automatic resume from inbound traffic is disabled for managed EIP resources. Resume remains an explicit Manager action, which keeps lifecycle observations and fresh binding construction in one path. Timeout can still pause the sandbox under the selected full or filesystem mode.

E2B advertises `resource_allocation=MULTIPLE_FROM_SPEC` and `attachment_concurrency=SINGLE`. Each successful create returns one independent sandbox state. Its pause modes follow the selected provider configuration and account capabilities.

### Manager behavior

`create()` calls the E2B SDK to create one sandbox from the selected template and lifecycle configuration, including bounded metadata for the Host operation ID, resource correlation, and provider/configuration fingerprint. It waits for provider running state, resolves the provider-routed HTTPS host for the dedicated EIP port, and makes that fresh routing available through the managed resource's HTTP session source. The first acquired attachment initializes EIP and must match the expected Environment identity, protocol, required methods, and limits before the Harness publishes a binding.

`resume()` uses the sandbox ID from validated provider resource state. E2B's connect operation attaches to a running sandbox or resumes a paused sandbox; it never creates a replacement for a missing or killed sandbox. After connect, the Manager resolves fresh routing and issues only a fresh session source; initialization remains owned by attachment entry.

`pause(mode=FULL)` preserves the sandbox filesystem, memory, and running processes. External network connections still close. On resume, the in-sandbox `agent-envd` process and daemon generation can remain, but the Host obtains a fresh endpoint/session and performs `initialize` again. No HTTP request, transfer, or EIP session survives pause.

`pause(mode=FILESYSTEM)` preserves the sandbox filesystem while discarding memory and processes. Resume reboots from disk, the template startup contract launches a new `agent-envd` process, and the daemon generation changes. Every previous operation receipt, process handle, output reference, transfer, and session is fenced. The Harness receives a fresh binding revision before operations resume.

`destroy()` kills the exact sandbox ID. A killed or expired sandbox is successful absence only when E2B reports that state authoritatively. Sandbox timeout and connect behavior follow the SDK contract: connect can extend an expiring running sandbox but does not silently shorten a longer current timeout; exact timeout changes use the SDK's explicit timeout operation.

E2B reconciliation uses exact sandbox ID when state exists and exact bounded operation/resource metadata when an uncertain create returned no state. One matching running or paused sandbox yields validated `RUNNING` or `PAUSED` state; authoritative killed/expired/absent evidence yields `ABSENT`; duplicate, inaccessible, or insufficient evidence yields `UNKNOWN`. Reconciliation does not connect, resume, pause, extend, or kill the sandbox.

The E2B Manager uses the SDK's async lifecycle surface. A provider call lacking an async SDK operation is isolated through a bounded worker-thread boundary only when required and preserves cancellation and unknown-outcome semantics. E2B API credentials remain with the Manager. Provider-routed HTTPS, E2B access controls, and EIP bootstrap credentials are separate layers; EIP authentication remains mandatory.

## E2B and Harness Environment Semantics

E2B persistence does not replace Harness Environment state:

| E2B event                   | Provider resource             | `agent-envd` generation           | Harness requirement                                                      |
| --------------------------- | ----------------------------- | --------------------------------- | ------------------------------------------------------------------------ |
| Connect to running sandbox  | Same sandbox                  | Same when daemon remained running | Fresh attachment, binding, and EIP session                               |
| Full pause and resume       | Same sandbox, memory restored | Can remain the same               | Fresh external connection/session; no transfer resume                    |
| Filesystem pause and resume | Same sandbox, rebooted        | Changes                           | Fresh binding revision; every daemon selector fenced                     |
| Kill or expiry              | Resource absent               | Absent                            | Resume fails; Host explicitly decides whether to create another resource |

Files written in the sandbox can survive both pause modes as provider-native resource state. EIP 1.0 still exports no `EnvironmentBindingState`. If the Host also selects a portable Harness workspace codec, that codec remains a separate explicit import/export mechanism and is not inferred from E2B pause.

## Dependencies and Public Surface

The main provider distribution depends on compatible Docker and E2B SDK versions. There are no provider extras. The package root exports:

- provider specification, catalog, factory, Manager, operation identity, reconciliation, state, lifecycle capability, and attachment contracts;
- the four built-in provider keys, typed configuration models, and exact runtime collaborator types;
- `EIPSessionSource` plus stdio, HTTP, and accepted reverse-WebSocket source configuration;
- stable provider error and outcome types.

It does not export vendor clients, Docker models, E2B SDK objects, raw EIP transfer handles, or a generic vendor API escape hatch.

## Failure Semantics

| Provider         | Material failure                                 | Outcome                                                               |
| ---------------- | ------------------------------------------------ | --------------------------------------------------------------------- |
| Direct Local     | Configured shared root validation fails          | No attachment and no filesystem mutation                              |
| Local Envd       | Runtime resolution or required isolation fails   | No daemon attachment and no Direct Local fallback                     |
| Local Envd       | Daemon stop or private-runtime cleanup uncertain | Preserve explicit cleanup failure; never delete the workspace         |
| Docker           | Container create/start outcome is uncertain      | Reconcile exact container labels/ID before another create             |
| Docker           | Stopped container resumes                        | New envd generation and fresh binding/session                         |
| E2B              | Sandbox create response is lost                  | Reconcile provider metadata before another create                     |
| E2B              | Sandbox is paused                                | Explicit resume/connect, fresh routing, and fresh EIP initialization  |
| E2B              | Filesystem-only resume fails to start envd       | Resource remains unavailable; no attachment is issued                 |
| E2B              | Sandbox is killed or expired                     | Resume fails as absent; no implicit replacement                       |
| Any EIP provider | EIP operation disconnect after possible dispatch | EIP outcome remains unknown under EIP operation-ID reconciliation     |
| Any provider     | Lifecycle API response is lost                   | Reconcile the exact Host operation/resource before another transition |
| Any provider     | Lifecycle cleanup fails after a Harness result   | Report cleanup separately and preserve the Harness result candidate   |

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

Disabling E2B traffic-triggered auto-resume adds one explicit Manager call. It makes resumed routing, daemon readiness, generation validation, and fresh attachment issuance deterministic and easy to test.

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
09. E2B automatic traffic-triggered resume is disabled; the Manager owns explicit resume and readiness validation.
10. Every resumed EIP-backed resource yields a fresh attachment, Harness binding, and initialized EIP session.
11. Docker SDK calls and any unavoidable synchronous E2B SDK call never block the async event loop; native E2B async lifecycle operations remain async.
12. Provider-native filesystem persistence and Harness portable Environment state remain separate mechanisms.
