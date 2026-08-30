# Foundation Management API

## Design Position

Foundation exposes one resource-oriented `/api/v1` management contract for IAM,
Agent authoring, managed Skills, model configuration, interaction, durable Turns,
deferred work, Environment configuration, events, and raw usage. The API follows
[Platform API Conventions](../api-conventions.md),
[Platform Data Conventions](../data-conventions.md), the
[HTTP ingress contract](05-http-ingress-and-request-contract.md), the shared
[durable operation contract](06-durable-operations-and-outbox.md), and the
[Identity and Access Management contract](10-identity-and-access-management.md);
this document owns the Foundation resource catalog, common command boundaries,
read models, and cross-resource mutation behavior. Agent invocation,
continuation, deferred feedback, and active control routes are owned by their
dedicated control contracts.

Workspace Skill route bodies, ZIP staging, GitHub selectors, revision receipts,
authorization, and error codes are owned in detail by [Foundation Skill
Management](27-skill-management.md#public-management-api); this catalog does not
redefine them.

The API is the public boundary consumed by Foundation SDKs and the remote `agent-foundation` CLI. SDKs map this contract and do not invent another lifecycle, retry policy, or HTTP client semantics. Harness Python APIs, Agent Stream Protocol, EIP, and provider APIs retain their own contracts; [Foundation Hook Notifications](20a-hook-notifications.md) owns Hook subscriptions, channel eligibility, and external notification semantics. The deployment-authenticated [Harness plugin artifact operator API](26-harness-plugin-artifacts-and-runtime-loading.md#internal-operator-api) and [Environment connector package operator API](19-environment-management.md#provider-catalog-and-workspace-selection) are deliberately outside `/api/v1` and are not added to public clients.

## Scope and Authorization

Every protected route authenticates one Principal and authorizes an explicit action against the selected Organization, Workspace, or resource. Login, invitation acceptance, bootstrap, and password reset authenticate their exact credentials before creating a Principal session. Identifiers, parent paths, cursors, Item references, TurnAttempt IDs, content delivery URLs, and idempotency keys grant no authority.

Workspace collections return only resources visible under current policy. A concealed resource can return `404`. Mutation authorization is re-evaluated at acceptance even when a caller can read the current resource. Turn workers use internal application capabilities rather than calling public HTTP routes to mutate lifecycle state.

## Resource Route Catalog

The following paths are relative to `/api/v1` and are the owning collection and command surfaces. Child reads can also return canonical links to their top-level resource representation; aliases do not create another identity.

| Resource                  | Core routes                                                                                                                                 | Notes                                                                                      |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Authentication            | `/auth/login`, `/auth/logout`, `/auth/password-reset`, `/auth/password-reset/complete`                                                      | Local password and browser-session boundary; no public signup                              |
| Current User and sessions | `/users/me`, `/users/me/auth-sessions`                                                                                                      | Self profile and browser-session lifecycle                                                 |
| Organizations             | `/organizations`, `/organizations/{organization_id}`                                                                                        | OSS reads its singleton and updates safe settings; no create, delete, transfer, or switch  |
| Organization RoleBindings | `/organizations/{organization_id}/role-bindings`                                                                                            | Canonical Organization grants; `/members` is an authorized User projection                 |
| Organization invitations  | `/organizations/{organization_id}/invitations`                                                                                              | Email invitation with one or more validated grants                                         |
| Workspaces                | `/organizations/{organization_id}/workspaces`, `/workspaces/{workspace_id}`                                                                 | Organization-owned collaboration and resource-isolation boundary                           |
| Workspace RoleBindings    | `/workspaces/{workspace_id}/role-bindings`                                                                                                  | Canonical Workspace grants; `/members` is an authorized User projection                    |
| Workspace invitations     | `/workspaces/{workspace_id}/invitations`                                                                                                    | Workspace Admin invitation with automatic Organization Member grant                        |
| Service Accounts          | `/workspaces/{workspace_id}/service-accounts`, `/service-accounts/{service_account_id}`                                                     | Workspace-owned non-human Principal lifecycle                                              |
| Personal API keys         | `/workspaces/{workspace_id}/personal-api-keys`, `/api-keys/{api_key_id}`                                                                    | Current User's Workspace-bound keys; safe Admin metadata projection                        |
| Service Account API keys  | `/service-accounts/{service_account_id}/api-keys`, `/api-keys/{api_key_id}`                                                                 | Admin-managed Workspace-bound keys                                                         |
| Presets and Agents        | `/workspaces/{workspace_id}/presets`, `/workspaces/{workspace_id}/agents`                                                                   | Mutable authoring heads and immutable selected revisions                                   |
| Agent revisions           | `/agents/{agent_id}/revisions`                                                                                                              | Immutable create/read collection; no in-place revision mutation                            |
| Skill uploads             | `/workspaces/{workspace_id}/skill-uploads`, `/skill-uploads/{upload_id}`                                                                    | Expiring bounded ZIP staging receipts; not Agent or package authority                      |
| Skills                    | `/workspaces/{workspace_id}/skills`, `/skills/{skill_id}`                                                                                   | Stable Workspace resources with versioned display/head selection                           |
| Skill revisions           | `/skills/{skill_id}/revisions`, `/skill-revisions/{skill_revision_id}`, `/skill-revisions/{skill_revision_id}/content`                      | Immutable normalized packages; binary content download is separately authorized            |
| Model Providers           | `/model-providers`                                                                                                                          | Read-only trusted Provider registry; not an installation API                               |
| Models                    | `/workspaces/{workspace_id}/models`, `/workspaces/{workspace_id}/models/{model_id}`                                                         | Mutable current ModelConfig resources with strong ETag concurrency                         |
| Connector Providers       | `/connector-providers`, `/connector-providers/{provider_key}`                                                                               | Read-only catalog of deployment-trusted Provider metadata; not an installation API         |
| Connectors                | `/workspaces/{workspace_id}/connectors`, `/connectors/{connector_id}`                                                                       | Stable Workspace resources; create atomically includes revision `1`                        |
| Connector revisions       | `/connectors/{connector_id}/revisions`, `/connector-revisions/{connector_revision_id}`                                                      | Immutable create/read configuration revisions                                              |
| Connections               | `/workspaces/{workspace_id}/connections`, `/connections/{connection_id}`                                                                    | Safe account and lifecycle projection; credentials and Provider state remain private       |
| Triggers                  | `/workspaces/{workspace_id}/triggers`, `/triggers/{trigger_id}`                                                                             | Mutable schedule or Connector-event source targeting one exact Agent revision              |
| Sessions                  | `/workspaces/{workspace_id}/sessions`                                                                                                       | Hosted interaction tree and product/presentation scope                                     |
| Threads                   | `/sessions/{session_id}/threads`, `/threads/{thread_id}`                                                                                    | Independently versioned advancing histories within one Session                             |
| Turns                     | `/workspaces/{workspace_id}/turns`, `/threads/{thread_id}/turns`, `/turns/{turn_id}`, `/turns/{turn_id}/lineage`                            | Root or continued Host-accepted advancement and exact ancestor lineage                     |
| Items                     | `/turns/{turn_id}/items`                                                                                                                    | Ordered user-visible semantic records                                                      |
| TurnAttempts              | `/turns/{turn_id}/attempts`, `/turn-attempts/{turn_attempt_id}`                                                                             | Read-only operational history; workers mutate internally                                   |
| Pending actions           | `/turns/{turn_id}/pending-actions`                                                                                                          | Read projections and authorized response commands for one waiting Turn                     |
| Environment Providers     | `/environment-providers`, `/workspaces/{workspace_id}/environment-providers/{provider_key}`                                                 | Read-only trusted catalog plus exact Workspace provider selection                          |
| Environments              | `/workspaces/{workspace_id}/environments`, `/environments/{environment_id}`                                                                 | Stable named connection configuration and current immutable revision                       |
| Environment revisions     | `/environments/{environment_id}/revisions`, `/environment-revisions/{environment_revision_id}`                                              | Immutable connection configuration, credential references, permissions, and connector lock |
| Secrets                   | Routes owned by [Secret Management](11-secret-management.md)                                                                                | Workspace and User ownership with metadata-only reads and write-only values                |
| Security audit            | `/organizations/{organization_id}/security-audit-events`, `/workspaces/{workspace_id}/security-audit-events`, `/users/me/security-activity` | IAM-owned bounded security projections                                                     |
| Lifecycle events          | `/workspaces/{workspace_id}/events` and resource-scoped event collections                                                                   | Durable replay, not ordinary pagination                                                    |
| Delivery stream           | `GET /workspaces/{workspace_id}/stream`, `WS /workspaces/{workspace_id}/stream`                                                             | SSE or WebSocket over the same retained and live delivery-envelope contract                |
| Hook subscriptions        | `/workspaces/{workspace_id}/hook-subscriptions`, `/hook-subscriptions/{hook_subscription_id}`                                               | Exact Hook-name filters and an authorized immutable webhook or sink destination reference  |
| Usage records             | `/workspaces/{workspace_id}/usage-records`                                                                                                  | Immutable raw records with durable attribution                                             |

The selected [distribution](02-distribution-composition-and-extensions.md) registers exactly the routes for its supported capabilities. An EE or Cloud capability can add Organization lifecycle, external identity, Group, custom-role, or Organization-bound credential routes without inserting license branches into OSS handlers or changing existing resource meaning.

Collection fields, filters, order, and payload limits are defined by the owning resource document. All ordinary collections use the shared cursor shape. Lifecycle replay uses its own monotonic cursor and explicit retention-gap response.

## Agent Invocation and Control

[Agent Input](28a-agent-input.md) owns the versioned `AgentInput` protocol.
[Agent Control: Input and Continuation](28b-agent-control-input-and-continuation.md)
owns root and existing-Thread submission, fork, retry, and pending-action
response routes. [Agent Control: Active
Execution](28c-agent-control-active-execution.md) owns the Turn cancellation
route. These operations follow the common API, authorization, idempotency, and
durable mutation conventions referenced by this catalog.

## Thread Reads

Foundation exposes the stored Thread resource directly:

```http
GET /api/v1/threads/{thread_id}
GET /api/v1/sessions/{session_id}/threads?limit=...&cursor=...
```

The exact read and each collection item have this conceptual wire shape:

```python
class ThreadResource:
    thread_id: str
    version: int
    session_id: str
    role: Literal["root", "child"]
    origin_kind: Literal["new", "fork", "child"]
    origin_thread_id: str | None
    origin_turn_id: str | None
    head_turn_id: str | None
    current_turn_id: str
    created_at: datetime
    updated_at: datetime
```

The response is an authorized projection of the
[durable Thread row](24-thread-persistence.md), not a grouping synthesized from
Turn activity. The Session collection defaults to deterministic
`updated_at desc, thread_id desc` order and supports explicit creation order.
An optional bounded current-Turn summary comes from `current_turn_id`; it never
changes Thread version or head meaning. Foundation exposes no general Thread
PATCH or independent hard-delete route. Origin references are present only when
the caller can currently read their source; otherwise both source identifiers
are omitted without weakening authorization for the current Thread.

## Commands

Commands are subordinate to the resource whose state they mutate:

| Command                    | Route                                                        | Required mutation contract                                                                                               |
| -------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| Accept invitation          | `POST /invitations/{invitation_id}/accept`                   | Exact single-use token; atomically creates User credentials and RoleBindings                                             |
| Resend invitation          | `POST /invitations/{invitation_id}/resend`                   | Current authorization; rotates the token under the same Invitation ID                                                    |
| Revoke invitation          | `POST /invitations/{invitation_id}/revoke`                   | Current authorization; terminal for the current invitation                                                               |
| Rotate API key             | `POST /api-keys/{api_key_id}/rotate`                         | Authorized owner or Service Account Admin; same key ID and immediate cutover                                             |
| Revoke API key             | `POST /api-keys/{api_key_id}/revoke`                         | Idempotently sets permanent revocation without deleting metadata                                                         |
| Test candidate ModelConfig | `POST /workspaces/{workspace_id}/models/test`                | Synchronous candidate test using Secret references; creates no health resource                                           |
| Test Environment revision  | `POST /environment-revisions/{environment_revision_id}/test` | Synchronously connects the exact revision with current credentials; creates no resource, lease, or retained health state |
| Copy ModelConfig           | `POST /workspaces/{workspace_id}/models/{model_id}/copy`     | Idempotency key; creates a new ModelConfig and copies no Secret value                                                    |

A command returns the mutated resource or a durable receipt. `202` means accepted, not completed. Unknown outcome after possible dispatch is reconciled by repeating the same idempotency key or reading the returned resource; clients never generate a new key merely because acknowledgement was lost.

OAuth redirects terminate at `GET /api/v1/connector-callbacks/{provider_key}` and Connector event delivery terminates at `POST /api/v1/connector-events/{trigger_id}`. These are bounded external ingress protocols, not management resources. The callback requires the exact expiring setup state; the event route requires Provider verification and a stable Provider event identity. Path identifiers grant no authority. Success means setup committed, or the event occurrence was accepted or already known; it never waits for Agent execution.

## Read Models

Public resources expose stable product fields and safe references, not ORM objects or provider-private state. Read models follow these boundaries:

- Session, Thread, Turn, and Item use the shared interaction meanings;
- Thread exposes its stored version, Session membership, origin, current Turn, continuation head, and timestamps; current-Turn status supplies the latest execution/result projection and determines whether the Thread is active;
- ModelConfig exposes only safe provider, endpoint, credential-reference, capability, lifecycle, and actor metadata;
- Skill exposes safe Workspace identity, version, current immutable revision,
  manifest, content digest, source kind, resolved GitHub commit when applicable,
  and actor metadata; it exposes no staged object, Secret selector, credential,
  native path, or object-store key;
- Turn exposes lifecycle, wait reason, selected revisions, effective Skill names, safe model observation, interaction lineage, trigger and retry correlation, cancellation intent, and timestamps;
- TurnAttempt exposes generation, worker-safe status, safe model observation, lease timing, Harness correlation, bounded Agent tool dispatch evidence, and bounded failure evidence, but no credential or process-private value;
- Environment exposes safe metadata and its current immutable revision; an authorized EnvironmentRevision detail exposes its protected non-secret connection configuration, connector lock, credential requirements, and permission ceiling without Secret values or provider state;
- LifecycleEvent reads preserve event type, schema version, owning-resource sequence, subject, actor when applicable, TurnAttempt attribution, resource version, bounded payload, and commit time;
- HookSubscription reads preserve version, active or paused status, exact Hook names, bounded resource filters, destination kind, safe immutable destination reference, and timestamps without endpoint credentials or signing Secret values;
- UsageRecord reads preserve immutable identity and attribution.

An Item read never substitutes for lifecycle event replay, and an event read never expands private Item or object-backed content without separate authorization.

## Turn Lineage Read

Foundation exposes the exact ancestor path of one caller-selected Turn:

```http
GET /api/v1/turns/{turn_id}/lineage
```

The route selects an explicit head and follows the
[`parent_turn_id` persistence contract](14-turn-persistence.md#git-like-turn-dag).
It does not infer the latest Turn or accept a Thread selector in place of the
head. Its direct response has this conceptual shape:

```python
class TurnLineageItem:
    turn_id: str
    session_id: str
    thread_id: str
    parent_turn_id: str | None
    lineage_kind: TurnLineageKind
    status: TurnStatus
    depth_from_head: int
    created_at: datetime


class TurnLineage:
    head_turn_id: str
    items: tuple[TurnLineageItem, ...]
```

`items` is ordered from root to head. The head has `depth_from_head=0`; each
ancestor's depth is its number of parent edges from the head. The path can cross
Session and Thread boundaries through retained parent edges and never includes
siblings or descendants.

The service authorizes the head and every ancestor under current tenant,
principal, visibility, archival, and retention policy. An absent or concealed
head returns `404 turn_not_found`. A missing or unauthorized ancestor, cycle, or
path deeper than 1,000 Turns returns `409 turn_lineage_invalid` with safe reason
`missing_parent`, `cycle`, or `max_depth`; the route never returns a
complete-looking prefix. The complete bounded path is one response and is not
cursor-paginated.

## Pagination, Filtering, and Replay

Ordinary collections use `limit` and opaque `cursor` exactly as defined by Platform API Conventions. Each resource defines deterministic default ordering and explicit filters. Cursors are bound to principal scope, filter, order, and retention.

Lifecycle and interaction replay use opaque replay cursors over the retained Workspace stream, with optional Session, Thread, Turn, TurnAttempt, source-kind, and event-type filters. A replay response labels each envelope source kind and reports `replay_gap` when the cursor generation differs or its sequence precedes the retained floor. The response includes the current generation, retained floor, high watermark, and authorized resource links. The client then reads current resource state and an authorized semantic snapshot; it never treats the newest event as a complete missing history.

`GET /api/v1/workspaces/{workspace_id}/stream` opens SSE. A WebSocket upgrade at `/api/v1/workspaces/{workspace_id}/stream` exposes the same envelope, cursor, filters, gap response, and replay-to-live cutover under the shared [HTTP streaming boundary](05-http-ingress-and-request-contract.md#streaming-connections). Transport disconnect does not cancel work, and live-only AG-UI observations do not advance the retained replay cursor.

## Concurrency and Idempotency

Routes identify which mutable resources require `expected_version`, which
intentionally non-versioned representations require a strong `ETag` and
`If-Match`, and which retryable creates or commands require `Idempotency-Key`.
Their shared wire behavior follows
[Platform API Conventions](../api-conventions.md#mutations-and-retries), and
their evidence and atomic commit follow
[Durable Operations and Outbox](06-durable-operations-and-outbox.md). Immutable
revisions and usage records reject mutation rather than carrying artificial
versions. ModelConfig is the non-versioned exception defined by
[Model Management](25-model-management.md).

## Errors and Compatibility

The API uses the shared bounded errors and stable codes enforced by the [HTTP ingress contract](05-http-ingress-and-request-contract.md#errors-and-diagnostics). Owning domains add safe details such as `current_version`, `wait_reason`, `pending_kind`, or `replay_gap`; they never expose traceback, SQL, provider payload, Secret value, credential, attachment, private path, or raw model/tool content. Turn submission returns `400` with `skill_selection_invalid` when an explicit Skill selection is null, too large, duplicate, or outside the selected AgentRevision catalog and creates no Turn.

`/api/v1` evolves additively. Removing or repurposing a field, changing a command side-effect boundary, weakening authorization, changing idempotency scope, or changing resource identity requires an incompatible API version. First-party SDK releases can add idiomatic convenience methods but preserve the same resources, receipts, errors, and retry boundaries.

## Invariants

01. Every protected Foundation operation authenticates a Principal and authorizes an explicit resource action; credential-establishment routes validate their exact one-time or login credential first.
02. Existing-Thread and root Turn submission are distinct acceptance routes over the same Turn resource and allocation contract.
03. Thread reads come from the independent durable Thread resource, and accepted advancement compares and updates its exact version.
04. Public API acceptance never waits for Harness completion.
05. TurnAttempt mutation is internal; public TurnAttempt routes are bounded operational reads.
06. Commands use resource-scoped paths, idempotency evidence, and version checks where lost updates are possible.
07. Replay cursors, identifiers, receipts, and signed URLs grant no authority by possession.
08. API read models contain no process-local object, provider resource-state data, attachment, credential, or Secret value.
09. SDKs and the CLI consume this API rather than defining parallel lifecycle or retry semantics.
10. Connector and Model Provider catalog routes never install or import
    caller-selected code, and public Turn routes never forge Trigger,
    Connection, or model selections.
