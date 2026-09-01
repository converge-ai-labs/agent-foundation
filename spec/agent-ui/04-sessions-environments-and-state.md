# Sessions, Environments, and State

## Design Position

An Agent UI Session is one local interaction history. It pins one resolved Agent snapshot, one resolved Environment snapshot, one exact Skill-exposure map, and one latest complete continuation bundle. It supports create, list, resume, fork, archive, pin, and delete without becoming a durable workflow.

A live Run, input, partial output, model client, Environment adapters, Runner-local async work, and subscriptions are process-local. Restart resumes from the last selected continuation and reconstructs all live authority. Work after that continuation can be lost or repeated.

Agent UI is the Host for Session Environments. It owns current state, root/child Thread association, changed-only publication, explicit cleanup, and prune behavior. Those behaviors use Agent UI's private persistence model and do not define shared Host record or link classes.

## Boundaries

| Concern                   | Owner                                 | Session relationship                                                                 |
| ------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------ |
| Agent composition         | Resolved Agent snapshot               | Session pins one immutable revision graph                                            |
| Desired Environment       | Resolved Environment snapshot         | Session pins exact Provider configuration, mount names, access, and provision policy |
| Current Environment state | Agent UI Host                         | Latest authoritative `EnvironmentState` or authoritative `None`                      |
| Continuation              | Harness `HarnessState`                | Stored without interpreting private Capability state                                 |
| Suspended requests        | Harness `DeferredToolRequests`        | Stored beside `HarnessState` in the selected continuation bundle                     |
| Current mount set         | Harness process-local bound facade    | Fresh for every independent root or child Run                                        |
| Provider operations       | Fresh entered `Environment` in Runner | Constructed from Host-selected state before each Run                                 |
| Backing-target lifecycle  | Host policy executed by Runner        | Warmup, explicit destroy, and prune never follow Harness close automatically         |
| Presentation              | Harness history plus live AG-UI       | Continuation supplies retained history; live output is best effort                   |

## Environment Definition

```python
class EnvironmentDefinitionDocument(BaseModel):
    schema_version: str
    environment_id: str
    display_name: str
    description: str | None
    mounts: tuple[EnvironmentMountDefinition, ...]
    default_mount: str | None
    provision: Literal["on_first_run", "eager"] = "on_first_run"


class EnvironmentMountDefinition(BaseModel):
    mount_name: str
    model_alias: str
    provider: EnvironmentProviderSpec
    access: Literal["read_only", "read_write", "full"] = "full"
```

`mount_name` is Agent UI's stable desired-mount identity. `model_alias` is the Harness mount name. Access selects a Harness ceiling and is narrowed by the entered provider descriptor. A Run translates each selected desired mount into one fresh `EnvironmentMount` containing an already constructed adapter.

The definition contains no credential, current provider target ID, endpoint resolved at runtime, live Provider/Environment/client, `EnvironmentState`, Harness mount ID, or `HarnessState`. Editing desired configuration or access creates a new immutable revision. Existing Sessions remain pinned until the user explicitly creates or forks a Session against another snapshot.

`on_first_run` lets state absence create the provider target during first entry. `eager` asks the Host to send a detached warmup command to an authorized Runner after Session creation. The Runner constructs the fresh Provider adapter, invokes `warmup()`, reads its cached state, closes it, and returns detached state and outcome. Eager warmup state publication follows the same changed-only Host rules as Run finalization and does not create a Harness continuation.

Agent UI's data root is internal storage and cannot be selected as an executable Session mount. Host-native roots are normalized and must not overlap the data root. Direct Local remains an explicit trusted local execution choice, not a sandbox.

## Session Record

```python
class LocalSession(BaseModel):
    session_id: str
    created_at: datetime
    updated_at: datetime
    title: str | None
    archived_at: datetime | None
    pinned: bool
    agent_snapshot: SnapshotReference
    environment_snapshot: SnapshotReference
    skill_selections: tuple[SessionAgentSkillSelection, ...]
    parent_fork: SessionForkRef | None
    continuation: ContinuationRef
```

The selected continuation supplies the root Thread identity. Agent UI does not need durable accepted/running/interrupted records to explain process-local root work. Current Run status is a process-memory observation exposed by the owning Host.

Session display fields and continuation selection use ordinary last-write-wins. One Host serializes its own Runs for a Session, but separate local processes can still race and the last committed write wins.

Session creation:

1. resolves and validates exact Agent and Environment snapshots;
2. creates `HarnessState.new()`;
3. publishes one baseline continuation;
4. inserts Session metadata and continuation reference in one short transaction;
5. initializes private Environment associations with authoritative no-state values;
6. performs eager warmup when selected and publishes any changed state.

Warmup failure is reported as Environment unavailability. It does not require a Session workflow state machine.

## Continuation Model

```python
class ContinuationRef(BaseModel):
    object_digest: str
```

The referenced `StoredSessionContinuation` contains the Harness release, complete `HarnessState`, optional exact `DeferredToolRequests`, and creation time. Agent UI does not duplicate Thread, suspension, or codec fields in SQLite.

The Host attempts to save every accepted complete state boundary:

- completed root Run;
- suspended root Run;
- failed or cancelled result only when Harness supplies a complete state that Agent UI intentionally accepts;
- completed child output only after it reaches a parent continuation through the selected subagent contract.

Partial messages, live AG-UI events, provider observations, and Environment files never synthesize a continuation.

`HarnessState.environment_states` is a portable observation. For an Agent UI-managed Session, current Host state wins, including authoritative `None`; the continuation mapping never overwrites that authority. Agent UI adopts portable state only during an explicit unmanaged/import operation in which no managed association exists.

## Host-private Environment State

Agent UI persists one private current-state association per desired Session mount. Conceptually it retains:

- Session and desired mount identity;
- selected Provider key and exact configuration snapshot/digest;
- current authoritative `EnvironmentState | None`;
- availability and last bounded failure observation;
- update time;
- enough private cleanup bookkeeping to retry or prune an unused/orphaned backing target.

This list describes behavior, not a shared schema. The implementation can use rows and immutable state objects without exporting a canonical `SessionEnvironmentResource`, Thread-link, or prune-candidate model.

State is sensitive Host data even when not a bearer credential. Provider payloads are immutable objects referenced by digest or stored directly under bounded size limits. Credentials, clients, PIDs, EIP sessions, Docker objects, live adapters, and bootstrap secrets are never persisted in the state envelope.

For each managed mount, state selection is:

1. load the Host's current association;
2. treat an existing association with `None` as authoritative no-state;
3. treat deleted or explicitly absent association as suppressing stale continuation fallback;
4. use continuation state only in a separate explicit import path;
5. validate Provider key, state version, JSON codec, configuration compatibility, and size before adapter construction.

## Run and Resume

A root Run proceeds:

01. acquire the current Host's Session Run lock;
02. load the selected continuation and current Host Environment associations;
03. reconstruct trusted Providers, exact configuration, and fresh runtime collaborators;
04. construct one fresh `Environment` per desired mount from current Host state without I/O;
05. dispatch the immutable inputs and adapters to the selected Runner;
06. run one Harness stream and forward live presentation best effort;
07. in unconditional finalization, inspect each adapter's latest known `dump_state()` and close any remaining local resources;
08. publish only changed Environment state, even after execution, cancellation, checkpoint, or close failure;
09. on an accepted complete/suspended result, publish and select one continuation bundle;
10. release the Session lock.

State publication and continuation publication are independent outcomes. A changed Environment value is not discarded merely because Harness state export or continuation storage failed. A continuation can be selected even when later local cleanup fails; the response reports all material outcomes.

Equal state performs no write. Changed state is ordinary last-write-wins. An older Run that dumps its originally supplied value cannot overwrite a concurrent replacement because equality causes no write. A genuinely changed later value can win and orphan a displaced provider target; Agent UI's explicit prune behavior handles that case.

Cancellation does not prove rollback. If Harness returns an accepted continuation, Agent UI may select it; otherwise the previous continuation remains current. Known changed Environment state is still published from finalization.

## Fresh Run Construction

For every independent root or async child Run, the selected Runner receives:

1. exact pinned desired Environment definitions;
2. Host-selected current state for each association;
3. fresh process-local Provider runtime collaborators and credentials;
4. one newly constructed Environment adapter per mount;
5. lightweight Harness `EnvironmentMount` values with access and working-directory policy.

Harness enters the adapters atomically, routes operations, snapshots non-`None` state into `HarnessState.environment_states`, and closes adapters non-destructively. Provider selection, state adoption, and `destroy()` remain outside Harness.

Inline child execution borrows the parent's already entered Harness facade. Async child Threads normally inherit the parent's Host Environment association but receive fresh adapters and independent Run correlation. A child policy can select no Environment or a dedicated Host association. Agent UI owns that selection; Harness does not infer it from continuation state.

The built-in Local Sandbox uses `a13n.local-envd`. It constructs a fresh daemon per independent Run, stores no PID in Environment state, and never falls back to Direct Local. Docker state includes the exact container ID. E2B state includes the exact sandbox identity.

## Forking

A fork loads one selected source continuation and creates a new Session:

- when the Agent snapshot is compatible, use `HarnessState.fork()`;
- when only readable history transfers, seed a fresh state from that history;
- select the requested Environment snapshot independently;
- create new Host-private Environment associations with authoritative no-state values rather than copying live adapters, current state, or authority;
- start the fork with empty `environment_states`; importing a backing target is a separate explicit non-fork operation;
- publish a baseline continuation and source lineage.

Forking does not copy current Environment state, live adapters, active Runs, AG-UI streams, local tasks, credentials, PIDs, or native handles. `HarnessState.fork()` changes the root Thread ID. Async subagent authority remains Host-owned and rejects source-Thread references; background processes have no Harness continuation mapping to copy.

## Queries and Process-local Work

Session list/search uses bounded metadata such as title, pinned/archive state, update time, Agent identity, and project association. Opening a Session projects message history from the selected continuation.

A model-visible Session browsing Capability can read bounded current projection. It cannot switch Sessions, select continuations, submit input, control Runs, inspect private Environment state, or access another Session by arbitrary ID.

Async subagent execution references and process references in messages are selectors, not Environment state or authority. An async reference is resolved only by the same generation operator under the owning Thread. A default process reference is valid only in its source Harness Run and fails in every continuation. Neither contains a raw PID, adapter, provider target state, task, callback, output buffer, or storage authority.

## Cleanup and Prune

Session deletion first makes the Session unavailable to new Runs, then schedules or performs best-effort explicit destruction for each Host-managed backing target. The Host sends an authorized Runner a detached command containing the exact configuration and last known state. The Runner resolves the trusted Provider and fresh runtime collaborators, constructs a fresh Environment, validates target identity, calls `destroy()`, reads the resulting cached state, closes the adapter, and returns detached state and outcome for Host publication. Harness is not involved, and no Provider or adapter enters the stable Host process.

Cleanup is idempotent where provider evidence permits. Unknown destroy outcome retains enough private state for retry. Agent UI never deletes Direct Local or Local Envd workspaces, Docker bind sources, external named volumes, or unrelated provider targets.

Agent UI periodically or explicitly prunes:

- targets from deleted Sessions whose destruction did not complete;
- displaced states orphaned by last-write-wins publication;
- provider-discoverable targets carrying exact Agent UI correlations but no current authorized association.

Grace periods, retry timing, and private candidate representation are implementation policy. There is no shared Host prune model, global lock, or exactly-once guarantee.

## Invariants

01. A Session pins exact Agent and Environment desired snapshots plus one selected continuation.
02. Agent UI current Environment state is independent from `HarnessState` and wins for managed mounts.
03. Every independent root or async child Run receives fresh Environment adapters.
04. Inline children borrow the parent's entered facade.
05. State is selected before adapter entry.
06. `HarnessState.environment_states` is portable observation, not managed Host authority.
07. Entry and close values are process-local and never persisted.
08. Harness close is non-destructive; only Agent UI policy invokes destroy.
09. Known changed state publishes from unconditional finalization after every Run outcome.
10. Equal state performs no write; changed state is last-write-wins.
11. Orphans are accepted and handled by explicit prune behavior.
12. Host persistence shape remains Agent UI-private.
13. Credentials, PIDs, live clients, adapters, and native handles never enter continuation or Environment state.
14. Session continuation publication and Environment state publication are independent outcomes.
