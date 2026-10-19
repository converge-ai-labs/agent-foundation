# Environment Execution

## Design Position

Harness owns the provider-neutral contracts and implementations for executing operations against one Host-selected Environment. This includes `EnvironmentState`, descriptors, availability, operation inputs and results, execution errors, fixed-target connectors, execution scopes, native file/process helpers, and EIP adaptation. [Environment Integration](08-environment-integration.md) owns `EnvironmentSource`, mounts, Run-local routing, permissions, and model-facing tools. [Service Environment Management](../a13n-service/06a-environment-management.md) owns built-in target management interfaces and vendor lifecycle implementations.

An embedded application can connect to an existing Direct Local, native Docker, native E2B, or Envd-backed target using Harness without installing Service. The [execution backend boundary](08b-environment-providers.md#execution-backend-boundary) owns Native Files & Shell and the general Envd path for OS sandbox, Egress, Computer use, and metrics. Built-in cloud allocation, start, stop, destroy, and target renewal belong to Service. An alternative Host that needs those effects supplies its own management implementation and readiness source. Harness UI retains native, Local Envd, and remote Device execution without a Service dependency.

`EnvironmentConnector` is the environment connection interface for one Host-selected target, such as a particular Docker container or E2B sandbox. Fixed-target means that opening or reconnecting cannot select a different native target. It is distinct from a third-party application Connector Provider, which supplies application accounts and tools.

`EnvironmentExecution` is the ready access scope returned by `EnvironmentConnector.open()`. It exposes the target's descriptor, availability, and operations, and owns acquired clients, Sessions, and observations until close. It is independent of an individual command or a Host's durable Run.

## Ownership and Dependencies

| Concern                                                                                                                                                                     | Owner                                                                |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Execution definitions, input schemas, portable state and codecs, identity validation, descriptors, operations, results and execution errors                                 | Harness                                                              |
| Fixed-target native and EIP connectors, execution clients, file/process/output/port/computer operations                                                                     | Harness                                                              |
| Local Envd executable resolution and daemon/Device connection support                                                                                                       | Harness support with explicit Host-owned runtime and launch policy   |
| Management definitions, lifecycle capabilities, allocation recipes, management status/results/errors and vendor create/start/inspect/stop/destroy/keepalive implementations | Service                                                              |
| Database access, credential storage, authorization, leases, operation claims, state publication, renewal scheduling and retirement                                          | Service for its managed resources; each other Host for its resources |
| `EnvironmentSource` protocol and its invocation                                                                                                                             | Harness                                                              |
| Source implementation, including any preparation and persistence                                                                                                            | The owning Host                                                      |

Service depends on Harness; Harness imports neither Service nor its ORM, tables, sessions, repositories, or lifecycle controllers. All Harness-owned Environment behavior is database-free, including the source protocol and built-in source wrappers. A Service source can access its database internally, but passes only validated plain inputs, portable state, authorized connection material, and process-local collaborators to Harness. No database session, transaction, ORM object, or Service principal crosses that boundary.

Native execution can use filesystem, network, subprocess, and vendor SDK I/O. Database-free does not mean I/O-free. Local Envd helper-process ownership is execution support, not managed cloud-target lifecycle: the Host chooses executable, containment, launch policy, connection sharing, and cleanup. It needs no Service management definition.

Execution metadata and factories load independently of management metadata and factories. Shared schemas, codecs, identity checks, and transport primitives required for execution live in Harness and are reused by Service. Management-only inputs stay in Service; Service composes or narrows the shared schemas instead of copying them. No shared helper carries both execution and lifecycle authority.

## Definitions and Inputs

`EnvironmentProviderDefinition` is immutable Harness metadata for one execution backend. It declares type and display name, configuration and credential schemas, credential-presence rules, target-compatible input schemas, setup help, inert descriptor projection, identity/state validation, and a fixed-target connector factory. It exposes no management factory or lifecycle methods. Definition construction and schema validation perform no external I/O and import no optional vendor SDK. Identity and credential metadata follow the [Provider subsystem](22-provider-subsystem.md).

Account configuration identifies a backend; target-compatible configuration describes the selected environment; execution options select operation limits and Session configuration. Service's allocation recipe can compose these inputs with management-only settings. Stable native target identity comes from authorized state or explicit connect-only configuration, never from a reusable allocation recipe. A changed state codec does not create a configuration schema version.

The Host selects trusted definitions, validates inputs and credential presence, and supplies current connection material. Secret values are absent from public representations and revealed only at the native transport boundary. Conditional required, optional, or forbidden presence is resolved before external I/O. `target_identity` and non-secret `backend_identity` distinguish the selected native resource and its namespace from connection tuning and Host record IDs; neither new credentials nor a new application record creates another namespace for the same target.

Built-ins and installed definitions use the same execution contract. An unsupported operation fails before dispatch. Host catalog membership is availability, not authorization or a management capability.

## Core Interfaces

The following is a conceptual Python API, not a wire schema. Concrete methods retain typed provider inputs, results, and errors.

```python
class EnvironmentProviderDefinition:
    def describe_environment(self, configuration) -> EnvironmentDescriptor: ...
    def connector(self, configuration, credential, state, options) -> EnvironmentConnector: ...


class EnvironmentConnector:
    state: EnvironmentState | None
    async def open(self) -> EnvironmentExecution: ...


class EnvironmentExecution:
    state: EnvironmentState | None
    descriptor: EnvironmentDescriptor
    availability: EnvironmentAvailability
    operations: EnvironmentOperations
    async def check_ready(self, operations): ...
    async def close(self): ...
```

Management and execution use distinct constructors and concrete objects. An execution implementation neither inherits nor delegates to a combined implementation carrying lifecycle methods. Shared support may hold configuration, identity validation, transport primitives, and read-only target queries. Observing a target during connection is distinct from Service's management `inspect()` and does not publish durable state.

An `EnvironmentConnector` captures the validated selector, execution options, and Host-authorized connection material. Operation arguments cannot change its target. It has no management methods and cannot hide management calls behind a credential callback. A Host-owned credential source may refresh connection material under the same authority; Harness owns no credential store. A narrow Python interface is not a vendor credential privilege boundary.

Connectors own no live clients requiring teardown. `open()` acquires and validates a connection or Session and returns a ready execution, or releases partial resources on failure or cancellation. Repeated opens produce independent scopes where supported. Each execution has one Run or explicit embedded caller as owner and is not shared with another independent execution owner. Borrowed Device connections have an explicit Host owner that outlives all borrowing executions. Closing Service management clients cannot close an active execution's client.

## State Envelope

The serialized `EnvironmentState` format is retained:

```python
class EnvironmentState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider_key: str
    state_version: str
    state: JsonValue
```

The payload is bounded canonical JSON sufficient to identify and validate one target, including a stopped target. Providers validate their exact codec version, backend and target identity, recipe compatibility, and ownership evidence. The state version is independent of package versions, Host operation generations, and Environment execution incarnations. An incompatible state fails explicitly rather than silently selecting another resource.

State is a reference, not a credential, lease, live client, authority grant, or availability guarantee. It excludes credentials, bearer URLs, transport Sessions, callbacks, process handles, temporary runtime paths, Harness task or routing identities, access ceilings, and Host persistence fields. A provider can retain stable native selectors required by its codec, but not a process-local observation registry. A stateless provider uses `None`; its validated configuration fixes the target, and `None` does not mean target absence.

Management results, including partial-failure evidence, carry any newly observed state for Host publication. Environment execution retains a detached reference to its fixed target and does not create new management state during opening or recovery. The Host resolves authoritative current state before constructing a connector, including authoritative `None`. Copying or aggregating references does not grant restoration or publication authority; application checkpoint selection is outside this contract.

## Execution and Reconnection

Opening validates provider, backend, target incarnation, required capabilities, and Session configuration before exposing operations. It can allocate Sessions owned by the Environment execution, observations, and buffers; it cannot create, start, resume, replace, or renew the backing target. A preflight lookup does not make a mutating attachment API safe. A backend that cannot satisfy this boundary reports an explicit unsupported outcome.

`check_ready()` observes an already open `EnvironmentExecution` for the required operation families. It neither reconnects nor performs management. Availability is process-local evidence, not authoritative Host lifecycle state. A ready scope can later fail because of transport loss, expiration, external deletion, or infrastructure failure.

Transport reattachment inside the same valid Session may retain identity and observations only where the backend confirms their continuity. Dispatched requests with uncertain outcomes are never replayed. When the Session itself is lost, the Harness may explicitly open a new Environment execution through the same Environment connector, according to backend capabilities and Host policy. The new Environment execution has fresh resource ownership; stale handles and cursors do not become valid merely because the target ID is unchanged.

The Harness coordinates reopening with admitted operations and cleanup. References owned by the old execution remain invalid in a new scope, and reopening cannot expand Host-granted authority. Closing the old scope can terminate its owned processes or temporary resources; reopening is not a promise of lossless recovery. A stopped, missing, incompatible, or unauthorized target is reported to the Host for handling rather than triggering implicit lifecycle calls. Execution adapters install no target-lifecycle retry or recovery scheduler.

## Operations and Observations

`EnvironmentDescriptor` describes target identity evidence, execution generation, execution boundary, default working directory, operation families, permission support, and limits. `EnvironmentAvailability` reports status, ready families, and a bounded reason. `EnvironmentOperations` exposes typed file, shell, process, output, port, and computer facets independently; unsupported guarantees fail before dispatch rather than degrading silently.

Configured descriptor projection is inert. The live descriptor can narrow configured capabilities but cannot expand admitted authority. The package supplies structured capability and observation data. The Host sets authorization policy; the Harness owns presentation and routing and applies that policy without expanding provider capabilities.

Operation paths, receipts, handles, and output references carry only provider, target, and Environment execution identity. Native target incarnation, execution resource generation, and Host-supplied lifecycle operation identity remain distinct. Application task, routing, and persistence identifiers are not part of these values; the Host and Harness maintain their own correlation separately.

File operations preserve bounded reads, streaming writes, staged publication, copy/move semantics, and typed mutation evidence. Shell foreground results remain bounded. Process completion, output completeness, and process-tree cleanup are separate observations. Capability declarations distinguish execution from process discovery, input, signals, retention, ports, and computer use; an unsupported family does not invalidate unrelated capabilities.

`FileOperator.batch` executes up to 128 typed stat, mkdir, whole-text write (create, replace, or upsert), and remove operations in order, with a 64 KiB JSON request limit. All paths and actions are authorized before dispatch in one pinned file scope. It stops at the first error without rollback or replay; failed or cancelled calls may have completed a prefix. Stat and remove can explicitly tolerate a missing path. Results preserve operation order and the same receipt validation as individual operations.

`FileOperator.write_archive` takes ZIP bytes, a selected archive root, and an exact manifest of relative file paths and expanded sizes. It creates an absent destination directory and extracts only the selected regular files, rejecting unsafe paths, duplicate paths, links, encrypted entries, and mismatched sizes. File limits apply to expanded members. The operation requires file-directory creation and binary-write authority in one pinned file scope; it grants no shell authority and cannot cross mounts. A failure can leave a private candidate; the caller publishes a complete directory through a separate move and never treats a partial extraction as complete.

## Process and Output Observations

The Provider owns native execution truth and the recovery its state codec supports. State can select a target from which commands are discovered without enumerating every command. Reconnecting to the same target permits attempting native lookup; it does not prove that a process survived, and a missing command is never automatically restarted.

`ProcessIdentity` binds a native selector to one Provider, logical Environment, and target generation. Handles scoped to an `EnvironmentExecution` additionally identify the scope that owns their observation. `ProcessInfo` carries a handle scoped to an `EnvironmentExecution`, native status, optional stdin-state evidence, and an optional richer output snapshot. Status may be `unknown` or `missing`; neither invents an exit code or a successful terminal outcome. Closing or capping output observation does not satisfy a process wait. An `initial_terminal` wait observes native completion independently from output completeness and process-tree cleanup.

`process.list` is optional and returns a bounded `ProcessDiscovery(processes, has_more)` without attaching output streams. `rebind` selects an explicit native identity without creating a command. Start, inspect, wait, output reads, discovery, stdin, signals, kill, and release have independent declared actions; lacking discovery, stdin, or arbitrary signals does not remove otherwise supported background observation. `release` relinquishes the local observation and its readers without implying termination; process survival after release or close depends on the backend, not on an application cleanup policy.

Output observations carry returned segments and available offsets together with `origin` (`native_bytes` or `sdk_text`), `coverage` (`complete`, `partial`, or `unknown`), `observation_closed`, a bounded optional reason such as `reattached`, `connection_lost`, `observation_limit`, or `observation_evicted`, and optional `produced_bytes`, `dropped_bytes`, and `producer_complete` evidence that is never fabricated. Native-byte offsets identify the backend's retained range; SDK-text offsets identify UTF-8 encoding of SDK-delivered text, not original stdout bytes. A transient reattachment retains the accumulated current-scope log, appends later text, and marks the gap; it neither resets offsets nor promises replay deduplication. Target replacement invalidates the identity, and a fresh scope starts a new observation.

Ordinary `shell.exec` has a bounded inline result contract. A Provider that internally uses native retention materializes that operation's bounded output and releases its own resources before returning; no retained reference escapes the ordinary result. A post-execution read failure preserves the known command outcome with incomplete output rather than inviting replay. The standalone retained-output facet keeps its independent read and release authorization and is not a prerequisite for shell execution or process observation.

## Resource Lifetime

The Harness closes each acquired Environment execution when its execution scope ends. Closing rejects new work, coordinates admitted operations, and releases the clients, Sessions, observations, and buffers owned by that execution. It is idempotent and never calls target stop or destroy. Failure to clean up is reported separately without concealing the original operation failure or inventing target destruction.

Process survival follows the execution backend: native Docker and E2B close observations without blanket termination; Direct Local closes owned process trees; Envd-backed providers, including Docker's explicit EIP route, close Session-owned execution resources. These distinctions are part of the [provider contract](08b-environment-providers.md), not inferred from connection ownership.

A process crash can bypass close. Whole-target expiry and Host durable lifecycle recovery are independent of Environment execution cleanup. Envd provides Session expiry; native backends and local temporary files do not thereby gain a universal crash-reclamation guarantee. An in-memory registry cannot establish cross-process cleanup ownership.

## Failures and Authority

Management outcome and publication rules belong to [Service Environment Management](../a13n-service/06a-environment-management.md#failures-and-publication).

Environment execution errors distinguish confirmed missing/stopped targets, identity mismatch, closed scopes, unsupported capabilities, and transport failures with unknown state. An authorization error or inaccessible account is not authoritative target absence. Endpoint/TLS policy, credential validation, finite request deadlines, and bounded results apply equally to initial opening and later connections; borrowed transports retain their declared enforcement owner. Blocking SDK work stays off the event loop.

Provider-neutral operation failures expose one safe projection containing `code`, a code-owned bounded `message`, an object-valued `details`, and an optional `retry_hint`. Exception descriptions remain local diagnostics and never become the public message. Providers identify correctable input failures with a public `field`, a stable `reason`, and a corrective `hint` where known. The projection bounds these strings and admits only explicitly public numeric and count fields, missing-operation names, and typed dispatch and retry evidence; arbitrary details, native paths, selectors, credentials, receipts, and raw provider text are omitted. A failure without specific diagnostics still receives safe code-level guidance. This operation projection is distinct from Provider lifecycle error categories and outcome certainty.

EIP adaptation preserves known input diagnostics beyond pattern errors, typed dispatch stage, provider retry semantics, and bounded effect counts without forwarding daemon exception text or native identities. `unknown_outcome` remains `environment_unknown_outcome` and requires reconciliation; it never degrades into an ordinary provider failure or permission to start a fresh mutation. A provider's `same_request` hint describes protocol operation-ID replay, not permission for a model to issue a new operation ID.

## Compatibility

Moving execution and management types into their owning packages preserves Provider type values, `EnvironmentState` envelopes and provider payload formats, native IDs, recipe fingerprints, operation error projections, and process-survival semantics. Changing a non-exempt provider from native execution to EIP is a separate behavioral transition under the [execution backend boundary](08b-environment-providers.md#execution-backend-boundary), including its configuration and Session resource-lifetime rules. Public execution and common types belong to `a13n_harness.environment`; execution definitions and built-ins belong to the Harness Provider domain. Management interfaces belong to `a13n_service`. There is no independent `a13n-environment` distribution or compatibility package.

Consumers update Python imports and package requirements to the owning distribution. Persisted records and EIP do not change merely because classes move. Embedded callers requiring built-in cloud management depend on Service or provide their own Host management; installing Harness alone supplies execution against existing targets. The split of a combined definition into execution and management definitions is a breaking plugin API change under the [manifest compatibility contract](22-provider-subsystem.md#compatibility), never silent acceptance of old combined factories.

## Invariants

1. Harness execution and metadata import without Service and perform no database operations.
2. The Host owns lifecycle policy and durable authority; Service owns its management drivers and orchestration.
3. Environment connectors select fixed authorized targets; opening, checking, and reattaching never allocate, explicitly resume, replace, or renew them.
4. Each execution owns its acquired resources; partial opening, failure, cancellation, and validation failure release those resources.
5. State is portable target data, not authority; execution does not publish management changes.
6. Unknown effects never authorize automatic replay, target recreation, or assumed completion.
7. Closing an execution and retiring its target are independent operations.

## Host Operation Admission

An `EnvironmentMount` may provide an `operation_guard(action)` asynchronous context manager. The coordinator enters it after normal mount, permission and readiness checks and keeps it open across native dispatch, failure and cancellation. Hosts can use this hook for durable mutual exclusion with external operators. The hook never broadens the mount permission ceiling and does not run provider I/O while holding Host storage locks. Provider clients opened outside Harness remain outside this boundary. A missing hook preserves standalone Harness behavior.
