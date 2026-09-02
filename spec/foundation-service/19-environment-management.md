# Environment Configuration and Re-entry

## Design Position

Foundation is a Host for managed Environments. It owns:

- stable Workspace Environment identity and immutable desired-configuration revisions;
- exact Agent and Run mount selection;
- current authoritative `EnvironmentState | None` for every managed Thread association;
- fresh process-local Environment construction for every independent RunAttempt;
- changed-only state publication after every outcome; and
- explicit backing-target cleanup and orphan prune.

Foundation uses the shared [`a13n-environment-provider`](../agent-environment-provider/README.md) contract directly. An `EnvironmentProvider` is an inert trusted plugin/factory, an `Environment` is one fresh process-local adapter, and `EnvironmentState` is the only shared durable provider lifecycle value. Foundation does not add a connector, Resource, attachment, binding, lifecycle record, or portable state wrapper between those entities.

Harness receives already constructed adapters as lightweight mounts. It enters and closes them non-destructively. Only Foundation policy constructs a fresh adapter and invokes `warmup()` or `destroy()`.

## Boundaries

| Concern                                                    | Owner                                        | Contract                                                                                    |
| ---------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Workspace Environment identity and immutable revisions     | Foundation                                   | Serializable desired Provider configuration, credentials references, and access             |
| Agent selection and Run execution configuration            | Foundation                                   | At most one primary Environment, exact Provider lock, Secret references, and access ceiling |
| Provider configuration schema and deterministic validation | Environment Provider                         | No external I/O during validation or Environment construction                               |
| Provider catalog and exact package lock                    | Foundation distribution or operator boundary | Trusted code selection; catalog presence grants no Workspace authority                      |
| Workspace Provider selection                               | Foundation authorization                     | Enables one exact trusted Provider package lock                                             |
| Secret storage and current eligibility                     | [Secret Management](11-secret-management.md) | Resolves fresh values without persisting them in Provider configuration or state            |
| Current state and root/child Thread association            | Foundation Host                              | Current managed value, including authoritative `None`, wins over portable continuation data |
| Fresh runtime collaborators and Environment adapters       | Worker                                       | Constructed for one independent RunAttempt and never persisted                              |
| Multi-mount routing, entry, portable snapshots, and close  | Harness                                      | Run-local bound facade; close never destroys backing targets                                |
| Background process control and active completion readiness | Harness                                      | Run-owned controller; killed and released before adapter close                              |
| Changed-only state publication                             | Foundation Host                              | Unconditional finalization and ordinary last-write-wins                                     |
| Retention, explicit destroy, and orphan prune              | Foundation Host                              | Host-private behavior with no shared record schema                                          |
| EIP session and daemon enforcement                         | Agent-envd and its client                    | Daemon generation and bounded Environment operations                                        |

Provider discovery, package upload, schema validity, state possession, or identifier possession does not authorize Provider use. API input and stored data never supply an arbitrary Python import target.

## Provider Catalog and Workspace Selection

Foundation exposes a safe catalog of deployment-trusted Environment Providers. Each entry contains bounded display metadata, supported configuration versions and JSON Schemas, non-secret runtime credential requirements, operation families, and an exact dependency lock. Reading the catalog performs no import, credential, filesystem, daemon, network, or provider-target I/O.

Provider code comes from fixed [distribution composition](02-distribution-composition-and-extensions.md) or an immutable managed Environment Provider package revision. Managed revisions reuse the upload, hashing, immutable object storage, dependency validation, content-addressed cache, and conflict handling defined for [managed Harness plugins](26-managed-harness-plugins-and-runtime.md), but remain a separate extension kind with their own identities, entry point, locks, authorization, and runtime contract.

Foundation reuses the artifact substrate, not the `HarnessPluginPackage` product identity or Harness plugin SPI. One managed Provider package contributes exactly one `EnvironmentProvider` through the standard `a13n_environment_provider.providers` entry-point group. Publication validates non-executing metadata without importing code. A Worker loads only the exact verified artifact on demand and rejects conflicting Provider keys, distributions, or top-level packages; a process never reloads a Provider implementation.

The deployment-authenticated operator API publishes and reads package revisions:

```http
POST /internal/v1/environment-provider-package-revisions
GET /internal/v1/environment-provider-package-revisions/{package_revision_id}
```

These routes are outside `/api/v1`, public OpenAPI, SDKs, tenant RoleBindings, and browser sessions. Upload grants trusted in-process code-execution authority. Untrusted Providers require a separate out-of-process protocol and security boundary.

Workspace users view the safe catalog and enable an exact entry:

```python
# Conceptual Foundation domain schema; not a wire or ORM model.
class EnvironmentProviderSelection:
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    provider_key: str
    provider_package_revision_id: EnvironmentProviderPackageRevisionId | None
    provider_lock: DependencyLock
    enabled: bool
    version: int
```

The versioned Workspace selection is the user-management surface for an uploaded Provider. Users can inspect catalog revisions, enable or disable one exact lock, and later select it from Environment revisions. Selection never mutates or deletes the operator-published package revision.

Only an enabled selection can create a revision, accept a Run configuration, validate a revision, or reconstruct a RunAttempt. Disabling it does not delete retained desired configuration or current state, but later use and lifecycle operations fail closed without substituting another Provider.

The Worker resolves the exact `EnvironmentProvider`, asks a Foundation-owned trusted runtime builder for fresh provider-specific collaborators from current credentials and deployment policy, and calls `create_environment()`. Provider configuration validation and Environment construction are deterministic and perform no external I/O. Runtime collaborators can contain clients, credential material, transport factories, Docker engine access, local allocators, or bounded timeout policy; they never enter configuration, `EnvironmentState`, Run state, or API output.

## Workspace Environment and Revision Model

The API resource named `Environment` is Foundation-owned Workspace configuration, not the shared process-local `Environment` adapter. It has stable identity for naming, authorization, archival, and current-revision selection. An `EnvironmentRevision` is immutable desired configuration.

```python
class FoundationEnvironment:
    id: EnvironmentId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    name: str
    description: str | None
    version: int
    current_revision_id: EnvironmentRevisionId
    archived_at: datetime | None


type EnvironmentAccess = Literal["read_only", "read_write", "full"]


class EnvironmentRevision:
    id: EnvironmentRevisionId
    environment_id: EnvironmentId
    workspace_id: WorkspaceId
    version: int
    provider: EnvironmentProviderSpec
    provider_package_revision_id: EnvironmentProviderPackageRevisionId | None
    provider_lock: DependencyLock
    credential_bindings: tuple[EnvironmentCredentialBinding, ...]
    access: EnvironmentAccess = "full"
    logical_digest_sha256: str
```

`EnvironmentProviderSpec` is the shared versioned envelope containing `provider_key`, `schema_version`, and canonical desired `configuration`. It contains no credential, current target identity, E2B sandbox ID, Docker container ID, client, endpoint resolved at runtime, PID, EIP session, operation receipt, Host association identity, or retention status. Provider target identity belongs only in provider-owned `EnvironmentState` after create or re-entry.

Creation atomically creates revision `1`. Provider configuration, credential-reference, exact package-lock, or access changes create a higher revision. Mutable display metadata changes do not. Restoring old configuration copies it into a new revision, and canonical semantic no-ops create nothing. Existing Runs retain their accepted revision or inline execution configuration; an edit never mutates active or resumable work in place.

Revision creation resolves the enabled Workspace selection, validates the exact Provider schema and access ceiling, and captures the exact package lock without importing code or performing external I/O. A revision contains no live adapter, Provider state, Harness state, current Thread association, or lifecycle operation status. A referenced revision cannot be deleted.

## Credential Bindings

Provider configuration contains no credential values. Each catalog-declared runtime credential requirement is bound to exactly one non-secret source:

```python
class EnvironmentCredentialBinding:
    requirement_key: str
    credential: SecretCredentialSource
```

`SecretCredentialSource` is the shared non-secret selector defined by the [Secret credential-reference contract](11-secret-management.md#credential-references). Environment bindings add only the Provider requirement key; they do not create Environment-specific variants of the same Secret reference.

Every RunAttempt and Host lifecycle operation reauthorizes the Workspace selection, Environment use, credential source, owning principal, and current Secret eligibility. Foundation decrypts values only after closing the authorization transaction and supplies them to one process-local runtime builder. Secret rotation therefore affects the next independent operation without creating another revision or changing current Environment state.

Secret values and value-derived data never enter revisions, Environment state, Run state, events, Items, logs, traces, metric labels, or API responses. Missing or denied credentials fail closed.

## Environment Selection and Run State

An `AgentPresetConfig` selects at most one primary exact Environment revision:

```python
class EnvironmentSelection:
    environment_revision_id: EnvironmentRevisionId
```

A typed Run override may replace that selection with another exact revision or one inline configuration. Explicit null clears the Environment; omission inherits the Revision:

```python
class InlineEnvironmentSelection:
    provider: EnvironmentProviderSpec
    credential_bindings: tuple[EnvironmentCredentialBinding, ...]
    access: EnvironmentAccess = "full"
```

Inline selection passes the same provider-selection, schema, credential-reference, access, and authorization validation but creates no reusable revision. Foundation exposes no public multi-Environment topology, mount-name map, default-mount selector, mutable Environment-head selector, or per-Preset Environment policy document in the first version.

AgentPreset Revision creation resolves an exact named selection into the immutable Revision. Run acceptance resolves the final exact or inline selection into `EffectiveAgentConfig.environment`:

```python
class EnvironmentExecutionConfig:
    schema_version: str
    source_environment_revision_id: EnvironmentRevisionId | None
    provider: EnvironmentProviderSpec
    provider_package_revision_id: EnvironmentProviderPackageRevisionId | None
    provider_lock: DependencyLock
    credential_bindings: tuple[EnvironmentCredentialBinding, ...]
    access: EnvironmentAccess
    logical_digest_sha256: str
```

`EnvironmentExecutionConfig` is one immutable field of the accepted `EffectiveAgentConfig`, not a relational Environment snapshot resource or current-state container. Every replacement RunAttempt reuses it while reauthorizing current selection and credentials. It never follows newer Environment revisions, Provider artifacts, or Workspace defaults.

No `EnvironmentState` is copied into this desired configuration. Foundation associates current state with the logical Thread mount separately so later Runs and replacement Attempts select the latest Host-authoritative value.

## Host-private Current State

For every managed root or child Thread primary Environment association, Foundation retains behavior equivalent to:

- the owning Thread and desired Environment association;
- exact Provider key and desired-configuration digest;
- current authoritative `EnvironmentState | None`;
- whether the association is active, detached, cleanup-pending, or no longer eligible;
- bounded last failure and update observations; and
- enough cleanup provenance to retry explicit destroy or prune a displaced target.

This is a behavioral contract, not a shared or public schema. Foundation can use relational rows, immutable payload objects, application locks, and jobs appropriate to the service, but it does not export a canonical Host Environment record, Thread-link, operation, or prune-candidate model.

An association initialized with `None` is authoritative no-state. Deleted, detached, or explicitly absent managed association also suppresses stale fallback. `HarnessState.environment_states` is a portable observation only. Foundation has no implicit unmanaged adoption path, so a continuation value never replaces current managed Host state.

State is sensitive tenant data even when it is not a credential. Foundation validates Provider key, state version, canonical JSON, configuration compatibility, and size before Environment construction. State contains no Secret, client, PID, daemon process, EIP session, task, lock, Harness mount ID, or RunAttempt authority.

A root Thread normally keeps one association for its selected primary Environment across all of its Runs. A Thread fork always creates a new association with authoritative `None`; it never copies the source Thread's current state or backing target. A shared-root child policy uses the same Host state authority while constructing a fresh child adapter. A dedicated child policy creates a private child association with authoritative no-state and explicitly destroys the resulting target after the child completes. A no-Environment child has no association. Inline child execution borrows the already entered parent Harness facade and does not create or publish separate state.

One association is bound to the exact Provider key and desired-configuration digest that created it. A later Run with the same accepted Environment configuration reuses its current Host state. Replacing the Environment through a compatible continuation or Run override creates a new association with authoritative `None` before execution and makes the displaced association eligible for explicit cleanup; clearing the Environment creates no replacement association. `shared_root` requires the child Environment configuration and Provider lock to match the root association exactly and permits only an equal or narrower access ceiling. `dedicated` uses the child's frozen configuration with independent no-state authority.

## RunAttempt Construction and Finalization

Run acceptance performs no Provider I/O. For each claimed independent RunAttempt, the Worker:

1. reads the optional exact `EnvironmentExecutionConfig` from `EffectiveAgentConfig` and validates its Provider lock;
2. when the configuration is absent, supplies no Environment mount and performs no Environment state, Provider, credential, lifecycle, or publication work;
3. when present, reauthorizes Environment use, Workspace Provider selection, access level, principal eligibility, and every credential source;
4. loads the coherent current Host-state value for the selected Thread association;
5. resolves fresh Secret values and process-local runtime collaborators outside the authorization transaction;
6. resolves the exact trusted `EnvironmentProvider`, validates configuration and state, and constructs one fresh `Environment` without external I/O;
7. wraps the adapter in a lightweight Harness `EnvironmentMount` with the accepted access ceiling and supplies it as the default `workspace` mount to one Harness Run;
8. lets Harness allocate a fresh opaque mount ID, enter the adapter, route operations, snapshot portable non-`None` state, and close the adapter non-destructively; and
9. in unconditional finalization, obtains the adapter's latest known state, closes any still-open process-local resources, and attempts changed-only Host publication independently from Run checkpoint and continuation publication.

Finalization runs after success, failure, cancellation, checkpoint failure, state-export failure, and adapter-close failure. A target created before later entry/readiness failure must still be publishable. `dump_state()` is an infallible process-local read of the adapter's last validated cache, so a later lifecycle or observation failure cannot hide newer state already known by the adapter.

Foundation does not supply `HostedProcessRunCapability`. When effective Environment actions expose background shell, Harness tracks the process only inside that logical Run, enqueues final-completion readiness while the Run remains active, and kills/releases remaining work before adapter close. Foundation stores no process reference, output cursor, status, result, or wake fact in Run state and starts no successor Run for process completion. A historical process reference in messages is non-authoritative and cannot resolve after Worker replacement or another Harness Run.

For each association:

- dumped state equal to the supplied value performs no write;
- a genuinely changed state publishes with ordinary last-write-wins semantics;
- state publication is independent from Harness continuation publication and Run terminalization;
- `None` after successful explicit destroy is publishable, but ordinary Harness close never causes it;
- a displaced state can identify an orphan and is handled by explicit prune.

Equal-state no-op prevents an older completing Attempt from overwriting a concurrent changed value merely because it finished later. A genuinely changed later result can still win and orphan another target. Foundation accepts that trade-off rather than imposing a shared global lease or exactly-once creation protocol.

If the Worker disappears before publication, the last Host state remains current. A newly created but unpublished target can be orphaned and later found only when Provider-supported discovery and exact Foundation correlation permit it. Replacement RunAttempts construct a fresh adapter from current Host state and never reuse live clients, envd processes, Harness mount IDs, or entered facades.

## Agent Input and Managed Skill Preparation

Managed Skill materialization is Host preparation, not an Agent tool call. It uses the fresh entered Harness Environment facade, is content-addressed, writes a completion manifest last, and may be repeated after re-entry without exposing a partial catalog.

[`environment_path` Agent input delivery](33-agent-input.md#binary-source-and-delivery) requires a writable default mount. The Worker reads the accepted URL, authorized source binding path, or exact immutable Asset into private local staging and transfers it through the active default Environment's authorized file-write operation to the deterministic logical path below `/workspace/.a13n/inputs/`. Only that Environment path enters Agent input; the Worker staging path is never exposed.

A replacement RunAttempt re-enters from current Host state and derives whether to rewrite the path from the Run's existing charged model-request usage. Zero prior model requests causes another bounded source read and deterministic replacement; a positive total assumes that input preparation already wrote the path and performs no file inspection or rewrite. Foundation stores no separate materialization status. A pending steer follows its existing inbox status and receipt: it can be reacquired and rewritten until consumption commits, while a consumed steer is not materialized again.

External Environment effects absent from the latest complete Run or continuation publication are not recoverable execution state and can repeat after replacement. Foundation does not infer rollback from RunAttempt cancellation, reconstruct external effects from message history, or maintain a generic tool invocation ledger. Environment operations that require stronger cross-crash behavior own idempotency or durable receipts in their provider contract.

## Warmup, Cleanup, and Prune

Harness never calls `warmup()` or `destroy()`. Foundation may proactively warm a managed association according to explicit product or deployment policy:

1. load exact desired configuration and current Host state;
2. construct a fresh Environment with fresh authorized runtime collaborators;
3. call `warmup()`;
4. dump and publish only changed state in unconditional finalization; and
5. call non-destructive `close()`.

Thread deletion, expiration, dedicated-child completion, or another explicit retention decision makes an association unavailable to new Runs before cleanup. Foundation then constructs a fresh Environment from the last known state and exact configuration, validates target identity, invokes `destroy()`, and publishes authoritative `None` only after confirmed success. Unknown destroy outcome preserves state and cleanup eligibility for retry.

Foundation never destroys Direct Local roots, Local Envd workspace directories, Docker bind sources, external named volumes, user files, or unrelated provider targets unless ownership is an explicit Provider contract. Run completion, failure, cancellation, Worker loss, or Harness close never implies destruction.

Explicit prune handles:

- cleanup-pending targets whose normal destruction did not complete;
- states displaced by last-write-wins publication;
- Provider-discoverable targets carrying exact Foundation correlation but no current authorized association; and
- state whose association was deleted only after enough cleanup evidence was retained.

Retention periods, retry schedules, discovery cadence, and private candidate representation are Foundation implementation policy. The service can use its normal durable jobs and short transactions, but the shared Provider package defines no Host tables, records, foreign keys, leases, locks, or fencing fields.

## Management API

The public `/api/v1` surface follows the shared [Management API](21-management-api.md):

| Resource                     | Route shape                                                                                                 |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Provider catalog             | `GET /environment-providers`, `GET /environment-providers/{provider_key}`                                   |
| Workspace Provider selection | `GET/PUT /workspaces/{workspace_id}/environment-providers/{provider_key}`                                   |
| Environments                 | `POST/GET /workspaces/{workspace_id}/environments`, `GET/PATCH /environments/{environment_id}`              |
| Revisions                    | `POST/GET /environments/{environment_id}/revisions`, `GET /environment-revisions/{environment_revision_id}` |
| Configuration validation     | `POST /environment-revisions/{environment_revision_id}/test`                                                |

Provider catalog reads authorize `environment_provider.read`; Workspace
selection mutation authorizes `environment_provider.select`; Environment and
revision reads authorize `environment.read`; create, metadata mutation,
revision publication, and archive mutation authorize `environment.manage`; a
configuration test authorizes `environment.test`; and an explicit Run selection
authorizes `environment.use` in addition to Agent invocation. These stable
actions and built-in grants are owned by the IAM
[registry](10-identity-and-access-management.md#stable-action-registry).

The synchronous `test` endpoint reauthorizes the exact revision and current credential sources, validates configuration, resolves required runtime collaborators, constructs and immediately discards a fresh Environment without external I/O. It reports bounded schema, package, credential, and construction diagnostics. It does not enter, warm, create, re-enter, destroy, publish state, or retain health. Runtime readiness is established only by an authorized warmup or RunAttempt.

An authorized revision-detail read can return its protected non-secret Provider configuration so that the user can manage it; collection and event projections contain only safe summaries. No projection exposes Secret values, Environment state, target identity, runtime objects, adapters, Host association details, or import paths. Backing-target lifecycle is Host policy rather than a model-facing or general public command surface.

## Built-in Provider Consequences

- Direct Local uses an explicitly authorized Host root and is normally stateless. Foundation never interprets it as sandbox isolation or deletes its files during cleanup.
- Local Envd starts a fresh process-local daemon for each independent adapter, stores no PID in state, and never falls back to Direct Local. Its selected workspace survives only according to explicit Host/provider configuration.
- Docker state includes the exact container ID needed for re-entry. A fresh Attempt re-enters that exact compatible container or creates a replacement only after confirmed absence.
- E2B state includes the exact Sandbox identity. A fresh Attempt re-enters that Sandbox or creates a replacement only after confirmed absence; credentials remain process-local.

Unavailable, inaccessible, incompatible, or unknown target evidence is not authoritative absence. Providers fail rather than speculatively replacing the target.

## Failure Semantics

| Failure                                                          | Foundation outcome                                                                                                  |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Invalid Provider key, schema, lock, primary selection, or access | No Environment revision or Run is accepted                                                                          |
| Archived, disabled, denied, or raced selection                   | Acceptance, Attempt reconstruction, or lifecycle operation fails closed without substitution                        |
| Missing, inactive, or denied credential                          | Attempt or lifecycle operation records a bounded credential failure                                                 |
| Invalid or incompatible current state                            | Fail before target mutation; do not adopt continuation state                                                        |
| Target missing with authoritative absence                        | Provider can create a replacement when its contract permits                                                         |
| Target unavailable, inaccessible, or outcome unknown             | Attempt fails without speculative replacement                                                                       |
| Entry creates state then later fails                             | Finalization still attempts changed-state publication                                                               |
| Harness checkpoint or continuation publication fails             | Environment state finalization still runs; prior selected continuation remains current                              |
| Adapter close fails                                              | State publication is still attempted and close failure is reported independently                                    |
| Concurrent changed publications                                  | Last write wins; displaced targets become explicit prune candidates                                                 |
| Worker disappears                                                | Last published Host state remains current; undisclosed creation can become a discoverable orphan                    |
| Explicit destroy has unknown outcome                             | Preserve last state and cleanup eligibility for inspection or retry                                                 |
| Agent Environment result is absent from the latest checkpoint    | Recovery cannot classify the external outcome; re-driven work can repeat unless the provider protocol is idempotent |

## Security and Compatibility

Provider publication, Workspace selection, Environment authoring, Environment use, Secret access, Host lifecycle actions, and Agent tool access are separate authorities. A model cannot select Providers, revisions, Secrets, current state, target identities, runtime collaborators, or lifecycle actions.

Provider code is trusted in-process code with worker-role authority. Exact locks and operator-only publication do not sandbox it. Desired configuration and current state are protected tenant data. Secret values, native clients, and EIP sessions remain process-local.

Environment revision schema, Provider configuration schema, `EnvironmentState` codec, package lock, Host-private state persistence, Harness mount contract, EIP, and Foundation APIs evolve independently. An incompatible exact lock, configuration version, or state version fails before model or tool work and never falls back to another revision, Provider, or target.

The pre-release connector, Resource, attachment, runtime-mount object, aggregate state wrapper, and shared Host lifecycle record designs are removed directly with no compatibility aliases.

## Invariants

01. Foundation uses only `EnvironmentProvider`, `Environment`, and `EnvironmentState` as shared Environment lifecycle entities.
02. Workspace Environment revisions contain exact desired Provider configuration, not current target identity or state.
03. An AgentPresetRevision and accepted Run select at most one primary Environment; Harness receives it as the default `workspace` mount.
04. Provider validation and fresh Environment construction perform no external I/O.
05. Every independent RunAttempt receives a fresh Environment adapter selected from current Host state.
06. Current managed Host state, including authoritative `None`, wins over `HarnessState.environment_states`.
07. Harness entry and close are Run-local and non-destructive; only Foundation policy invokes `destroy()`.
08. Background processes are Run-owned, contribute no durable Foundation or Harness continuation state, and cannot trigger post-Run wake.
09. Known changed state publishes from unconditional finalization after every Run outcome.
10. Equal state performs no write; genuinely changed state is last-write-wins.
11. State publication, continuation publication, and Run terminalization are independent outcomes.
12. Root/child association, retention, cleanup, and prune remain Foundation-owned behavior without a shared record schema.
13. Credentials, PIDs, live clients, adapters, mount IDs, and native handles never enter desired configuration, Environment state, or continuation.
14. Orphans are accepted as a concurrency and failure consequence and handled by explicit prune.
