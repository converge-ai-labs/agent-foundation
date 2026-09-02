# Sessions, Workspace Binding, and Environments

## Design Position

A Session is one local root Thread. It pins one Agent snapshot, one Environment-profile snapshot, and one selected root continuation. It does not own workspace folders. Every submitted message carries a `WorkspaceBinding`, and the App combines that binding with the pinned Environment profile to construct fresh mounts for that root Run.

This separation keeps two independently changing facts independent:

- an Environment profile answers how a folder executes;
- a `WorkspaceBinding` answers which folders this message can use.

Agent UI creates no Workspace resource, ID, revision, table, or lifecycle. A binding is only an ordered local path tuple captured for one Run.

## Session Model

```python
class Session(BaseModel):
    session_id: str
    created_at: datetime
    updated_at: datetime
    title: str | None
    archived_at: datetime | None
    pinned: bool
    agent_snapshot: SnapshotRef
    environment_snapshot: SnapshotRef
    parent_fork: SessionForkRef | None
    continuation: ContinuationRef
```

The selected root `HarnessState.thread_id` is the root Thread identity. Agent UI does not persist another root Run lifecycle model. Current Run status, subscribers, steering queues, and live output remain process-local.

Session creation:

1. captures one accepted configuration;
2. resolves and publishes exact Agent and Environment-profile snapshots;
3. verifies that the profile has one compatible workspace binder;
4. creates and publishes a baseline root `HarnessState` continuation;
5. inserts Session metadata and the selected continuation in one short transaction.

Creation performs no workspace binding and provisions no Environment target. The first submitted message supplies the first binding.

## Workspace Binding

```python
class WorkspaceBinding(BaseModel):
    folders: tuple[Path, ...]
```

The value is conceptual App input, not a serialized configuration or durable record. Admission normalizes it as follows:

1. require at least one folder;
2. expand surface-supported user syntax before App validation;
3. resolve each path to a normalized absolute existing directory;
4. remove exact duplicates while preserving first occurrence;
5. reject inaccessible paths, non-directories, and paths that cannot be represented by the selected binder.

The first folder is the Run working directory and default mount `workspace`. Later folders become `workspace-2`, `workspace-3`, and so on. Ordering therefore affects behavior and belongs to the submitted message.

CLI interactive and one-shot submission default to the current process directory. A caller can supply several explicit folders. WebUI submits its selected folder list through the same App command.

The binding is authoritative for one admitted Run. A later message to the same Session can use a different list. Steering an active Run cannot change its captured binding. Root-only Session tools inherit the calling root Run's exact binding and cannot inject arbitrary Host paths through model arguments.

## Environment Profiles and Binders

An Environment profile selects one execution strategy:

| Kind        | Binder behavior                                                                                                 |
| ----------- | --------------------------------------------------------------------------------------------------------------- |
| `native`    | Creates a Direct Local adapter rooted at each bound folder with unrestricted Host execution semantics           |
| `local_eip` | Creates a Local EIP adapter rooted at each bound folder through the package-selected local `agent-envd` runtime |
| `provider`  | Invokes the explicitly selected trusted extension binder for each folder                                        |

Native is the omission default and an explicit full-control choice. Local EIP is the explicit built-in sandbox profile and requires its isolation contract; failure never falls back to Native. Docker, E2B, and other Providers are extension entries, not built-in Agent UI kinds.

The workspace-binder boundary is:

```python
class EnvironmentWorkspaceBinder(Protocol):
    @property
    def key(self) -> str: ...

    async def bind(
        self,
        *,
        profile: ResolvedEnvironmentProfile,
        folder: Path,
        state: EnvironmentState | None,
        runtime: ProviderRuntime,
    ) -> Environment: ...
```

`bind()` returns a fresh inert adapter and performs no external I/O. The binder key is stable trusted provenance included in the Environment-profile snapshot. A custom Provider either supplies a compatible local-folder binder or rejects folder binding explicitly during profile validation.

The profile and binder contain no current workspace list. A binder receives one folder at a time so Agent UI can construct a deterministic Harness mount set without adding a product-level multi-workspace resource.

## Host-authoritative Environment State

Native and Local EIP use the submitted folders directly and retain no re-entry state across adapter generations. A stateful extension Provider can return portable `EnvironmentState` for one bound folder.

Agent UI uses one complete private binding key everywhere:

```text
Session ID
+ pinned Environment-profile digest
+ stable binder key
+ normalized bound folder
```

State lookup, supplied-state comparison, publication, cleanup, destroy, and prune use exactly that key. The key prevents accidental cross-Session or cross-profile target sharing. It does not create a Workspace identity.

For each key, the stored current value is authoritative `EnvironmentState | None`. Existing authoritative `None` does not permit fallback from root or child `HarnessState.environment_states`. Continuation state is a portable observation only and can be adopted only through an explicit unmanaged import operation.

State payloads are bounded and validated against Provider key, version, codec, profile digest, and binder provenance before adapter construction. A missing codec or incompatible state fails admission rather than silently creating a new target.

## Root Run Environment Flow

For one submitted root message, the App:

01. captures and validates the `WorkspaceBinding`;
02. loads the Session's pinned Environment profile;
03. resolves the exact binder and fresh Provider runtime collaborators;
04. computes the complete binding key for each folder;
05. loads the current Host-authoritative state for each key;
06. constructs one fresh adapter per folder without I/O and retains the supplied state for later comparison;
07. creates the deterministic Harness mount set and starts the Run;
08. lets Harness enter adapters, execute operations, stop Run-owned processes, and close adapters non-destructively;
09. performs bounded fallback close for an adapter not already closed;
10. reads each adapter's final infallible cached state;
11. compare-and-selects only values different from the supplied state under the same complete key.

Cleanup precedes final state reading. Environment-state publication occurs even after Run failure or cancellation when a changed final value is known. Equal state performs no write. If another App process changed the current reference after admission, selection fails explicitly and preserves that newer state; Agent UI does not claim the Provider's external effects were rolled back.

Continuation selection and Environment-state publication are independent. One can succeed while the other fails, and the App reports both outcomes without rolling back an already committed fact.

## Async Child Environments

Every async child segment inherits the exact `WorkspaceBinding` and pinned Environment profile captured by the parent segment that admitted it. It receives fresh Provider runtime collaborators and fresh adapters loaded from current state under the same complete Session-scoped keys. It never borrows the parent's entered facade.

A descendant admitted by that child inherits the child's same captured binding. Linked `resume_subagent` runs from a later parent Run and captures that parent Run's binding for the new segment; it does not restore stale adapter objects or hidden paths from the prior child checkpoint.

Inline Harness execution, where used by code-first Harness hosts, follows the Harness borrowed-facade contract. Agent UI-authored rosters use the persisted async operator.

## Local EIP Runtime

Agent UI releases select one exact `agent-envd` release manifest and target hashes. Local EIP resolves only that managed executable or one explicit validated local override. It does not search ambient `PATH` or download a binary for Native execution.

Before first Local EIP use, Agent UI verifies the executable identity, required isolation, and EIP compatibility. The executable cache carries no Session, folder, or Environment authority. A daemon, transport, process handle, and output cursor are process-local to the fresh adapter and never enter `EnvironmentState` or Session storage.

## Forking and Profile Changes

Changing Agent or Environment-profile snapshots creates a fork:

- the source root history transfers through compatible `HarnessState.fork()` or an explicit readable-history seed;
- the fork receives a new root Thread and Session identity;
- it pins the selected current Agent and Environment-profile snapshots;
- it starts with no Host-authoritative state associations;
- it copies no workspace binding, Provider target, child Thread, live Run, credential, adapter, process, or subscriber.

A caller supplies a new binding when submitting the first message to the fork. Existing Sessions are never mutated in place by configuration reload.

## Session Tools and Workspace Authority

Model-visible root Session tools can list and inspect Sessions, start or continue work in another Session, and steer an active Run. They call App commands and receive detached bounded projections.

`run_session` inherits the caller's captured `WorkspaceBinding`; its arguments contain no local paths. `steer_session` targets an already active Run and therefore keeps that Run's existing binding. Same-active-Session recursive run or steer calls are rejected.

## Deletion, Destroy, and Prune

Session deletion:

1. prevents new root and child admissions for the Session;
2. cancels active process-local root and child Runs owned by the App;
3. invokes explicit Provider `destroy()` for each retained stateful binding through a fresh adapter;
4. publishes the resulting cached state or cleanup failure;
5. removes Session, child Thread, and current-state indexes.

Destroy is idempotent where Provider evidence permits. Unknown outcome retains cleanup bookkeeping for explicit retry. Agent UI never deletes a user's Native or Local EIP source folder, Docker bind source, external named volume, or unrelated Provider target.

Explicit prune handles displaced or deleted-Session Provider states that remain safely attributable to Agent UI. Agent UI adds no global target scanner, distributed lock, or exactly-once destruction guarantee.

## Failure Semantics

| Failure                             | Outcome                                                                     |
| ----------------------------------- | --------------------------------------------------------------------------- |
| Invalid folder list                 | Message rejected before Run admission                                       |
| Binder missing or incompatible      | Profile or Run rejected; no implicit Provider substitution                  |
| Current state missing or invalid    | Admission fails explicitly; stale continuation state is not adopted         |
| Adapter entry fails                 | Harness reports the Run failure; known changed cached state still publishes |
| Cleanup or close fails              | Failure is reported; final cached state is still compared when available    |
| State publication fails             | Continuation selection proceeds independently                               |
| Root continuation publication fails | Changed Environment state remains published                                 |
| Destroy outcome unknown             | Cleanup bookkeeping remains for retry or prune                              |

## Invariants

01. A Session pins Agent and Environment-profile snapshots but no workspace folders.
02. Every message carries one ordered `WorkspaceBinding`.
03. The first folder is cwd/default mount; later folders preserve order.
04. Native and Local EIP are the only built-in profiles; extensions use trusted binders.
05. Every independent root or async child Run receives fresh adapters.
06. Host state is keyed by Session, profile digest, binder key, and normalized folder.
07. Cleanup precedes final cached-state comparison and changed-only publication.
08. Harness close is non-destructive; Agent UI alone decides destroy and prune.
09. Steering cannot change an active Run's binding.
10. Workspace folders never become a Session, Agent, or Environment-profile resource.
