# Environment Integration

## Design Position

Harness integrates already constructed `Environment` instances from `a13n-environment-provider`. It owns Run-local multi-mount routing, access ceilings, mount-incarnation fencing, readiness aggregation, model projection, portable state aggregation, and non-destructive cleanup. It does not discover Providers, construct provider targets, or choose backing-target retention and destruction policy.

The Environment Provider package owns the only shared lifecycle entities: `EnvironmentProvider`, `Environment`, and `EnvironmentState`. Harness adds only lightweight mount configuration and a process-local bound aggregate. Those Harness values are not provider lifecycle entities.

Every independent Harness Run receives fresh Environment instances. Harness enters them before Agent input production and closes them after the terminal Run fence. `close()` releases local adapter resources and never destroys a Docker container, E2B sandbox, Host workspace, or other backing target. Inline child execution borrows the parent Run's entered facade; an async child is an independent Run and receives fresh adapters from its Host.

`DynamicEnvironmentCapability` is the optional model adapter. It derives a fixed standard Toolset from the effective actions of the selected mounts, projects bounded current mount context, and exposes only operations permitted by both Harness access ceilings and provider descriptors. Environment lifecycle administration never becomes a model tool.

## Run Inputs

The public values are:

```python
class EnvironmentAccess(StrEnum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class EnvironmentMount:
    environment: Environment
    access: EnvironmentAccess = EnvironmentAccess.FULL
    working_directory: str | None = "/"
```

`EnvironmentMount` is a Run input/configuration value. It contains one already constructed adapter plus Run-local policy. It has no independent identity, lifecycle, durable serialization, or Provider discovery behavior.

`EnvironmentAccess` is the complete user-facing access model:

- `READ_ONLY` permits provider-neutral file observation and file-copy source access;
- `READ_WRITE` adds file mutation;
- `FULL` permits every Agent-facing operation family offered by the Provider, including command/process behavior;
- provider descriptors always narrow these ceilings;
- state dump and local close remain trusted lifecycle operations and are not model-authored permissions.

`working_directory` is `None` or a canonical absolute provider path. It contains no NUL, repeated separator, trailing separator other than `/`, or `.`/`..` segment.

`ExecutableAgent.run()` and `stream()` accept:

```python
run(
    input=None,
    *,
    environment: Environment | EnvironmentMount | None = None,
    environments: Mapping[str, Environment | EnvironmentMount] | None = None,
    default_environment: str | None = None,
    environment_run_extensions: Sequence[EnvironmentRunExtension] = (),
    bindings: RunBindings | None = None,
    ...,
)
```

The rules are:

1. `environment` and `environments` are mutually exclusive.
2. `default_environment` is valid only with `environments` and names one supplied mount.
3. Singular input normalizes to mount name `workspace` and becomes the default.
4. A one-entry mapping selects its only mount as default.
5. A mapping with several entries has no default unless explicit. Mapping order never selects authority.
6. An empty mapping, invalid mount name, duplicate Environment instance, invalid policy, or conflict with `RunBindings` fails before `enter()`.
7. Omitting all Environment input creates an empty bound facade and exposes no Environment tools.
8. `environment_run_extensions` is the ordered finite set of fresh extension instances for this Run; duplicate extension IDs fail before Environment entry.
9. Inputs never accept an `EnvironmentProvider`, provider specification, Provider Resource, attachment, state envelope, or catalog key.

The default mount is addressable at `/workspace`. Every mount is addressable at `/environment/{name}`. Without a default, `/workspace` is unavailable.

A hosted worker normally constructs Environment instances from Host-authoritative configuration and state before invoking Harness. An embedded caller can construct them directly through a trusted Provider.

## Ownership Boundary

| Concern                                            | Owner                                                            |
| -------------------------------------------------- | ---------------------------------------------------------------- |
| Provider selection and desired configuration       | Host                                                             |
| Current state, Thread association, retention       | Host                                                             |
| Environment construction and single-target I/O     | Environment Provider package                                     |
| Entry metadata correlation                         | Harness supplies ephemeral values from Host bindings             |
| One Run's mount names, IDs, access, and routing    | Harness                                                          |
| Entered multi-mount facade                         | Harness-internal bound aggregate                                 |
| Environment Run Extension protocol and ordering    | Harness                                                          |
| Extension selection and serializable configuration | Host                                                             |
| Provider operation execution and local cleanup     | Entered Environment                                              |
| Model-facing file and shell Toolsets               | Harness Capabilities                                             |
| Run-owned process tracking, readiness, and cleanup | Harness-private Run process controller                           |
| Portable mount-name-to-state continuation          | `HarnessState.environment_states`                                |
| State publication and backing-target destruction   | Host                                                             |
| Async subagent admission, lifecycle, cleanup, wake | [Async Subagent Lifecycle](20-async-components-and-lifecycle.md) |

Provider denial always narrows Harness access. Mount names, mount IDs, paths, process references, cursors, and saved state are selectors or observations, not bearer credentials.

## Identity and Core Values

| Value                    | Meaning                                                 | Visibility                                       |
| ------------------------ | ------------------------------------------------------- | ------------------------------------------------ |
| Mount name               | Stable Run-local routing name such as `workspace`       | Host, routing, model projection                  |
| Mount ID                 | Opaque Harness-generated incarnation identity           | Harness internals and provider-neutral artifacts |
| Provider key             | Provider implementation discriminator                   | Trusted Host and provider integration            |
| Provider target identity | Provider-owned state data such as a Docker container ID | Trusted Host/provider state; never model context |
| Provider generation      | Entered operation fence when a backend exposes one      | Trusted operation and stale-handle checks        |
| Run ID                   | One process-local logical Harness Run                   | Host and entry correlation                       |

A replacement keeps the mount name and allocates a fresh mount ID. An old path, process handle, output cursor, or operation receipt never retargets to the replacement.

Core immutable values include:

- `EnvironmentPath(mount_id, path)`;
- `EnvironmentOperationReceipt(mount_id, observed_generation, operation_id, stage, outcome)`;
- `BoundProcessHandle(mount_id, observed_generation, process_id, handle)`;
- retained-output references carrying the same mount and generation fence;
- `EnvironmentSnapshot(mounts, default_mount)`;
- `EnvironmentChange(sequence, kind, name, previous_default, current_default)`.

`EnvironmentMountInfo` contains the mount name, provider key, entered descriptor, access ceiling, and default working directory. It omits opaque mount ID and provider target identity from model context.

## Environment Run Extensions

An `EnvironmentRunExtension` is a trusted async context-manager scope around one complete entered Environment aggregate. It is separate from Harness middleware Plugins, declarative Capabilities, and Environment Providers. A Host supplies ordered fresh, pre-entry-inert instances as Run inputs.

```python
@dataclass(frozen=True, slots=True)
class EnvironmentRunExtensionContext:
    run_id: str
    instance: AgentInstanceContext
    environment: BoundEnvironment


class EnvironmentRunExtension(Protocol):
    @property
    def extension_id(self) -> str: ...

    def bind(
        self,
        *,
        context: EnvironmentRunExtensionContext,
    ) -> AbstractAsyncContextManager[None]: ...
```

Extension IDs are unique within one Run. Harness enters scopes in supplied order after the initial Environment aggregate is available and exits them in reverse order before adapter teardown. An entry failure unwinds scopes already entered and then follows ordinary Environment cleanup. Exit failures are aggregated with other cleanup outcomes without selecting backing-target destruction.

Harness also exposes the optional `a13n_harness.environment_run_extensions` factory catalog. Metadata discovery returns keys and available package provenance without importing targets. Catalog construction imports only selected entry points and can also accept explicit trusted factories. A factory receives only `extension_key`, the Host-selected `extension_id`, and detached finite JSON configuration, and returns one fresh extension with that exact ID. Duplicate keys, invalid factories, and mismatched IDs fail explicitly.

The catalog does not select packages or read Host resource files. A Host decides which keys are trusted, validates its serializable configuration, creates fresh instances for each independent Run, and records any provenance it needs.

## Entry and Aggregate Lifecycle

Harness validates the complete initial mapping before provider effects. Entry then proceeds:

01. normalize each raw Environment into an `EnvironmentMount`;
02. allocate a fresh opaque mount ID per mount;
03. call each adapter's `enter()` with ephemeral `thread_id`, `run_id`, `agent_instance_id`, mount ID, and bounded Host references;
04. validate each immutable provider descriptor and derive effective actions;
05. if every mount entered successfully, publish one complete internal `EnvironmentSnapshot` and stable bound facade;
06. enter Environment Run Extension scopes in supplied order;
07. bind Environment-aware Capabilities and produce Agent input;
08. execute model attempts and operations against that same facade;
09. snapshot portable states when requested;
10. install the terminal mutation fence, exit extension scopes in reverse order, drain operation leases, and call non-destructive `close()` on each adapter in reverse entry order.

A failure before publication unwinds entered adapters and closes every supplied adapter that might own local resources. No partial initial mount set becomes model-visible.

Provider state is supplied during adapter construction before Harness receives it. Harness does not restore state after entry.

A successful Run, failed Run, cancellation, state-export failure, or close failure does not select backing-target destruction. The Host can inspect adapter state in its unconditional finalization independently of whether Harness produced a checkpoint.

## Internal Bound Facade

The entered multi-mount aggregate is process-local and Harness-internal. `AgentContext.environment` exposes its provider-neutral operation facade to trusted Capabilities; it does not expose adapter construction, entry, close, destroy, Provider discovery, or Host state publication.

Conceptually it supports:

```python
class BoundEnvironment(ABC):
    @property
    def snapshot(self) -> EnvironmentSnapshot: ...

    async def select_path(self, path: str) -> EnvironmentPath: ...
    async def select_files(...) -> SelectedFileScope: ...
    async def shell(...) -> CommandResult: ...
    async def process(...) -> ProcessResult: ...
    async def describe(self, name: str) -> EnvironmentMountObservation: ...
    async def ensure_ready(self, requirement: EnvironmentReadinessRequirement) -> None: ...
```

`BoundEnvironment` is not exported as a cross-package lifecycle entity and is never durable. Implementation can split its routing and mutation responsibilities without changing the accepted contract.

## Run-local Mount Mutation

A trusted Run integration can mutate the internal mount set through a Harness-owned controller associated with that exact Run. The controller is not Provider lifecycle authority and is never placed in model context or durable state.

Mutations are linearizable:

- `mount(name, EnvironmentMount)` requires an absent name;
- `replace(name, EnvironmentMount)` requires an existing name and preserves default selection;
- `unmount(name)` removes the selected incarnation and clears default when needed;
- `set_default(name | None)` changes routing only.

Mount and replacement preparation enters the fresh Environment before commit. Preparation failure leaves the published snapshot unchanged and closes the candidate. Commit publishes one new snapshot and one `EnvironmentChange`; the retired adapter closes after its operation leases drain.

Dynamic mutations are Run-local. They do not discover a Provider, persist desired mounts, mutate Host Thread association, invoke `destroy()`, or change another Run. A durable desired-mount change is a separate Host operation applied before constructing a later Run.

Mutation after the terminal fence fails. A caller that needs an initial mount must supply it before Run entry rather than racing input production.

## Routing and Operation Fencing

Logical routing is:

- `/workspace/...` selects the current default mount;
- `/environment/{name}/...` selects a named mount;
- an explicit alias selects a named mount for shell/process/port operations;
- relative command working directories resolve below the selected mount's configured provider directory.

A routed operation captures current mount ID and provider generation before authorization. Immediately before provider dispatch, Harness verifies that the incarnation is still current. A mismatch fails `environment_stale_mount` and never retargets to a replacement.

Each provider call holds an operation lease. Replacement and unmount stop new routing immediately and retain the old adapter until leases and tracked handles release. Process and retained-output operations route by captured identity, not by mount name.

Compound file operations pin exact source and destination incarnations before I/O. Cross-mount copy never re-resolves one endpoint after the other begins.

## Readiness

Readiness is operation-family scoped. Entry establishes provider identity, descriptor, and minimum operation viability. More expensive preparation can remain lazy behind `ensure_ready()`.

Harness groups requirements by current mount incarnation, intersects requested operations with access ceilings and provider descriptors, and invokes readiness only for required families. Concurrent equivalent waits can share provider work. A replacement cannot satisfy a wait captured for an old incarnation.

Timeout, provider failure, unavailable family, replacement, and closure produce typed bounded errors. Harness never widens a requirement or retries an uncertain side effect automatically.

## Model Context Projection

At each input boundary, Environment projects one bounded trusted block:

```text
Current Environment mounts (trusted dynamic context):
{"default_mount":"workspace","mounts":[...],"truncated":false}
```

Each projected mount contains only name, logical root, effective operation families, readiness summary, availability, and read-only observation. It excludes mount IDs, target IDs, state payload, credentials, native handles, and lifecycle administration.

`DynamicEnvironmentCapability` observes Run-local mount changes and enqueues at most one bounded refresh notice for a pending set. It does not duplicate the complete projection in ordinary messages.

The model-facing Toolset is standard:

- read-only actions expose `view`, `ls`, `glob`, and `grep`;
- file-mutation actions add `write`, `edit`, `multi_edit`, `mkdir`, `move`, `copy`, and `delete` as applicable;
- command execution adds `shell_exec`;
- process actions add `shell_wait`, `shell_input`, and `shell_signal` when their complete effective action requirements are present;
- no model-facing `background` flag, `shell_status`, or `shell_kill` exists;
- an empty Environment exposes no Environment tools.

The fixed schema is derived from the union of the initial effective mount actions. Every call re-authorizes the exact selected current mount incarnation and fails without provider effects when that mount lacks an action required by the requested operation. A foreground-only mount keeps completion-only `shell_exec`; a process-capable mount uses the same name with automatic bounded yield.

## Portable Environment State

`HarnessState` stores provider states directly:

```python
class HarnessState(BaseModel):
    # other fields omitted
    environment_states: Mapping[str, EnvironmentState] = {}
```

There is no `EnvironmentMapState` or `EnvironmentMountState` type.

Keys are Harness mount names. Values are imported `a13n-environment-provider` `EnvironmentState` envelopes. Singular Environment input uses `workspace`. A mount whose adapter returns `None` is omitted; Direct Local and Local Envd are normally stateless.

Export rules:

1. capture one complete current mount-set observation under the aggregate operation fence;
2. call the infallible process-local `dump_state()` cache read for each selected current adapter without refreshing any target;
3. validate the state envelope shape, provider-key consistency, non-blank state-version identifiers, canonical JSON, and Host-admitted size bounds; exact codec compatibility remains Provider-owned;
4. omit `None` values;
5. fail the complete export on cancellation or an adapter contract violation rather than silently dropping a stateful mount.

The mapping contains no default mount, desired mount definition, access policy, working directory, mount ID, provider generation, credential, handle, lease, pending mutation, change sequence, Host Thread association, or retention policy.

Harness does not use this mapping to construct or authorize adapters. A Host selects already constructed adapters before Run entry. For managed Environments, Host current state wins, including authoritative `None`, and suppresses stale portable fallback. A Host may adopt the mapping only through an explicit unmanaged/import flow where no Host authority exists.

State export is a continuation observation, not durable publication. Host finalization independently compares each adapter's supplied and dumped values and publishes changed state even after execution, cancellation, checkpoint, or close failure. Equal state performs no Host write.

## File Surface

The Provider package owns the async single-Environment file contract. Harness routes it across mounts and provides bounded provider-neutral operations for stat, listing, byte/text reads, streaming writes, patching, directory creation, move, copy, removal, glob/query, and text search.

Every mutation returns a typed receipt. Reads and listings carry explicit offsets or continuation. Text reads report truncated lines. Search and glob expose bounded pages with deterministic ordering.

Direct Local confines native paths beneath its configured root and keeps blocking filesystem work off the event loop. It makes no sandbox claim. EIP-backed Providers perform all Agent file operations through EIP.

Tool results use bounded disclosures and stable model-safe errors. Internal mount identity, generation, state, and receipts are not model-editable arguments.

## Run-owned Shell Processes

`DynamicEnvironmentCapability` composes one standard Shell Toolset over the current entered `BoundEnvironment`. A foreground-only `shell_exec` runs to completion against the exact selected mount incarnation. A process-capable `shell_exec` starts through `BoundEnvironment.processes`, waits for a bounded yield window, and either returns the completed command result or publishes a live Run-owned process reference. The model never predicts foreground versus background through a launch flag.

The process-capable surface contains exactly four shell tools:

```python
async def shell_exec(
    command: str,
    *,
    cwd: str | None = None,
    environment: Mapping[str, str] | None = None,
    yield_time_seconds: float = 10,
    timeout_seconds: float | None = None,
    alias: str | None = None,
) -> ShellExecResult: ...


async def shell_wait(
    process_id: str,
    *,
    stdout_offset: int = 0,
    stderr_offset: int = 0,
    timeout_seconds: float = 180,
) -> ProcessObservation: ...


async def shell_input(
    process_id: str,
    data: str = "",
    *,
    close_stdin: bool = False,
) -> ProcessInputResult: ...


async def shell_signal(
    process_id: str,
    signal: Literal["interrupt", "terminate", "kill"],
) -> ProcessSignalResult: ...
```

`yield_time_seconds` bounds only the initial tool wait. `timeout_seconds` on `shell_exec` is the provider-enforced total process wall-time limit. Completion inside the yield window returns terminal status and bounded stdout/stderr directly without exposing a process reference. If the process remains live, Harness returns a concise reference such as `process-k7m2-1`, current status, initial retained output pages, and the next stdout/stderr offsets. A short yield supports known servers and interactive commands without introducing another execution mode.

`shell_wait` is the sole process status and output observation tool. A zero timeout is an ordinary non-blocking poll; a positive timeout performs one bounded provider wait before inspecting status and retained output. Reads are non-consuming and use the caller's independent stdout and stderr byte offsets. Repeating the same offsets after cancellation, projection failure, or an uncertain client boundary is valid. Harness keeps no model unread cursor.

`shell_input` owns only stdin mutation. It reports accepted bytes, resulting stdin state, and bounded current status; it reads no output. `shell_signal` sends `interrupt`, `terminate`, or `kill` and reports acceptance plus bounded current status; final output is always read through `shell_wait`. There is no `shell_status`, output-returning `shell_kill`, reservation, commit, abort, acknowledgement, or output-consumption lock.

### Private Run controller

Every published live process belongs to the exact Harness Run that started it. The private controller allocates a random concise incarnation plus monotonic local sequence and retains only:

- the model-facing process reference;
- the opaque `BoundProcessHandle` from the current entered Environment;
- one control lock for stdin and signal mutations;
- one active-Run final-completion watcher; and
- the latest bounded `ProcessInfo` observation.

The controller owns no portable process state, backend-ID projection, unread cursor, Host hook, adapter factory, rebind operation, cross-Run lookup, delivery ledger, or stable shutdown API. Process references from an earlier or foreign Run fail without retargeting even when a later Run mounts the same backing target.

Process admission is one cancellation-safe boundary:

1. validate the command, exact selected mount incarnation, access ceiling, effective process actions, output bounds, yield, and total timeout;
2. reserve a never-reused reference from the controller incarnation and sequence;
3. start through the current bound Environment process facade;
4. create a provisional entry and install its non-consuming final-completion watcher;
5. publish the entry only after watcher installation succeeds;
6. wait up to the yield boundary and read initial retained output from offset zero;
7. if final completion is observed, construct the terminal result and release the unpublished-to-model live reference;
8. otherwise return the published reference and next offsets;
9. on failure or cancellation after provider acceptance but before publication, perform shielded bounded kill/release compensation.

Cancellation after publication does not kill the accepted process merely because the current tool return was lost. The entry remains owned and queryable in the same Run, and Run cleanup remains its final owner boundary. Unknown provider acceptance before a usable bound handle remains Environment reconciliation and adapter-close responsibility.

### Explicit-offset output

Each stdout and stderr page reports:

- `requested_offset`, the caller selector;
- `start_offset`, the first returned retained byte;
- `next_offset`, the first byte after returned content;
- `available_start` and `available_end`, the current retained byte range;
- `produced_bytes`, total bytes observed from that producer;
- `producer_complete`, whether no later bytes can appear;
- `content_complete`, whether content faithfully represents the selected retained range; and
- `omitted_before_bytes`, the unavailable prefix between the requested and returned start offsets.

`next_offset` is derived only from the returned page. Repeating a request can observe the same bytes or a later retention view, but the result never claims omitted bytes were delivered. Harness validates handle identity, mount incarnation, monotonic produced-byte observations, retained ranges, page bounds, and aggregate disclosure limits. Retention overflow is explicit rather than a tool failure. Terminal output remains readable under provider retention bounds until the entry is released or the Run closes.

### Active-Run completion readiness

Each published live process has one non-consuming watcher. Final completion requires a terminal process phase, non-pending cleanup, and completed stdout and stderr producers. While the exact Run still accepts native enqueue input, the watcher emits at most one bounded instruction equivalent to:

```text
Background process process-<ref> has finished and final output is ready. Call shell_wait with the last returned stdout_offset and stderr_offset to read it.
```

The hint contains no output, provider handle, target identity, credential, native exception, or durable-delivery claim. Enqueue failure, closed-turn timing, watcher cancellation, duplicate observation, and notification loss never change process truth. Explicit `shell_wait` polling remains authoritative and complete.

### Run cleanup and state

Run cleanup closes admission and new controller operations, cancels only Harness watcher tasks, kills every still-live process, waits under provider cleanup bounds, and releases every handle and retained-output object before Environment adapters close. Cleanup failures join the normal Run cleanup aggregate. Repeated caller cancellation cannot abandon accepted cleanup, and cleanup never calls provider `destroy()` or chooses backing-target retention.

No process handle, reference, output offset, status mirror, watcher, or cleanup fact enters `AgentContextState`, `HarnessState`, or `EnvironmentState`. A continuation Run starts with an empty controller and a new incarnation; process references retained in messages are historical text, not authority. State import rejects the removed `a13n.dynamic-environment.processes` namespace before Environment entry or any other Run effect.

Cross-Run process lifetime, hosted process operators, post-Run wake, durable process/result storage, and Agent UI process integration are outside this contract. Async subagents remain behind their independent Host-owned operator and share no process lifecycle abstraction.

## Ports

Port operations inspect or wait for one provider-neutral target selected by mount name or captured process identity. Provider descriptors and access ceilings must permit the operation. Replacement or stale process identity fails closed.

Environment ports are observations only. Public exposure, URL allocation, proxy lifecycle, authentication, and durable route ownership remain Host or Provider responsibilities.

## Multimedia Understanding

File-media understanding pins and reads one exact file scope, releases the provider lease before external inference, supplies `EnvironmentPath` only as provenance, and records nested provider usage. The external model or service cannot retain Environment authority.

## Failure Surface

`EnvironmentError` carries a stable code, bounded safe details, and optional retry hint. Core codes include invalid selection, denial, unsupported operation, unavailable dependency, not found, conflict, stale mount, invalid state, provider failure, activation failure, and closed Environment.

Provider-native messages, credentials, target IDs, mount IDs, generations, raw Host paths outside the logical root, and backend handles are removed from model-facing errors unless a specific safe field belongs to the tool contract.

Cancellation and timeout do not prove that a mutation failed. Receipts and provider evidence remain authoritative. Harness never automatically replays an uncertain external operation.

Cleanup aggregates failures without changing lifecycle ownership. A close failure does not trigger `destroy()` and does not suppress adapter state needed by Host finalization.

## Invariants

01. Harness receives already constructed Environment instances and never discovers Providers.
02. Every independent Run uses fresh adapters; inline children borrow the current entered facade.
03. State is supplied before Harness entry through adapter construction.
04. Initial mount publication is atomic.
05. One Run exposes one stable internal bound facade.
06. Mount names route; opaque mount IDs fence exact incarnations.
07. Access is the intersection of Harness ceiling and provider descriptor.
08. Dynamic mutation is Run-local and cannot change Host durable association.
09. `HarnessState.environment_states` directly maps mount names to provider state envelopes.
10. Stateless mounts are omitted; singular input uses `workspace`.
11. No aggregate or per-mount state wrapper exists.
12. Harness close never destroys a backing target.
13. Host state publication is independent from successful Harness checkpoint export.
14. Model-facing context and tools contain no lifecycle administration, credentials, target IDs, or raw PIDs.
15. Process-capable `shell_exec` automatically yields a Run-owned process reference when the command does not complete inside its initial wait.
16. Process references are Run-incarnation-qualified, never persisted, reused, rebound, or retargeted.
17. `shell_wait` uses caller-supplied stdout and stderr offsets; Harness stores no model unread cursor.
18. Retention omission is explicit, and repeating an offset never falsely claims omitted output was delivered.
19. Active-Run final-completion readiness is standard best-effort behavior; explicit polling remains authoritative and complete.
20. Run cleanup kills and releases every remaining process before Environment adapters close.
21. No process projection, backend ID, offset, status, loss marker, watcher, or readiness fact enters portable Harness or Environment state.
22. Run-owned shell and async subagents share no Manager, store, projection, observer registry, or shutdown lifecycle.
23. Environment Run Extensions are fresh Host-selected aggregate scopes, not another Plugin, Capability, or Provider plane.
