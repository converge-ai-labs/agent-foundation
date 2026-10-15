# Environment Management Providers

## Design Position

Service owns built-in Environment management definitions and vendor implementations. They perform explicit operations on one target; [Environments](06-environments.md) owns durable instances, lifecycle operations, coordination, renewal scheduling, and retirement policy. Harness owns [Environment Execution](../a13n-harness/08a-environment-execution.md): shared state, execution definitions, fixed-target connectors, and native/EIP operations. Service composes those execution definitions rather than maintaining a second implementation of execution.

This boundary separates database-backed orchestration from vendor I/O inside Service as well. Management drivers receive plain validated account settings, current credentials, recipes, portable state, and stable operation identity. They never read business tables or receive database sessions, ORM objects, or principals. Management status, lifecycle capability flags, operation results, and lifecycle error categories are Service contracts; shared identity, state codecs, and execution types remain Harness contracts.

## Definitions and Inputs

An immutable `EnvironmentManagementDefinition` composes one Harness `EnvironmentProviderDefinition` with an allocation recipe schema, management factory, and explicit managed/stop/destroy/renewal capabilities. It reuses execution-side account, credential, identity, and state schemas; management-only inputs stay in Service. Its recipe can include both shared target-compatible inputs and allocation settings, with no duplicate codec or validator for the same fact. Definition construction performs no external I/O and imports no optional vendor SDK.

This separation does not rewrite accepted account configuration, frozen recipes, or state payloads. Service validates the complete recipe and projects the inputs needed for Harness execution. Shared target-compatibility checks preserve existing fingerprints, including provisioning characteristics needed to verify an existing target; carrying those values grants no allocation authority.

Service's registry and deployment qualifications follow [Providers](08-providers.md#registry). Execution and management definitions must agree on Provider type, backend and native target identity, state codec, and shared input meaning. Assembly rejects mismatches and capability flags unsupported by their factories. Connect-only registrations construct Harness connectors directly and need no fictitious allocation lifecycle or management definition. A custom Host can supply its own management and `EnvironmentSource`; the Harness catalog never imports Service management implementations.

The following is a conceptual Python API, not a wire schema:

```python
class EnvironmentManagementDefinition:
    execution: EnvironmentProviderDefinition  # Harness-owned execution metadata
    async def open_provider(self, configuration, credential) -> EnvironmentProvider: ...


class EnvironmentProvider:
    async def create(self, recipe, *, environment_id, operation_id) -> EnvironmentState | None: ...
    async def start(self, state, *, operation_id) -> EnvironmentState | None: ...
    async def inspect(self, target) -> EnvironmentStatus: ...
    async def stop(self, state, *, operation_id): ...
    async def destroy(self, state, *, operation_id): ...
    async def keepalive(self, state, *, deadline, operation_id): ...
    async def close(self): ...
```

Management providers hold account-scoped clients and close them independently from execution clients. Connector construction uses the composed Harness execution definition with authoritative state and current credentials, allocating no live execution client and performing no target I/O. Management can perform initialization and readiness probes without constructing a Harness execution scope. No factory returns an object carrying both roles.

## Management Operations

| Operation   | Observable contract                                                                                                                                                                                                        |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `create`    | Allocate under the supplied stable Environment and operation identities, or recover the result of that same allocation after an ambiguous response. Preserve observed state even if later readiness fails.                 |
| `start`     | Start the represented existing environment under its declared stop/resume guarantees; never silently replace a lost target. Snapshot-backed providers report any resulting native incarnation change explicitly.           |
| `inspect`   | Observe a saved target or find an interrupted allocation by stable ownership correlation, without creating, starting, renewing, or deleting it. Distinguish running, stopped, confirmed absence, and unavailable evidence. |
| `stop`      | Stop or pause the exact validated target and preserve its declared resume state. Repeated calls reconcile actual state without waking it.                                                                                  |
| `destroy`   | Remove the exact owned target. Confirmed absence is an idempotent result; uncertainty retains state for reconciliation. Host-supplied directories, bind sources, and unrelated volumes are never removed.                  |
| `keepalive` | Extend a running target's supported lifetime and report expiry evidence. Never start a stopped target; disclose hard limits and uncertain outcomes.                                                                        |
| `close`     | Release owned management clients, without stopping or destroying the target.                                                                                                                                               |

A stopped target remains distinct from a lost one. A provider whose explicit stop retains a native snapshot can resume from that retained state under its declared contract; a missing target without such evidence is not permission to create a replacement. Replacement is a new Host-authorized allocation and state publication.

Provider metadata declares renewal requirements and supported bounds; the Host owns scheduling, retry policy, idle stop/delete decisions, and active-use protection. Local synchronization protects clients and operations, not cross-process ownership. The Host reconciles outstanding mutations before issuing conflicting ones; an expired database lease cannot cancel an already dispatched vendor request. Provider I/O occurs outside Host database transactions.

## Service and Harness Collaboration

Service workers embed Harness in the same process. The boundary remains `EnvironmentSource.ensure_ready() -> EnvironmentConnector`, followed by `EnvironmentConnector.open() -> EnvironmentExecution`; it introduces no RPC or new remote service.

```mermaid
sequenceDiagram
    participant Service as Service source and lifecycle coordinator
    participant Driver as Service management provider
    participant Harness as Harness Run
    participant Connector as Harness connector
    participant Execution as Harness execution
    Service->>Harness: Authorized source with frozen mount policy
    Harness->>Service: ensure_ready() on first dependent use
    Service->>Service: Read current authority and state; end transaction
    opt Target requires management
        Service->>Service: Claim operation in a short transaction
        Service->>Driver: Inspect / create / start outside transaction
        Driver-->>Service: State and outcome evidence
        Service->>Service: Publish under current claim in a short transaction
    end
    Service-->>Harness: Fixed-target connector using authoritative state
    Harness->>Connector: open()
    Connector-->>Harness: Ready execution
    Harness->>Execution: Execute permitted operations
    Harness->>Execution: close()
    Note over Service,Driver: Renewal and target retirement remain Service-owned
```

The Agent's `lazy_environment` option keeps its [existing readiness semantics](06-environments.md#execution): default true defers managed preparation until first dependent use; false prepares selected managed instances before Harness execution. Connection and Session acquisition remain lazy in either mode. Source metadata is inert. Host sources may perform database reads and lifecycle coordination internally, but every transaction ends before vendor I/O, readiness waits, Harness execution, or cleanup; no live session is retained by a source between calls.

Harness never claims a lifecycle operation, writes current state, extends a target lease, or decides to replace a target. Source failure and cancellation keep Service's classification and durable operation identity. A cancelled wait does not undo a dispatched vendor operation or erase published state. Service reconciles uncertain effects under the same operation, while Harness releases only its acquired execution resources. Current mount authorization, active-use protection, publication fencing, and lazy/eager failure handling remain owned by [Environments](06-environments.md).

## Failures and Publication

Management errors retain a typed category, dispatch/outcome certainty, stable operation identity, and any observed target state. A timeout after possible dispatch is unknown, not a safe-to-repeat rejection. The Host queries or reconciles before retrying management; provider idempotency and native correlation remain backend-specific. A failed readiness check after successful creation must not discard the known target ID.

Management results and partial failures carry the Harness-owned `EnvironmentState` envelope to the Service coordinator for fenced publication. Only that coordinator decides which observation is authoritative. Management drivers themselves own no persistence. State remains credential-free and contains no lease, claim, principal, session, or transaction; [the state contract](../a13n-harness/08a-environment-execution.md#state-envelope) owns its format. No state is discarded until deletion or absence is confirmed under the backend's guarantees.

## Built-in Management Guarantees

The registry selects supported deployments and narrower account/recipe choices. The following guarantees cover the native management drivers; [Harness Providers](../a13n-harness/08b-environment-providers.md) owns execution and connect-only behavior.

### Docker

The recipe includes the `image` and `pull_policy` (`if_missing` by default, or `never`). The selected Engine uses a local image when present; `if_missing` pulls an absent image, while `never` fails with `environment_image_missing` and build/load guidance without contacting a registry. Acquisition policy is excluded from the configuration fingerprint because it does not change an existing container. The other recipe fields include `environment` and an optional `init_script` executed only on a newly created container; optional `cpus`, `memory_gb` (decimal GB, minimum 6,291,456 bytes), and `pids_limit`; `disable_network`, default false, whose true value selects Docker's `none` network for initialization and commands alike with no published ports; `mounts` with an existing absolute host `source`, an absolute container `target`, and `read_only` defaulting to true; and advanced user, shell, Python, stop grace, request timeout, file and query limits, output preview and capture limits, and concurrent observation limit. Named volumes are not a recipe option, and external mounts cannot replace `/workspace`, the root filesystem, or the Provider's private `/tmp/a13n` command metadata directory.

`/workspace` belongs to the container writable layer, and each Environment owns a separate container. Explicit external host directories are shared only when recipes name the same source; Docker resolves source paths in the Engine's filesystem namespace, which may differ from the process hosting the Harness. Destruction never removes an external source. The default image is `ghcr.io/converge-ai-labs/a13n-sandbox:dev`; a custom image supplies Linux, Python 3.10 or later, the configured shell, and a user able to write `/workspace` and `/tmp/a13n`, with Git additionally required for Git-ignore queries. With `execution="native"` (the default), the Provider replaces the image's ENTRYPOINT and CMD, enables Docker init, and runs a Python waiting process. The container user is the explicit recipe `user`, otherwise the image label `ai.a13n.environment.user`, otherwise the image default USER. The sandbox image labels its native execution user as `sandbox` (UID/GID 1000) while retaining root for standalone Envd startup. Native Docker does not start the bundled daemon. There is no guest operation server: file and port operations use bounded one-shot Python standard-library commands through Docker exec, and file semantics share the native helper.

Creation uses a deterministic name plus ownership and configuration labels; interrupted creation discovers that target and validates its labels without starting it. Foreign or incompatible targets are never adopted or destroyed. `create()` recovers its allocation, and `start()` starts the retained container; confirmed loss is not permission to recreate. Initialization must finish before readiness is published: a marker records completion, and a retained container without it fails preparation rather than replaying a partial script. `stop()` preserves the writable filesystem. `destroy()` removes only the validated container and its private filesystem, never external sources; confirmed absence succeeds idempotently. Changing an image tag does not replace an existing target. Execution attachment and close follow the [Docker execution contract](../a13n-harness/08b-environment-providers.md#docker).

With `execution="envd_http"`, Service preserves the image's entrypoint and command, sets the allocation device ID and envd HTTP bootstrap settings, and publishes container port 8888 on the account's configured bind interface with a Docker-assigned host port. The image must start envd as root with Session execution under its own provisioned identity, honor `A13N_ENVD_HTTP_CREDENTIAL_FILE`, and atomically create a protected random per-instance credential if absent. The supplied images implement this in their common entrypoint. Custom compatible images need no official image label or particular window manager. Recipe user and envd/display variable overrides are rejected. Native defaults and saved native fingerprints remain compatible; unpublished experimental relay targets must be recreated with the explicit execution configuration.

Console offers Standard environment, Desktop environment and Custom image. The Docker definition publishes the two built-in recipe tuples in `x-environment-presets`, following Service's sandbox image policy and the desktop variant's `-desktop` tag suffix. Standard fixes the standard image, native execution and no desktop requirement; Desktop fixes the desktop image, HTTP EIP execution and the desktop requirement. Only Custom exposes editable image, execution, desktop and advanced/JSON configuration. Existing nonmatching images or advanced overrides open as Custom without rewriting them. Explicitly choosing a built-in preset restores advanced defaults and keeps CPU, memory and mounts; no preset identifier is persisted. Connection routing and Agent permissions remain independently configured. Publishing a Service release that offers the desktop preset requires publishing its matching sandbox desktop tag first.

`desktop=true` additionally requires an observable, input-ready desktop before create/start succeeds. The supplied desktop image owns Xvfb/Openbox/envd supervision. EIP without this requirement only needs the ordinary execution protocol. Readiness failure retains the allocation for inspection and explicit retry; it never authorizes a replacement. Agent tools and Run-frozen mount permissions remain separate authorization layers.

The forward HTTP route is separate from `docker_host`: Service/Workers must reach the explicitly configured host and the published per-instance port. Remote Engine access alone is insufficient. Published plaintext HTTP is intended for loopback or an explicitly trusted private link, never an unprotected public route; deployment endpoint policy still applies. `disable_network=true` is incompatible and rejected. There is no Docker exec HTTP proxy. All execution operations reuse Harness HTTP EIP, which verifies the expected device identity. `max_file_bytes`, output limits and concurrent-process limits configure envd; native `max_query_entries` does not change EIP traversal semantics.

Credentials stay in each container's protected writable layer and survive stop/start. Destroying that container removes its credential. Revocation stops/destroys the container; in-place rotation requires stopping it, replacing its credential file through authorized operator access, and restarting it. New attachments read the replacement; already-open sessions must be closed before rotation. No shared provider token or plaintext database credential is introduced. Closing execution leaves the desktop running; stop/start retains files but recreates GUI/session/observation state. Reverse WebSocket Service enrollment, ingress and cross-Worker ownership are a separate integration.

### Cloud Providers

| Provider       | Native lifecycle                                                                               | Stop guarantees                                                                      | Lifetime                                                                                                                                         |
| -------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| E2B            | Native SDK create/connect/pause/resume/kill                                                    | Native pause preserves memory and files                                              | Renewable whole-sandbox TTL; expiry is observed from the native service                                                                          |
| Daytona        | Create/get/start/stop/delete through the control API; exec through the validated toolbox proxy | Files survive native stop, archive, and start; memory is not promised                | Automatic deletion and hard TTL are disabled at creation; an organization-enforced hard TTL fails readiness                                      |
| Modal          | Named sandbox discovery, filesystem snapshot/terminate, snapshot-backed creation               | Files survive explicit managed stop; memory does not; resume changes native identity | Fixed running timeout up to 24 hours that keepalive cannot extend; internal snapshots have no expiry and are ownership-validated before deletion |
| Vercel Sandbox | Named persistent sandbox with native running sessions, stop/resume/delete                      | Native stop preserves files; memory is not promised                                  | Session renewal is bounded by the recipe's total timeout; internal native snapshots have no expiry                                               |
| Sprites        | Named Sprite creation/discovery/deletion; automatic wake on exec                               | Explicit stop is unsupported; native automatic sleep preserves disk                  | No execution-owned renewal; native durable filesystem                                                                                            |
| Runloop        | Devbox creation/discovery/suspend/resume/shutdown                                              | Suspend preserves disk, not memory                                                   | Idle policy suspends; an acknowledged keepalive resets the configured idle interval                                                              |

Managed creation is recoverable through deterministic native names or ownership metadata, including after an interrupted allocation, and native metadata is revalidated before adopting a recovered target. Unknown existence, transient errors, and ambiguous control-plane states fail closed. Reconciliation does not allocate, start, stop, or delete. HTTP deletion waits for authoritative absence or the provider's terminal state before state is discarded, and timeout or polling failure retains state and uncertainty. Execution cancellation, dispatch evidence, and command completion belong to the Harness execution contract.

Native lookup names and backing identities are distinct: Sprites state retains its native ID and Vercel state retains name plus creation timestamp, and `target_identity()` exposes that evidence to Host persistence. Reconnecting preserves Host generation; confirmed replacement advances it even when the lookup name is unchanged.

#### E2B

The allocation recipe selects the template (default `base`), sandbox-wide internet access, and sandbox TTL (`timeout_seconds`, default 3600), alongside the Harness-owned execution inputs. Compatible backends can require their own templates and may lack lifecycle operations. Lifecycle calls use the explicit account API URL or `https://api.<domain>` and never inherit an ambient API URL. Ownership metadata contains the stable Environment identity and configuration fingerprint. Interrupted creation can search allocation metadata; multiple matches and incompatible metadata are conflicts. Search is not atomic create-if-absent, so Service lifecycle coordination remains necessary.

With `desktop=true`, an omitted template selects `desktop`; an explicitly supplied compatible custom template is preserved. The recipe selects width, height, DPI and X11 display. Service invokes the official synchronous Desktop SDK's `create()` off the event loop; that SDK initializes Xvfb/XFCE. The adapter supplies the configured user, DISPLAY, locale and request bounds. Native identity is recorded as soon as the SDK constructs its allocated client, before desktop initialization. On initialization failure the SDK attempts to kill that allocation; its recorded identity remains available for inspection/cleanup and does not authorize automatic replacement. Interrupted creation is reconciled through the existing ownership metadata. Explicit start/resume uses the base SDK and checks the retained desktop; it does not reinitialize or replace a missing desktop. Execution attachment never initializes or resumes it. Template selection grants neither Agent tools nor mount permissions. Existing ordinary templates keep the base asynchronous SDK, `base`, their configuration fingerprints and lifecycle behavior.

`stop()` pauses the validated sandbox with memory preservation; `destroy()` kills only that sandbox. Neither prepares or resumes it. Keepalive rejects paused or missing targets, never shortens an existing deadline, and reports observed expiry. [Execution attachment](../a13n-harness/08b-environment-providers.md#e2b) never performs those operations.

#### Daytona, Modal, Vercel Sandbox, Sprites, and Runloop

Daytona native stop and start preserve files only with confirmed disabled auto-deletion, so stop fails before dispatch otherwise; delete acknowledgement is fire-and-forget, so destruction polls until `destroyed` or absence.

Modal rejects stop for external registrations because snapshot-based resume would require allocation. A managed stop caches its snapshot selector before terminating the sandbox, resume retains that selector until readiness succeeds, and deletion retains pending snapshot cleanup state until cleanup succeeds. Native ownership tags must agree before deleting snapshot images. Modal uses an existing deployed App; the execution creates no Apps and deletes no Host-supplied base image. If an upstream snapshot response is lost before its image ID is known, the provider cannot enumerate that orphan image; this is an upstream limitation, not permission to infer completion.

Vercel Sandbox destruction verifies target absence and requests native orphan-snapshot deletion, which the provider performs asynchronously without deleting snapshots still used elsewhere.

Fly.io Sprites automatic native sleep and wake retain disk. There is no supported explicit stop operation, and the capability declaration prevents a Host from presenting one. Destruction verifies absence of the exact named backing target.

Runloop suspend and resume preserve disk. Keepalive uses the returned target idle policy rather than an assumed recipe interval, and shutdown is complete only after confirmed native shutdown or absence.

The command-only drivers refuse destruction of externally owned targets. Service's managed `local` driver and its directory lifetime remain defined by [Environments](06-environments.md); it is distinct from Harness `direct_local`, which operates an existing Host directory and has no management lifecycle.

## Invariants

1. Service owns vendor management and durable lifecycle decisions; drivers perform no database operations.
2. Shared execution schemas and codecs have one Harness owner and are reused by Service.
3. Vendor I/O and waits occur outside database transactions.
4. Only management dispatches explicit target allocation, start, stop, destruction, or renewal; declared native automatic sleep/wake remains a backend behavior.
5. Connector construction is inert; execution cleanup cannot retire a target.
6. Unknown dispatch retains operation identity and observed state for reconciliation.
