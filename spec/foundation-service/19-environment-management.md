# Environment Configuration and Runtime Mounts

## Design Position

Foundation lets a Workspace describe and reuse a connection to an Environment that already exists. It owns:

- stable `Environment` identity and immutable `EnvironmentRevision` configuration;
- exact Agent and Run selection, including inline configuration; and
- a trusted process-local connector that opens the selected Environment as a canonical Harness runtime attachment.

Foundation does not create, resume, pause, destroy, replace, assign, lease, or reconcile a Sandbox or other provider resource. A referenced resource must already be running and compatible when a RunAttempt connects. Failure is reported to the caller through the RunAttempt; it does not trigger lifecycle repair.

While a RunAttempt is actively using an attachment, its connector may perform bounded provider-specific keep-alive so that the already-running resource does not expire mid-run. This is run-scoped liveness maintenance, not durable lifecycle management: it starts only after a successful connection, stops when the attachment scope closes or the Attempt loses authority, and never runs as a background Foundation workflow.

## Boundaries

| Concern                                                         | Owner                                        | Contract                                                                                     |
| --------------------------------------------------------------- | -------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Environment identity and immutable connection revisions         | Foundation                                   | Serializable non-secret connection configuration                                             |
| Agent requirements and Run execution configuration              | Foundation                                   | Exact desired mount definitions, connector locks, Secret references, and permission ceilings |
| External Sandbox or provider-resource lifecycle                 | User and external provider                   | Resource exists and is running before Foundation connects                                    |
| Connector schema, connection, keep-alive, and local close       | Trusted Foundation Environment connector     | Produces one process-local canonical runtime attachment                                      |
| Connector artifact trust and availability                       | Foundation distribution or operator boundary | Exact code lock; no Workspace authorization                                                  |
| Workspace provider selection                                    | Foundation authorization                     | Enables one exact trusted connector lock                                                     |
| Secret storage and current eligibility                          | [Secret Management](11-secret-management.md) | Resolves fresh values without persisting them in Environment data                            |
| Current mount set, provider-neutral routing, and portable state | Harness                                      | Enters fresh mount candidates and stores portable Environment data                           |
| EIP session and daemon enforcement                              | Agent-envd and its client                    | Daemon generation and bounded Environment operations                                         |

Provider discovery, package upload, schema validity, or identifier possession does not authorize provider use. API input and stored data never supply an arbitrary Python import target.

## Provider Catalog and Workspace Selection

Foundation exposes a safe catalog of deployment-trusted Environment providers. Each entry contains bounded display metadata, connection JSON Schemas, non-secret credential requirements, attachment compatibility, optional keep-alive capability, and an exact dependency lock. Reading the catalog performs no import, credential, network, or provider-resource I/O. Provider detail lists the safe metadata and exact package-revision locks that a Workspace can select; it never returns wheel bytes or import targets.

Connector code comes from fixed [distribution composition](02-distribution-composition-and-extensions.md) or an immutable managed Environment Provider package revision. Managed revisions reuse the upload, hashing, immutable object storage, dependency validation, content-addressed cache, and conflict handling defined for [managed Harness plugin artifacts](26-harness-plugin-artifacts-and-runtime-loading.md), but remain a separate extension kind with their own identities, entry point, locks, authorization, and runtime contract.

Therefore Foundation reuses the managed-plugin artifact substrate, not the `HarnessPluginPackage` product identity or Harness plugin SPI. Unifying those identities would couple two different selection scopes and runtime contracts.

A managed package contains exactly one `a13n_service.environment_connectors` factory for its provider key. Publication validates non-executing metadata without importing code. A Worker loads only the exact verified artifact on demand and rejects conflicting provider keys, distributions, or top-level packages; a process never reloads a connector.

The deployment-authenticated operator API publishes and reads package revisions:

```http
POST /internal/v1/environment-provider-package-revisions
GET /internal/v1/environment-provider-package-revisions/{package_revision_id}
```

These routes are outside `/api/v1`, public OpenAPI, SDKs, tenant RoleBindings, and browser sessions. Upload grants trusted in-process code-execution authority. Untrusted connectors require a separate out-of-process protocol and security boundary.

Workspace users view the safe catalog and enable an exact entry:

```python
# Conceptual Foundation domain schema; not a wire or ORM model.
class WorkspaceEnvironmentProviderSelection:
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    provider_key: str
    connector_package_revision_id: EnvironmentProviderPackageRevisionId | None
    connector_lock: DependencyLock
    enabled: bool
    version: int
```

The versioned Workspace selection is the user-management surface for an uploaded connector: users can inspect catalog revisions, enable or disable one exact lock, and later bind it from Environment revisions. Selection never mutates or deletes the operator-published package revision.

Only an enabled selection can create a revision, accept a Run configuration, test a revision, or reconstruct a RunAttempt. Disabling it does not delete retained data, but later use fails closed without substituting another connector.

The connector receives only the accepted connection specification, bounded RunAttempt context, and resolved credentials. It returns one fresh canonical `EnvironmentRuntimeAttachment`. The Worker passes that attachment through the Harness attachment adapter to construct a fresh `EnvironmentRuntimeMount`; the connector does not construct or mutate the Harness runtime. It receives no unrestricted Secret resolver, database session, repository, request, or ambient credential lookup.

Its Foundation-owned service-provider interface is conceptually:

```python
class FoundationEnvironmentConnector(Protocol):
    provider_key: str

    def open(
        self,
        connection: EnvironmentConnectionSpec,
        credentials: Mapping[str, SecretValue],
        runtime: EnvironmentConnectorRuntime,
    ) -> AbstractAsyncContextManager[EnvironmentRuntimeAttachment]: ...
```

Entering the context connects and validates the attachment, then starts any configured keep-alive. Exiting stops keep-alive and closes local clients. The runtime supplies only the Attempt cancellation signal, clock, and deployment-bounded timeout and keep-alive policy. The interface deliberately has no create, resume, pause, destroy, assignment, resource-state, or reconciliation method.

## Environment and Revision Model

`Environment` is the stable Workspace resource for naming, authorization, archival, and current-revision selection. `EnvironmentRevision` is immutable connection configuration.

```python
class EnvironmentConnectionSpec:
    provider_key: str
    schema_version: str
    parameters: JsonObject


class Environment:
    id: EnvironmentId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    name: str
    description: str | None
    version: int
    current_revision_id: EnvironmentRevisionId
    archived_at: datetime | None


class EnvironmentRevision:
    id: EnvironmentRevisionId
    environment_id: EnvironmentId
    workspace_id: WorkspaceId
    version: int
    connection_spec: EnvironmentConnectionSpec
    connector_package_revision_id: EnvironmentProviderPackageRevisionId | None
    connector_lock: DependencyLock
    credential_bindings: tuple[EnvironmentCredentialBinding, ...]
    permission_ceiling: EnvironmentPermissionSet
    logical_digest_sha256: str
```

`parameters` is validated by the connector catalog schema and can contain an external resource identifier, endpoint, region, or other non-secret connection data. For E2B it normally includes the existing Sandbox ID. The identifier is an opaque connection parameter, not a Foundation-managed resource identity.

Creation atomically creates revision `1`; connection, credential-reference, or permission changes create a higher revision. Mutable display metadata changes do not. Restoring old configuration copies it into a new revision, and canonical semantic no-ops create nothing.

Revision creation resolves the enabled Workspace selection, validates schemas and permissions, and captures the exact connector lock without importing code or performing external I/O. A revision contains no Secret value, provider resource state, lifecycle policy, attachment, EIP session, Python object, import target, or Harness state. A referenced revision cannot be deleted.

## Credential Bindings

Connection parameters contain no credential values. Each declared credential requirement is bound to exactly one non-secret source:

```python
type EnvironmentCredentialSource = (
    WorkspaceSecretEnvironmentCredential
    | InvokingUserSecretEnvironmentCredential
)


class WorkspaceSecretEnvironmentCredential:
    source: Literal["workspace_secret"]
    secret_id: SecretId


class InvokingUserSecretEnvironmentCredential:
    source: Literal["invoking_user_secret"]
    secret_key: str


class EnvironmentCredentialBinding:
    requirement_key: str
    credential: EnvironmentCredentialSource
```

Every RunAttempt reauthorizes the Environment, Workspace selection, credential source, owning principal, and current Secret eligibility. Foundation decrypts values only after closing the authorization transaction and supplies them to one process-local connector. Secret rotation therefore affects the next Attempt without creating another revision.

Secret values and value-derived data never enter revisions, Run state, events, Items, logs, traces, metric labels, or API responses. Missing or denied credentials fail closed.

## Environment Selection and Run State

An AgentPresetVersion stores an ordered set of exact desired Environment mount requirements plus its runtime-selection policy:

```python
class AgentEnvironmentRequirement:
    mount_name: str
    environment_revision_id: EnvironmentRevisionId
    required: bool
```

Mount names are unique and use the Harness mount-name syntax. An optional default mount names one requirement. Authoring may accept an `EnvironmentId`, but materialization stores its current revision. Runtime-selection policy controls whether a caller can replace or add desired mounts and the maximum mount count and permission ceilings it can select.

A permitted Run mount selection uses an exact revision, a mutable Environment resolved at acceptance, or inline configuration:

```python
type EnvironmentSourceSelection = (
    EnvironmentIdSelection
    | EnvironmentRevisionSelection
    | InlineEnvironmentSelection
)


class EnvironmentIdSelection:
    environment_id: EnvironmentId


class EnvironmentRevisionSelection:
    environment_revision_id: EnvironmentRevisionId


class InlineEnvironmentSelection:
    connection_spec: EnvironmentConnectionSpec
    credential_bindings: tuple[EnvironmentCredentialBinding, ...]
    permission_ceiling: EnvironmentPermissionSet


class EnvironmentSelectionEntry:
    mount_name: str
    required: bool
    source: EnvironmentSourceSelection
```

Inline entries pass the same selection, schema, credential-reference, permission, and authorization validation but create no reusable revision.

Acceptance writes the complete non-secret configuration into the initial `state.json`:

```python
class EnvironmentExecutionMount:
    mount_name: str
    required: bool
    source_environment_revision_id: EnvironmentRevisionId | None
    connection_spec: EnvironmentConnectionSpec
    connector_package_revision_id: EnvironmentProviderPackageRevisionId | None
    connector_lock: DependencyLock
    credential_bindings: tuple[EnvironmentCredentialBinding, ...]
    permission_ceiling: EnvironmentPermissionSet
    logical_digest_sha256: str


class EnvironmentExecutionConfig:
    schema_version: str
    desired_mounts: tuple[EnvironmentExecutionMount, ...]
    default_mount: str | None
    logical_digest_sha256: str
```

`EnvironmentExecutionConfig` is one immutable field of the Run state envelope, not a relational Environment snapshot resource or column. It records desired mount definitions, not a Harness current mount set. Every replacement RunAttempt reuses it while reauthorizing current selection and credentials. It never follows newer Environment revisions, connector artifacts, or Workspace defaults.

## Management API

The public `/api/v1` surface follows the shared [Management API](21-management-api.md):

| Resource                     | Route shape                                                                                                 |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Provider catalog             | `GET /environment-providers`, `GET /environment-providers/{provider_key}`                                   |
| Workspace provider selection | `GET/PUT /workspaces/{workspace_id}/environment-providers/{provider_key}`                                   |
| Environments                 | `POST/GET /workspaces/{workspace_id}/environments`, `GET/PATCH /environments/{environment_id}`              |
| Revisions                    | `POST/GET /environments/{environment_id}/revisions`, `GET /environment-revisions/{environment_revision_id}` |
| Connection test              | `POST /environment-revisions/{environment_revision_id}/test`                                                |

The synchronous test uses the exact revision and current authorized credentials, opens and validates one connection, returns a bounded safe capability/readiness result, and closes local clients. It creates no lifecycle state and does not start run keep-alive.

An authorized revision-detail read can return its protected non-secret connection specification so that the user can manage it; collection and event projections contain only safe summaries. No projection exposes Secret values, provider-private state, runtime objects, attachments, or import paths. Foundation exposes no provider resource, assignment, lease, or lifecycle-command API.

## RunAttempt Runtime Mount Construction

Run acceptance performs no provider I/O. For each claimed RunAttempt, the Worker:

1. reads the exact `EnvironmentExecutionConfig` from `state.json` and verifies schemas and connector locks;
2. reauthorizes provider selection, Environment use, permission ceilings, principal eligibility, and every credential source;
3. resolves fresh Secret values outside the authorization transaction;
4. asks each exact connector to connect to the already-running resource and return one fresh attachment after validating EIP compatibility;
5. adapts each attachment into a fresh `EnvironmentRuntimeMount`, constructs one `EnvironmentRuntime` with the complete desired initial mount mapping and default mount, retains that runtime, and supplies it through `RunBindings.environment`; and
6. while the Harness run and attachment scopes are active, performs connector-defined bounded keep-alive and then closes process-local clients.

Keep-alive may extend a provider timeout only while the current Attempt remains authorized to run. It stops promptly on attachment-scope close, cancellation, lease loss, or Worker shutdown. An extension already accepted by the provider is not rolled back or reconciled. Connector and deployment policy bound its interval and maximum extension; it never outlives the Run recovery deadline as an autonomous task.

Foundation has no connection lease and does not serialize consumers of the same external resource. If the resource or provider rejects another connection, the Attempt fails with bounded `environment_connection_conflict` evidence surfaced to the user. Foundation does not queue, silently retry, or substitute a resource. Connectors may apply bounded transport retries before attachment or keep-alive; exhaustion fails the Attempt.

On each Harness run, the runtime allocates a fresh opaque `mount_id` for every mounted incarnation. Foundation does not persist a `mount_id`, use it as an Environment or provider-resource identity, or expect it to survive a replacement RunAttempt. The Harness current mount set and its run-local change journal are not Foundation state.

Portable provider-defined data can appear only in `HarnessState.environment_state` under the Harness codec contract. It does not identify or recreate an external resource, restore a current mount set, or supply mount authority. Agent-visible Environment operations are ordinary Agent tool calls and use the shared `unknown_outcome` recovery contract. Foundation performs no Sandbox inspection or lifecycle reconciliation during recovery.

Managed Skill materialization is Host preparation, not an Agent tool call. It is content-addressed, writes a completion manifest last, and may be repeated after reconnecting without exposing a partial catalog.

[`environment_path` Agent input delivery](33-agent-input.md#binary-source-and-delivery) requires a writable default binding. The Worker reads the accepted URL, authorized source binding path, or exact immutable Asset into private local staging and transfers it through the active default attachment's authorized file-write interface to the deterministic logical path below `/workspace/.a13n/inputs/`. Only that Environment path enters Agent input; the Worker staging path is never exposed. Foundation assigns no temporary or persistent retention class to the materialized Environment file, does not manage the external Environment lifecycle because of it, and never treats the path as durable input authority.

A replacement RunAttempt reconnects to the configured Environment and derives whether to rewrite the path from the Run's existing charged model-request usage. Zero prior model requests causes another bounded source read and deterministic replacement; a positive total assumes that input preparation already wrote the path and performs no file inspection or rewrite. Foundation stores no separate materialization status. A pending steer follows its existing inbox status and receipt instead: it can be reacquired and rewritten until consumption commits, while a consumed steer is not materialized again.

## E2B Connector

The E2B connector accepts an existing Sandbox ID and an E2B API key resolved from a Secret binding. The Sandbox must already be running and contain a compatible agent-envd/EIP endpoint. Connection must not use an SDK path that implicitly creates or resumes a Sandbox; a paused, stopped, missing, or incompatible Sandbox fails the Attempt.

After successful attachment, the connector may call E2B keep-alive to prevent the Sandbox timeout from expiring during the active Harness run. It stops those calls when the attachment scope closes and never pauses or destroys the Sandbox.

## Failure Semantics

| Failure                                                 | Foundation outcome                                                                         |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Invalid schema, lock, desired mount set, or permission  | No Environment revision or Run is accepted                                                 |
| Archived, disabled, denied, or raced selection          | Acceptance or Attempt reconstruction fails closed without substitution                     |
| Missing, inactive, or denied credential                 | Attempt records a bounded credential failure                                               |
| Resource is missing, stopped, paused, or incompatible   | Attempt records a bounded connection or EIP compatibility failure                          |
| Concurrent attachment is rejected                       | Attempt records `environment_connection_conflict`; no Foundation lease or queue is created |
| Connection or keep-alive exhausts bounded retries       | Attempt fails; normal Run recovery policy decides whether another Attempt is allocated     |
| Worker disappears                                       | Keep-alive stops; a replacement Attempt reconnects using the same accepted configuration   |
| Agent Environment tool result is missing after dispatch | Invocation becomes `unknown_outcome`; Foundation does not replay it automatically          |

## Security and Compatibility

Connector publication, Workspace selection, Environment authoring, Environment use, Secret access, and Agent tool permission are separate authorities. A model cannot select connectors, revisions, Secrets, connection targets, or lifecycle actions.

Connector code is trusted in-process code with worker-role authority. Exact locks and operator-only publication do not sandbox it. Connection parameters, including external resource IDs and private endpoints, are protected tenant data. Secret values remain process-local.

Environment revisions, connection schemas, connector locks, desired mount configuration, Harness runtime mounts, EIP, and Foundation APIs evolve independently. An incompatible exact lock or state schema fails before model or tool work and never falls back to another revision, connector, or resource.

## Invariants

1. One EnvironmentRevision describes how to connect to an existing resource and performs no provider I/O.
2. Foundation never creates, resumes, pauses, destroys, replaces, assigns, leases, or reconciles an external Environment resource.
3. A Run's exact `EnvironmentExecutionConfig` is stored only in its immutable `state.json` envelope.
4. Every RunAttempt reauthorizes current provider selection and resolves fresh credential values.
5. Runtime attachments, clients, and credentials are process-local and never persisted.
6. Keep-alive exists only within an active attachment scope and creates no durable lifecycle state.
7. Foundation creates no connection lease; provider concurrency rejection is surfaced to the user.
8. Replacement RunAttempts reconnect to the accepted target, construct a fresh Host-retained `EnvironmentRuntime`, and receive fresh opaque mount IDs with no process-local continuity.
9. Agent Environment tool uncertainty uses the same `unknown_outcome` contract as every other Agent tool.
