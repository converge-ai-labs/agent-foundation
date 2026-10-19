# Environment Management Providers

## Design Position

Service owns built-in Environment management definitions and vendor implementations. Each managed infrastructure vendor implements its own `EnvironmentProvider` subclass for resource lifecycle and connection preparation, independently of the selected execution path. They perform explicit operations on one target; [Environments](06-environments.md) owns durable instances, lifecycle operations, coordination, renewal scheduling, and retirement policy. Harness owns [Environment Execution](../a13n-harness/08a-environment-execution.md): shared state, execution definitions, fixed-target connectors, and native/EIP operations. Service composes those execution definitions rather than maintaining a second implementation of execution.

The [execution backend boundary](../a13n-harness/08b-environment-providers.md#execution-backend-boundary) governs which Harness execution route Service composes. For Envd-backed targets, vendor integration owns lifecycle, daemon installation/bootstrap and connection setup; Agent operations use the shared EIP implementation. Bounded vendor commands used to prepare a target do not create a vendor-native Agent execution path. Human desktop access remains a separate management responsibility.

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

### Desktop Access Enforcement

E2B, Docker and Daytona management adapters support an optional, internal desktop-access endpoint on an existing running target. The endpoint is distinct from Agent Computer execution and environment lifecycle credentials. Service owns its public control-plane authorization. A desktop recipe alone does not enable human access. Discovery reports unsupported unless the template, image or snapshot supplies the endpoint; access calls never install dependencies, create a target, restart its display, resume it or extend its sandbox lifetime.

The Host owns human authorization and exclusive managed execution admission. It must reserve the actual target, drain managed execution and reconcile background activity before issuing control. Provider grants alone do not exclude native Desktop SDK, envd, shell commands or guest administrators. Service exposes these operations through Agent-requested assistance and user-requested control, as specified below.

The internal endpoint accepts discovery, grant, inspect, renew, revoke and held-input cleanup operations under desktop-access protocol version 1, separate from EIP. Every request carries its protocol version; unsupported versions are rejected before dispatch. Discovery confirms the supported version so independently published Service and sandbox artifacts fail closed on incompatibility. Each grant is bound to an endpoint generation, a Host-chosen grant identity, mode (`view` or `control`) and positive epoch. A generation change invalidates previous commands. Only one controller can be active or awaiting confirmed cleanup; later controllers require increasing epochs. Retrying a grant identity with different mode or epoch conflicts; retrying a revoked or expired grant never resurrects it. Inspection and revocation return no credentials. Discovery must advertise `revocation_fences_grants`; revoking an unknown grant records a tombstone, preventing a delayed grant request from resurrecting revoked authority.

Each viewer/controller has an independent authenticated WebSocket-to-RFB connection grant. Read-only permission is enforced at the RFB endpoint, including for a client sending raw input; a browser `viewOnly` flag is not authorization. Tokens authorize only a loopback backend and do not carry cloud API, sandbox-wide preview, Docker Engine or envd credentials. Clipboard synchronization is disabled. Revocation first removes reconnection authority, then terminates the grant's backend and its existing connections. Controller revocation also releases held keys/buttons and confirms those release requests at the X server. Unconfirmed cleanup prevents another controller from being admitted. This held-input cleanup result alone does not prove that other X clients, previously queued input or application work are quiescent; a Host must establish that separately before resuming managed execution.

Leases last at most 60 seconds and may be renewed up to 15 minutes from grant creation. Both wall and monotonic clocks bound expiry. Expiry is enforced in the guest independently of a connected Host/Worker. Endpoint process death terminates its direct input backends. Restarting an endpoint changes generation and requires reconciliation; a missing endpoint during revocation is an unknown outcome, not proof of successful cleanup. The supervisor keeps at most 16 active grants and 4,096 grant identities per generation.

The management socket is root-private in the supplied deployment; guest root/sudo and host administrators remain trusted. WebSocket traffic goes directly to the provider-local endpoint on port 6081. Remote deployments must supply browser-reachable HTTPS/WSS routing and prevent bypass of the authenticated route; Docker Engine or envd reachability is insufficient. Tokens and VNC passwords must not enter ordinary state, tool results, audit logs or referrer-bearing page URLs. Ordinary Console snapshots grant no interactive access. The assistance card obtains a short-lived connection only through the authorized control plane.

## Agent-requested Desktop Assistance

A root agent with the Computer toolset enabled can call `request_desktop_assistance(mount, reason)` for a frozen mount that permits observation. This is a deferred tool call, not forced takeover of a running task. The recipe must opt into `desktop_assistance=true`. E2B requires a compatible operator-built desktop template; stock `desktop` alone is insufficient. Docker requires the opt-in desktop image endpoint and an operator-provided `desktop_viewer_url` containing `{container_id}`, routed to that container's port 6081. Daytona requires an operator-built snapshot that preinstalls and starts the same supervisor, and uses short-lived, port-scoped signed preview hosts for port 6081. Standard sandbox-wide preview credentials are never returned. The signed route may expire independently; the supervisor grant remains the authority for read-only enforcement, lease expiry and termination of existing connections. Revocation and cleanup do not depend on issuing or expiring a signed preview URL. Routes require WSS, except loopback development WS. Service does not publish Docker ports or provide a media proxy.

Service reserves the actual native sandbox/container identity across registered aliases. Other accepted runs and every Harness-managed operation, including Envd Computer, file and shell dispatch, consult the same target reservation. The requesting run closes its execution scopes before its durable wait is published. No Worker is held while a person responds. A mount's optional Host `operation_guard` spans each Agent operation dispatch, whether native Local/Docker/E2B or EIP, after ordinary authorization; it can only narrow authority. A failed mutating operation leaves durable uncertainty and blocks a human grant. Failed read-only operations drain their admission. Targets with prior untracked execution, initialization scripts or dispatched shell/process-input work are not eligible: Service cannot prove that arbitrary background processes stopped. Guest administrators, independently connected SDK clients and externally launched X clients remain trusted operators outside this managed exclusivity boundary.

The public control-plane routes are:

| Route                                             | Contract                                                                                                      |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `GET /runs/{run_id}/desktop-assistance/{call_id}` | Authorized metadata only; no connection secrets.                                                              |
| `POST /desktop-assistance/{id}/view`              | Issue or renew a per-principal, per-browser-session read-only grant.                                          |
| `POST /desktop-assistance/{id}/control`           | Claim or renew the single human controller using a fenced epoch.                                              |
| `POST /desktop-assistance/{id}/disconnect`        | Revoke this browser session's viewer/controller grants without answering the deferred call.                   |
| `POST /desktop-assistance/{id}/complete`          | Revoke all viewers and the controller, confirm held-input cleanup, then persist a bounded assistance outcome. |

All routes use the ordinary workspace/run authorization. Access mutations additionally require the original run's current execution authority, an idle waiting thread head for Agent-requested assistance (the current or latest Run for user-requested access), current environment/provider access and matching native identity/generation. A controller belongs to a principal and browser session; another tab cannot steal its lease. Access responses use `Cache-Control: no-store`; only these responses contain the provider-direct WebSocket URL and VNC password. Console keeps them in component memory, enables input only after a confirmed control grant connects, renews every 15 seconds, disables input on renewal failure and disconnects on closure. The server endpoint enforces the actual permission and expiry even if the client is malicious or disappears.

The complete assistance interval is bounded to 15 minutes; individual grants last at most 60 seconds. Control-role reconciliation revokes expired grants and grants whose principal or originating run loses authority, without silently answering other pending calls. Timeout or invalidation revokes all grants before releasing the reservation and records an abandoned outcome. A missing endpoint, generation change, unknown operation or unconfirmed input cleanup keeps the target blocked. Lease expiry alone never means cleanup succeeded. Operators must reconcile the target before clearing a blocked reservation; the first version has no unsafe force-release API.

Completion does not start a run. The application submits the existing exact, full pending batch to `/runs/{run_id}/resume`, including the exact persisted assistance outcome and explicit answers for every other call and approval. Forged outcomes, unresolved assistance or uncertain target authority reject resume transactionally. The successor retains the reservation, verifies the same endpoint generation and cleanup, and initially permits only Computer description/observation. A successful fresh observation followed by the next model request releases the gate; input from the same pre-observation tool batch remains denied. The model must replan from the new screen. If the successor terminates without passing this gate, reconciliation cleans up and releases the target.

Console provides the same flow as a thin assistance card: inspect read-only, start operating, finish assistance, then submit the complete pending batch. User-requested control follows the suspension contract below. Neither flow offers shared simultaneous control, clipboard synchronization or automatic responses to unrelated approvals/questions.

## User-requested desktop control

An authorized user can open a read-only desktop for the current or latest Run and request control during execution. The same E2B/Docker target identity, provider-local endpoint, direct media route, permission ceiling and revocation rules apply. Viewing does not pause execution, provision an environment or extend its lifetime. One user-control context selects one desktop mount per Run; another mount selection conflicts rather than silently switching the target.

| API                                             | Contract                                                                                                                                              |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /runs/{run_id}/desktop-control`            | Current user-control metadata, or null; no access credentials.                                                                                        |
| `POST /runs/{run_id}/desktop-control`           | Open an idempotent user-origin context for a frozen mount permitting observation. It starts in `viewing`, with no tool-call ID or target reservation. |
| `POST /desktop-assistance/{id}/request-control` | Reserve the target and request durable suspension. Return `preparing` until admitted work settles; never grant input in this operation.               |

The existing view/control/disconnect/complete routes operate on the resulting context. Control requires `awaiting_user` or an existing controller and a confirmed waiting/completed Run; it cannot bypass preparation. A completed Run admits idle control only after settled execution evidence. Handback for an idle desktop does not create a Run.

Suspension fences all managed tool dispatch in the selected Run, including file and connection tools. A call admitted before the request may settle; a later call receives an explicit not-executed result. Parallel calls are accounted independently. Computer operations admitted before the request may drain under the existing target reservation, but no unrelated Run or alias may use it. At the next complete model boundary, the Worker commits the resulting history and a distinct `desktop_control_id` wait before releasing its slot. Desktop-enabled Runs acknowledge the model checkpoint before dispatch, making that boundary the model-request admission point. Generation already admitted may finish; its subsequent tool calls still pass the pause fence. A completed response racing suspension ends the task and leaves only idle control eligibility.

Agent-requested assistance and user-requested takeover serialize under the same Run lock, across all mounts. The first committed reservation wins. If user takeover wins before an admitted assistance tool finishes discovery, that tool returns a known no-op result and joins the user suspension; it creates no deferred assistance call and does not mark execution uncertain. If Agent assistance wins, user open/acquire returns `desktop_agent_assistance_pending`. Console disables the losing entry, refreshes until the native pending batch is available, and replaces it with the assistance card without automatically granting control or answering the batch. A losing read-only viewer may still revoke its own grant; it cannot acquire or renew input authority.

The first implementation supports a root Agent without a configured child-agent graph. An execution with children is explicitly refused with `desktop_task_tree_unsupported`; it is never partially paused. Independent accepted/running Runs sharing the target conflict. Arbitrary shell/process-input work, initialization scripts, unsettled tool calls and uncertain results prevent control. The guarantee is managed Service mutual exclusion, not OS-wide freezing or rollback of external effects.

Worker loss after a takeover request fails the Run with `desktop_pause_attempt_lost` instead of retrying potentially executed calls. Unknown tool or native input effects keep the target blocked until reconciled. Cancellation, failure and a changed environment generation do not become a resumable user-control wait.

Completion revokes connections and confirms held-input cleanup before `ready_to_resume`. Resume validates the exact pending batch and the stored control completion transactionally. A desktop-only wait uses empty `calls` and `approvals`; a mixed wait still requires every native deferred result. With no caller-supplied continuation input, Service supplies the persisted human note and an instruction to observe and replan. The successor must observe the current desktop and reach the following model boundary before other tools or mutations are admitted. Old unexecuted gestures are not replayed.

Disconnect and controller lease expiry revoke access but leave the task paused without an answer. After the entire user-control context expires, explicit resume is permitted only after reconciliation confirms revocation and input cleanup; unresolved cleanup still rejects continuation. A reconnect has no implicit control. The existing 15-minute context deadline and 60-second independently enforced grant bound apply. Stop/delete, provider expiry and loss of current authority invalidate access; cleanup uncertainty must not permit a competing controller. No force-release API or implicit replacement environment is provided.

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

`/workspace` belongs to the container writable layer, and each Environment owns a separate container. Explicit external host directories are shared only when recipes name the same source; Docker resolves source paths in the Engine's filesystem namespace, which may differ from the process hosting the Harness. Destruction never removes an external source. The default image is `ghcr.io/converge-ai-labs/a13n-sandbox:dev`; a custom image supplies Linux, Python 3.10 or later, the configured shell, and a user able to write `/workspace` and `/tmp/a13n`, with Git additionally required for Git-ignore queries. With `execution="native"` (the default), the Provider replaces the image's ENTRYPOINT and CMD, enables Docker init, and runs a Python waiting process. The container user is the explicit recipe `user`, otherwise the image label `ai.a13n.environment.user`, otherwise the image default USER. The sandbox image labels its native execution user as `sandbox` (UID/GID 1000) while retaining root for standalone Envd startup. Native Docker does not start the bundled daemon. There is no guest operation server: file operations use bounded one-shot Python standard-library commands through Docker exec, and file semantics share the native helper.

Creation uses a deterministic name plus ownership and configuration labels; interrupted creation discovers that target and validates its labels without starting it. Foreign or incompatible targets are never adopted or destroyed. `create()` recovers its allocation, and `start()` starts the retained container; confirmed loss is not permission to recreate. Initialization must finish before readiness is published: a marker records completion, and a retained container without it fails preparation rather than replaying a partial script. `stop()` preserves the writable filesystem. `destroy()` removes only the validated container and its private filesystem, never external sources; confirmed absence succeeds idempotently. Changing an image tag does not replace an existing target. Execution attachment and close follow the [Docker execution contract](../a13n-harness/08b-environment-providers.md#docker).

With `execution="envd_http"`, Service preserves the image's entrypoint and command, sets the allocation device ID and envd HTTP bootstrap settings, and publishes container port 8888 on the account's configured bind interface with a Docker-assigned host port. The image must start envd as root with Session execution under its own provisioned identity, honor `A13N_ENVD_HTTP_CREDENTIAL_FILE`, and atomically create a protected random per-instance credential if absent. The supplied images implement this in their common entrypoint. Custom compatible images need no official image label or particular window manager. Recipe user and envd/display variable overrides are rejected. Native defaults and saved native fingerprints remain compatible; unpublished experimental relay targets must be recreated with the explicit execution configuration.

Console offers Standard environment, Desktop environment and Custom image. The Docker definition publishes the two built-in recipe tuples in `x-environment-presets`, following Service's sandbox image policy and the desktop variant's `-desktop` tag suffix. Standard fixes the standard image, native execution and no desktop requirement; Desktop fixes the desktop image, HTTP EIP execution and the desktop requirement. Only Custom exposes editable image, execution, desktop and advanced/JSON configuration. Existing nonmatching images or advanced overrides open as Custom without rewriting them. Explicitly choosing a built-in preset restores advanced defaults and keeps CPU, memory and mounts; no preset identifier is persisted. Connection routing and Agent permissions remain independently configured. Publishing a Service release that offers the desktop preset requires publishing its matching sandbox desktop tag first.

`desktop=true` additionally requires an observable, input-ready desktop before create/start succeeds. The supplied desktop image owns Xvfb/Openbox/envd supervision. EIP without this requirement only needs the ordinary execution protocol. Readiness failure retains the allocation for inspection and explicit retry; it never authorizes a replacement. Agent tools and Run-frozen mount permissions remain separate authorization layers.

With `execution="envd_http"`, the forward HTTP route is separate from `docker_host`: Service/Workers must reach the explicitly configured host and the published per-instance port. Remote Engine access alone is insufficient. Published plaintext HTTP is intended for loopback or an explicitly trusted private link, never an unprotected public route; deployment endpoint policy still applies. `disable_network=true` is incompatible and rejected. There is no Docker exec HTTP proxy. All execution operations on this route reuse Harness HTTP EIP, which verifies the expected device identity. `max_file_bytes`, output limits and concurrent-process limits configure envd; native `max_query_entries` does not change EIP traversal semantics.

Credentials stay in each container's protected writable layer and survive stop/start. Destroying that container removes its credential. Revocation stops/destroys the container; in-place rotation requires stopping it, replacing its credential file through authorized operator access, and restarting it. New attachments read the replacement; already-open sessions must be closed before rotation. No shared provider token or plaintext database credential is introduced. Closing execution leaves the desktop running; stop/start retains files but recreates GUI/session/observation state. Reverse WebSocket Service enrollment, ingress and cross-Worker ownership are a separate integration.

### Cloud Providers

| Provider       | Native lifecycle                                                                  | Stop guarantees                                                                      | Lifetime                                                                                                                                         |
| -------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| E2B            | Native SDK create/connect/pause/resume/kill                                       | Native pause preserves memory and files                                              | Renewable whole-sandbox TTL; expiry is observed from the native service                                                                          |
| Daytona        | Create/get/start/stop/delete through the control API; prepare the Envd connection | Files survive native stop, archive, and start; memory is not promised                | Automatic deletion and hard TTL are disabled at creation; an organization-enforced hard TTL fails readiness                                      |
| Modal          | Named sandbox discovery, filesystem snapshot/terminate, snapshot-backed creation  | Files survive explicit managed stop; memory does not; resume changes native identity | Fixed running timeout up to 24 hours that keepalive cannot extend; internal snapshots have no expiry and are ownership-validated before deletion |
| Vercel Sandbox | Named persistent sandbox with native running sessions, stop/resume/delete         | Native stop preserves files; memory is not promised                                  | Session renewal is bounded by the recipe's total timeout; internal native snapshots have no expiry                                               |
| Sprites        | Named Sprite creation/discovery/deletion; automatic wake on exec                  | Explicit stop is unsupported; native automatic sleep preserves disk                  | No execution-owned renewal; native durable filesystem                                                                                            |
| Runloop        | Devbox creation/discovery/suspend/resume/shutdown                                 | Suspend preserves disk, not memory                                                   | Idle policy suspends; an acknowledged keepalive resets the configured idle interval                                                              |

Managed creation is recoverable through deterministic native names or ownership metadata, including after an interrupted allocation, and native metadata is revalidated before adopting a recovered target. Unknown existence, transient errors, and ambiguous control-plane states fail closed. Reconciliation does not allocate, start, stop, or delete. HTTP deletion waits for authoritative absence or the provider's terminal state before state is discarded, and timeout or polling failure retains state and uncertainty. Execution cancellation, dispatch evidence, and command completion belong to the Harness execution contract.

Native lookup names and backing identities are distinct: Sprites state retains its native ID and Vercel state retains name plus creation timestamp, and `target_identity()` exposes that evidence to Host persistence. Reconnecting preserves Host generation; confirmed replacement advances it even when the lookup name is unchanged.

#### E2B

The allocation recipe selects the template (default `base`), sandbox-wide internet access, and sandbox TTL (`timeout_seconds`, default 3600), alongside the Harness-owned execution inputs. Compatible backends can require their own templates and may lack lifecycle operations. Lifecycle calls use the explicit account API URL or `https://api.<domain>` and never inherit an ambient API URL. Ownership metadata contains the stable Environment identity and configuration fingerprint. Interrupted creation can search allocation metadata; multiple matches and incompatible metadata are conflicts. Search is not atomic create-if-absent, so Service lifecycle coordination remains necessary.

With `desktop=true`, an omitted template selects `desktop`; an explicitly supplied compatible custom template is preserved. The recipe selects width, height, DPI and X11 display. Service invokes the official synchronous Desktop SDK's `create()` off the event loop; that SDK initializes Xvfb/XFCE. The adapter supplies the configured user, DISPLAY, locale and request bounds. Native identity is recorded as soon as the SDK constructs its allocated client, before desktop initialization. On initialization failure the SDK attempts to kill that allocation; its recorded identity remains available for inspection/cleanup and does not authorize automatic replacement. Interrupted creation is reconciled through the existing ownership metadata. Explicit start/resume uses the base SDK and checks the retained desktop; it does not reinitialize or replace a missing desktop. Execution attachment never initializes or resumes it. Template selection grants neither Agent tools nor mount permissions. Native E2B remains Files & Shell only; Agent Computer use requires a compatible Envd runtime and an explicit EIP connection. The Desktop SDK here owns resource preparation, not an Agent execution adapter. Existing ordinary templates keep the base asynchronous SDK, `base`, their configuration fingerprints and lifecycle behavior.

`stop()` pauses the validated sandbox with memory preservation; `destroy()` kills only that sandbox. Neither prepares or resumes it. Keepalive rejects paused or missing targets, never shortens an existing deadline, and reports observed expiry. [Execution attachment](../a13n-harness/08b-environment-providers.md#e2b) never performs those operations.

#### Daytona, Modal, Vercel Sandbox, Sprites, and Runloop

These providers prepare a compatible Envd runtime and an authorized connection on the selected target. Images and snapshots can preinstall Envd; provider bootstrap reuses the [shared executable distribution and installation contract](../a13n-envd/08-protocol-source-client-and-generation.md#executable-distribution-and-installation). Management verifies the required EIP capabilities before publishing readiness. Vendor-specific connection routes do not substitute vendor file, command or Computer APIs for Agent operations.

With Daytona `desktop=true`, the snapshot supplies Envd and compatible desktop packages; management create/start checks the EIP Computer readiness of the recorded target. A startup failure retains the observed target identity for reconciliation and cleanup. Execution attachment does not start or repair desktop processes. With `desktop_assistance=true`, a nonempty custom snapshot is required and management also confirms its preinstalled endpoint. The snapshot starts the supervisor at boot; access calls never bootstrap it. Snapshot construction is an operator task requiring separate snapshot-build authority.

Daytona native stop and start preserve files only with confirmed disabled auto-deletion, so stop fails before dispatch otherwise; delete acknowledgement is fire-and-forget, so destruction polls until `destroyed` or absence.

Modal rejects stop for external registrations because snapshot-based resume would require allocation. A managed stop caches its snapshot selector before terminating the sandbox, resume retains that selector until readiness succeeds, and deletion retains pending snapshot cleanup state until cleanup succeeds. Native ownership tags must agree before deleting snapshot images. Modal uses an existing deployed App; the execution creates no Apps and deletes no Host-supplied base image. If an upstream snapshot response is lost before its image ID is known, the provider cannot enumerate that orphan image; this is an upstream limitation, not permission to infer completion.

Vercel Sandbox destruction verifies target absence and requests native orphan-snapshot deletion, which the provider performs asynchronously without deleting snapshots still used elsewhere.

Fly.io Sprites automatic native sleep and wake retain disk. There is no supported explicit stop operation, and the capability declaration prevents a Host from presenting one. Destruction verifies absence of the exact named backing target.

Runloop suspend and resume preserve disk. Keepalive uses the returned target idle policy rather than an assumed recipe interval, and shutdown is complete only after confirmed native shutdown or absence.

These management drivers refuse destruction of externally owned targets. Service's managed `local` driver and its directory lifetime remain defined by [Environments](06-environments.md); it is distinct from Harness `direct_local`, which operates an existing Host directory and has no management lifecycle.

## Invariants

1. Service owns vendor management and durable lifecycle decisions; drivers perform no database operations.
2. Shared execution schemas and codecs have one Harness owner and are reused by Service.
3. Vendor I/O and waits occur outside database transactions.
4. Only management dispatches explicit target allocation, start, stop, destruction, or renewal; declared native automatic sleep/wake remains a backend behavior.
5. Connector construction is inert; execution cleanup cannot retire a target.
6. Unknown dispatch retains operation identity and observed state for reconciliation.
7. Every infrastructure vendor retains an Environment Provider subclass for its resource management. Native Agent execution is limited to Local/Docker/E2B Files & Shell; general execution and advanced operations use Envd/EIP without native fallback.
