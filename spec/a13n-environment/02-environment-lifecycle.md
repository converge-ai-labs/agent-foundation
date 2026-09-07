# Environment Preparation and Lifecycle

## Design Position

`Environment` is the process-local operation object for one provider-backed environment. A Host supplies current `EnvironmentState`, configuration and fresh runtime collaborators through an `EnvironmentProvider`. The Provider implementation owns target preparation and connections. Harness consumes the operation object and never selects a target lifecycle policy.

Construction is inert. Host-directed preparation can happen before a Run, or transparently on first actual operation. `enter()` binds a Run-local scope without creating, starting or connecting a target. `close()` releases local resources; `stop()` preserves recoverability, while `destroy()` removes the backing target. Retention timing and durable state authority belong to the Host.

## State Envelope

```python
class EnvironmentState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_key: str
    state_version: str
    state: JsonValue
```

The provider-owned payload is canonical JSON sufficient to validate and reconnect to one target, including recoverable stopped targets. It is not a credential, lease, live client, durable ownership grant or proof of existence. Target identity can be sensitive even when it is not a bearer credential.

State excludes credentials, secret bootstrap material, bearer URLs, transport sessions, callbacks, Host OS process handles, pipes, live SDK handles, temporary runtime paths, daemon generations, Harness mount names/IDs, Run access ceilings, working directories and Host persistence/retention fields. Provider-owned stable target correlation and backend-native process selectors needed by its recovery codec are permitted; Host Run authority and database fencing are not. A remote command PID is a backend selector, not a Host process handle.

Providers validate the exact state version, configuration compatibility and target ownership evidence. A stateless Provider may return `None`; this means its documented deterministic configuration selects the environment, not that an external target is absent. Current Host state always wins over portable Harness observations, including authoritative `None`.

## Environment Contract

The conceptual process-local API is:

```python
class Environment(ABC):
    @property
    def provider_key(self) -> str: ...

    async def prepare(self) -> None: ...
    async def check_ready(self, operations: frozenset[EnvironmentOperationFamily]) -> None: ...
    async def recover(self) -> None: ...

    async def enter(
        self,
        *,
        thread_id: str,
        run_id: str,
        agent_instance_id: str,
        mount_id: str,
        host_refs: Mapping[str, str] = {},
    ) -> None: ...

    def dump_state(self) -> EnvironmentState | None: ...
    async def close(self) -> None: ...
    async def stop(self) -> None: ...
    async def reconcile(self) -> Literal["running", "stopped", "absent"]: ...
    async def keepalive(self, *, deadline: datetime, operation_id: str) -> datetime | None: ...
    async def destroy(self) -> None: ...
```

The same object exposes provider-neutral file, shell, process, output, readiness and port operations. Lifecycle methods are trusted Host operations, not Agent tools. One Provider implementation supplies this whole contract; separate attachment or retention Provider registrations are unnecessary. Unsupported lifecycle operations raise typed errors and are advertised through the Provider's small capability declaration, so Hosts validate requested policy before effects.

One fresh object serves one independent Run or one bounded Host lifecycle operation. It is not shared across independent Runs, retained after close, or stored in a database. Several fresh objects can refer to the same backing environment when the Provider supports shared use.

### Construction and scope entry

`create_environment(configuration, state, runtime)` validates configuration/state codecs and constructs the object without filesystem, subprocess or network I/O. Runtime collaborators include current credentials, transport factories and any Host preparation coordination. These values stay process-local.

`enter()` validates and binds ephemeral Run/Thread/mount correlation and local operation scope. It does not connect a second time when the object is already prepared, and it does not force an unprepared lazy object to prepare. Before preparation, the object exposes a validated configured descriptor and bounded unprepared availability; a live descriptor can only narrow the advertised capabilities. Unsupported or incompatible live identity fails before dispatch.

### Preparation

`prepare()` ensures a usable target and the provider-owned operation connection. It may be invoked by the Host before Harness entry or by the operation object on first actual use. A lazy object consults supplied Host coordination before external preparation or use acquisition; it cannot bypass Host authorization, retention fencing or state publication.

Preparation validates current evidence and:

1. creates a target from desired configuration when no target has been allocated and creation is supported;
2. reuses a matching running target;
3. resumes a matching stopped target;
4. rebuilds a managed target only after authoritative absence and Host authorization;
5. establishes and validates operation clients, identity, descriptor and required readiness;
6. caches changed target state immediately and publishes through the Host's coordination boundary before admitting operations that rely on that state.

An inaccessible, incompatible, unknown or temporarily unreachable target is not absent. Creation uses stable provider idempotency or recoverable ownership correlation supplied by the Host. The shared API does not claim exactly-once external effects. A known create result remains available even if subsequent readiness fails; a Host can recover it after cancellation or failure.

Concurrent first operations on one object share preparation. Initial preparation, pre-dispatch recovery and close use one process-local lifecycle control entry; Host wrappers do not mutate another layer’s preparation state. Readiness may await outside this control, but recovery rechecks closure and whether the connection was already replaced after acquiring it. A closed scope never publishes a replacement connection. Passive observers receive actual preparation started/ready/failed and local-close events without owning separate lifecycle state. Close is serialized with preparation so a cancelled or closing scope cannot publish a newly prepared adapter after cleanup. Repeated preparation of a valid ready object does not allocate another target. Hosted use of several objects for one target coordinates through the Host's Environment authority, rather than an implementation-global cache.

Actual file, shell, port or explicitly requested readiness operations can trigger lazy preparation. Scope entry, configured descriptor projection, synchronous state dump and local close cannot. A Run that never uses the environment need not start it. Hosts doing input or Skill materialization through file operations naturally trigger preparation.

### Recovery and reconciliation

Readiness checks use the existing operation connection. Healthy operations do not repeat target discovery, image validation or bootstrap. A pre-dispatch unavailable connection enters the same preparation path as first use; concurrent recovery shares that path. Unknown outcomes after dispatch are never replayed by recovery.

`check_ready()` checks an already prepared, entered adapter without recovering it. A Host wrapper uses this check so an inner Provider cannot rebuild behind the Host's fence. The wrapper reacquires current authority and state, then calls `recover()` on the existing entered adapter only when state and credential generation still match. Recovery shares the adapter's preparation/close lock and preserves native observations when the target is unchanged. Otherwise the Host closes the old adapter and constructs a fresh one. A changed target is published before operations are exposed; the outer scope returns `environment_rebuilt` or `environment_connection_refreshed` before the caller retries an undispatched operation.

`reconcile()` observes an abandoned preparation without creating, starting, replacing or deleting a target. It recovers target state from stable ownership correlation even when an interrupted create returned no target ID. It reports `running`, `stopped` or authoritative `absent`; ambiguous observations retain the pending operation. Hosts can reconcile after the originating Run ends without manufacturing Run execution authority.

`target_identity(configuration, state)` returns only the canonical native target selector. It excludes bootstrap, credential and connection metadata. The Host namespaces it by Provider type and `backend_identity()` from validated immutable backend configuration. A state checksum is not a target identity. An externally registered adapter can have a logical Environment ID distinct from its validated native daemon identity; wire receipts validate the native identity while Harness artifacts retain the logical identity.

### Stop, keepalive and destruction

`stop()` validates and stops the exact represented target while preserving the state needed to resume it. A Provider declares whether stop preserves process memory or only persistent filesystem state. Stop does not erase the state selector. It never deletes caller-owned directories, bind sources or unrelated volumes. Repeated stop reconciles actual state instead of creating or starting a target.

`keepalive_horizon` supplies the desired renewal interval, defaulting to 300 seconds; a Provider narrows it to fit the target configuration (E2B uses at most `timeout_seconds`). It is an interval request, not observed expiry evidence.

`keepalive()` extends a running target's supported lifetime and reports actual expiry evidence, or returns `None` for a declared backend without expiration. The Host supplies one stable operation identity per intended renewal and owns timing/retry policy. Renewal cannot start a stopped target. Provider limits, inability to satisfy the requested horizon and uncertain outcomes are explicit. A generic operation ID does not imply upstream idempotency or external fencing unless the implementation provides evidence.

`destroy()` removes the exact target after ownership validation. Confirmed absence is an idempotent result; unknown outcome retains state for reconciliation. Destroying an environment never deletes caller-owned storage. A later Host-authorized managed preparation can create a new target, but this is reconstruction from the recipe, not recovery of destroyed files.

Lifecycle operations run on a trusted object without entering a Harness Run, and stop/keepalive/destroy never call prepare first. Thus cleaning up a stopped target cannot accidentally wake it. Host scheduling decides when to stop or delete, including deletion long after a target has stopped.

### Close and state dump

`close()` fences new local operations and releases clients, sessions, subprocess resources owned by the operation scope and other local handles. It neither stops nor destroys a durable backing target. It remains safe for an unprepared object and does not allocate resources merely to clean up. Provider-specific ephemeral transport processes can be closed without destroying the underlying workspace.

`dump_state()` synchronously returns a detached copy of the last validated cached state or `None`; it performs no I/O and is available after construction, preparation failure, cancellation and close failure. Values are validated before caching. A failed refresh does not erase the last known state. Known stop preserves its reconnect state; confirmed destruction may clear it, with the Host separately retaining generation/history.

## Process and Output Observations

The Provider owns native execution truth and the recovery supported by its `EnvironmentState` codec. State can select a target from which commands are discovered without enumerating every command. Reconnection to the same target permits attempting native lookup; it does not prove that a process survived. A missing command is never automatically restarted. Backend recovery guarantees and local-close behavior are defined by the [built-in Provider contracts](03-built-in-providers.md).

The shared process facet uses `ProcessIdentity` to bind a native selector to one Provider, logical Environment and target generation. `ProcessInfo` carries a bound handle, native status, optional stdin-state evidence and an optional richer output snapshot. Status may be `unknown` or `missing`; neither invents an exit code or a successful terminal outcome. Tree-cleanup evidence is optional. Closing or capping output observation does not satisfy a process wait. An `initial_terminal` wait observes native completion independently from output completeness and process-tree cleanup; stronger backend wait conditions remain available only where supported.

`process.list` is optional. It returns a bounded `ProcessDiscovery(processes, has_more)` without attaching output streams. `rebind` selects an explicit native identity without creating a command. Start, inspect, wait, output reads, discovery, stdin, signals, kill and release have independent declared actions. Lack of discovery, stdin or arbitrary signals does not remove otherwise supported background observation. Unsupported guarantees are rejected before dispatch.

`release` relinquishes the local observation and its associated output readers. It does not imply command termination. A Provider may retain private native-scope bookkeeping until its documented close boundary. Process survival after local release or close depends on the backend, not on a Harness cleanup policy. Direct Local and EIP-backed Providers retain their native scope cleanup semantics; no universal cross-Run persistence is implied.

Common output observations carry returned segments and available offsets together with:

- `origin`: `native_bytes` or `sdk_text`;
- `coverage`: `complete`, `partial` or `unknown`;
- `observation_closed`: whether local collection is stopped;
- a bounded optional reason such as `reattached`, `connection_lost`, `observation_limit` or `observation_evicted`;
- optional `produced_bytes`, `dropped_bytes` and `producer_complete` evidence, never fabricated when unavailable;
- captured length and content completeness independent of process completion.

Native-byte offsets identify the backend's retained range. SDK-text offsets identify UTF-8 encoding of SDK-delivered text, not the original stdout bytes. A transient reattachment retains the accumulated current-scope log and appends later text while marking the gap; it neither resets offsets nor promises replay deduplication. Target replacement invalidates the identity. A fresh scope starts a new observation. Neither state nor discovery implies historical output recovery.

Ordinary `shell.exec` has a bounded inline/observed result contract. A Provider that internally uses native retention materializes that operation's bounded output and releases its own resources before returning; no retained reference escapes the ordinary result. A post-execution read failure preserves known command outcome with incomplete output rather than inviting command replay. The standalone retained-output facet retains its independent read/release authorization and stronger native byte semantics. It is not a prerequisite for Shell execution or process observation.

## Host State Authority and Concurrency

The Host owns durable selection, publication, lifecycle serialization and retention. a13n Service's [Environment records](../a13n-service/29-environment-management.md) and Harness UI's [local state](../a13n-harness-ui/04-projects-threads-and-environments.md#host-authoritative-environment-state) are distinct Host policies over the same contract.

A Host publishes changed state when known and attempts publication from unconditional finalization even after execution, checkpoint or local-close failure. Equal state needs no write. Publication follows the owning Host's consistency contract; a stale adapter cannot overwrite newer state solely because it finishes later. Distributed Hosts use their own conditional publication and operation reconciliation. The shared package prescribes no global table, last-write-wins policy or exactly-once guarantee.

Provider-local synchronization protects concurrent preparation and native handles. Hosts serialize conflicting target lifecycle operations across objects. Database leases alone cannot cancel a dispatched provider mutation: a new preparation must reconcile an outstanding stop/delete before serving operations. Inline children borrow the parent's scope; async children receive fresh Host-selected objects.

## Replacement and Operation Outcomes

A target generation change invalidates native handles, readiness evidence and operation references tied to the displaced target. The Provider exposes the change through its operation object; the Host updates durable generation evidence and safe context, and Harness invalidates affected routing/operation observations before later use. Harness does not construct a new network client itself.

Automatic preparation/rebuild is permitted before dispatch. A command or mutation already dispatched with an unknown outcome is not silently repeated after connection loss or rebuild. Return an explicit bounded error and preserve evidence. The Agent or operation's explicit idempotency contract decides subsequent work. Successful reconnection, resume or rebuild is not evidence that a prior effect did not occur.

## Failure and Security Semantics

| Condition                                               | Behavior                                                |
| ------------------------------------------------------- | ------------------------------------------------------- |
| Invalid configuration, state codec or Provider identity | Fail before external effects                            |
| Matching running/stopped target                         | Reuse/resume under Host authority                       |
| Confirmed missing managed target                        | Rebuild only through the Host's authorized preparation  |
| Unknown existence or ownership mismatch                 | Fail/reconcile without speculative creation             |
| Preparation fails after target creation                 | Preserve known state and recoverable operation identity |
| Stop/delete races new use                               | Reconcile the in-flight lifecycle action before use     |
| Renewal cannot satisfy requested lifetime               | Report bounded actual evidence/failure                  |
| Dispatched operation has unknown outcome                | Do not implicitly replay                                |
| Local cleanup fails                                     | Report separately; never escalate to target destruction |

Credentials remain process-local and Provider state cannot grant authority. EIP implementations authenticate fresh sessions and validate identity/method compatibility before operations. Direct Local follows embedding OS authority and does not claim sandbox isolation. Blocking native/SDK operations stay off the event loop and use bounded timeouts. Public errors omit credentials, private target details and raw provider exceptions.

## Compatibility and Invariants

Configuration and `EnvironmentState.state_version` evolve independently. Unsupported versions fail explicitly. Providers never reinterpret incompatible state as permission to create a different target.

1. Construction and scope entry do not allocate or connect backing targets.
2. Provider implementations perform all preparation and connection work.
3. Hosts select eager or lazy preparation and own lifecycle policy.
4. Stop preserves recoverability; destroy removes the target; close only ends the local scope.
5. Current Host state is authoritative over portable continuation.
6. Known target changes survive execution and cleanup failure.
7. Unknown effects never authorize silent replay.
8. Harness receives operation objects and owns no target lifecycle policy.
