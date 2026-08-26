# Built-in Environment Providers

## Design Position

`converge-agent-environment-provider` ships three providers in its main distribution:

| Provider key            | Managed resource                             | Runtime attachment | Harness backend |
| ----------------------- | -------------------------------------------- | ------------------ | --------------- |
| `converge.direct-local` | One configured local root and process policy | Direct Local       | Direct Local    |
| `converge.docker`       | One Docker container running `agent-envd`    | EIP                | EIP             |
| `converge.e2b`          | One E2B sandbox running `agent-envd`         | EIP                | EIP             |

The built-ins share the specification, Manager, resource-state, and attachment contracts. They do not share vendor lifecycle implementation. Docker and E2B use their SDKs for resource lifecycle and bootstrap; after attachment, every Harness file, shell, process, output, and port operation uses EIP.

## Shared Configuration Rules

Each built-in owns an exact versioned Pydantic configuration model. Configuration is desired behavior, not runtime state. It contains no API key, Docker socket, sandbox/container ID, resolved endpoint, EIP credential, or session.

The provider factory constructs an inert Manager. Current credentials and provider clients enter through a fresh Host runtime context. No built-in touches the filesystem, Docker daemon, E2B API, network, or `agent-envd` during import, catalog construction, configuration validation, factory construction, or Manager construction.

Each provider validates its own configuration, resource lifecycle, daemon bootstrap, and EIP compatibility before issuing an attachment. The Harness remains the sole owner of operation-family/facet consistency and permission-ceiling intersection against the entered Direct Local or EIP descriptor. Unsupported operations fail explicitly; no built-in emulates them through another vendor API.

## Direct Local

### Configuration

The provider package owns the public Direct Local configuration values:

```python
class DirectLocalRootConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    ownership: Literal["caller_owned", "manager_owned"]
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

### Manager behavior

`create()` validates the configured root and creates it only when `ownership="manager_owned"`. It returns a managed local resource and a resource state containing the Environment identity and a non-authoritative configuration fingerprint. `resume()` revalidates the same configured root and fingerprint. It never uses the state to select a different path.

Direct Local advertises no pause mode. Disconnecting the managed resource closes only Manager-owned live bookkeeping. `destroy()` removes a manager-owned root only under its configured ownership contract and refuses to remove a caller-owned root. Each attachment becomes a fresh Harness Direct Local binding.

Direct Local makes no sandbox or network-isolation claim. Its existing path, process, output, cancellation, and cleanup contracts remain owned by [Harness Environment Integration](../agent-harness/08-environment-integration.md).

## Docker

### Configuration

The Docker provider accepts a bounded versioned configuration including:

- an exact image reference selected by the Host;
- the container's logical Environment identity;
- explicit workspace mounts and read-only flags;
- finite CPU, memory, process, and lifetime limits supported by the selected Docker deployment;
- an `agent-envd` bootstrap profile and compatible EIP requirement;
- either private stdio or Host-dialed HTTP as the EIP session-source profile;
- explicit container ownership on destroy.

The provider does not accept arbitrary Docker API objects, callbacks, socket paths from model input, or a second command-execution configuration. A Host can expose a narrower authoring schema while still resolving to this typed configuration.

### Manager behavior

The Docker Manager uses the Docker SDK for Python. Blocking SDK calls run through `anyio.to_thread.run_sync` or an equivalent bounded worker-thread boundary.

`create()` creates and starts one container whose image includes compatible `agent-envd` bootstrap. `resume()` inspects the exact container ID from provider resource state and starts it when stopped or reconnects when already running. A missing container fails rather than creating another one.

Docker advertises filesystem pause only. `pause(mode=FILESYSTEM)` stops the container after closing the active attachment; the writable container filesystem or selected volumes remain, but process memory and `agent-envd` generation do not. Resume starts a fresh daemon generation and issues a fresh EIP attachment. `FULL` is unsupported rather than being mapped to Docker's process-freeze operation, because a frozen container is not a portable retained sandbox lifecycle.

`destroy()` stops and removes the exact managed container according to the configured volume ownership. A not-found response is successful absence only after the Docker daemon authoritatively reports it.

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

### Manager behavior

`create()` calls the E2B SDK to create one sandbox from the selected template and lifecycle configuration. It waits for provider running state, resolves the provider-routed HTTPS host for the dedicated EIP port, and makes that fresh routing available through the managed resource's HTTP session source. The first acquired attachment initializes EIP and must match the expected Environment identity, protocol, required methods, and limits before the Harness publishes a binding.

`resume()` uses the sandbox ID from validated provider resource state. E2B's connect operation attaches to a running sandbox or resumes a paused sandbox; it never creates a replacement for a missing or killed sandbox. After connect, the Manager resolves fresh routing and issues only a fresh session source; initialization remains owned by attachment entry.

`pause(mode=FULL)` preserves the sandbox filesystem, memory, and running processes. External network connections still close. On resume, the in-sandbox `agent-envd` process and daemon generation can remain, but the Host obtains a fresh endpoint/session and performs `initialize` again. No HTTP request, transfer, or EIP session survives pause.

`pause(mode=FILESYSTEM)` preserves the sandbox filesystem while discarding memory and processes. Resume reboots from disk, the template startup contract launches a new `agent-envd` process, and the daemon generation changes. Every previous operation receipt, process handle, output reference, transfer, and session is fenced. The Harness receives a fresh binding revision before operations resume.

`destroy()` kills the exact sandbox ID. A killed or expired sandbox is successful absence only when E2B reports that state authoritatively. Sandbox timeout and connect behavior follow the SDK contract: connect can extend an expiring running sandbox but does not silently shorten a longer current timeout; exact timeout changes use the SDK's explicit timeout operation.

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

- provider specification, catalog, factory, Manager, state, lifecycle capability, and attachment contracts;
- the three built-in provider keys and typed configuration models;
- `EIPSessionSource` plus stdio, HTTP, and accepted reverse-WebSocket source configuration;
- stable provider error and outcome types.

It does not export vendor clients, Docker models, E2B SDK objects, raw EIP transfer handles, or a generic vendor API escape hatch.

## Failure Semantics

| Provider         | Material failure                               | Outcome                                                              |
| ---------------- | ---------------------------------------------- | -------------------------------------------------------------------- |
| Direct Local     | Root or ownership validation fails             | No attachment and no sandbox claim                                   |
| Docker           | Container create/start outcome is uncertain    | Reconcile exact container labels/ID before another create            |
| Docker           | Stopped container resumes                      | New envd generation and fresh binding/session                        |
| E2B              | Sandbox create response is lost                | Reconcile provider metadata before another create                    |
| E2B              | Sandbox is paused                              | Explicit resume/connect, fresh routing, and fresh EIP initialization |
| E2B              | Filesystem-only resume fails to start envd     | Resource remains unavailable; no attachment is issued                |
| E2B              | Sandbox is killed or expired                   | Resume fails as absent; no implicit replacement                      |
| Any EIP provider | HTTP disconnect after possible dispatch        | Unknown operation outcome; reconcile by operation ID                 |
| Any provider     | Lifecycle cleanup fails after a Harness result | Report cleanup separately and preserve the Harness result candidate  |

Raw vendor exceptions remain protected causes. Safe errors expose only bounded provider key, lifecycle action, resource correlation, and stable failure class.

## Compatibility

Built-in key, configuration schema, provider resource-state codec, lifecycle behavior, template/image bootstrap contract, vendor SDK range, EIP version, and Harness adapter evolve independently. A template or image is compatible only when its `agent-envd` and carrier profile satisfy the configured protocol requirements.

Changing E2B full pause into filesystem pause, enabling traffic-triggered resume implicitly, using Docker/E2B native operations as Harness fallbacks, or preserving a daemon generation claim across reboot is incompatible.

## Trade-offs

### Shared main-distribution providers

Shipping Docker and E2B SDKs increases installation size. It gives Hosts one typed catalog and avoids extras or thin adapter packages. Inert imports and lazy clients keep unused providers effect-free.

### Explicit resume

Disabling E2B traffic-triggered auto-resume adds one explicit Manager call. It makes resumed routing, daemon readiness, generation validation, and fresh attachment issuance deterministic and easy to test.

### EIP-only sandbox operations

Baking and starting `agent-envd` adds a template/image requirement. It avoids divergent Docker, E2B, and local command/file semantics and lets the same Harness conformance suite validate every sandbox backend.

## Invariants

01. The three exact built-in keys are available without entry-point discovery or extras.
02. Direct Local and EIP are the only Harness Environment operation backends.
03. Docker and E2B SDKs manage resource lifecycle and bootstrap only; Harness operations always use EIP.
04. Provider configuration and resource state contain no credential or live SDK/EIP object.
05. Docker filesystem pause and E2B filesystem pause discard process memory and create a fresh `agent-envd` generation on resume.
06. E2B full pause can preserve the daemon process but never preserves external connections, transfers, or EIP sessions.
07. A missing, expired, killed, or incompatible resource is never replaced implicitly by `resume()`.
08. E2B automatic traffic-triggered resume is disabled; the Manager owns explicit resume and readiness validation.
09. Every resumed resource yields a fresh attachment, Harness binding, and initialized EIP session.
10. Docker SDK calls and any unavoidable synchronous E2B SDK call never block the async event loop; native E2B async lifecycle operations remain async.
11. Provider-native filesystem persistence and Harness portable Environment state remain separate mechanisms.
