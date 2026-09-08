# Environment Integration

## Design Position

Harness integrates already constructed `Environment` instances from `a13n-environment`. It owns Run-local multi-mount routing, access ceilings, mount-incarnation fencing, readiness aggregation, model projection, portable state aggregation, and non-destructive cleanup. It does not discover Providers, construct provider targets or connections, or choose preparation timing and backing-target retention policy. Hosts supply ready objects or objects that transparently prepare on first actual operation.

The Environment package owns the only shared lifecycle entities: `EnvironmentProvider`, `Environment`, and `EnvironmentState`. Harness adds only lightweight mount configuration and a process-local bound aggregate. Those Harness values are not provider lifecycle entities.

Every independent Harness Run receives fresh Environment instances. Harness enters them before Agent input production and closes them after the terminal Run fence. `close()` releases local adapter resources and never destroys a Docker container, E2B sandbox, Host workspace, or other backing target. Inline child execution borrows the parent Run's entered facade; an async child is an independent Run and receives fresh adapters from its Host.

`DynamicEnvironmentCapability` is the optional model adapter. It derives a fixed standard Toolset from the effective actions of the selected mounts, projects bounded current mount context, and exposes only operations permitted by both Harness access ceilings and provider descriptors. Environment lifecycle administration never becomes a model tool.

Each Toolset owns the mapping from its tool arguments to canonical authorization resources, declared with the tool registration. File tools resolve all affected paths, including both endpoints of every copy or move; shell tools resolve command bindings and their own process references. The Environment layer owns route selection, readiness, and invocation-local mount-publication fences without interpreting tool identifiers or argument schemas. Dynamic model context only projects Environment changes and does not mediate Toolset resource resolution. These resource semantics apply to Environment-backed Toolsets independently of the dynamic capability. Direct FileOperator and generic file-scope callers retain their existing behavior, and explicit resource-resolver and execution-guard callbacks remain supported overrides.

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
    mount_path: str | None = None
```

`EnvironmentMount` is a Run input/configuration value. It contains one already constructed adapter plus Run-local policy. It has no independent identity, lifecycle, durable serialization, or Provider discovery behavior.

`EnvironmentAccess` is the complete user-facing access model:

- `READ_ONLY` permits provider-neutral file observation and file-copy source access;
- `READ_WRITE` adds file mutation;
- `FULL` permits every Agent-facing operation family offered by the Provider, including command/process behavior;
- provider descriptors always narrow these ceilings;
- state dump and local close remain trusted lifecycle operations and are not model-authored permissions.

`working_directory` is `None` or a canonical absolute provider-local path. It contains no NUL, repeated separator, trailing separator other than `/`, or `.`/`..` segment.

`mount_path` is an optional Host-selected root in the aggregate and model-facing path space. It does not change the Provider's root or filesystem authority. When present, it is a canonical absolute POSIX, Windows drive, or UNC path written with `/` separators; Windows matching is case-insensitive. A root contains no NUL, empty interior segment, or `.`/`..` segment and has no trailing separator except an absolute filesystem root. The Harness keeps Provider operations in their provider-local `/` namespace and translates at the aggregate boundary.

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
6. An empty mapping, invalid mount name, duplicate Environment instance, invalid policy, equal aggregate route owned by different mounts, or conflict with `RunBindings` fails before `enter()`.
7. Omitting all Environment input creates an empty bound facade and exposes no Environment tools.
8. `environment_run_extensions` is the ordered finite set of fresh extension instances for this Run; duplicate extension IDs fail before Environment entry.
9. Inputs never accept an `EnvironmentProvider`, provider specification, Provider Resource, attachment, state envelope, or catalog key.

A mount without `mount_path` retains the compatibility routes: every such mount is addressable at `/environment/{name}`, and the current default is also addressable at `/workspace`. Without a default, `/workspace` is unavailable. A mount with `mount_path` is addressable only at that explicit root; the Harness does not also expose `/workspace` or `/environment/{name}` for it. Relative paths still select the explicit alias or current default and begin at that mount's provider-local `working_directory`.

A hosted worker constructs Environment instances from Host-authoritative configuration and state, and either prepares them before Harness execution or supplies Host-coordinated lazy preparation. An embedded caller can construct them directly through a trusted Provider.

## Ownership Boundary

| Concern                                                          | Owner                                                            |
| ---------------------------------------------------------------- | ---------------------------------------------------------------- |
| Provider selection and desired configuration                     | Host                                                             |
| Current state, Thread association, retention                     | Host                                                             |
| Environment construction and single-target I/O                   | Environment package                                              |
| Entry metadata correlation                                       | Harness supplies ephemeral values from Host bindings             |
| One Run's mount names, IDs, access, aggregate roots, and routing | Harness                                                          |
| Entered multi-mount facade                                       | Harness-internal bound aggregate                                 |
| Environment Run Extension protocol and ordering                  | Harness                                                          |
| Extension selection and serializable configuration               | Host                                                             |
| Provider operation execution and local cleanup                   | Entered Environment                                              |
| Model-facing file and shell Toolsets                             | Harness Capabilities                                             |
| Run-local process references, readiness, and observation release | Harness-private Run process controller                           |
| Portable mount-name-to-state continuation                        | `HarnessState.environment_states`                                |
| State publication and backing-target destruction                 | Host                                                             |
| Async subagent admission, lifecycle, cleanup, wake               | [Async Subagent Lifecycle](20-async-components-and-lifecycle.md) |

Provider denial always narrows Harness access. Mount names, mount IDs, paths, process references, cursors, and saved state are selectors or observations, not bearer credentials.

## Identity and Core Values

| Value                    | Meaning                                                 | Visibility                                       |
| ------------------------ | ------------------------------------------------------- | ------------------------------------------------ |
| Mount name               | Stable Run-local alias such as `workspace`              | Host, routing, model projection                  |
| Aggregate mount path     | Optional Host-selected model-facing root                | Routing, model projection, provider-result paths |
| Mount ID                 | Opaque Harness-generated mount scope identity           | Harness internals and provider-neutral artifacts |
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

`EnvironmentMountInfo` contains the mount name, provider key, entered descriptor, access ceiling, provider-local default working directory, and optional aggregate `mount_path`. It omits opaque mount ID and provider target identity from model context.

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

Harness validates the complete initial mapping before binding any local scope. The Host may already have prepared the supplied objects; Harness entry itself causes no target I/O. Entry then proceeds:

01. normalize each raw Environment into an `EnvironmentMount`;
02. allocate a fresh opaque mount ID per mount;
03. bind each adapter's local scope with `enter()` and ephemeral `thread_id`, `run_id`, `agent_instance_id`, mount ID, and bounded Host references;
04. validate configured provider descriptors and derive bounded effective actions without forcing lazy preparation;
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

The entered multi-mount aggregate is process-local and Harness-internal. `AgentContext.environment` exposes its provider-neutral operation facade to trusted Capabilities; it does not expose adapter construction, lifecycle preparation, entry, close, stop, keepalive, destroy, Provider discovery, or Host state publication.

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

Mount and replacement preparation binds the fresh Environment scope before commit without forcing target I/O for a lazy object. Preparation failure leaves the published snapshot unchanged and closes the candidate. Commit publishes one new snapshot and one `EnvironmentChange`; the retired adapter closes after its operation leases drain.

Dynamic mutations are Run-local. They do not discover a Provider, persist desired mounts, mutate Host Thread association, invoke `destroy()`, or change another Run. A durable desired-mount change is a separate Host operation applied before constructing a later Run.

Mutation after the terminal fence fails. A caller that needs an initial mount must supply it before Run entry rather than racing input production.

## Routing and Operation Fencing

Logical routing is:

- an absolute path under an explicit `mount_path` selects that mount;
- overlapping explicit roots are valid, and the longest complete path-component prefix wins;
- `/workspace/...` selects the current default mount only when that mount omits `mount_path`;
- `/environment/{name}/...` selects a named mount only when it omits `mount_path`;
- an explicit alias selects a named mount for shell/process/port operations;
- an alias and absolute path supplied together must select the same mount;
- relative file paths and command working directories resolve below the selected mount's configured provider-local directory.

Equal or Windows-equivalent routes owned by different mounts are invalid. Initial construction rejects them before Provider entry. Dynamic mount, replacement, and default changes validate the prospective complete route set before candidate transfer or publication. An overlap at different path depths is not a conflict because component-prefix routing remains deterministic.

A routed operation captures current mount ID and provider generation before authorization. Immediately before provider dispatch, Harness verifies that the incarnation is still current. A mismatch fails `environment_stale_mount` and never retargets to a replacement.

Each provider call holds an operation lease. Replacement and unmount stop new routing immediately and retain the old adapter until leases and tracked handles release. Process and retained-output operations route by captured identity, not by mount name.

Compound file operations pin exact source and destination incarnations before I/O. Cross-mount copy never re-resolves one endpoint after the other begins.

## Readiness

Readiness is operation-family scoped. Entry binds configured identity and descriptor; it does not establish a target connection or assert live viability for an unprepared object. An actual operation or explicit `ensure_ready()` invokes the object's coordinated preparation when necessary. Merely projecting descriptors or readiness summaries never does. A live descriptor can narrow configured capabilities but cannot broaden accepted access.

Harness groups requirements by current mount incarnation, intersects requested operations with access ceilings and provider descriptors, and invokes readiness only for required families. Concurrent equivalent waits can share provider work. A replacement cannot authorize dispatch captured for an old incarnation; initial preparation can replace the `unprepared` descriptor before the first operation is authorized.

Timeout, provider failure, unavailable family, replacement, and closure produce typed bounded errors. Harness never widens a requirement or retries an uncertain side effect automatically.

## Model Context Projection

At each input boundary, Environment projects one bounded trusted block:

```text
Current Environment mounts (trusted dynamic context):
{"default_mount":"workspace","mounts":[...],"truncated":false}
```

Each projected mount contains only name, effective aggregate root, effective operation families, readiness summary, availability, and read-only observation. The root is the explicit `mount_path` when configured, otherwise the preferred compatibility alias. The projection excludes mount IDs, target IDs, provider-local paths, state payload, credentials, native handles, and lifecycle administration.

`DynamicEnvironmentCapability` observes Run-local mount changes and enqueues at most one bounded refresh notice for a pending set. It does not duplicate the complete projection in ordinary messages.

The model-facing Toolset is standard:

- `view` requires text-read on one mount or both stat and byte-read on one mount; `ls`, `glob`, and `grep` independently require list, query, and text-search;
- file-mutation actions add `write`, `edit`, `multi_edit`, `mkdir`, `move`, `copy`, and `delete` as applicable;
- command execution adds `shell_exec`;
- process actions independently add `shell_info`, `shell_wait`, `shell_input`, and `shell_signal` when their corresponding effective actions are present;
- no model-facing `background` flag, `shell_status`, or `shell_kill` exists;
- an empty Environment exposes no Environment tools.

The file tool surface exposes a tool when at least one mount supports a valid argument branch. Mutation-only mounts do not require an unrelated read action. Write and empty-old-string edit branches require text-write; existing edits require byte-read plus text-write, not patch-text. A captured mount root is not recreated, including explicit roots without a default mount. Parent creation additionally requires mkdir only when the requested path makes the tool call mkdir, even if that parent already exists. Mkdir, move, and delete require their corresponding actions. Copy requires copy-source and copy-destination, possibly on different mounts; internal streaming does not add separate byte-read/write requirements. Other same-mount requirements cannot be assembled by combining partial actions from different mounts. Tool instructions follow the exposed names. Every call re-authorizes the exact selected current mount incarnation and fails without provider effects when that mount lacks an action required by the requested operation. A foreground-only mount keeps completion-only `shell_exec`; a process-capable mount uses the same name with automatic bounded yield.

Provider preparation and rebuild remain behind the operation object. When the backing target changes, the object supplies bounded change evidence and refreshed context; Harness fences stale operation leases/handles and publishes a new mount incarnation before later dispatch. No stale process reference or pending readiness observation can cross the change. Unknown outcomes from already dispatched operations are returned as explicit errors rather than replayed against a replacement target. Harness does not construct connections itself.

## Portable Environment State

`HarnessState` stores provider states directly:

```python
class HarnessState(BaseModel):
    # other fields omitted
    environment_states: Mapping[str, EnvironmentState] = {}
```

There is no `EnvironmentMapState` or `EnvironmentMountState` type.

Keys are Harness mount names. Values are imported `a13n-environment` `EnvironmentState` envelopes. Singular Environment input uses `workspace`. A mount whose adapter returns `None` is omitted; Direct Local and Local Envd are normally stateless.

Export rules:

1. capture one complete current mount-set observation under the aggregate operation fence;
2. call the infallible process-local `dump_state()` cache read for each selected current adapter without refreshing any target;
3. validate the state envelope shape, provider-key consistency, non-blank state-version identifiers, canonical JSON, and Host-admitted size bounds; exact codec compatibility remains Provider-owned;
4. omit `None` values;
5. fail the complete export on cancellation or an adapter contract violation rather than silently dropping a stateful mount.

The mapping contains no default mount, desired mount definition, access policy, working directory, aggregate mount path, mount ID, provider generation, credential, handle, lease, pending mutation, change sequence, Host Thread association, or retention policy.

Harness does not use this mapping to construct or authorize adapters. A Host selects already constructed adapters before Run entry. For managed Environments, Host current state wins, including authoritative `None`, and suppresses stale portable fallback. A Host may adopt the mapping only through an explicit unmanaged/import flow where no Host authority exists.

State export is a continuation observation, not durable publication. Host finalization independently compares each adapter's supplied and dumped values and publishes changed state even after execution, cancellation, checkpoint, or close failure. Equal state performs no Host write.

## File Surface

The Provider package owns the async single-Environment file contract. Harness routes it across mounts and provides bounded provider-neutral operations for stat, listing, byte/text reads, streaming writes, patching, directory creation, move, copy, removal, glob/query, and text search.

Every mutation returns a typed receipt. Reads and listings carry explicit offsets or continuation. Text reads report truncated lines. Search and glob expose bounded pages with deterministic ordering.

Direct Local confines native paths beneath its configured root and keeps blocking filesystem work off the event loop. It makes no sandbox claim. EIP-backed Providers perform all Agent file operations through EIP.

Provider file arguments remain provider-local after routing. Paths returned by stat, list, glob, query, and search are reprojected beneath the selected aggregate root, so a returned path can be reused in a later aggregate operation. Compound file scopes capture both mount incarnation and aggregate root; replacement or a changed route cannot retarget the operation.

Tool results use bounded disclosures and stable model-safe errors. Internal mount identity, generation, state, and receipts are not model-editable arguments.

## Run-local Shell Observations

`DynamicEnvironmentCapability` composes the standard Shell Toolset over the current entered `BoundEnvironment`. Foreground-only execution returns a bounded inline/observed command result without requiring standalone output permissions. The Provider owns internal capture materialization and cleanup. Background observation requires start, inspect, wait, read-output and release, not stdin, all signals, native discovery or standalone output resources.

The conceptual model-facing surface is:

```python
async def shell_exec(
    command: str,
    *,
    cwd: str | None = None,
    environment: Mapping[str, str] | None = None,
    yield_time_seconds: float = 10,
    execution_timeout_seconds: float | None = None,
    alias: str | None = None,
) -> ShellExecResult: ...


async def shell_info(
    process_id: str | None = None,
    *,
    alias: str | None = None,
    limit: int = 50,
) -> ProcessInfoResult: ...


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

`yield_time_seconds` and `shell_wait.timeout_seconds` bound waiting only. `execution_timeout_seconds` requests a Provider-enforced hard execution deadline and is rejected before dispatch where unsupported. There is no generic default deadline for a backend lacking that guarantee. Foreground-only mounts omit the yield argument. There is no model-selected background mode.

Completion inside the yield window returns observed terminal status and bounded output without a process reference. Otherwise Harness publishes a concise Run-local reference such as `process-a3f1-1`. Process completion depends on native terminal evidence, not completed output producers or tree cleanup. Missing or unknown status does not imply successful completion.

### Query, observation and control

`shell_info()` without an ID requests bounded native discovery on the selected alias/default mount and assigns fresh Run-local references. Repeated discovery deduplicates the same bound native identity. It returns status and optional stdin-state evidence, not native PIDs, arbitrary argv or environment variables. `has_more` describes the bounded projection; no generic stable server cursor is promised. Discovery allocates no output stream and installs no watcher for every listed process.

`shell_info(process_id=...)` inspects that reference without waiting or reading output. The reference selects its exact bound mount; a conflicting explicit alias fails. List and inspect require separate actions. A Provider can support inspection without listing. Neither query refreshes, resets or reconnects output.

`shell_wait` waits boundedly and reads available output at explicit caller offsets. Zero is a poll. Output provenance and evidence follow the [Provider observation contract](../a13n-environment/02-environment-lifecycle.md#process-and-output-observations). Returned `requested_offset`, `start_offset`, `next_offset`, available bounds and `omitted_before_bytes` describe this page only. `next_offset` advances only across returned bytes. Unknown producer totals are null rather than buffer lengths. Repeating offsets is valid; Harness has no hidden unread cursor.

A transient native reconnect preserves the reference and accumulated observation offsets, appends newly observed text and reports partial coverage. A new Run creates fresh references and observations at zero. Target replacement invalidates identities rather than reusing a PID against another sandbox. Capped output remains readable but cannot be reset by querying or waiting.

`shell_input` mutates UTF-8 stdin/EOF without reading output. `shell_signal` requests only a supported native control action and reports acceptance independently from later exit evidence. Kill support does not imply interrupt/terminate support. Mixed mounts compose the union of tools but each call rechecks its exact selected mount and action before effects.

### Run-local controller and failure

The controller owns references, authorized routing, latest bounded observations, serialized local control and optional best-effort completion watchers. It owns no execution database, portable process state, native process lifecycle, unread cursor or Host process operator. Canonical process policy resources are scoped to the mount and native identity rather than a bare PID.

Admission reserves a fresh reference, starts once through the authorized Provider and publishes the accepted handle cancellation-safely. Failure after native acceptance does not grant permission to kill or repeat the command. A usable but unpublished observation is released; an uncertain start is reported without replay. Provider state and native discovery own any later recovery.

An active watcher polls status without reconnecting streams. After native terminal evidence it may enqueue one bounded hint:

```text
Background process process-<ref> has exited. Call shell_wait for available output.
```

The hint contains no output, secret, native handle or durable-delivery promise. The same active-attempt boundary can emit the native advisory `ShellStatusEvent` described by [Events and Usage](12-events-observability-and-usage.md); that event carries observed process status, not captured output or a new process-control authority. Missing/unknown status does not emit an exit hint. Notification loss or cancellation does not change process truth. Published terminal references remain readable until local release or Run close; completion alone does not retire them.

### Run cleanup and recovery ownership

Run cleanup fences admission, cancels local watchers and releases observations through Provider operations before adapters close. It never blanket-kills commands or separately coordinates mandatory standalone output references. Cleanup failures join the normal Run cleanup aggregate. Repeated cancellation cannot abandon accepted cleanup, and cleanup never invokes target destruction.

A released observation no longer pins mount retirement even if its remote command survives. Native scope-close semantics remain Provider-owned. E2B can preserve commands in a surviving sandbox; Direct Local and Envd-backed Providers retain their actual scope cleanup behavior. Harness never infers persistence from the presence of non-null state.

No model reference, log offset, status mirror or watcher enters Harness Capability state. Provider `EnvironmentState` remains opaque and owns backend-specific recovery evidence. A later Run starts an empty controller, uses native discovery when supported and rejects references copied from old messages. No Service process table, UI process store, durable wake service or automatic command replay is introduced. Async subagents retain their independent Host operator.

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
15. Process-capable `shell_exec` automatically yields a Run-local process reference when the command does not complete inside its initial wait.
16. Process references are Run-incarnation-qualified, never persisted, reused, rebound, or retargeted.
17. `shell_wait` uses caller-supplied stdout and stderr offsets; Harness stores no model unread cursor.
18. Retention omission is explicit, and repeating an offset never falsely claims omitted output was delivered.
19. Active-Run final-completion readiness is standard best-effort behavior; explicit polling remains authoritative.
20. Run cleanup releases local observations without blanket process termination.
21. No model reference, offset, status mirror or watcher enters portable Harness state; backend recovery state remains Provider-owned.
22. Shell observations and async subagents share no Manager, store, projection, observer registry, or shutdown lifecycle.
23. Environment Run Extensions are fresh Host-selected aggregate scopes, not another Plugin, Capability, or Provider plane.

## Publishing Live Environment Changes

A mount incarnation is the pair of Harness mount ID and published provider generation. Provider readiness publishes one immutable descriptor, effective permission set and operation-facet snapshot together. The aggregate snapshot and change journal update in the same local publication step. A provider recovery that reports a replacement error still publishes its new observation before the next model context projection.

Dispatch rechecks permissions and generation after readiness. Compound background start rechecks the complete start/inspect/wait/read-output/release action set inside the prepared lease before native start; readiness that narrows any required action prevents dispatch rather than falling back or replaying the command. Already dispatched operations retain their captured operation facets and receipt fence. File scopes, process handles and retained outputs from an older generation cannot silently retarget. A new mount ID is required for explicit mount replacement; refreshing the same provider scope advances its observed generation instead.

Standard Environment tools, downloads, and document conversion share one canonical resource mapping. A file resource identifier remains `mount_id:generation:provider_path` for current invocation policy and dispatch, while its `approval_revision` is only the resolved Provider-local path. Mount resources retain the incarnation identifier and use `/` as their approval revision. Mount and command-cwd selection prepare the relevant operation family without requiring a file facet. Deferred approval captures those revisions, resource kinds, and the tool/argument digest. Resumption resolves current resources and rejects changed approval facts before dispatch, independently of whether the current policy still asks for approval.

Mount IDs, operation-session generations, and Provider `backing_identity` are not automatic cross-Run approval restrictions. An approval can resume after reconnecting or replacing a backing target when its tool, arguments, resource kinds, and resolved paths still match and current policy allows it. This is operation approval, not proof of backing-target continuity. Hosts needing stricter target binding use the existing invocation policy or approval verifier; custom tool resource resolvers may still supply stricter approval revisions. Provider backing evidence remains available for recovery and Host policy. Runtime mount/generation fencing and spill cleanup remain independent and unchanged.

Download approval covers its destination directory. Document approval covers both the source and its output parent directory, not a speculative generated leaf. Changed paths or tool/argument facts require fresh approval, and current denial still applies. Previously captured approvals using backing- or incarnation-based revisions, and legacy download/document approvals without revision facts, require fresh approval once. Newly captured approvals do not repeat the connection-bound rejection. File selections no longer carry backing identity for automatic approval construction; custom Providers may continue supplying it in their descriptors. Direct non-Environment FileToolset callers acquire no fabricated Environment identity.
