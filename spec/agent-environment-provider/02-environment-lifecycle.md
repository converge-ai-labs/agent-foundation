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

State excludes credentials, secret bootstrap material, bearer URLs, transport sessions, callbacks, raw PIDs, pipes, native handles, temporary runtime paths, daemon generations, Harness mount names/IDs, Run access ceilings, working directories and Host persistence/retention fields. Provider-owned stable target correlation is permitted; Host Run authority and database fencing are not.

Providers validate the exact state version, configuration compatibility and target ownership evidence. A stateless Provider may return `None`; this means its documented deterministic configuration selects the environment, not that an external target is absent. Current Host state always wins over portable Harness observations, including authoritative `None`.

## Environment Contract

The conceptual process-local API is:

```python
class Environment(ABC):
    @property
    def provider_key(self) -> str: ...

    async def prepare(self) -> None: ...

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

Concurrent first operations on one object share preparation. Repeated preparation of a valid ready object does not allocate another target. Hosted use of several objects for one target coordinates through the Host's Environment authority, rather than an implementation-global cache.

Actual file, shell, port or explicitly requested readiness operations can trigger lazy preparation. Scope entry, configured descriptor projection, synchronous state dump and local close cannot. A Run that never uses the environment need not start it. Hosts doing input or Skill materialization through file operations naturally trigger preparation.

### Stop, keepalive and destruction

`stop()` validates and stops the exact represented target while preserving the state needed to resume it. A Provider declares whether stop preserves process memory or only persistent filesystem state. Stop does not erase the state selector. It never deletes caller-owned directories, bind sources or unrelated volumes. Repeated stop reconciles actual state instead of creating or starting a target.

`keepalive()` extends a running target's supported lifetime and reports actual expiry evidence, or returns `None` for a declared backend without expiration. The Host supplies one stable operation identity per intended renewal and owns timing/retry policy. Renewal cannot start a stopped target. Provider limits, inability to satisfy the requested horizon and uncertain outcomes are explicit. A generic operation ID does not imply upstream idempotency or external fencing unless the implementation provides evidence.

`destroy()` removes the exact target after ownership validation. Confirmed absence is an idempotent result; unknown outcome retains state for reconciliation. Destroying an environment never deletes caller-owned storage. A later Host-authorized managed preparation can create a new target, but this is reconstruction from the recipe, not recovery of destroyed files.

Lifecycle operations run on a trusted object without entering a Harness Run, and stop/keepalive/destroy never call prepare first. Thus cleaning up a stopped target cannot accidentally wake it. Host scheduling decides when to stop or delete, including deletion long after a target has stopped.

### Close and state dump

`close()` fences new local operations and releases clients, sessions, subprocess resources owned by the operation scope and other local handles. It neither stops nor destroys a durable backing target. It remains safe for an unprepared object and does not allocate resources merely to clean up. Provider-specific ephemeral transport processes can be closed without destroying the underlying workspace.

`dump_state()` synchronously returns a detached copy of the last validated cached state or `None`; it performs no I/O and is available after construction, preparation failure, cancellation and close failure. Values are validated before caching. A failed refresh does not erase the last known state. Known stop preserves its reconnect state; confirmed destruction may clear it, with the Host separately retaining generation/history.

## Host State Authority and Concurrency

The Host owns durable selection, publication, lifecycle serialization and retention. Foundation's [Environment records](../foundation-service/29-environment-management.md) and Agent UI's [local state](../agent-ui/04-projects-threads-and-environments.md#host-authoritative-environment-state) are distinct Host policies over the same contract.

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
