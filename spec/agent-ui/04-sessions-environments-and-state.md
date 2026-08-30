# Sessions, Environments, and State

## Design Position

An Agent UI Session is one local interaction history. It pins one resolved Agent snapshot, one resolved Environment snapshot, one exact Skill-exposure map, and one latest complete continuation bundle. It supports create, list, resume, fork, archive, pin, and delete without becoming a durable workflow.

A live Run, its input, partial output, model client, provider attachment, `EnvironmentRuntime`, async-child tasks, and subscriptions are process-local. Restart resumes the Session from its last successfully selected continuation. Work after that continuation can be lost or repeated.

## Boundaries

| Concern                   | Owner                                   | Session relationship                                                                     |
| ------------------------- | --------------------------------------- | ---------------------------------------------------------------------------------------- |
| Agent composition         | Resolved Agent snapshot                 | Session pins one exact immutable revision graph                                          |
| Desired Environment       | Resolved Environment snapshot           | Session pins exact desired mounts and lifecycle policy                                   |
| Continuation              | Harness `HarnessState`                  | Stored in the selected continuation bundle without interpreting private Capability state |
| Suspended requests        | Harness `DeferredToolRequests`          | Stored beside `HarnessState` in the same selected continuation bundle                    |
| Session selection         | Agent UI SQLite                         | Stores metadata and one current continuation reference                                   |
| Provider resource effects | Environment Provider package            | Agent UI keeps provider state only when needed to reconnect or clean up                  |
| Current mount set         | Harness `EnvironmentRuntime`            | Fresh and process-local for every Run                                                    |
| Presentation              | Harness message history plus live AG-UI | Continuation supplies retained history; AG-UI is best-effort live output                 |
| Async children            | Process-local Agent UI service          | Lost with the Host unless their result already reached a selected parent continuation    |

## Environment Definition

An Environment definition describes desired mounts and simple Session lifecycle policy:

```python
class EnvironmentDefinitionDocument(BaseModel):
    schema_version: str
    environment_id: str
    display_name: str
    description: str | None
    mounts: tuple[EnvironmentMountDefinition, ...]
    default_mount: str | None
    lifecycle: SessionEnvironmentLifecyclePolicy


class EnvironmentMountDefinition(BaseModel):
    mount_name: str
    model_alias: str
    provider: EnvironmentProviderSpec
    permission_ceiling: EnvironmentPermissionSet


class SessionEnvironmentLifecyclePolicy(BaseModel):
    provision: Literal["on_first_run", "eager"] = "on_first_run"
    idle: Literal["keep_running", "pause"] = "keep_running"
```

The default provisions on first use and keeps the resource available. `pause` is accepted only when the provider supports it. Session deletion requests provider destruction best effort. Agent UI does not retain a configurable cleanup workflow or orphan-resource policy.

The definition contains no credential, provider resource ID, endpoint resolved at runtime, attachment, live provider object, `EnvironmentRuntime`, or `HarnessState`.

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

1. acquire the current Host's Session Run lock;
2. read and validate the Session's latest continuation bundle;
3. construct fresh Model and Environment authority;
4. execute one Harness stream in the selected runtime Runner;
5. forward live presentation best effort;
6. on a complete or suspended result, publish one continuation bundle;
7. update the Session's latest continuation reference;
8. release runtime resources and the Session lock.

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

The provider owns the state payload and lifecycle behavior. Agent UI stores only the latest provider state needed to reconnect, pause, destroy, or report an unavailable resource. Transient operation status remains process-local. A successful operation writes its resulting status and state; failure reports the error and can mark the resource unavailable when the prior state is no longer usable.

- `unprovisioned` provisions on first use;
- `available` acquires a fresh attachment for a Run;
- `paused` resumes before attachment acquisition;
- `unavailable` requires an explicit retry or provider-aware inspection;
- successful explicit destroy clears provider state and returns the row to `unprovisioned`; Session deletion removes the row.

Agent UI does not persist active attachment counts, operation IDs, or fences. One Host serializes its own lifecycle commands with a process-local lock. If another local process operates on the same row concurrently, the last persisted result wins. Process loss can leave external effects unknown; Agent UI reports that limitation rather than maintaining a recovery workflow.

## Runtime Attachments

For every root or child Run, the selected Runner:

1. reconstructs the pinned Environment definition;
2. creates fresh provider collaborators;
3. restores selected provider state when present;
4. ensures each desired resource is available;
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

Forking does not copy provider attachments, active Runs, live AG-UI events, async-child tasks, or pending process-local delivery.

## Session Queries and History

Session list and search use small metadata fields such as title, pinned state, archive state, update time, Agent identity, and project association. Opening a Session loads the selected continuation and projects its Harness message history into frontend items.

The first implementation does not need a durable semantic Item database or full-text projection. If continuation-derived queries later prove insufficient for a real WebUI need, a disposable cache can be added from measured requirements. It must remain rebuildable and cannot become continuation authority.

A model-visible Session browsing Capability can expose bounded reads from the current continuation projection. It cannot switch Sessions, select continuations, submit input, control Runs, read raw private Capability state, or access another Session by arbitrary ID.

## Async Subagents

Async children are current-Host conveniences:

- the Harness builds the exact child collection;
- Agent UI exposes a fresh process-local Capability for delegate, info, wait, steer, and cancel;
- a delegated child receives fresh Model, Identity, Skills, Capabilities, and Environment attachments;
- child status and output stay in the owning Host's in-memory registry;
- parent delivery uses the current live parent input mechanism;
- once the parent incorporates child output and selects a continuation, ordinary continuation persistence preserves that result;
- process loss forgets child tasks and undelivered results.

There are no persistent child Threads, job rows, child continuations, steering records, delivery identities, linked-successor fences, or retention dependencies. The parent can delegate again after resuming from its last continuation.

## Delete and Cleanup

Deleting a Session first stops process-local work in the current Host, requests destruction of its Environment resources best effort, and removes the Session and resource rows. A failed external cleanup is reported but does not create a durable deletion workflow or cleanup-pending state machine.

Unreferenced immutable objects are eligible for an explicit garbage-collection pass.
