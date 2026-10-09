# Environment Contract

## Design Position

The shared Environment contract separates explicit target management from connections used to execute operations. Hosts authorize and coordinate target lifecycle; the library implements one target's capabilities. Harness consumes a fixed-target Environment connector and owns the Environment execution scopes it opens, without receiving management authority.

`EnvironmentConnector` is the environment connection interface for one Host-selected target, such as a particular Docker container or E2B sandbox. "Fixed-target" means that the target is selected before the object is passed to Harness and cannot be changed by opening or reconnecting it. It is distinct from a third-party application Connector Provider, which supplies application accounts and tools.

`EnvironmentExecution` is the ready environment execution object returned by `EnvironmentConnector.open()`. It exposes the target's descriptor, availability, and operations, and owns the acquired clients, Sessions, and observations until close. It represents an open environment access scope, not an Agent Run or an individual command invocation.

## Definitions and Inputs

An Environment Provider is a backend integration owned by `a13n-environment`. Its implementation supplies a definition, supported management operations, and environment connection and execution behavior. `EnvironmentProvider` below names its management interface; `EnvironmentConnector` and `EnvironmentExecution` name its execution-side interfaces.

`EnvironmentProviderDefinition` is immutable metadata for a trusted Environment Provider type and its construction entry points. It declares the type and display name, account configuration schema, optional credential schema and presence rules, target recipe schema, setup help, and management capabilities. Definition construction and schema validation perform no external I/O and import no optional vendor SDK. Type identifiers follow [platform naming](../data-conventions.md#public-and-internal-naming).

Account configuration identifies a backend; the recipe describes the desired target; execution options select operation limits and Session configuration. These have distinct meanings even where an existing serialized configuration contains fields from several groups. Stable target identity is not supplied through a reusable recipe. Each provider has one schema per input role; a changed state codec does not create a configuration schema version.

The Host selects trusted definitions, validates inputs and credential presence, and supplies current connection material. Credentials use secret types, are absent from public representations, and are revealed only at the native transport boundary. A definition with no credential schema forbids credentials. Conditional required, optional, or forbidden presence is resolved against validated account configuration before external I/O. Hosts can project these schemas and rules without importing a vendor SDK or maintaining vendor-specific forms.

`target_identity` and non-secret `backend_identity` distinguish the selected native resource and its namespace from connection tuning and Host resource IDs. A Host combines them with provider type when deduplicating targets. Neither credentials nor a new Host row creates another namespace for the same target.

Management capabilities are explicit: connect-only targets need no fictitious allocation lifecycle, and an unsupported management or execution operation fails before dispatch. Built-ins and third-party definitions implement the same contract. A Host may compose definitions directly; a Harness Host can use its [installed Provider catalog](../a13n-harness/22-provider-subsystem.md#installed-plugins), without making that loader a dependency of this package.

## Core Interfaces

The following is a conceptual Python API, not a wire schema. Concrete methods retain typed provider inputs, results, and errors.

```python
class EnvironmentProviderDefinition:
    def describe_environment(self, recipe) -> EnvironmentDescriptor: ...
    async def open_provider(self, configuration, credential) -> EnvironmentProvider: ...


class EnvironmentProvider:
    async def create(self, recipe, *, environment_id, operation_id) -> EnvironmentState | None: ...
    async def start(self, state, *, operation_id) -> EnvironmentState | None: ...
    async def inspect(self, target) -> EnvironmentStatus: ...
    async def stop(self, state, *, operation_id): ...
    async def destroy(self, state, *, operation_id): ...
    async def keepalive(self, state, *, deadline, operation_id): ...
    def execution_connector(self, state, options) -> EnvironmentConnector: ...
    async def close(self): ...


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

The management provider holds account-scoped clients. Its `execution_connector()` constructs inert connection inputs for one fixed target; it performs no target I/O and allocates no live execution client. Connect-only providers can construct `EnvironmentConnector` objects directly without implementing management operations.

An `EnvironmentConnector` captures the validated target selector, execution options, and Host-authorized connection material. It cannot select arbitrary model-supplied targets, expose management methods, or hide management calls behind a credential callback. A Host-owned credential source may supply refreshed connection material under the same authority; the library owns no credential store. A narrow Python interface is not a vendor credential privilege boundary.

`EnvironmentConnector` objects own no live client requiring teardown. `open()` acquires and validates a connection or Session for the selected environment and returns a ready `EnvironmentExecution`, or releases partial resources on failure or cancellation. Repeated opens produce independent scopes where the backend supports them; an `EnvironmentExecution` is never shared across independent Runs. Borrowed Device connections or clients have an explicit Host owner that outlives every borrower. Closing a management provider must not close an active `EnvironmentExecution`'s client.

## Management Operations

| Operation   | Observable contract                                                                                                                                                                                                        |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `create`    | Allocate under the supplied stable Environment and operation identities, or recover the result of that same allocation after an ambiguous response. Preserve observed state even if later readiness fails.                 |
| `start`     | Start the represented existing environment under its declared stop/resume guarantees; never silently replace a lost target. Snapshot-backed providers report any resulting native incarnation change explicitly.           |
| `inspect`   | Observe a saved target or find an interrupted allocation by stable ownership correlation, without creating, starting, renewing, or deleting it. Distinguish running, stopped, confirmed absence, and unavailable evidence. |
| `stop`      | Stop or pause the exact validated target and preserve its declared resume state. Repeated calls reconcile actual state without waking it.                                                                                  |
| `destroy`   | Remove the exact owned target. Confirmed absence is an idempotent result; uncertainty retains state for reconciliation. Caller-owned directories, bind sources, and unrelated volumes are never removed.                   |
| `keepalive` | Extend a running target's supported lifetime and report expiry evidence. Never start a stopped target; disclose hard limits and uncertain outcomes.                                                                        |
| `close`     | Release owned management clients, without stopping or destroying the target.                                                                                                                                               |

A stopped target remains distinct from a lost one. A provider whose explicit stop retains a native snapshot can resume from that retained state under its declared contract; a missing target without such evidence is not permission to create a replacement. Replacement is a new Host-authorized allocation and state publication.

Provider metadata declares renewal requirements and supported bounds; the Host owns scheduling, retry policy, idle stop/delete decisions, and active-use protection. Local synchronization protects clients and operations, not cross-process ownership. Hosts reconcile outstanding mutations before issuing conflicting ones; an expired database lease cannot cancel an already dispatched vendor request. Provider I/O occurs outside Host database transactions.

## State Envelope

The existing serialized `EnvironmentState` format is retained:

```python
class EnvironmentState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider_key: str
    state_version: str
    state: JsonValue
```

The payload is bounded canonical JSON sufficient to identify and validate one target, including a stopped target. Providers validate their exact codec version, backend and target identity, recipe compatibility, and ownership evidence. The state version is independent of package versions, Host operation generations, and Environment execution incarnations. An incompatible state fails explicitly rather than silently selecting another resource.

State is a reference, not a credential, lease, live client, authority grant, or availability guarantee. It excludes credentials, bearer URLs, transport Sessions, callbacks, Host process handles, temporary runtime paths, Harness identities, access ceilings, and Host persistence fields. A provider can retain stable native selectors required by its codec, but not a process-local observation registry. A stateless provider uses `None`; its validated configuration fixes the target, and `None` does not mean target absence.

Management results, including partial-failure evidence, carry any newly observed state for Host publication. Environment execution retains a detached reference to its fixed target and does not create new management state during opening or recovery. Host current state takes precedence over portable continuation data, including authoritative `None`. Harness may aggregate references in its existing [continuation mapping](../a13n-harness/08-environment-integration.md#portable-environment-state); that does not grant restoration or publication authority.

## Execution and Reconnection

Opening validates provider, backend, target incarnation, required capabilities, and Session configuration before exposing operations. It can allocate Sessions owned by the Environment execution, observations, and buffers; it cannot create, start, resume, replace, or renew the backing target. A preflight lookup does not make a mutating attachment API safe. A backend that cannot satisfy this boundary reports an explicit unsupported outcome.

`check_ready()` observes an already open `EnvironmentExecution` for the required operation families. It neither reconnects nor performs management. Availability is process-local evidence, not authoritative Host lifecycle state. A ready scope can later fail because of transport loss, expiration, external deletion, or infrastructure failure.

Transport reattachment inside the same valid Session may retain identity and observations only where the backend confirms their continuity. Dispatched requests with uncertain outcomes are never replayed. When the Session itself is lost, the consumer may explicitly open a new Environment execution through the same Environment connector, according to backend capabilities and Host policy. The new Environment execution has fresh resource ownership; stale handles and cursors do not become valid merely because the target ID is unchanged.

Consumers coordinate reopening with admitted operations and cleanup. Harness uses its [mount replacement boundary](../a13n-harness/08-environment-integration.md#run-local-mount-mutation), invalidates old Environment execution references, and refreshes effective context without expanding permissions. Closing the old scope can terminate its owned processes or temporary resources; reopening is not a promise of lossless recovery. A stopped, missing, incompatible, or unauthorized target fails for Host handling rather than triggering implicit lifecycle calls. The library installs no retry or recovery scheduler.

## Operations and Observations

`EnvironmentDescriptor` describes target identity evidence, execution generation, execution boundary, default working directory, operation families, permission support, and limits. `EnvironmentAvailability` reports status, ready families, and a bounded reason. `EnvironmentOperations` exposes typed file, shell, process, output, port, and computer facets independently; unsupported guarantees fail before dispatch rather than degrading silently.

Configured descriptor projection is inert. The live descriptor can narrow configured capabilities but cannot expand admitted authority. Structured data belongs to this package; mount policy, tool schemas, prompt text, and model-context assembly belong to Harness or the other consumer.

Shared operation paths, receipts, handles, and output references carry provider, target, and Environment execution identity, never Harness mount, Agent, Thread, or Run IDs. Native target incarnation, Environment execution resource generation, Host lifecycle operation generation, and Harness mount incarnation remain distinct. Consumers attach their own correlation and stale-reference checks when adapting operations.

File operations preserve bounded reads, streaming writes, staged publication, copy/move semantics, and typed mutation evidence. Shell foreground results remain bounded. Process completion, output completeness, and process-tree cleanup are separate observations. Capability declarations distinguish execution from process discovery, input, signals, retention, ports, and computer use; an unsupported family does not invalidate unrelated capabilities.

## Process and Output Observations

The Provider owns native execution truth and the recovery its state codec supports. State can select a target from which commands are discovered without enumerating every command. Reconnecting to the same target permits attempting native lookup; it does not prove that a process survived, and a missing command is never automatically restarted.

`ProcessIdentity` binds a native selector to one Provider, logical Environment, and target generation. Handles scoped to an `EnvironmentExecution` additionally identify the scope that owns their observation. `ProcessInfo` carries a handle scoped to an `EnvironmentExecution`, native status, optional stdin-state evidence, and an optional richer output snapshot. Status may be `unknown` or `missing`; neither invents an exit code or a successful terminal outcome. Closing or capping output observation does not satisfy a process wait. An `initial_terminal` wait observes native completion independently from output completeness and process-tree cleanup.

`process.list` is optional and returns a bounded `ProcessDiscovery(processes, has_more)` without attaching output streams. `rebind` selects an explicit native identity without creating a command. Start, inspect, wait, output reads, discovery, stdin, signals, kill, and release have independent declared actions; lacking discovery, stdin, or arbitrary signals does not remove otherwise supported background observation. `release` relinquishes the local observation and its readers without implying termination; process survival after release or close depends on the backend, not on a Harness cleanup policy.

Output observations carry returned segments and available offsets together with `origin` (`native_bytes` or `sdk_text`), `coverage` (`complete`, `partial`, or `unknown`), `observation_closed`, a bounded optional reason such as `reattached`, `connection_lost`, `observation_limit`, or `observation_evicted`, and optional `produced_bytes`, `dropped_bytes`, and `producer_complete` evidence that is never fabricated. Native-byte offsets identify the backend's retained range; SDK-text offsets identify UTF-8 encoding of SDK-delivered text, not original stdout bytes. A transient reattachment retains the accumulated current-scope log, appends later text, and marks the gap; it neither resets offsets nor promises replay deduplication. Target replacement invalidates the identity, and a fresh scope starts a new observation.

Ordinary `shell.exec` has a bounded inline result contract. A Provider that internally uses native retention materializes that operation's bounded output and releases its own resources before returning; no retained reference escapes the ordinary result. A post-execution read failure preserves the known command outcome with incomplete output rather than inviting replay. The standalone retained-output facet keeps its independent read and release authorization and is not a prerequisite for shell execution or process observation.

## Resource Lifetime

Closing an Environment execution rejects new work, coordinates admitted operations, and releases the clients, Sessions, observations, and buffers it owns. It is idempotent and never calls target stop or destroy. Failure to clean up is reported separately without concealing the original operation failure or inventing target destruction.

Process survival is provider-specific: native Docker and E2B close observations without blanket termination; Direct Local closes owned process trees; Envd closes Session-owned execution resources. These distinctions are part of the [provider contract](02-providers.md), not inferred from connection ownership.

A process crash can bypass close. Whole-target expiry and Host durable lifecycle recovery are independent of Environment execution cleanup. Envd provides Session expiry; native backends and local temporary files do not thereby gain a universal crash-reclamation guarantee. An in-memory registry cannot establish cross-process cleanup ownership.

## Failures and Authority

Management errors retain a typed category, dispatch/outcome certainty, stable operation identity, and any observed target state. A timeout after possible dispatch is unknown, not a safe-to-repeat rejection. Consumers query or reconcile before retrying; provider idempotency and native correlation remain backend-specific. A failed readiness check after successful creation must not discard the known target ID.

Environment execution errors distinguish confirmed missing/stopped targets, identity mismatch, closed scopes, unsupported capabilities, and transport failures with unknown state. An authorization error or inaccessible account is not authoritative target absence. Endpoint/TLS policy, credential validation, finite request deadlines, and bounded results apply equally to initial opening and later connections; borrowed transports retain their declared enforcement owner. Blocking SDK work stays off the event loop.

Provider-neutral operation failures expose one safe projection containing `code`, a code-owned bounded `message`, an object-valued `details`, and an optional `retry_hint`. Exception descriptions remain local diagnostics and never become the public message. Providers identify correctable input failures with a public `field`, a stable `reason`, and a corrective `hint` where known. The projection bounds these strings and admits only explicitly public numeric and count fields, missing-operation names, and typed dispatch and retry evidence; arbitrary details, native paths, selectors, credentials, receipts, and raw provider text are omitted. A failure without specific diagnostics still receives safe code-level guidance. This operation projection is distinct from Provider lifecycle error categories and outcome certainty.

EIP adaptation preserves known input diagnostics beyond pattern errors, typed dispatch stage, provider retry semantics, and bounded effect counts without forwarding daemon exception text or native identities. `unknown_outcome` remains `environment_unknown_outcome` and requires reconciliation; it never degrades into an ordinary provider failure or permission to start a fresh mutation. A provider's `same_request` hint describes protocol operation-ID replay, not permission for a model to issue a new operation ID.

## Compatibility

Extraction preserves Provider type values, existing `EnvironmentState` envelopes and provider payload formats, target IDs, recipe fingerprints, and provider-specific process-survival semantics. Import paths change to the owning `a13n_environment` package without introducing a second implementation. Host records and Harness continuation do not require a new state format merely because the classes move.

Installed definitions and consumers use the same boundaries between management, Environment connector, and Environment execution. A plugin API change is explicit through its owning manifest compatibility contract, never a silent reinterpretation of the old combined adapter. Other Provider domains do not acquire a new lifecycle framework as part of Environment extraction.

## Invariants

1. A plain Python consumer can manage and execute an environment without importing Harness or Service.
2. Hosts own lifecycle policy and durable authority; the library implements explicit single-target operations.
3. Environment connectors select fixed authorized targets, and opening, checking, or reattaching never performs target management.
4. Each Environment execution owns its acquired resources; partial opening, cancellation, and failed consumer binding have a cleanup owner.
5. State is portable target data, not live authority; an Environment execution does not publish management changes.
6. Unknown effects never authorize automatic replay, target recreation, or assumed completion.
7. Closing an Environment execution and retiring its target are independent operations.
