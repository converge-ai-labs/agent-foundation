# Environment Integration

## Design Position

Environment is the Harness-owned, run-scoped boundary for files, foreground commands, provider process ports, retained output, ports, readiness, and portable provider state. It is not a Pydantic Capability.

An embedded caller supplies no Environment, one `EnvironmentProvider` or entered `EnvironmentResource` through `environment=`, or a named mapping through `environments=`. The Harness normalizes every form into one single-use `EnvironmentRuntime`, enters it before input production, publishes one stable `Environment` facade through `AgentContext.environment`, and closes it only after the logical terminal fence and run-local cleanup complete.

A Host that needs explicit mount mutation or runtime-wide run extensions constructs an `EnvironmentRuntime` directly and supplies it through `RunBindings.environment`. The runtime itself is the Host mutation authority and the complete process-local owner of the current mount set.

The current mount set is run-local. Durable Environment definitions, resource identity, desired mounts, revision selection, provisioning, retry, idempotency, reconciliation, and provider resource lifecycle remain Host-owned. Harness state never recreates those authorities.

`DynamicEnvironmentCapability` is the optional model adapter. It derives the model-visible Toolset from the union of effective actions across the current mounts, composes the provider-neutral `FileToolset` and `ShellToolset`, observes run-local mount changes, and enqueues bounded refresh notices. Read-only file actions expose only `view`, `ls`, `glob`, and `grep`; file-mutation actions add the mutation tools; and effective shell execution adds `shell_exec`. An empty Environment exposes no Environment tools. Its constructor receives a `ShellOperator`. The operator's immutable `supports_background` declaration fixes the shell Toolset when shell execution is effective: a foreground-only operator exposes only `shell_exec` without a `background` argument, while a background-capable operator exposes foreground/background `shell_exec` plus the standard wait, status, input, signal, and kill tools. The Host configures or subclasses the operator and never supplies or replaces the Toolset.

The entered Environment owns mount routing, readiness, mutation, leases, stale-incarnation fencing, current mount projection, portable state aggregation, and cleanup. Foreground commands remain bound to that Environment. Canonical detached background-process execution belongs to the configured background-capable operator and can outlive one Harness Run; shared admission, observation, cleanup, loss, and Host-takeover semantics are owned by [Async Components and Lifecycle](20-async-components-and-lifecycle.md).

## Developer-facing Inputs

The public high-level values are:

```python
type EnvironmentSource = EnvironmentProvider | EnvironmentResource
type EnvironmentEntry = EnvironmentSource | EnvironmentMount


class EnvironmentAccess(StrEnum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class EnvironmentMount:
    source: EnvironmentSource
    access: EnvironmentAccess = EnvironmentAccess.FULL
    working_directory: str | None = "/"
```

`EnvironmentAccess` is the complete user-facing access model and defaults to `FULL`. `READ_ONLY` selects file observations and file-copy source access. `READ_WRITE` selects every file action. `FULL` selects the complete current Agent-facing `EnvironmentAction` catalog, including command and process operations when the Provider offers them. All three levels include state export and restore because those are Host lifecycle operations rather than model-authored file mutations. Provider descriptors always retain the final narrowing authority, so `FULL` never creates a capability the entered Provider does not advertise.

Ordinary callers cannot configure an arbitrary action set. Exact `EnvironmentPermissionSet` values remain an advanced Harness/Provider seam for runtime construction, provider descriptors, and operation-level enforcement.

`working_directory` is `None` or a canonical absolute provider path. It contains no NUL, repeated separator, trailing separator other than `/`, or `.` or `..` segment.

`ExecutableAgent.run()` and `stream()` expose the same Environment arguments:

```python
run(
    input=None,
    *,
    environment: EnvironmentEntry | None = None,
    environments: Mapping[str, EnvironmentEntry] | None = None,
    default_environment: str | None = None,
    bindings: RunBindings | None = None,
    ...,
)
```

The rules are:

1. `environment` and `environments` are mutually exclusive.
2. `default_environment` is valid only with `environments` and names one supplied mount.
3. Singular input uses mount name `workspace` and is the default.
4. A one-entry mapping selects its only mount as the default.
5. A mapping with several entries has no default unless `default_environment` is explicit. Mapping order never chooses authority.
6. An empty mapping, invalid name, invalid mount policy, or high-level/advanced input conflict fails before provider effects.
7. Omitting every Environment input creates a fresh empty runtime.
8. Provider input explicitly selects one ephemeral Resource lifetime. Entered Resource input selects one fresh attachment for the run and no pause or destroy action.

The default mount serves `/workspace`. Every mount is also addressable at `/environment/{name}`. Without a default mount, `/workspace` is unavailable.

## Advanced Runtime API

The advanced construction values are:

```python
@dataclass(frozen=True, slots=True)
class EnvironmentRuntimeMount:
    binding: EnvironmentProviderBinding
    permission_ceiling: EnvironmentPermissionSet
    working_directory: str | None = "/"


def create_environment_runtime(
    *,
    mounts: Mapping[str, EnvironmentRuntimeMount],
    default_mount: str | None = None,
    extensions: Sequence[EnvironmentRunExtension] = (),
) -> EnvironmentRuntime: ...
```

`create_empty_environment_runtime()` creates the same single-use runtime with no initial mounts.

`EnvironmentRuntime` exposes Host mutation operations:

```python
class EnvironmentRuntime(ABC):
    def bind(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
    ) -> AbstractAsyncContextManager[BoundEnvironment]: ...

    async def wait_until_active(self) -> None: ...

    async def mount(
        self,
        name: str,
        mount: EnvironmentRuntimeMount,
        *,
        make_default: bool = False,
    ) -> EnvironmentChange: ...

    async def replace(
        self,
        name: str,
        mount: EnvironmentRuntimeMount,
    ) -> EnvironmentChange: ...

    async def unmount(self, name: str) -> EnvironmentChange: ...

    async def set_default(self, name: str | None) -> EnvironmentChange: ...
```

The Harness alone invokes `_activate()` and `_begin_close()` as lifecycle operations. They are not Host orchestration commands.

A runtime is bound exactly once. Initial mount capture is atomic. A caller that cannot construct the complete initial mapping supplies an empty runtime, starts its reconciliation task, enters the Harness stream, awaits `wait_until_active()`, and then performs explicit mount operations.

## Ownership Boundary

| Concern                                                                                                     | Owner                                                                  |
| ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Durable Environment definitions, revisions, desired mounts, idempotency, retry, fencing, and reconciliation | Host                                                                   |
| Provider specifications, catalogs, Resources, resource state, and attachments                               | `a13n-environment-provider` and Host                                   |
| One run's current mount set, opaque mount incarnations, routing, leases, and terminal mutation fence        | Harness Environment core                                               |
| Provider generation, operation execution, and provider-local cleanup                                        | Entered provider binding                                               |
| Aggregate run-extension selection and extension-owned resources                                             | Host and selected extension                                            |
| Provider-neutral current mount Model Context Projection                                                     | Entered `BoundEnvironment`                                             |
| Model-facing file and command/process tools                                                                 | `FileToolset` and `ShellToolset`                                       |
| Effective-action tool-surface selection, mount-change notices, and process projection                       | `DynamicEnvironmentCapability`                                         |
| Canonical detached process and retained output                                                              | `ProcessManager` or custom `ShellOperator`                             |
| Shared async admission, observation, cleanup, loss, and Host wake boundary                                  | [Async Components and Lifecycle](20-async-components-and-lifecycle.md) |
| Direct Local path and provider-process enforcement                                                          | Direct Local provider and embedding OS                                 |
| EIP resources, handles, offsets, and side-effect evidence                                                   | `agent-envd` and generated client                                      |
| Durable checkpoint selection and recovery                                                                   | Host                                                                   |

Provider denial always narrows a Harness allow decision. Mount IDs, paths, handles, cursors, change sequences, and saved state are selectors or observations, never bearer credentials.

## Identity and Core Values

The identities are distinct:

| Value                   | Meaning                                                     | Visibility                                       |
| ----------------------- | ----------------------------------------------------------- | ------------------------------------------------ |
| Mount name              | Stable run-local routing name such as `workspace` or `data` | Host, routing, model projection                  |
| Mount ID                | Opaque Harness-generated incarnation identity               | Harness internals and provider-neutral artifacts |
| Provider environment ID | Resource identity reported by the provider                  | Trusted Host and provider integration            |
| Provider generation     | Incarnation fence reported by the provider                  | Trusted operation and stale-handle checks        |
| Run ID                  | One logical Harness run                                     | Host and provider operation context              |

A replacement keeps the mount name and creates a fresh mount ID. An old path, handle, cursor, operation receipt, or selected file scope never retargets to the replacement.

Core immutable values include:

- `EnvironmentPath(mount_id, path)`;
- `EnvironmentOperationReceipt(mount_id, observed_generation, operation_id, stage, outcome)`;
- `BoundProcessHandle(mount_id, observed_generation, process_id, handle)`;
- retained-output references carrying the same mount and generation fence;
- `EnvironmentSnapshot(mounts, default_mount)`;
- `EnvironmentChange(sequence, kind, name, previous_default, current_default)`.

`EnvironmentMountInfo` contains the mount name, provider type, provider descriptor, permission ceiling, and default working directory. It does not expose the opaque mount ID to model context.

## Provider Binding Contract

`EnvironmentProviderBinding` is a trusted single-use candidate:

```python
class EnvironmentProviderBinding(ABC):
    @property
    def provider_type(self) -> str: ...

    @property
    def environment_id(self) -> str: ...

    def bind(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
        mount_id: str,
    ) -> AbstractAsyncContextManager[BoundEnvironmentProvider]: ...

    async def discard(self) -> None: ...
```

Binding entry returns a `BoundEnvironmentProvider` with one immutable descriptor, readiness operations, optional file/shell/process/output/port facets, and portable state operations. The descriptor's generation and advertised operation families are authoritative for that entered scope.

A candidate enters at most once. `discard()` is idempotent and disposes a candidate that cannot be entered or committed. Provider bindings do not publish themselves into the aggregate.

The attachment adapter is the only public bridge from an `EnvironmentAttachment` to an `EnvironmentProviderBinding`. Direct Local and EIP concrete binding classes remain implementation details.

## Aggregate Lifecycle

Run entry proceeds in this order:

1. validate the complete initial mount mapping and default name;
2. allocate a fresh opaque mount ID for each initial mount;
3. enter every candidate and validate its descriptor;
4. publish one complete `EnvironmentSnapshot`;
5. restore optional portable `EnvironmentState` into matching selected mounts;
6. enter ordered `EnvironmentRunExtension` scopes;
7. activate the runtime;
8. produce input and begin Agent execution.

A failure before publication unwinds entered provider scopes in reverse order and discards remaining candidates. No partial initial mount set becomes observable.

Run closure installs the terminal mutation fence before terminal result delivery. It then stops readiness and observation work, closes extension scopes in reverse order, waits for operation leases, releases provider scopes, destroys only Provider-input ephemeral Resources, and reports cleanup failure through the normal run cleanup boundary. Closing an entered Resource input or advanced Host runtime never selects provider pause or destroy.

## Mount Mutation

Mutation operations are linearizable:

- `mount(name, candidate)` requires an absent name;
- `mount(..., make_default=True)` publishes the new mount and default choice atomically;
- `replace(name, candidate)` requires an existing name and preserves whether it was default;
- `unmount(name)` removes the selected incarnation and clears the default when that name was default;
- `set_default(name)` requires an existing name;
- `set_default(None)` clears the default without changing mounts.

`mount()` and `replace()` prepare the new candidate before commit. A preparation failure leaves the published mount set unchanged and disposes the candidate. Commit changes the snapshot, publishes exactly one `EnvironmentChange`, and then retires the removed provider scope after its leases drain.

The runtime rejects mutations before binding, before activation completes, after activation failure, and after the terminal fence. Queued mutations re-check the fence before commit. Cancellation cannot publish a candidate whose caller did not receive a committed result.

The run-local change journal begins at sequence zero. Every committed mutation increments the sequence by one. It retains all changes for the run and supports internal reads after a sequence, with optional waiting. It is not durable replay state.

Harness adapts each change to one context event:

```json
{
  "type": "environment_changed",
  "sequence": 1,
  "kind": "mounted",
  "name": "data",
  "previous_default": null,
  "current_default": "data"
}
```

`kind` is `mounted`, `replaced`, `unmounted`, or `default_changed`. The event adapter drains through the terminal sequence before the terminal result is emitted.

## Stable Bound Environment

`BoundEnvironment` is one stable facade whose current snapshot changes in place:

```python
class BoundEnvironment(ABC):
    @property
    def snapshot(self) -> EnvironmentSnapshot: ...

    @property
    def files(self) -> FileOperator: ...

    def select_files(self, path: str) -> FileScopeSelection: ...
    def open_files(self, selection: FileScopeSelection) -> AbstractAsyncContextManager[FileOperator]: ...

    @property
    def shell(self) -> BoundShellOperations: ...

    @property
    def processes(self) -> BoundProcessOperations: ...

    @property
    def outputs(self) -> BoundOutputOperations: ...

    @property
    def ports(self) -> BoundPortOperations: ...

    def resolve_path(self, path: str, *, alias: str | None = None) -> EnvironmentPath: ...
    async def describe(self, name: str) -> EnvironmentMountObservation: ...
    async def ensure_ready(self, requirement: EnvironmentReadinessRequirement) -> None: ...
    async def export_state(self) -> EnvironmentState: ...
    async def restore_state(self, state: EnvironmentState) -> None: ...
```

Capabilities and tools receive this facade, not the Host runtime. Host mutation authority is never placed in model context, metadata, a Capability namespace, or durable state.

## Routing and Operation Fencing

Logical path routing is:

- `/workspace/...` selects the current default mount;
- `/environment/{name}/...` selects the named mount;
- an explicit operation alias selects the named mount for command and port operations;
- a relative command working directory resolves beneath the selected mount's configured provider working directory.

A routed operation captures the current mount ID and provider generation before authorization. Managed-policy resources carry the same identity. Immediately before provider dispatch, the Harness verifies that the mount incarnation is still current. If it is not, the operation fails with `environment_stale_mount` and never dispatches to the replacement.

Each provider call holds an operation lease for the selected incarnation. Replacement and unmount stop new routing to the old incarnation immediately but retain its provider scope until all leases and tracked process handles release. Process and retained-output operations route by their captured identity rather than by mount name.

File compound operations use `select_files()` and `open_files()` to pin an exact incarnation across all steps. A cross-mount copy captures both endpoints before opening either scope and never re-resolves one endpoint after the other begins.

## Readiness

Readiness is operation-family scoped. Provider entry establishes identity and descriptor; concrete resources may continue preparing inside the provider scope.

`ensure_ready()` groups requirements by current mount incarnation, intersects the requested operations with the permission ceiling and provider descriptor, and invokes provider readiness only for required families. Concurrent equivalent waits share provider work. A replacement cannot satisfy a wait captured for an old incarnation.

Readiness errors are typed and bounded. A timeout, provider failure, unavailable family, replacement, or closure never silently widens the request or retries a side effect.

## Model Context Projection

For each input request the entered Environment projects one bounded trusted block:

```text
Current Environment mounts (trusted dynamic context):
{"default_mount":"workspace","mounts":[...],"truncated":false}
```

Each projected mount contains only its name, logical root, operation families, readiness summary, availability, and read-only observation. It excludes mount IDs, provider credentials, provider-native handles, state, limits that are not useful to the model, and Host mutation authority.

The projection is fresh for each input boundary. `DynamicEnvironmentCapability` observes the change journal and enqueues at most one bounded refresh notice for a pending change set. It does not duplicate the full mount projection in messages.

## Portable Environment State

`EnvironmentState` is a mapping from current mount name to `EnvironmentMountState`:

```python
class EnvironmentState(BaseModel):
    mounts: dict[str, EnvironmentMountState] = {}


class EnvironmentMountState(BaseModel):
    provider_type: str
    state_version: str
    state: JsonValue
```

The provider-defined `state_version` describes only the provider's portable state codec. It is not mount identity and grants no authority.

Export captures the current mount set under the aggregate operation fence, exports only mounts whose permission ceiling permits state export, validates canonical JSON, and returns one complete value. Failure aborts the complete export. The Host owns admission and storage limits for the resulting state.

Restore occurs after fresh provider entry and before runtime activation. It matches saved entries by mount name and provider type. Unknown saved names are ignored. A provider-type mismatch for a current mount fails the restore. Permission checks apply before provider state is accepted; the Host owns state-size admission before starting the run.

State does not contain the default mount, opaque mount IDs, provider generations, attachments, handles, operation leases, pending mutations, change sequence, credentials, policy, or desired mounts.

## Environment Run Extensions

A trusted Host can register ordered `EnvironmentRunExtension` objects on an advanced runtime. Each extension receives `EnvironmentRunExtensionContext(run_id, instance, environment)`, enters after portable state restoration, and exits in reverse order while the Environment remains open.

Extensions can own aggregate-wide process-local resources and can observe or use the stable bound Environment. They do not create mounts, gain Host mutation authority, edit Harness state directly, or bypass provider permissions. Factory discovery is explicit and allowlisted as specified in [Plugin System](05-plugin-system.md).

## File Surface

`FileOperator` is provider-neutral and async. It provides bounded stat, listing, byte and text reads, streaming writes, text patching, directory creation, move, copy, removal, glob/query, and text search operations.

Every mutation returns a typed result with an `EnvironmentOperationReceipt`. Reads and listings carry explicit offsets or continuation fields. Text reads report truncated lines rather than silently splitting a line. Search and glob expose bounded pages and deterministic ordering.

Virtual routing enforces mount selection before provider calls. Direct Local confines native paths beneath its configured root, keeps blocking filesystem work off the event loop, stages replacement writes, and applies configured executable and process policies. It makes no sandbox claim.

The model-facing `FileToolset` owns stable tools including `view`, `write`, `edit`, `multi_edit`, `mkdir`, `move`, `copy`, `delete`, `ls`, `glob`, and `grep`. `DynamicEnvironmentCapability` includes the read tools when any current mount has effective file-read actions and includes the mutation tools when any current mount has effective file-mutation actions. When effective shell execution is present, prepared shell execution supersedes exactly `move`, `copy`, and `delete`; `mkdir` remains visible. Tool results use bounded disclosures and typed model-safe errors. Internal mount identity, generation, and receipts are not model-editable arguments.

## Command and Background Process Operators

`ShellOperator` is the public shell execution boundary. The default implementation is foreground-only and delegates captured execution to the current `AgentContext.environment.shell`. It cannot outlive Environment closure.

```python
class ShellOperator:
    supports_background: ClassVar[bool] = False

    async def execute(
        self,
        context: AgentContext,
        request: CommandRequest,
        alias: str | None,
    ) -> ShellExecResult: ...

    async def force_close(self) -> None: ...
```

`supports_background` is an immutable type/instance declaration validated when `DynamicEnvironmentCapability` is constructed. It is not inferred from a `RunBindings` field, current attachment, callback, or successful backend probe. A foreground-only operator produces `shell_exec` without a `background` parameter. A background-capable operator produces `shell_exec(background=...)` and the standard `shell_wait`, `shell_status`, `shell_input`, `shell_signal`, and `shell_kill` tools.

The Harness provides `ProcessManager` as the concrete default background-capable `ShellOperator`. Its constructor accepts the concrete launcher needed to create a detached managed process plus optional stable Host hooks. The launcher must return an object whose process, output, and control lifetime is owned independently of the parent Run's `BoundEnvironment`, provider attachment, and operation lease. Returning a handle that borrows those run-scoped resources is invalid because Run cleanup could otherwise block on the process or leave a live compact reference backed by a closed resource.

`ProcessManager` owns canonical process objects, completion watchers, retained output access, and control state in memory. Its lifetime, admission boundary, monotonic projection rules, cancellation-safe cleanup, restart loss, and replacement by an independently managed Host process operator follow [Async Components and Lifecycle](20-async-components-and-lifecycle.md). A custom `ShellOperator` never supplies or overrides the standard shell tools.

The conceptual background operations are:

```python
class ProcessExecutionSnapshot(BaseModel):
    backend_id: str
    status: ProcessStatus
    stdin_open: bool
    stdout_produced_bytes: int = 0
    stderr_produced_bytes: int = 0


class ProcessOutputPage(BaseModel):
    snapshot: ProcessExecutionSnapshot
    stdout: ProcessOutputChunk
    stderr: ProcessOutputChunk


class ProcessManager(ShellOperator):
    supports_background: ClassVar[bool] = True

    async def start(
        self,
        context: AgentContext,
        request: CommandRequest,
        alias: str | None,
        process_id: str,
        observer: ProcessBackendEventHook,
    ) -> ProcessExecutionSnapshot: ...

    async def rebind(
        self,
        context: AgentContext,
        backend_id: str,
        observer: ProcessBackendEventHook,
    ) -> ProcessExecutionSnapshot | None: ...

    async def inspect(
        self,
        context: AgentContext,
        backend_id: str,
    ) -> ProcessExecutionSnapshot | None: ...

    async def read_output(
        self,
        context: AgentContext,
        backend_id: str,
        stdout_offset: int,
        stderr_offset: int,
        wait_seconds: float,
        max_bytes: int,
    ) -> ProcessOutputPage | None: ...

    async def write_stdin(...): ...
    async def signal(...): ...
    async def kill(...): ...
    async def force_close(self) -> None: ...
```

This is a normal process-local class boundary, not a wire protocol, storage framework, provider framework, or generic job API. Every operation receives the current borrowed `AgentContext` for correlation and authorization, but the operator must not retain it. Operator implementation and dependencies determine storage; no per-process storage metadata, namespace field, provider field, or permission configuration enters Harness state.

`backend_id` is the only opaque canonical locator retained by the parent projection. Later snapshots preserve that ID. `None` means the Manager or custom operator no longer owns the process, and the projection becomes lost without substituting another backend.

Process observation uses the independent active-observer and stable-Host-hook paths defined by [Async Components and Lifecycle](20-async-components-and-lifecycle.md#active-observers-and-stable-host-hooks). While the exact parent Harness Run remains active, its projection updates current state and enqueues a concise instruction to call `shell_wait` or `shell_status`. The Manager always dispatches its stable hooks regardless of parent activity.

The parent-private projection is:

```python
class ManagedProcessState(BaseModel):
    backend_id: str
    stdout_offset: int
    stderr_offset: int
    status: ProcessStatus
    stdin_open: bool
    stdout_produced_bytes: int
    stderr_produced_bytes: int
    backend_lost: bool = False


class ProcessManagerState(BaseModel):
    owner_thread_id: str
    next_sequence: int
    processes: dict[str, ManagedProcessState]
```

`process-N` is unique only inside one parent Thread's `AgentContextState`. Status and produced-byte counts are bounded non-authoritative observations. State contains no managed process object, output buffer, mount ID, provider generation, attachment, task, callback, Session ID, storage metadata, or wake record. Independent stdout and stderr offsets advance only after bounded output is projected to the model. Drains for one compact reference are serialized. Before either cursor advances, each page must preserve the requested stream offsets, remain within the aggregate byte budget, expose retained ranges consistent with the snapshot's produced-byte counts, and continue from the requested offset or the exact retained-range start after dropped output. An invalid page fails without consuming output.

At Run entry, a mismatched `owner_thread_id`, including one produced by `HarnessState.fork()`, invalidates copied compact references. Otherwise the projection rebinds each nonterminal backend ID. Missing default-Manager records after restart become lost. A run observer is weak or replaceable and cannot mutate an already exported continuation after Run closure; the next Run reconciles from canonical operator state.

The Host never supplies shell tools or schemas. Process tools accept only compact `process-N` references and never provider-native handles. Operator unavailability produces a bounded tool failure without changing the fixed Toolset.

## Ports

Port operations inspect or wait for one provider-neutral `PortTarget`. The target resolves through a mount name or captured process identity and returns bounded observations. Provider descriptors and permission ceilings must advertise the required port family. A replacement or stale process identity fails closed.

Environment ports are observations only. Exposure, public URL allocation, proxy lifecycle, authentication, and durable route ownership remain Host or provider responsibilities.

## Multimedia Understanding

File media understanding is an optional collaborator of `FileToolset`. It pins and reads one exact file scope, releases that scope before invoking the understanding model or service, passes `EnvironmentPath` only as source provenance, and records nested provider usage. The model call cannot retain an operation lease across external inference.

## Failure Surface

`EnvironmentError` carries a stable code, bounded model-safe details, and optional retry hint. Core codes include invalid selection, denial, unsupported operation, unavailable dependency, not found, conflict, stale mount, invalid state, provider failure, activation failure, and closed runtime.

Provider-native messages, credentials, mount IDs, generations, filesystem paths outside the logical root, and backend handles are removed from model-facing error projection unless a specific safe field is part of the public tool contract.

Cancellation and timeout do not imply that an external mutation failed. Side-effect evidence and operation receipts remain authoritative where available. The Harness never automatically replays an uncertain mutation.

## Invariants

01. One logical Harness run has one stable `BoundEnvironment` facade and one single-use `EnvironmentRuntime` Host authority.
02. Initial mount entry is atomic; no partial initial mount set is published.
03. Mount names are routing names. Opaque mount IDs identify exact run-local incarnations.
04. Replacement never retargets a path, process, output reference, cursor, receipt, selected file scope, or authorization fence.
05. Every committed mutation is linearizable and publishes exactly one run-local change sequence entry.
06. The terminal fence rejects every later mutation and bounds event draining before terminal result delivery.
07. Provider entry and operation readiness are distinct; readiness is scoped to required operation families.
08. Provider permissions and availability can narrow but never widen Harness authority.
09. Portable Environment state restores data into already selected fresh mounts and never restores authority or desired mounts.
10. The Provider-input convenience path explicitly destroys its ephemeral Resources; entered Resource and advanced Host-runtime paths never select pause or destroy.
11. Operation leases keep retired provider scopes alive only for already authorized work.
12. Model-facing tools and projections never expose Host mutation authority or provider credentials.
13. Dynamic Environment Capability behavior is optional; Environment routing and lifecycle are not.
14. Foreground shell work is Environment-bound; canonical background work belongs to the configured background-capable operator.
15. `ShellOperator.supports_background`, fixed at Capability construction, determines whether background tools exist.
16. A default `ProcessManager` owns detached process objects independently of the parent Run's `BoundEnvironment` lifetime.
17. Active-run enqueue and stable Host-hook dispatch are independent; the operator always dispatches hooks.
18. Parent process state stores only Thread-scoped compact backend references and bounded portable observations.
19. Durable definitions, reconciliation, retries, and provider Resource lifecycle remain outside the Harness.
