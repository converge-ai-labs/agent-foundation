# Sessions, Environments, and State

## Design Position

An Agent UI Session is one local interaction history. It pins one resolved Agent snapshot, one resolved Environment snapshot, one exact Skill-exposure map, and one latest complete continuation bundle. It supports create, list, resume, fork, archive, pin, and delete without becoming a durable workflow.

A live Run, its input, partial output, model client, provider attachment, `EnvironmentRuntime`, Runner-local async-child work, and subscriptions are process-local. Restart resumes the Session from its last successfully selected continuation. Work after that continuation can be lost or repeated.

## Boundaries

| Concern                   | Owner                                   | Session relationship                                                                         |
| ------------------------- | --------------------------------------- | -------------------------------------------------------------------------------------------- |
| Agent composition         | Resolved Agent snapshot                 | Session pins one exact immutable revision graph                                              |
| Desired Environment       | Resolved Environment snapshot           | Session pins exact desired mounts and provisioning policy                                    |
| Continuation              | Harness `HarnessState`                  | Stored in the selected continuation bundle without interpreting private Capability state     |
| Suspended requests        | Harness `DeferredToolRequests`          | Stored beside `HarnessState` in the same selected continuation bundle                        |
| Session selection         | Agent UI SQLite                         | Stores metadata and one current continuation reference                                       |
| Provider resource effects | Runner and Environment Provider package | Host selects detached state needed to reconnect or clean up                                  |
| Current mount set         | Harness `EnvironmentRuntime`            | Fresh and process-local for every Run                                                        |
| Presentation              | Harness message history plus live AG-UI | Continuation supplies retained history; AG-UI is best-effort live output                     |
| Async children            | Selected Runner generation              | Lost with that generation unless their result already reached a selected parent continuation |

## Environment Definition

An Environment definition describes desired mounts and initial provisioning policy:

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

The default provisions on first use and gives each mount `full` access. `read_only` permits model-facing file reads, `read_write` permits all file operations, and `full` permits every Agent-facing Environment capability offered by the Provider. Agent UI defines no idle timer or automatic pause policy. Provider-native idle or auto-stop behavior belongs to validated provider parameters. Session pause is an explicit Host command, and Session deletion requests provider destruction best effort. Agent UI does not retain a configurable cleanup workflow or orphan-resource policy.

Editing a mount's access creates a new immutable Environment revision and therefore a new resolved Environment snapshot. Existing Sessions continue to use their pinned snapshot; Agent UI never changes the access of an active or resumable Session in place. A user selects the new revision by creating or forking a Session through the ordinary snapshot-selection flow.

The definition contains no arbitrary action set, credential, provider resource ID, endpoint resolved at runtime, attachment, live provider object, `EnvironmentRuntime`, or `HarnessState`. Exact Harness `EnvironmentPermissionSet` values are reconstructed only at the trusted Runner boundary from the selected access level and are not ordinary Agent UI configuration.

`mount_name` is the stable desired-mount identity. `model_alias` is the Harness mount name. A Run translates the selected desired definitions into one fresh `EnvironmentRuntime`. Agent UI does not persist or restore that runtime.

Agent UI's data root is internal storage and cannot be selected as an executable Session mount. Existing Host-native roots are normalized before use and must not overlap the data root. This direct overlap check is sufficient; Agent UI does not build a broader filesystem sandbox policy around ordinary local configuration.

## Session Record

The application projection is intentionally small:

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

The selected continuation supplies the root Thread identity. Agent UI does not need durable `accepted`, `running`, `interrupted`, or queue records to explain process-local work. Current Run status is a detached process-memory observation exposed only by the Host that owns it.

Session display fields and the latest continuation reference use normal last-write-wins behavior. One Host serializes its own Runs for a Session, but Agent UI does not coordinate separate local processes or merge divergent continuations.

A Session is created by:

1. resolving and validating the selected Agent and Environment snapshots;
2. creating `HarnessState.new()`;
3. publishing one baseline continuation bundle;
4. inserting Session metadata and its continuation reference in one short transaction;
5. provisioning eager Environment resources when requested, without changing continuation semantics.

A resource provisioning failure is reported by Environment availability. It does not require a separate Session state machine with provisioning, blocked, deleting, and cleanup-pending states. A Session can exist while an Environment is temporarily unavailable.

## Continuation Model

```python
class ContinuationRef(BaseModel):
    object_digest: str
```

The object digest is the complete continuation identity. The referenced `StoredSessionContinuation` contains the Harness release, complete `HarnessState`, optional exact `DeferredToolRequests`, and creation time. Agent UI does not duplicate Thread, suspension, or codec fields in SQLite; it validates them when loading the object.

The Host attempts to save every complete state boundary returned by the Harness:

- completed root Run;
- suspended root Run;
- failed or cancelled result only when the Harness supplies a complete state that Agent UI intentionally accepts;
- completed child output only indirectly after it reaches the parent and the parent produces a complete continuation.

Partial messages, live AG-UI events, model-provider history, and Environment files never synthesize a continuation.

## Run and Resume

One process-local Run uses this flow:

01. acquire the current Host's Session Run lock;
02. read and validate the Session's latest continuation bundle and provider-state references;
03. dispatch those exact immutable inputs to the selected runtime Runner;
04. reconstruct fresh Model and Environment authority and execute one Harness stream in that Runner;
05. forward live presentation best effort;
06. publish and select each detached provider-state update before acknowledging its required lifecycle transition;
07. on a complete or suspended result, publish one continuation bundle;
08. update the Session's latest continuation reference;
09. close the current attachments and process-local Resource scopes without selecting provider pause or destroy;
10. release the Session lock.

No input row is committed before execution. If the process exits before the database update, the input and partial work are forgotten and the Session retains whichever continuation reference was last committed. If runtime cleanup fails after a continuation was selected, the Run reports that cleanup failure together with the selected continuation identity; cleanup failure does not roll back or obscure the successful selection.

A suspended continuation exposes its exact deferred requests. Supplying results starts another ordinary process-local Run from that continuation. The old suspended continuation remains selected until a later continuation commits. Agent UI does not maintain a separate request-consumption ledger or claim exactly-once application of external results.

Cancellation requests the current process-local Run to stop. If the Harness returns a complete accepted continuation, the Host can select it; otherwise the prior continuation remains current. Cancellation does not roll back model, tool, or provider effects.

## Local Write Behavior

One Host serializes Runs for one Session with a process-local lock. Separate local processes can still run and update the same Session. The update is intentionally ordinary:

```sql
UPDATE session
SET continuation = :continuation,
    updated_at = :updated_at
WHERE session_id = :session_id
```

The last committed write becomes current. Agent UI does not detect a stale base, preserve both branches, merge histories, retry model or tool work, or expose a continuation conflict protocol. Users who intentionally need independent continuations fork Sessions before running them.

## Environment Resources

A Session stores one resource row per desired mount instead of separate assignment and resource graphs:

```python
class SessionEnvironmentResource(BaseModel):
    session_id: str
    mount_name: str
    model_alias: str
    access: Literal["read_only", "read_write", "full"]
    provider_key: str
    provider_spec_digest: str
    status: Literal[
        "unprovisioned",
        "available",
        "paused",
        "unavailable",
    ]
    provider_state: ProviderStateRef | None
    updated_at: datetime
```

The provider owns the state payload and lifecycle behavior. The Host stores only the latest provider state needed to reconnect, pause, destroy, or report an unavailable resource. Before dispatch, it selects the current rows and exact state objects. During execution, the Runner returns detached state after each create, resume, explicit pause, or explicit destroy transition that changes durable provider state. The Host validates and publishes that object, replaces the corresponding row with an ordinary last-write-wins update, and then acknowledges the transition. Ordinary Run and Resource-scope exit publishes no lifecycle transition because it closes only process-local responsibilities. The Runner does not continue a transition that requires persistence when the Host returns a publication or save failure. Transient operation status remains process-local; a lifecycle failure is reported and can mark the resource unavailable when the prior state is no longer usable.

- `unprovisioned` provisions on first use;
- `available` acquires a fresh attachment for a Run;
- `paused` resumes before attachment acquisition;
- `unavailable` requires an explicit retry or provider-aware inspection;
- successful explicit destroy clears provider state and returns the row to `unprovisioned`; Session deletion removes the row.

Agent UI does not persist active attachment counts, operation IDs, or fences. One Host serializes its own lifecycle commands with a process-local lock. If another local process operates on the same row concurrently, the last persisted result wins. Process loss can leave external effects unknown; Agent UI reports that limitation rather than maintaining a recovery workflow.

## Runtime Attachments

For every root or child Run, the selected Runner:

1. reconstructs the pinned Environment definition from the exact Host-selected snapshot;
2. creates fresh provider collaborators;
3. restores only the exact provider state selected by the Host when present;
4. ensures each desired resource is available, completing required state-update acknowledgements;
5. acquires one fresh attachment per mount;
6. creates one new `EnvironmentRuntime` with the complete current mount set;
7. closes the runtime and attachments after the Harness stream closes.

Attachments, provider instances, clients, credentials, and `EnvironmentRuntime` values never enter a continuation. Restart constructs all of them again.

The built-in Local Sandbox selects `a13n.local-envd`. If envd, native isolation, or EIP initialization is unavailable, Local Sandbox is unavailable. Agent UI never silently falls back to Direct Local.

## Forking

A fork loads one selected source continuation and creates a new Session:

- when the logical Agent snapshot is compatible, use `HarnessState.fork()`;
- when only readable message history can transfer, create a fresh state seeded from that history;
- select the requested Environment snapshot independently;
- create new Session Environment resource rows;
- publish a new baseline continuation and record source lineage.

Forking does not copy provider attachments, active Runs, live AG-UI events, Runner-local child work, or pending process-local delivery. `HarnessState.fork()` changes the root Thread ID and creates a new Session ID, so any async execution IDs retained in copied message history are not visible through the fork's Runner scope.

## Session Queries and History

Session list and search use small metadata fields such as title, pinned state, archive state, update time, Agent identity, and project association. Opening a Session loads the selected continuation and projects its Harness message history into frontend items.

The first implementation does not need a durable semantic Item database or full-text projection. If continuation-derived queries later prove insufficient for a real WebUI need, a disposable cache can be added from measured requirements. It must remain rebuildable and cannot become continuation authority.

A model-visible Session browsing Capability can expose bounded reads from the current continuation projection. It cannot switch Sessions, select continuations, submit input, control Runs, read raw private Capability state, or access another Session by arbitrary ID.

## Process-local Async Work

An Agent UI parent continuation can retain compact `subagent-N` and `process-N` values returned in ordinary model-visible tool history. Each value names one canonical record in the current Runner generation's standard Harness Manager. It does not embed a child `HarnessState`, managed process, callback, task, provider attachment, storage metadata, or storage authority.

The generation `SubagentManager` retains child state, status, bounded output or failure, usage, steering, and cancellation. The generation `ProcessManager` retains detached process control and output access. Runner exit makes both unavailable. Agent UI does not persist or recover them. A later Run in the same Session and generation reconciles through the compact projection; after generation loss the projection becomes explicitly lost and is never retargeted.

A delegated child receives fresh Model, Identity, Skills, Capabilities, and Environment authority from its exact resolved definition. Nested reconstruction fixes subagents to inline execution and shell to foreground execution. Once a parent Run collects async output and selects a continuation, ordinary continuation persistence preserves the incorporated model/tool result, not the canonical Manager record.

Stable Manager events carry Host-only correlation copied from the initiating `AgentInstanceContext`. Completion usage is deduplicated in generation memory. The Runner reports correlated Harness activity and the Host independently checks Session request activity; the stable hook is a no-op only while both remain active. Otherwise Agent UI schedules at most one best-effort wake behind the Session lock and reloads the latest selected continuation, including when Harness has terminated while the original Host request is still finishing cleanup. A draining generation may publish usage but cannot wake or retarget the replacement generation.

There are no child Session rows, generic Job rows, durable process rows, steering ledgers, delivery identities, linked-successor fences, or retention dependencies. Forking never copies canonical Manager work, and the parent can start new work after resuming from its selected continuation.

## Delete and Cleanup

Deleting a Session first cancels its active root Run and acquires the same Session lock used by root and wake Runs. The selected Runner then force-closes only Manager records whose captured Host correlation names that Session, which cancels live children, terminates live processes, and releases their independent Environment scopes. After that local cleanup completes, the Runner destroys the Session's Environment resources best effort and the Host removes the Session and resource rows. This targeted cleanup does not close generation Managers or affect another Session. Session deletion creates no durable child cleanup workflow. A failed local or external Environment cleanup is reported but does not create a durable deletion workflow or cleanup-pending state machine.

Unreferenced immutable objects are eligible for an explicit garbage-collection pass.
