# Environment Connections and Runtime Attachments

## Design Position

Foundation manages references to customer-owned Environments. It does not manage the
provider-side lifecycle of their backing targets.

Every Foundation Environment, named or inline, identifies an already-existing target.
Foundation validates and freezes that connection, authorizes its use, opens a
process-local attachment for each independent RunAttempt, and closes only the local
attachment. It never creates, starts, resumes, replaces, pauses, stops, destroys,
leases, or prunes the provider target.

The shared [`a13n-environment-provider`](../agent-environment-provider/README.md)
contract remains broader. An `EnvironmentProvider` can support creation, re-entry,
warmup, replacement, destruction, and state export for other Hosts. Foundation does
not narrow that contract and does not call those lifecycle paths. It requires a
separate Foundation-owned attach-only capability from a selected provider package.

Harness receives one fresh process-local `Environment` adapter as its default
`workspace` mount. The adapter's entry attaches to the exact accepted target, and its
close releases only process-local clients, sessions, carriers, and handles.

## Boundaries

| Concern                                                    | Owner                                        | Contract                                                                                   |
| ---------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Workspace Environment identity and immutable revisions     | Foundation                                   | Reusable connection definitions, credential references, and access ceilings                |
| Provider-side target creation and lifecycle                | Customer and provider                        | Entirely outside Foundation                                                                |
| Generic Provider capabilities                              | `a13n-environment-provider`                  | May include create, re-entry, warmup, replacement, state, and destroy                      |
| Foundation attachment capability                           | Foundation integration contract              | Validates a connection and constructs an adapter that can only attach to the exact target  |
| Provider catalog and exact package lock                    | Foundation distribution or operator boundary | Trusted code selection; catalog presence grants no Workspace authority                     |
| Workspace Provider selection                               | Foundation authorization                     | Enables one exact trusted provider package lock                                            |
| Secret storage and current eligibility                     | [Secret Management](27-secret-management.md) | Resolves fresh values without persisting them in a connection                              |
| Agent selection and immutable Run execution configuration  | Foundation                                   | At most one primary connection, exact provider lock, Secret references, and access ceiling |
| Run-to-Environment correlation                             | Foundation                                   | One immutable `RunEnvironmentBinding` for every accepted Run that has an Environment       |
| Fresh runtime collaborators and attachment adapters        | Worker                                       | Constructed for one independent RunAttempt and never persisted                             |
| Multi-mount routing, entry, portable snapshots, and close  | Harness                                      | Run-local bound facade; close never mutates the provider-side target lifecycle             |
| Background process control and active completion readiness | Harness                                      | Run-owned controller; killed and released before adapter close                             |
| EIP session and daemon enforcement                         | Agent-envd and its client                    | Daemon generation and bounded Environment operations                                       |

Provider discovery, package upload, schema validity, target-identifier possession,
or a prior successful attachment does not authorize Provider or target use. API input
and stored data never supply an arbitrary Python import target.

## Foundation Attachment Capability

Foundation exposes a safe catalog of deployment-trusted Environment provider
packages. A package can implement the generic `EnvironmentProvider` contract and any
other capabilities it needs. To be selectable by Foundation, it additionally exposes
the following Foundation-owned attach-only capability:

```python
# Conceptual Foundation integration protocol; not part of
# a13n-environment-provider.
class FoundationEnvironmentAttachProvider(Protocol):
    provider_key: str
    connection_versions: frozenset[str]

    def validate_connection(
        self,
        *,
        schema_version: str,
        parameters: JsonObject,
    ) -> BaseModel: ...

    def target_key(self, *, connection: BaseModel) -> str: ...

    def create_attachment_environment(
        self,
        *,
        connection: BaseModel,
        runtime: object,
    ) -> Environment: ...
```

`validate_connection()`, `target_key()`, and
`create_attachment_environment()` are deterministic and perform no filesystem,
subprocess, daemon, network, SDK, or provider API I/O. The returned value is a fresh,
single-use implementation of the shared `Environment` interface. Its `enter()` path:

1. resolves and inspects only the exact target named by the validated connection;
2. establishes fresh operation clients, an EIP session, readiness, and provider
   facets;
3. fails if the target is missing, stopped, paused, inaccessible, incompatible, or
   otherwise unavailable; and
4. never falls back to generic Provider create, start, resume, replacement, or
   recovery behavior.

Foundation never calls the generic Provider's `create_environment()`, `warmup()`, or
`destroy()` methods. A provider package may share internal implementation between its
generic and Foundation-specific capabilities, but the attach-only behavior is a
separate conformance boundary. The adapter's `dump_state()` output is not Foundation
authority. A Foundation attachment adapter returns `None` from `dump_state()` because
the exact connection is already frozen in `EnvironmentExecutionConfig`; Harness
therefore publishes no provider target state for this mount.

`target_key()` returns a bounded canonical provider-defined identifier for equality
and correlation. It is unique only within the Workspace, provider key, and the
provider-defined connection namespace. For E2B it is the Sandbox ID. For a provider
whose native ID is not globally unique, the connection schema includes every
non-secret namespace component needed to make the target key unambiguous. The target
key is not a credential, ownership proof, or authorization token.

## Provider Catalog and Workspace Selection

Each Foundation catalog entry contains bounded display metadata, supported
connection versions and JSON Schemas, non-secret runtime credential requirements,
operation families, and an exact dependency lock. Reading the catalog performs no
import, credential, filesystem, daemon, network, or provider-target I/O.

Provider code comes from fixed
[distribution composition](02-distribution-composition-and-extensions.md) or an
immutable managed Environment Provider package revision. Managed revisions reuse the
upload, hashing, immutable object storage, dependency validation, content-addressed
cache, and conflict handling defined for
[managed Harness plugins](36-managed-harness-plugins-and-runtime.md), but remain a
separate extension kind with their own identities, entry point, locks,
authorization, and runtime contract.

Foundation reuses the artifact substrate, not the `HarnessPluginPackage` product
identity or Harness plugin SPI. One selected package contributes exactly one
Foundation attachment capability. It may also contribute the generic
`EnvironmentProvider` entry point, but Foundation selection does not grant authority
to call its broader lifecycle methods. Publication validates non-executing metadata
without importing code. A Worker loads only the exact verified artifact on demand and
rejects conflicting Provider keys, distributions, or top-level packages; a process
never reloads an implementation.

The deployment-authenticated operator API publishes and reads package revisions:

```http
POST /internal/v1/environment-provider-package-revisions
GET /internal/v1/environment-provider-package-revisions/{package_revision_id}
```

These routes are outside `/api/v1`, public OpenAPI, SDKs, tenant RoleBindings, and
browser sessions. Upload grants trusted in-process code-execution authority.
Untrusted Providers require a separate out-of-process protocol and security boundary.

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
    updated_at: datetime
```

The Workspace selection is the user-management surface for an uploaded package. GET
returns a strong ETag and changes require `If-Match`; it has no generic version
counter. Users can inspect catalog revisions, enable or disable one exact lock, and
later select it from Environment revisions. Selection never mutates or deletes the
operator-published package revision.

Only an enabled selection can create a revision, accept a Run configuration, test a
revision, or reconstruct a RunAttempt. Disabling it does not delete retained
connections or bindings, but later use fails closed without substituting another
package, Provider, or target.

## Connection Definition

Foundation owns the serializable connection envelope:

```python
class EnvironmentConnectionSpec:
    provider_key: str
    schema_version: str
    parameters: JsonObject
```

`parameters` is validated by the selected Foundation attachment capability. It
contains the exact non-secret provider target reference and any non-secret namespace
or connection options required to attach. Unlike generic
`EnvironmentProviderSpec.configuration`, it can and normally does contain an existing
provider target ID.

The envelope and provider-owned parameter model reject unknown fields. Values are
bounded canonical JSON. Every supported schema identifies exactly one existing target;
it cannot describe a target to create, a fallback target, or a target-selection query.

The connection contains no credential value, SDK client, bearer URL, resolved
short-lived endpoint, EIP session, PID, process handle, Harness mount ID, RunAttempt
authority, lifecycle receipt, or Foundation record identity. A target ID can still be
sensitive tenant data and follows the protected projection rules below.

## Workspace Environment and Revision Model

The API resource named `Environment` is a Foundation-owned reusable connection
definition, not the shared process-local adapter and not the provider target itself.
It has stable identity for naming, authorization, archival, and current-revision
selection. An `EnvironmentRevision` is an immutable connection revision.

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
    connection: EnvironmentConnectionSpec
    provider_package_revision_id: EnvironmentProviderPackageRevisionId | None
    provider_lock: DependencyLock
    credential_bindings: tuple[EnvironmentCredentialBinding, ...]
    access: EnvironmentAccess = "full"
    target_key: str
    logical_digest_sha256: str
```

Creation atomically creates revision `1`. Connection, credential-reference, exact
package-lock, or access changes create a higher revision. Mutable display metadata
changes do not. Restoring old configuration copies it into a new revision, and
canonical semantic no-ops create nothing. Existing Runs retain their accepted
revision or inline execution configuration; an edit never mutates active or resumable
work in place.

Revision creation resolves the enabled Workspace selection, validates the exact
connection schema and access ceiling, derives the target key, and captures the exact
package lock without performing external I/O. Trusted capability loading follows the
selected distribution's runtime boundary and never uses a caller-supplied import
target. Validation proves only that the connection is well-formed. Target existence
and readiness are checked when a test or RunAttempt attaches. A revision contains no
live adapter, generic Provider state, Harness state, target lifecycle status, or
lifecycle operation. A referenced revision cannot be deleted.

## Credential Bindings

Connection parameters contain no credential values. Each catalog-declared runtime
credential requirement is bound to exactly one non-secret source:

```python
class EnvironmentCredentialBinding:
    requirement_key: str
    credential: SecretCredentialSource
```

`SecretCredentialSource` is the shared non-secret selector defined by the
[Secret credential-reference contract](27-secret-management.md#credential-references).
Environment bindings add only the Provider requirement key; they do not create
Environment-specific variants of the same Secret reference.

Every RunAttempt and connection test reauthorizes the Workspace selection,
Environment use, credential source, owning principal, and current Secret eligibility.
Foundation decrypts values only after closing the authorization transaction and
supplies them to one process-local runtime builder. Secret rotation therefore affects
the next independent attachment without creating another revision.

Secret values and value-derived data never enter revisions, bindings, Run state,
events, Items, logs, traces, metric labels, or API responses. Missing or denied
credentials fail closed.

## Environment Selection and Inline Parameters

An `AgentConfig` selects at most one primary exact Environment revision:

```python
class EnvironmentSelection:
    environment_revision_id: EnvironmentRevisionId
```

A typed Run override can replace that selection with another exact revision or one
inline connection. Explicit null clears the Environment; omission inherits the Agent
Revision:

```python
class InlineEnvironmentSelection:
    connection: EnvironmentConnectionSpec
    credential_bindings: tuple[EnvironmentCredentialBinding, ...] = ()
    access: EnvironmentAccess = "full"
```

The value of `config_override.environment` for an inline selection has exactly these
fields:

| Field                                   | Required | Meaning                                                                                  |
| --------------------------------------- | -------- | ---------------------------------------------------------------------------------------- |
| `connection.provider_key`               | yes      | Selected namespaced provider key                                                         |
| `connection.schema_version`             | yes      | Version of the Foundation connection schema                                              |
| `connection.parameters`                 | yes      | Provider-specific existing-target reference and non-secret attachment options            |
| `credential_bindings`                   | no       | One non-secret Secret source per declared runtime credential requirement; defaults empty |
| `credential_bindings[].requirement_key` | yes      | Catalog-declared credential requirement name                                             |
| `credential_bindings[].credential`      | yes      | `workspace_secret` or `invoking_user_secret` reference                                   |
| `access`                                | no       | `read_only`, `read_write`, or `full`; defaults to `full`                                 |

There is no Foundation `environment_id` or `environment_revision_id` in an inline
selection. The provider-side target identifier is inside
`connection.parameters`. The complete Run request fragment for E2B attachment version
`1` is:

```json
{
  "config_override": {
    "environment": {
      "connection": {
        "provider_key": "a13n.e2b",
        "schema_version": "1",
        "parameters": {
          "sandbox_id": "customer-created-sandbox-id"
        }
      },
      "credential_bindings": [
        {
          "requirement_key": "api_key",
          "credential": {
            "source": "workspace_secret",
            "secret_id": "sec_0123456789abcdef"
          }
        }
      ],
      "access": "full"
    }
  }
}
```

`sandbox_id` is required and names an already-created E2B Sandbox. Foundation does
not accept an image, template, CPU, memory, timeout, auto-pause, or creation option in
this schema. An E2B attachment that requires a non-secret region or account namespace
uses an explicit connection-schema field or a new schema version; Foundation never
infers that namespace from credentials.

Inline selection passes the same provider-selection, schema, credential-reference,
access, and authorization validation as a named revision, but creates no reusable
`Environment` or `EnvironmentRevision`. Foundation exposes no public multi-Environment
topology, mount-name map, default-mount selector, mutable Environment-head selector,
or per-Agent Environment policy document in the first version.

## Accepted Execution Configuration and Run Binding

Agent Revision creation resolves an exact named selection into an immutable execution
configuration. Run acceptance resolves the final exact or inline selection into
`EffectiveAgentConfig.environment`:

```python
class EnvironmentExecutionConfig:
    schema_version: Literal["1"]
    source_environment_revision_id: EnvironmentRevisionId | None
    connection: EnvironmentConnectionSpec
    provider_package_revision_id: EnvironmentProviderPackageRevisionId | None
    provider_lock: DependencyLock
    credential_bindings: tuple[EnvironmentCredentialBinding, ...]
    access: EnvironmentAccess
    target_key: str
    logical_digest_sha256: str
```

This immutable value is the authority for the exact connection used by the Run. A
named selection records its source revision; an inline selection records `None`.
Neither form follows a later Environment revision, package selection, or Workspace
default.

Every accepted Run with a non-null Environment also receives one immutable relational
binding:

```python
class RunEnvironmentBinding:
    id: RunEnvironmentBindingId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    run_id: RunId
    mount_name: Literal["workspace"]
    source_environment_revision_id: EnvironmentRevisionId | None
    provider_key: str
    target_key: str
    environment_execution_config_digest_sha256: str
    created_at: datetime
```

The binding is inserted atomically with the Run. `id` is a Foundation-generated
`envb_...` identity and is the unique identity of this Run's binding, including for
inline selections. `target_key` is the provider-normalized external target identity;
for E2B it equals the accepted `sandbox_id`. The exact connection and credential
references remain owned by `EffectiveAgentConfig.environment`; the binding stores only
the relation, protected correlation fields, and its digest, so it is not a second
mutable configuration authority.

There is no uniqueness constraint that prevents multiple Runs, Threads, or Agents
from binding to the same target. Provider concurrency rules are checked when each
RunAttempt attaches. Possessing a binding ID or target key grants no authority.

A replacement RunAttempt reuses the same Run binding and exact execution
configuration. Retry creates another Run and therefore another binding while copying
the accepted execution configuration. An ordinary continuation or fork creates a new
binding from its own accepted effective configuration, even when it points to the
same customer-owned target.

The relational responsibilities are therefore:

| Table                             | Information owned                                                                                                        |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `environment_provider_selections` | One Workspace's enabled exact trusted attachment-provider package lock                                                   |
| `environments`                    | Stable reusable name, metadata, lifecycle, version, and current revision                                                 |
| `environment_revisions`           | Immutable exact connection, credential references, access ceiling, provider package lock, target key, and logical digest |
| `run_environment_bindings`        | Immutable Run relation, optional source revision, provider and target correlation, and matching execution-config digest  |

No Foundation table represents target creation, target runtime status, current
provider state, attachment sessions, target ownership, leases, cleanup, or deletion.

## RunAttempt Attachment

Run acceptance performs no provider-target I/O. For each claimed independent
RunAttempt, the Worker:

1. reads the optional exact `EnvironmentExecutionConfig` and corresponding
   `RunEnvironmentBinding`, and verifies their digests and target identity;
2. when the configuration is absent, supplies no Environment mount and performs no
   Environment, Provider, credential, or attachment work;
3. when present, reauthorizes Environment use, Workspace Provider selection, access,
   principal eligibility, and every credential source;
4. resolves fresh Secret values and process-local runtime collaborators outside the
   authorization transaction;
5. resolves the exact trusted Foundation attachment capability, revalidates the
   connection, and constructs one fresh attach-only `Environment` adapter without
   external I/O;
6. wraps it in a lightweight Harness `EnvironmentMount` with the accepted access
   ceiling and supplies it as the default `workspace` mount;
7. lets Harness allocate a fresh opaque mount ID, enter the exact target, route
   operations, and close the adapter; and
8. releases any still-open process-local resources during unconditional finalization.

Foundation persists no `EnvironmentState`, current Thread-to-target association,
attachment session, SDK client, EIP session, mount ID, lifecycle operation, or target
health record. `HarnessState.environment_states` is not used to select or retarget a
Foundation attachment. Worker loss discards only process-local attachment state; a
replacement Attempt attaches again to the same frozen target.

Foundation does not supply `HostedProcessRunCapability`. When effective Environment
actions expose background shell, Harness tracks the process only inside that logical
Run, enqueues final-completion readiness while the Run remains active, and
kills/releases remaining process-local work before adapter close. Foundation stores
no process reference, output cursor, status, result, or wake fact in Run state and
starts no successor Run for process completion.

## Continuation, Fork, and Child Semantics

A new Run that inherits an Environment configuration receives a new binding to the
same exact external target. Foundation never creates a target to isolate a
continuation, fork, or child automatically.

- `none` gives the child no Environment.
- `shared_root` requires the root's exact connection and provider lock and permits
  only an equal or narrower access ceiling. The child receives its own Run binding to
  the same target.
- `dedicated` requires the child Agent's frozen configuration to name another exact
  customer-created target. It means independently selected, not
  Foundation-provisioned or Foundation-destroyed.
- Inline child execution borrows the already-entered parent Harness facade and creates
  no separate binding or attachment.

A fork that inherits the source effective configuration binds to the same target. A
fork that must use a different sandbox supplies an explicit compatible Environment
override naming that already-existing target.

## Agent Input and Managed Skill Preparation

Managed Skill materialization is Host preparation, not an Agent tool call. It uses the
fresh entered Harness Environment facade, is content-addressed, writes a completion
manifest last, and may be repeated after a later attachment without exposing a
partial catalog.

[`environment_path` Agent input delivery](17-agent-input.md#binary-source-and-delivery)
requires a writable default mount. The Worker reads the accepted URL, authorized
source binding path, or exact immutable Asset into private local staging and transfers
it through the active default Environment's authorized file-write operation to the
deterministic logical path below `/workspace/.a13n/inputs/`. Only that Environment path
enters Agent input; the Worker staging path is never exposed.

A replacement RunAttempt derives whether to rewrite the path from the Run's existing
charged model-request usage. Zero prior model requests causes another bounded source
read and deterministic replacement; a positive total assumes that input preparation
already wrote the path and performs no file inspection or rewrite. Foundation stores
no separate materialization status.

External Environment effects absent from the latest complete Run or continuation
publication are not recoverable execution state and can repeat after replacement.
Foundation does not infer rollback from RunAttempt cancellation, reconstruct external
effects from message history, or maintain a generic tool invocation ledger.

## Management API

The public `/api/v1` surface follows the shared
[Management API](16-management-api.md):

| Resource                     | Route shape                                                                                                 |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Provider catalog             | `GET /environment-providers`, `GET /environment-providers/{provider_key}`                                   |
| Workspace Provider selection | `GET/PUT /workspaces/{workspace_id}/environment-providers/{provider_key}`                                   |
| Environments                 | `POST/GET /workspaces/{workspace_id}/environments`, `GET/PATCH /environments/{environment_id}`              |
| Revisions                    | `POST/GET /environments/{environment_id}/revisions`, `GET /environment-revisions/{environment_revision_id}` |
| Attachment test              | `POST /environment-revisions/{environment_revision_id}/test`                                                |

Provider catalog reads authorize `environment_provider.read`; Workspace selection
mutation authorizes `environment_provider.select`; Environment and revision reads
authorize `environment.read`; create, metadata mutation, revision publication, and
archive mutation authorize `environment.manage`; an attachment test authorizes
`environment.test`; and an explicit Run selection authorizes `environment.use` in
addition to Agent invocation. These stable actions and built-in grants are owned by
the IAM [registry](33-identity-and-access-management.md#stable-action-registry).

The synchronous `test` endpoint reauthorizes the exact revision and current credential
sources, opens an attachment to the exact existing target, verifies bounded readiness,
and closes local resources. It performs external I/O but never creates, starts,
resumes, replaces, pauses, stops, or destroys the target, and it retains no health or
attachment state.

An authorized revision-detail read can return its protected non-secret connection so
that the user can manage it. That detail can therefore contain the provider target ID
inside `connection.parameters`; returning it requires `environment.read` on the exact
resource and private no-store handling. Collection, event, Run, and model-facing
projections contain only safe summaries and expose no Secret value, target key,
connection parameter, runtime object, adapter, attachment detail, or import path.

## Built-in Provider Consequences

- Direct Local attaches an explicitly authorized existing Host root. Foundation never
  interprets it as sandbox isolation or deletes its files.
- Local Envd attaches an explicitly authorized existing workspace. A process-local
  envd carrier can be opened and closed as transport, but Foundation does not create,
  delete, or own the workspace.
- Docker requires an exact existing container ID. Foundation does not create, start,
  restart, replace, stop, or remove the container.
- E2B requires an exact existing Sandbox ID. Foundation does not create, start,
  resume, pause, replace, time out, or destroy the Sandbox.

The generic providers may still support broader lifecycle behavior outside
Foundation. Their generic behavior does not weaken these Foundation consequences.

## Failure Semantics

| Failure                                                        | Foundation outcome                                                                       |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Invalid Provider key, connection schema, lock, or access       | No Environment revision or Run is accepted                                               |
| Archived, disabled, denied, or raced selection                 | Acceptance, test, or Attempt reconstruction fails closed without substitution            |
| Missing, inactive, or denied credential                        | Test or Attempt records a bounded credential failure                                     |
| Target key differs from the frozen accepted value              | Fail before attachment; do not retarget                                                  |
| Target missing, stopped, paused, inaccessible, or incompatible | Test or Attempt fails; do not create, start, resume, replace, or mutate lifecycle        |
| Provider reports unknown attachment outcome                    | Attempt fails; retry attaches again only to the same target                              |
| Provider rejects concurrent attachment                         | The affected Attempt fails with a bounded conflict                                       |
| Harness checkpoint or continuation publication fails           | Attachment finalization still closes process-local resources                             |
| Adapter close fails                                            | Report bounded cleanup failure; never escalate to provider-target destruction            |
| Worker disappears                                              | The customer-owned target is unchanged; replacement attaches to the same frozen target   |
| Agent Environment result is absent from the latest checkpoint  | Recovery cannot classify the external operation outcome; re-driven Agent work can repeat |

## Security and Compatibility

Provider publication, Workspace selection, Environment authoring, Environment use,
Secret access, and Agent tool access are separate authorities. A model cannot select
Providers, revisions, Secrets, target identities, runtime collaborators, or attachment
parameters.

Provider code is trusted in-process code with worker-role authority. Exact locks and
operator-only publication do not sandbox it. Connection parameters, target keys, and
binding correlation are protected tenant data. Secret values, native clients, and EIP
sessions remain process-local.

The Foundation connection schema, package lock, Run binding schema, Harness mount
contract, EIP, and Foundation APIs evolve independently from the generic Provider
configuration and `EnvironmentState` codecs. An incompatible exact lock or connection
version fails before model or tool work and never falls back to another revision,
Provider, target, or generic lifecycle path.

This contract directly replaces the pre-release Foundation desired-configuration,
current Host `EnvironmentState`, target lifecycle, cleanup, and prune design. It does
not remove or deprecate those capabilities from `a13n-environment-provider`.

## Invariants

01. Every Foundation Environment connection names an already-existing customer-owned target.
02. Foundation never creates, starts, resumes, replaces, pauses, stops, destroys, leases, or prunes a provider target.
03. The generic Environment Provider contract remains broader and independently reusable.
04. Named revisions and inline selections use the same `EnvironmentConnectionSpec`.
05. Inline selection creates no reusable Environment or EnvironmentRevision.
06. Every accepted Run with an Environment has one immutable `RunEnvironmentBinding`.
07. A binding ID identifies the Run relation; a provider target key identifies the external target; neither grants authority.
08. An AgentRevision and accepted Run select at most one primary Environment; Harness receives it as the default `workspace` mount.
09. Validation and fresh adapter construction perform no external I/O; target observation begins only during test or entry.
10. Every independent RunAttempt receives a fresh attach-only adapter for the same frozen target.
11. Harness entry and close are Run-local; close releases only process-local resources.
12. Foundation persists no current provider state, attachment session, client, EIP session, mount ID, or target lifecycle record.
13. Secret values, live clients, and native handles never enter connections, bindings, Run state, or continuation.
14. Missing or unavailable targets fail closed without lifecycle mutation or substitution.
