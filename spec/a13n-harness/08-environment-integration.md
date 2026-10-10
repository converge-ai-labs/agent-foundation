# Environment Integration

## Design Position

Harness receives Host-owned `EnvironmentSource` objects implementing `ensure_ready()`. That method returns a fixed-target connector from the [Environment library](../a13n-environment/01-environment-contract.md). Harness registers mounts without target I/O, calls the selected source's `ensure_ready()` when execution is needed, and opens the returned connector. It owns Run-local mounts, routing, permissions, readiness, tools, context, continuation references, and execution cleanup. Hosts own selection, authorization, management, and state publication, whether they prepare a target before the Run or during `ensure_ready()`.

Each used mount opens its own Environment execution; an unused mount opens none. A Run needing no Environment I/O can finish with every mount unactivated. Inline children borrow the parent Run's bound facade; independent Runs, including async children, open independent scopes. Closing an Environment execution never stops or destroys a backing sandbox or workspace. It preserves provider-specific resource semantics: Envd closes Session-owned commands, output, and transfers while preserving the Host-owned daemon; Direct Local terminates its owned processes; native Docker and E2B release observations without blanket command termination. A changed Session working directory requires a new Environment execution through mount replacement. Directory routing and shell cwd are not OS isolation boundaries.

`DynamicEnvironmentCapability` derives tools and bounded model context from inert configured descriptors, available live observations, and effective mount permissions. Provider implementations supply structured facts, not Harness prompt text or tool schemas. Target administration never becomes a model tool.

Each Toolset owns the mapping from arguments to canonical authorization resources. File tools resolve all affected paths, including both endpoints of a copy or move; shell tools resolve command bindings and their own process references. Environment integration owns route selection, readiness, permission checks, and execution-local scopes without interpreting tool names or argument schemas. Metadata resolution does not reserve a mount across policy waits; explicit resource resolvers and execution guards remain supported overrides.

## Run Inputs

The following conceptual Python interfaces describe public Run inputs, not serialized schemas:

```python
class EnvironmentSource(Protocol):
    @property
    def provider_key(self) -> str: ...

    @property
    def environment_id(self) -> str: ...

    @property
    def descriptor(self) -> EnvironmentDescriptor: ...

    @property
    def state(self) -> EnvironmentState | None: ...

    async def ensure_ready(self) -> EnvironmentConnector: ...


@dataclass(frozen=True, slots=True)
class EnvironmentMount:
    source: EnvironmentSource
    permission_ceiling: EnvironmentPermissionSet = EnvironmentPermissionSet(operations=FILE_EXECUTION_ACTIONS)
    working_directory: str | None = None
    mount_path: str | None = None
    provider_root: str = "/"
```

`EnvironmentSource` is a trusted Host integration boundary owned by Harness, not a Provider or execution connection. Its inert `provider_key` and `environment_id` fix the logical target for routing and continuation matching before activation; the returned connector and execution retain both values. These values do not assert native allocation or readiness. Its inert descriptor declares configured operation support, limits, and the default directory needed for routing and tool composition. Its inert state property returns the current detached Host-selected reference or authoritative `None`. Neither property performs I/O, activates a target, or claims live readiness. Native identity and execution generation remain absent until observed. The logical environment is fixed for this source's lifetime, although the Host may allocate its first native target during activation; a lost target is never silently replaced.

`ensure_ready()` completes Host-authorized preparation and required state publication, then returns an inert fixed-target connector. The Host implementation may call management and its own persistence in the same process; no Service RPC or database dependency enters Harness. An already prepared source validates current authority and returns its connector. Sources own no Run execution; Harness opens and closes executions. Hosts coordinate shared management across sources and Runs.

Every mount supplies an `EnvironmentSource`, including mounts whose targets are already ready. Harness does not accept a bare `EnvironmentConnector` or automatically wrap one. An already prepared Host implements the same `ensure_ready()` contract and returns its connector. There is no Harness `lazy` switch; the caller decides when to prepare the target and may call its source before Run entry. Harness always uses the same readiness boundary on first use.

`EnvironmentMount` contains a source plus Run-local policy, with no independent durable identity, serialization, or Provider discovery behavior. Its `source` implements `ensure_ready()`; there is no `connector=` compatibility input.

`EnvironmentMount.permission_ceiling` is an exact `EnvironmentPermissionSet` action ceiling. It defaults to `FILE_EXECUTION_ACTIONS`, preserving existing access to file operations and command execution but excluding `COMPUTER_ACTIONS`. A Host must explicitly include desktop actions; adding a desktop-capable Provider never silently grants them. A narrower ceiling can expose any combination, such as text read, text write, and remove without other file operations; `FILE_READ_ACTIONS` is the shared constant for file actions that only observe. The ceiling is intersected with configured support and then the live Provider descriptor and can never grant an operation the Provider does not offer. Reading the fixed target state and closing an `EnvironmentExecution` remain trusted lifecycle operations and are not model-authored permissions.

`working_directory` defaults to `None`, selecting the source's configured default, validated against the opened execution. An explicit override is a canonical absolute provider-local path. It contains no NUL, repeated separator, trailing separator other than `/`, or `.`/`..` segment.

`provider_root` is the canonical absolute Provider path represented by the aggregate route root; it defaults to `/`. Absolute aggregate suffixes append to this path, and returned Provider paths strip it before aggregate projection. Relative paths still start from `working_directory`, independently of this mapping. A Host-path-preserving envd mount sets both `mount_path` and `provider_root` to the captured native root; a whole-Device virtual route retains `provider_root="/"`. This is file routing, not Session configuration or shell confinement.

`mount_path` is an optional Host-selected root in the aggregate and model-facing path space. It does not change the Provider's root or filesystem authority. When present, it is a canonical absolute POSIX, Windows drive, or UNC path written with `/` separators; Windows matching is case-insensitive. A root contains no NUL, empty interior segment, or `.`/`..` segment and has no trailing separator except an absolute filesystem root. The Harness keeps Provider operations in their provider-local `/` namespace and translates at the aggregate boundary.

`ExecutableAgent.run()` and `stream()` accept:

```python
run(
    input=None,
    *,
    environment: EnvironmentSource | EnvironmentMount | None = None,
    environments: Mapping[str, EnvironmentSource | EnvironmentMount] | None = None,
    default_environment: str | None = None,
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
6. An empty mapping, invalid mount name or policy, equal aggregate route owned by different mounts, or conflict with `RunBindings` fails before opening connections. Each activated mount owns a distinct `EnvironmentExecution` even when `EnvironmentSource` objects select the same target.
7. Omitting all Environment input creates an empty bound facade and exposes no Environment tools.
8. Inputs never accept an `EnvironmentProviderDefinition`, Provider configuration, Provider Resource, attachment, state envelope, or Provider type.

A mount without `mount_path` retains the compatibility routes: every such mount is addressable at `/environment/{name}`, and the current default is also addressable at `/workspace`. Without a default, `/workspace` is unavailable. A mount with `mount_path` is addressable only at that explicit root; the Harness does not also expose `/workspace` or `/environment/{name}` for it. Relative paths still select the explicit alias or current default and begin at that mount's provider-local `working_directory`.

A hosted worker supplies sources from Host-authoritative selection, state, and credentials. Embedded Hosts follow the same boundary. Registration, tool schemas, model-context projection, and continuation export do not invoke `ensure_ready()`. Input production, Skill materialization, an extension, or a file/command operation can be the first use, before any model tool call.

### Explicit Host runtime construction

An advanced Host supplies an explicit runtime through `RunBindings.environment`, including through `RunBindings.embedded(environment=runtime)`. This conflicts with simultaneous `environment` or `environments` arguments. Ordinary callers can omit bindings and pass `EnvironmentSource` or `EnvironmentMount` values directly.

Environment Run Extensions are supplied through the runtime factory's `extensions` argument, not a separate `run()` or `stream()` keyword. They form an ordered finite set of fresh instances for this Run; duplicate extension IDs fail before opening Environment executions.

`create_environment_runtime(mounts=..., default_mount=..., extensions=...)` accepts the same `EnvironmentSource` and `EnvironmentMount` values. A bare source selects the default file/execution ceiling and configured working directory (default `/`), and the ordinary implicit aggregate routes. The explicit runtime selects `default_mount` only when supplied. Run-local `mount()` and `replace()` accept these same values. All entry paths, including runtime construction and Run-local mutation, require sources implementing `ensure_ready()`. Harness owns opening and closing their returned connectors' executions.

There is no separate `bind()`-only Environment input. Host-specific authorization and preparation belong to the source's `ensure_ready()`; execution resource acquisition and cleanup belong to the returned connector and its execution. Source construction and metadata reads acquire no execution resources requiring Harness teardown. The Host releases management clients used during preparation and owns any longer-lived dependencies.

Validation of the complete initial mount set precedes source activation. Each activated mount owns a distinct execution, even when sources return the same connector. An execution cannot be accepted by two mounts or independent Runs; rejection of an already-owned execution never closes its existing scope. Failed opening cleans its own partial acquisition, and subsequent validation failure closes the newly acquired execution. Dynamic `mount()` and `replace()` validate the complete prospective route set before registration; static validation failure leaves the existing mount intact. Cleanup of retired executions follows the [Run-local mount mutation contract](#run-local-mount-mutation).

## Ownership Boundary

| Concern                                                          | Owner                                                            |
| ---------------------------------------------------------------- | ---------------------------------------------------------------- |
| Provider selection, activation, and desired configuration        | Host                                                             |
| Current state, Thread association, retention                     | Host                                                             |
| Fixed-target connection and single-target I/O                    | Environment library                                              |
| Entry metadata correlation                                       | Harness supplies ephemeral values from Host bindings             |
| One Run's mount names, IDs, access, aggregate roots, and routing | Harness                                                          |
| Entered multi-mount facade                                       | Harness-internal bound aggregate                                 |
| Environment Run Extension protocol and ordering                  | Harness                                                          |
| Extension selection and serializable configuration               | Host                                                             |
| Provider operation execution and local cleanup                   | EnvironmentExecution                                             |
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
| Mount ID                 | Opaque Harness-generated mount scope identity           | Harness integration and its bound artifacts      |
| Provider key             | Provider implementation discriminator                   | Trusted Host and provider integration            |
| Provider target identity | Provider-owned state data such as a Docker container ID | Trusted Host/provider state; never model context |
| Provider generation      | Entered operation fence when a backend exposes one      | Trusted operation and stale-handle checks        |
| Run ID                   | One process-local logical Harness Run                   | Host and entry correlation                       |

A replacement keeps the mount name and allocates a fresh mount ID. An old path, process handle, output cursor, or operation receipt never retargets to the replacement.

Harness-bound immutable values include:

- `EnvironmentPath(mount_id, path)`;
- `EnvironmentOperationReceipt(mount_id, observed_generation, operation_id, stage, outcome)`;
- `BoundProcessHandle(mount_id, observed_generation, process_id, handle)`;
- retained-output references carrying the same mount and generation fence;
- `EnvironmentSnapshot(mounts, default_mount)`;
- `EnvironmentChange(sequence, kind, name, previous_default, current_default)`.

These wrappers add Harness mount correlation to the shared library's target/execution-scoped results. The shared provider receives no mount ID.

`EnvironmentMountInfo` contains the mount name, provider key, configured descriptor narrowed by available live observations, readiness summary, access ceiling, provider-local default working directory, mapped `provider_root`, and optional aggregate `mount_path`. It omits opaque mount ID and provider target identity from model context.

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

Harness validates the complete initial mapping before registering any source:

1. Normalize sources into mounts and allocate Harness mount identities.
2. Validate inert descriptors, configured directories, permissions, and the complete route set without activating sources or opening executions.
3. Publish one complete initial snapshot, then enter Run Extensions and bind Environment-aware Capabilities before Agent input production. Published mounts may be unactivated.
4. On first use of a mount, apply the [readiness boundary](#readiness); publish live facts after successful opening and validation.
5. Export available portable references without activation.
6. At the terminal fence, reject new activation, exit extensions, drain admitted work, and close acquired executions in reverse opening order.

Initial registration failure publishes no partial mount set. Later activation failure belongs to the selected mount and dependent operation; unrelated mounts remain available. Ephemeral Thread, Run, Agent-instance, and mount correlation stays in Harness wrappers rather than the library API.

The Host fixes logical selection before registration and owns later native allocation and state publication. Harness never restores portable state into a source or live execution. Activation, execution cleanup, continuation export, and management-state publication are independent outcomes; Run failure or cancellation does not authorize target destruction.

## Internal Bound Facade

The entered multi-mount aggregate is process-local and Harness-internal. `AgentContext.environment` exposes its provider-neutral operation facade to trusted Capabilities; it does not expose `EnvironmentConnector` objects, target management, `EnvironmentExecution` close, Provider discovery, or Host state publication.

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

Mount and replacement validate and register a fresh source without forcing activation. Static validation failure leaves the snapshot unchanged. Commit publishes one new snapshot and one `EnvironmentChange`; the retired incarnation drains admitted operations and activation before closing acquired executions. A late activation cannot publish into a replacement. A Host requiring verified readiness before replacement explicitly prepares its candidate before publication. Reopening the same target uses this boundary and invalidates references scoped to the old Environment execution. It does not imply the old Session or its processes survived.

Dynamic mutations are Run-local. They do not discover a Provider, persist desired mounts, mutate Host Thread association, invoke `destroy()`, or change another Run. A durable desired-mount change is a separate Host operation. A Host may reconcile that accepted change into this Run through the controller at a model-request boundary, or supply it before constructing a later Run; Harness owns neither persistence nor authorization of the durable association.

Mutation after the terminal fence fails. A caller that needs an initial mount must supply it before Run entry rather than racing input production.

### Host Changes at Model-Request Boundaries

A Host that supports live additions installs its trusted integration and `DynamicEnvironmentCapability` before execution, including when the bound facade starts empty. At a boundary after the complete active tool batch has settled and before the next root model request is assembled, the integration constructs authorized candidates and applies them through the existing Run-local controller. Published routes, effective standard Toolset schemas and trusted Environment context are projected from the same resulting snapshot for that request. The bounded snapshot is an input preamble or an epilogue after an ordinary complete tool-result batch; no synthetic prompt or enqueue notice is needed to refresh it. Static validation failure preserves existing mounts; successful registration advertises unchecked readiness until first use. Disabled capabilities remain disabled.

A model request or tool batch already in flight keeps its inputs and captured operation scopes. Late changes wait for the next root model-request boundary; nested calls and compaction are not such boundaries. A Run that finishes first need not apply a pending addition. Durable associations, application acknowledgements and execution authority belong to the Host and remain outside `HarnessState`.

## Routing and Operation Fencing

Logical routing is:

- an absolute path under an explicit `mount_path` selects that mount;
- overlapping explicit roots are valid, and the longest complete path-component prefix wins;
- `/workspace/...` selects the current default mount only when that mount omits `mount_path`;
- `/environment/{name}/...` selects a named mount only when it omits `mount_path`;
- an explicit alias selects a named mount for shell/process/port operations;
- an alias and absolute path supplied together must select the same mount;
- relative file paths and command working directories resolve below the selected mount's configured provider-local directory.

Operation paths, unlike configured mount roots and working directories, accept trailing `/`, repeated separators within a path, and `.` segments. The aggregate boundary normalizes these spellings before resource metadata, route selection, and scoped file or command dispatch. POSIX, Windows drive, and UNC anchors retain their meaning; Windows paths use forward slashes. Empty inputs, NUL characters, and `..` segments are rejected with `environment_request_invalid` and actionable `field`, `reason`, and `hint` details. Parent traversal is not collapsed lexically across mount or symbolic-link boundaries. Normalization does not expand filesystem authority or change longest-component-prefix selection.

Equal or Windows-equivalent routes owned by different mounts are invalid. Initial construction rejects them before Provider entry. Dynamic mount, replacement, and default changes validate the prospective complete route set before source registration or publication. An overlap at different path depths is not a conflict because component-prefix routing remains deterministic.

Invocation authorization covers the tool operation and arguments. Resource metadata describes the Environment when it was resolved, not a reserved backend for later dispatch. After policy, review, credential, or approval waits, a new routed operation selects the current mount and checks its current permitted actions at the dispatch/readiness boundary. Replacement or default-route changes during those waits do not by themselves invalidate the invocation. A stale explicit selector still fails rather than granting access to another resource.

Each provider call holds an operation lease. Replacement and unmount stop new routing immediately and retain the old adapter until leases and tracked handles release. Process and retained-output operations route by captured identity, not by mount name.

Compound file operations capture exact source and destination incarnations when execution starts, before I/O. Cross-mount copy never re-resolves one endpoint after the other begins. Document conversion keeps one file scope across source reads, converter I/O, and output publication; downloads keep one destination scope across network I/O and writes. Replacement can drain an admitted scope against its captured Provider, but cannot redirect a later step to the new default or replacement. Scope selection is execution-local and is released on success, failure, or cancellation; metadata resolution never populates it.

## Readiness

A published mount starts unactivated with configured capabilities and no execution readiness evidence. Harness groups requirements by mount incarnation and rejects unsupported or forbidden actions before Host preparation. Routing and static description alone do not require activation; filesystem, command, port, process, and computer access do.

On first use, Harness calls the source's `ensure_ready()`, opens the returned connector, validates live identity, descriptor, required directory, and effective actions, then publishes that execution. Concurrent first uses share one activation/opening result per mount incarnation. Inline children borrow it; independent Runs open their own scopes. Live capabilities may narrow configured support, never expand the admitted ceiling. Incompatible identity or directory evidence fails closed before the requested operation dispatches.

Successful activation is retained for the mount's lifetime. Later readiness checks use `execution.check_ready()` for required families without repeating Host preparation. An activation error or cancellation is retained for that incarnation; retry requires explicit Host mount replacement or a new Run. A failed source may have completed management effects: the Host owns reconciliation and state publication even when no execution opens.

Cancelling one waiter does not cancel activation needed by other admitted waiters. Run cancellation, unmount, and replacement fence new use and settle in-flight activation before releasing its resources. The opener cleans partial acquisition; Harness closes acquired but unpublished executions. Late completion cannot revive a retired mount or escape cleanup. The Host preserves dispatched management outcomes, including unknown outcomes, independently of local waits. Cleanup never implicitly stops or destroys a target.

A failed live check returns a typed bounded error. A trusted Host may supply a fresh source through mount replacement after required management. Library `open()`, `check_ready()`, and transport reconnection remain management-free. Backend-confirmed reattachment within the same valid execution may preserve references; a new execution requires fresh incarnation checks. Reconnection coordinates with admitted operations and close, and uncertain side effects are never replayed automatically.

## Model Context Projection

At each input boundary, Environment projects one bounded trusted block:

```text
Current Environment mounts (trusted dynamic context):
{"default_mount":"workspace","mounts":[...],"truncated":false}
```

Each projected mount contains only name, effective aggregate root, effective operations, readiness summary, availability, and an optional availability reason. An unactivated mount exposes configured operations intersected with its ceiling and explicitly reports unchecked readiness; it is not presented as ready or failed. Projection and tool composition never activate it. First use may narrow operations or report activation failure. `operations` maps each effective operation family to sorted action suffixes from the existing action catalog; for example, `{"computer":["observe"]}` distinguishes observation-only access from `{"computer":["observe","type_text"]}`. State actions are omitted. These are permitted Provider actions, not model tool names; Host tool filtering can further narrow the exposed tools. Readiness does not grant permission. There is no mount-wide `read_only` flag: file-write access does not describe shell or desktop effects. This model-context representation replaces the historical family-only list and file-derived `read_only` flag, without changing Provider descriptors, persisted state, or authorization values; historical overlays retain their original bytes.

The root is the explicit `mount_path` when configured, otherwise the preferred compatibility alias. The projection excludes mount IDs, target IDs, provider-local paths, state payload, credentials, native handles, and lifecycle administration. It does not infer OS, shell dialect, or device identity from a mount name or root.

When Environment tools are exposed and selection requires multiple mounts or an explicit selector because no default exists, their dynamic composition contributes one stable routing instruction block, governed by the ordinary Toolset-instruction switch. Exactly one mount selected as the default needs no routing instruction block. Composition reevaluates this condition at model-step boundaries, including transitions back to a single default mount. The bounded current-mount projection remains present for single and empty Environments: roots, effective permissions, availability, and removal of previous mounts are facts, not a routing tutorial.

The routing block explains latest-snapshot precedence, exact mount selection, per-mount permissions, path routing, and error recovery without embedding current mount values in instructions or schemas. Selector semantics live in tool parameter descriptions; operation-specific Toolsets own guidance such as observation-reference lifetime, foreground focus, and uncertain effects regardless of mount count. Selection and permission failures retain their existing codes and include bounded actionable details through the public error projection; internal exception text is not model guidance. Missing-default, unknown-alias, alias/path-conflict, and denied-action failures identify the reason and correction without leaking native identities or suggesting an authority bypass.

At each eligible model-request boundary, `DynamicEnvironmentCapability` refreshes the effective tool surface and Environment projects the current bounded mount snapshot. A Host that publishes mounts at this boundary orders publication before projection so that the same request receives the current tools and trusted Environment context. Committed changes are independently observed through the typed `environment_changed` lifecycle payload owned by [Events and Usage](12-events-observability-and-usage.md). Harness does not add a synthetic user prompt or enqueue refresh notice; nested same-Agent compaction cannot consume the outer Run's change journal reader.

The model-facing Toolset is standard:

- `view` requires text-read on one mount or both stat and byte-read on one mount; `ls`, `glob`, and `grep` independently require list, query, and text-search;
- file-mutation actions add `write`, `edit`, `multi_edit`, `mkdir`, `move`, `copy`, and `delete` as applicable;
- command execution adds `shell_exec`;
- process actions independently add `shell_info`, `shell_wait`, `shell_input`, and `shell_signal` when their corresponding effective actions are present;
- no model-facing `background` flag, `shell_status`, or `shell_kill` exists;
- an empty Environment exposes no Environment tools.

The file tool surface exposes a tool when at least one mount supports a valid argument branch. Mutation-only mounts do not require an unrelated read action. Write and empty-old-string edit branches require text-write; existing edits require byte-read plus text-write, not patch-text. A captured mount root is not recreated, including explicit roots without a default mount. Parent creation additionally requires mkdir only when the requested path makes the tool call mkdir, even if that parent already exists. Mkdir, move, and delete require their corresponding actions. Copy requires copy-source and copy-destination, possibly on different mounts; internal streaming does not add separate byte-read/write requirements. Its `overwrite` flag permits replacing an existing regular file but also allows creating an absent destination, consistently with the Provider `copy(replace=True)` contract. With the flag false, an existing destination is a conflict and remains unchanged. Other same-mount requirements cannot be assembled by combining partial actions from different mounts. Tool instructions follow the exposed names. Every call re-authorizes the exact selected current mount incarnation and fails without provider effects when that mount lacks an action required by the requested operation. A foreground-only mount keeps completion-only `shell_exec`; a process-capable mount uses the same name with automatic bounded yield.

After Environment execution replacement or changed availability, Harness derives the next model-request context and tool selection from the resulting current snapshot. No stale process reference or readiness observation crosses an Environment execution incarnation. Changing the backing target requires an explicit Host selection and mount replacement with a new source; a failed operation is not replayed against the new target.

## Portable Environment State

`HarnessState` stores provider states directly:

```python
class HarnessState(BaseModel):
    # other fields omitted
    environment_states: Mapping[str, EnvironmentState] = {}
```

There is no `EnvironmentMapState` or `EnvironmentMountState` type.

Keys are Harness mount names. Values are imported `EnvironmentState` envelopes. Singular Environment input uses `workspace`. A mount whose current source or execution has `state=None` is omitted; Direct Local and Local Envd are normally stateless.

Export rules:

1. capture one complete current mount-set observation under the aggregate operation fence;
2. read each activated execution's detached `state` reference, or the unactivated source's current detached reference, without activation, opening, refreshing, or waiting for in-flight preparation;
3. validate the state envelope shape, provider-key consistency, non-blank state-version identifiers, canonical JSON, and Host-admitted size bounds; exact codec compatibility remains Provider-owned;
4. omit `None` values;
5. fail the complete export on cancellation or an Environment execution contract violation rather than silently dropping a stateful mount.

The mapping contains no default mount, desired mount definition, access policy, working directory, aggregate mount path, mount ID, provider generation, credential, handle, lease, pending mutation, change sequence, Host Thread association, or retention policy.

Harness does not use this mapping to select or authorize targets. A Host supplies authorized sources before Run entry. For managed Environments, Host current state wins, including authoritative `None`, and suppresses stale portable fallback. A Host may adopt the mapping only through an explicit unmanaged/import flow where no Host authority exists.

State export is a continuation observation, not durable management publication. Management operations return observed state to the Host independently of a Run checkpoint. Environment execution only retains its fixed target reference; using it does not require a management-state writeback at Run exit.

## File Surface

The Provider package owns the async single-Environment file contract. Harness routes it across mounts and provides bounded provider-neutral operations for stat, listing, byte/text reads, streaming writes, patching, directory creation, move, copy, removal, glob/query, and text search.

Every mutation returns a typed receipt. Reads and listings carry explicit offsets or continuation. Text reads report truncated lines. Search and glob expose bounded pages with deterministic ordering. Model-facing `glob.pattern` and `grep.include` use the [EIP path-glob contract](../a13n-envd/04-resource-operations.md#filefind), including bounded brace alternatives. `grep.root` accepts a directory or a regular file. A directory is searched recursively; an explicit file searches only itself, bypasses hidden-name and Git-ignore discovery filters, and applies `include` to the requested basename. Explicit contained file symlinks are permitted and results retain the selected logical path; traversal still does not follow descendant symlinks. Missing or inaccessible explicit roots fail rather than becoming zero matches, and special files are rejected without reading them. Single-file searches retain the same text, byte, per-file match, context, and paging limits. `glob.root` and listing paths remain directories. `grep` exposes `regex=true` and `case_sensitive=true` defaults, with explicit literal and case-insensitive modes. Zero matches retain `ok: true`; invalid patterns retain the stable failure envelope and identify `error.details.field`, `reason`, and a corrective `hint`. Paging preserves filters and uses returned continuation offsets; it assumes a stable filesystem. Portable regex uses literals, classes, groups, alternation, anchors, and quantifiers. Direct Local/E2B use Python `re`, while envd uses Rust `regex`; lookaround, backreferences, engine-specific extensions, and Unicode edge cases are not cross-provider guarantees.

Model-facing text `view` treats line bounds as ceilings. After applying any skill or external file-view profile, it fills the requested line count through bounded provider reads until EOF or its actual UTF-8 page budget, rather than reducing the total row request or returning `environment_too_large` because of the product of valid line bounds. A smaller page remains a successful result. Final model-output budgeting preserves complete leading source lines and supplies `next_line_offset` and explicit continuation instructions whenever later lines remain, including when the provider page reached EOF. `lines_read` and the continuation offset describe only the source rows actually shown. If even one source line cannot fit the model-output budget, the result shows a prefix of that single line, marks it in `truncated_lines`, and attempts to save the fuller provider page through ordinary output disclosure; later source lines remain available at the next offset. Source-line clipping is separate: non-empty `truncated_lines` carries an incomplete-content disclosure and guidance for rereading the affected one-based source lines with a larger line bound. `has_more` describes later lines, not omitted suffixes. Provider failures such as missing paths, denied access, and invalid text retain their error semantics.

Direct Local confines native paths beneath its configured root and keeps blocking filesystem work off the event loop. It makes no sandbox claim. EIP-backed Providers perform all Agent file operations through EIP.

Provider file arguments remain provider-local after routing. Paths returned by stat, list, glob, query, and search are reprojected beneath the selected aggregate root, so a returned path can be reused in a later aggregate operation. Compound file scopes capture both mount incarnation and aggregate root; replacement or a changed route cannot retarget the operation.

Tool results use bounded disclosures and stable model-safe errors. An absolute path outside every available mount returns `environment_selection_invalid` with an actionable routing diagnostic: the path was not checked for existence, and relocating files or worktrees is not a routing remedy. Any Shell alternative remains subject to the selected Environment's authority and the user's task. A failed file lookup within a selected mount returns `environment_not_found` with path-verification guidance, rather than implying an unavailable route or global Host absence. Structured safe hints are preserved without exposing raw provider exceptions. Internal mount identity, generation, state, and receipts are not model-editable arguments.

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

`shell_info(process_id=...)` inspects that reference without waiting or reading output. The reference selects its exact bound mount; a conflicting explicit alias fails. List and inspect require separate actions. A Provider can support inspection without listing. When entered mounts permit inspection but none permit listing, the model-facing `shell_info` schema requires `process_id` and omits the discovery `limit` argument. With listing available, discovery still checks the selected/default mount, not a union of permissions across mounts. Neither query refreshes, resets or reconnects output.

The `alias` argument selects an existing public mount name from the active Environment context; it never labels a command or process. Schemas and model guidance make this distinction explicit. An omitted alias uses the default mount unless an absolute command `cwd` or an existing process reference selects its own mount. Unknown or conflicting selections remain errors; Harness does not silently retry on another mount.

`shell_wait` waits boundedly and reads available output at explicit caller offsets. Zero is a poll. Output provenance and evidence follow the [Provider observation contract](../a13n-environment/01-environment-contract.md#process-and-output-observations). Returned `requested_offset`, `start_offset`, `next_offset`, available bounds and `omitted_before_bytes` describe this page only. `next_offset` advances only across returned bytes. Unknown producer totals are null rather than buffer lengths. Repeating offsets is valid; Harness has no hidden unread cursor.

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

No model reference, log offset, status mirror or watcher enters Harness Capability state. Provider `EnvironmentState` remains opaque and owns backend-specific recovery evidence. A later Run starts an empty controller, uses native discovery when supported and rejects references copied from old messages; no command is replayed automatically. Async subagents retain their independent Host operator.

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

01. Harness accepts Host sources implementing `ensure_ready()` and mount policy; the Host owns Provider selection, activation, and management authority.
02. Each used mount opens a distinct execution; unused mounts open none, and independent Runs never share execution ownership.
03. Initial publication is all-or-nothing, and opening, binding failure, cancellation, replacement, and close have explicit cleanup owners.
04. Effective actions are the intersection of Host ceilings and provider capabilities; desktop access remains explicit.
05. Mount and Environment execution identities prevent stale paths, process references, observations, and cursors from retargeting.
06. Harness owns routing, tools, events, and model-context assembly; providers supply structured Environment execution facts.
07. First-use readiness delegates preparation to the Host; execution opening, live checks, and reconnection never manage targets or replay uncertain operations.
08. Portable state is a direct mount-name mapping of existing `EnvironmentState` values, not authority or a desired mount set.
09. Run cleanup preserves provider-specific process survival and never selects target destruction.
10. Dynamic changes publish through the existing mutation boundary and do not persist Host associations.

## Publishing Live Environment Changes

An unactivated mount has its Harness mount ID and no observed provider generation. After activation, operation fences pair that mount ID with the published provider generation. Activation preserves the mount ID; explicit replacement allocates a new one. Provider readiness publishes one immutable descriptor, effective permission set and operation-facet snapshot together. The aggregate snapshot and change journal update in the same local publication step. A provider recovery that reports a replacement error still publishes its new observation before the next model context projection.

Dispatch rechecks permissions and generation after readiness. Compound background start rechecks the complete start/inspect/wait/read-output/release action set inside the prepared lease before native start; readiness that narrows any required action prevents dispatch rather than falling back or replaying the command. Already dispatched operations retain their captured operation facets and receipt fence. File scopes, process handles and retained outputs from an older generation cannot silently retarget. A new mount ID is required for explicit mount replacement; refreshing the same provider scope advances its observed generation instead.

Standard Environment tools, downloads, and document conversion share one canonical resource mapping. An activated file resource identifier is `mount_id:generation:provider_path` for current policy and observation. Before activation, static resource metadata uses mount identity and configured path without fabricating a native generation or forcing readiness. Dispatch establishes live execution fences before effects. Mount resources retain the incarnation identifier. Mount and command-cwd selection prepare the relevant operation family without requiring a file facet. Resumption resolves resources under current authority; canonical metadata does not attest or authenticate historical approval.

Approval does not reserve a backing target. Hosts whose authorization depends on an exact target must enforce that constraint on the actual execution path, for example through Provider policy or a Host-controlled binding that cannot change during the invocation. An execution-guard callback remains a Host extension, not a built-in target-reservation guarantee. Provider backing evidence remains available for recovery and Host policy. Execution-local generation validation, exact handle identity, Provider draining, and spill cleanup remain independent of approval. No automatic replay is introduced for unknown outcomes.

Download approval covers its destination directory. Document approval covers both the source and its output parent directory, not a speculative generated leaf. Changed paths or tool/argument facts require fresh approval, and current denial still applies. Previously captured approvals using backing- or incarnation-based revisions, and legacy download/document approvals without revision facts, require fresh approval once. Newly captured approvals do not repeat the connection-bound rejection. File selections no longer carry backing identity for automatic approval construction; custom Providers may continue supplying it in their descriptors. Direct non-Environment FileToolset callers acquire no fabricated Environment identity.

## Computer Operations

An optional `computer` operation family exposes Provider `describe`, `observe` and typed `execute` operations through the bound Environment. Exact actions are `environment.computer.describe`, `.observe`, `.click`, `.move`, `.drag`, `.scroll`, `.type_text` and `.press_keys`. Each call rechecks the mount's effective ceiling and readiness; observation-only access does not grant input. Default file/execution ceilings exclude these actions. Direct Local, native Docker and E2B do not advertise this facet; EIP adapters derive it from exact negotiated methods and require both observe and reader-close support for screenshot access.

A `ComputerScreenshot` contains bounded image bytes and a `ComputerObservation` bound to the mount incarnation and observed Provider generation. Pointer requests carry that observation; keyboard/text requests select an explicit alias or current default. Stale observations cannot retarget into a replacement mount or generation. A screenshot reference binds target geometry, not pixel freshness, foreground focus or desktop ownership. Provider close releases observations and temporary media, never a shared GUI application.

`DynamicEnvironmentCapability` includes `ComputerToolset` only for explicitly permitted actions. Its Run-local `obs-` references map to bound observations and are finite; they are not resumable handles. `computer_observe` returns the image directly as Pydantic AI `BinaryContent` alongside readable geometry, a reference, and the source mount alias, rather than a path requiring another model call. `computer_describe` also returns its selected alias. These aliases are provenance, not incarnation fences; pointer dispatch remains bound to the observation. Computer tool descriptions and stable instructions explain that keyboard input does not inherit the previous observation's mount, and missing/stale observation errors direct the model to reobserve the intended mount and reassess the GUI. Same-Run history compaction does not itself invalidate references; a new Run does not restore the observation registry from summary text. A Host may retain an independently governed display copy. Native IDs and images never grant authority to another mount.

Input results preserve effect, cleanup completeness and operation receipt. Partial/unknown outcomes and post-dispatch transport errors are not replay-safe. Tool metadata declares input non-idempotent; neither mount recovery nor image-display failure automatically repeats it. [Envd computer use](../a13n-envd/10-computer-use.md) owns the macOS, Linux X11 and Windows implementations and native coordinate/input bounds. Model-facing partial/unknown results and incomplete cleanup include inspect-before-retry guidance; failed cleanup explicitly warns that input may remain held. A computer mutation whose transport or response fails without dispatch evidence requires reconciliation, not automatic replay in a new Run or Session. Controlled native reason/type pairs receive allowlisted guidance without exposing raw provider messages. Scroll requests preserve an explicit `pixels` (default) or `steps` unit; descriptions advertise supported units, and unsupported units fail without pointer movement. Exact method availability can omit literal text input on X11.
