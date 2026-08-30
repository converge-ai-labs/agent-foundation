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
continuation, queued submission, deferred feedback, and active control routes
are owned by their dedicated control contracts.

Workspace Skill route bodies, ZIP staging, GitHub selectors, revision receipts,
authorization, and error codes are owned in detail by [Foundation Skill
Management](27-skill-management.md#public-management-api); this catalog does not
redefine them.

The API is the Native boundary consumed by Foundation SDKs and the remote
`agent-foundation` CLI. The [Protocol Gateway](28-protocol-gateway.md) also
exposes Hosted AG-UI and A2A as separate standard protocol surfaces; they call
the same application authority but are not `/api/v1` aliases. SDKs map this
contract and do not invent another lifecycle, retry policy, or HTTP client
semantics. Harness Python APIs, Agent Stream Protocol, EIP, provider APIs, and
external webhook payloads retain their own contracts. Plugin artifact
validation, on-demand import, and Runner materialization are internal execution
boundaries behind the public Plugin resources and commands. The
deployment-authenticated Environment connector package operator API remains
outside `/api/v1` and is not added to public clients.
[Foundation Hook Notifications](20a-hook-notifications.md) owns durable Hook
subscriptions and external Webhook delivery. Native Turn SSE, lifecycle
event reads, and notification WebSocket behavior remain owned by [Native
Streaming and Notifications](29-native-streaming-and-notifications.md).

## Scope and Authorization

Every protected route authenticates one Principal and authorizes an explicit action against the selected Organization, Workspace, or resource. Login, invitation acceptance, bootstrap, and password reset authenticate their exact credentials before creating a Principal session. Identifiers, parent paths, cursors, Item references, TurnAttempt IDs, content delivery URLs, and idempotency keys grant no authority.

Workspace collections return only resources visible under current policy. A concealed resource can return `404`. Mutation authorization is re-evaluated at acceptance even when a caller can read the current resource. Turn workers use internal application capabilities rather than calling public HTTP routes to mutate lifecycle state.

## Resource Route Catalog

The following paths are relative to `/api/v1` and are the owning collection and command surfaces. Child reads can also return canonical links to their top-level resource representation; aliases do not create another identity.

| Resource                     | Core routes                                                                                                                                 | Notes                                                                                              |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Authentication               | `/auth/login`, `/auth/logout`, `/auth/password-reset`, `/auth/password-reset/complete`                                                      | Local password and browser-session boundary; no public signup                                      |
| Current User and sessions    | `/users/me`, `/users/me/auth-sessions`                                                                                                      | Self profile and browser-session lifecycle                                                         |
| Organizations                | `/organizations`, `/organizations/{organization_id}`                                                                                        | OSS reads its singleton and updates safe settings; no create, delete, transfer, or switch          |
| Organization RoleBindings    | `/organizations/{organization_id}/role-bindings`                                                                                            | Canonical Organization grants; `/members` is an authorized User projection                         |
| Organization invitations     | `/organizations/{organization_id}/invitations`                                                                                              | Email invitation with one or more validated grants                                                 |
| Workspaces                   | `/organizations/{organization_id}/workspaces`, `/workspaces/{workspace_id}`                                                                 | Organization-owned collaboration and resource-isolation boundary                                   |
| Workspace RoleBindings       | `/workspaces/{workspace_id}/role-bindings`                                                                                                  | Canonical Workspace grants; `/members` is an authorized User projection                            |
| Workspace invitations        | `/workspaces/{workspace_id}/invitations`                                                                                                    | Workspace Admin invitation with automatic Organization Member grant                                |
| Service Accounts             | `/workspaces/{workspace_id}/service-accounts`, `/service-accounts/{service_account_id}`                                                     | Workspace-owned non-human Principal lifecycle                                                      |
| Personal API keys            | `/workspaces/{workspace_id}/personal-api-keys`, `/api-keys/{api_key_id}`                                                                    | Current User's Workspace-bound keys; safe Admin metadata projection                                |
| Service Account API keys     | `/service-accounts/{service_account_id}/api-keys`, `/api-keys/{api_key_id}`                                                                 | Admin-managed Workspace-bound keys                                                                 |
| Agent Presets                | `/workspaces/{workspace_id}/agent-presets`, `/agent-presets/{agent_preset_id}`                                                              | Mutable authoring configuration and active-Version pointer                                         |
| Agent Preset Versions        | `/agent-presets/{agent_preset_id}/versions`, `/agent-preset-versions/{agent_preset_version_id}`                                             | Immutable paginated collection and exact Version read; no in-place mutation                        |
| Plugins                      | `/plugins`, `/plugins/{plugin_id}`                                                                                                          | Deployment-level stable identity; active-Version pointer is runner-profile-only                    |
| Plugin Versions              | `/plugins/{plugin_id}/versions`, `/plugin-versions/{plugin_version_id}`                                                                     | Immutable artifact metadata; no in-place mutation                                                  |
| Skill uploads                | `/workspaces/{workspace_id}/skill-uploads`, `/skill-uploads/{upload_id}`                                                                    | Expiring bounded ZIP staging receipts; not Agent or package authority                              |
| Skills                       | `/workspaces/{workspace_id}/skills`, `/skills/{skill_id}`                                                                                   | Stable Workspace resources with versioned display/head selection                                   |
| Skill revisions              | `/skills/{skill_id}/revisions`, `/skill-revisions/{skill_revision_id}`, `/skill-revisions/{skill_revision_id}/content`                      | Immutable normalized packages; binary content download is separately authorized                    |
| Model Providers              | `/model-providers`                                                                                                                          | Read-only trusted Provider registry; not an installation API                                       |
| Models                       | `/workspaces/{workspace_id}/models`, `/workspaces/{workspace_id}/models/{model_id}`                                                         | Mutable current ModelConfig resources with strong ETag concurrency                                 |
| Connector Providers          | `/connector-providers`, `/connector-providers/{provider_key}`                                                                               | Read-only catalog of deployment-trusted Provider metadata; not an installation API                 |
| Connectors                   | `/workspaces/{workspace_id}/connectors`, `/connectors/{connector_id}`                                                                       | Stable Workspace resources; create atomically includes revision `1`                                |
| Connector revisions          | `/connectors/{connector_id}/revisions`, `/connector-revisions/{connector_revision_id}`                                                      | Immutable create/read configuration revisions                                                      |
| Connections                  | `/workspaces/{workspace_id}/connections`, `/connections/{connection_id}`                                                                    | Safe account and lifecycle projection; credentials and Provider state remain private               |
| Triggers                     | `/workspaces/{workspace_id}/triggers`, `/triggers/{trigger_id}`                                                                             | Mutable schedule or Connector-event source targeting one stable AgentPreset                        |
| Sessions                     | `/workspaces/{workspace_id}/sessions`                                                                                                       | Hosted interaction tree and product/presentation scope                                             |
| Threads                      | `/sessions/{session_id}/threads`, `/threads/{thread_id}`                                                                                    | Independently versioned advancing histories within one Session                                     |
| Thread submissions and queue | `/threads/{thread_id}/submissions`, `/threads/{thread_id}/queued-submissions`, `/queued-submissions/{queued_submission_id}`                 | Queue-if-busy input plus editable ordered queued resources; a queued entry is not a Turn           |
| Turns                        | `/workspaces/{workspace_id}/turns`, `/threads/{thread_id}/turns`, `/turns/{turn_id}`, `/turns/{turn_id}/lineage`                            | Root or continued Host-accepted advancement and exact ancestor lineage                             |
| Steer receipts               | `/turns/{turn_id}/steers/{steer_id}`                                                                                                        | Exact authorized acceptance and consumption status for one submitted active-Turn steer             |
| Items                        | `/turns/{turn_id}/items`                                                                                                                    | Ordered user-visible semantic records                                                              |
| TurnAttempts                 | `/turns/{turn_id}/attempts`, `/turn-attempts/{turn_attempt_id}`                                                                             | Read-only operational history; workers mutate internally                                           |
| Pending actions              | `/turns/{turn_id}/pending-actions`                                                                                                          | Read-only projections of one waiting Turn's frozen pending set                                     |
| Environment Providers        | `/environment-providers`, `/workspaces/{workspace_id}/environment-providers/{provider_key}`                                                 | Read-only trusted catalog plus exact Workspace provider selection                                  |
| Environments                 | `/workspaces/{workspace_id}/environments`, `/environments/{environment_id}`                                                                 | Stable named connection configuration and current immutable revision                               |
| Environment revisions        | `/environments/{environment_id}/revisions`, `/environment-revisions/{environment_revision_id}`                                              | Immutable connection configuration, credential references, permissions, and connector lock         |
| Secrets                      | Routes owned by [Secret Management](11-secret-management.md)                                                                                | Workspace and User ownership with metadata-only reads and write-only values                        |
| Security audit               | `/organizations/{organization_id}/security-audit-events`, `/workspaces/{workspace_id}/security-audit-events`, `/users/me/security-activity` | IAM-owned bounded security projections                                                             |
| Lifecycle events             | `/workspaces/{workspace_id}/events`, `/turns/{turn_id}/events`, `/turn-attempts/{turn_attempt_id}/events`                                   | Workspace cursor replay plus resource-sequence gap recovery                                        |
| Turn stream                  | `GET /turns/{turn_id}/stream`                                                                                                               | Detailed Turn SSE with bounded replay and live cutover                                             |
| Native notifications         | `WS /notifications`                                                                                                                         | Explicit Thread or Workspace subscriptions; best-effort wake-ups without replay                    |
| Hook subscriptions           | `/workspaces/{workspace_id}/hook-subscriptions`, `/hook-subscriptions/{hook_subscription_id}`                                               | Long-lived creation plus management of every durable subscription, including Turn-inline resources |
| Usage records                | `/workspaces/{workspace_id}/usage-records`                                                                                                  | Immutable raw records with durable attribution                                                     |

The selected [distribution](02-distribution-composition-and-extensions.md) registers exactly the routes for its supported capabilities. An EE or Cloud capability can add Organization lifecycle, external identity, Group, custom-role, or Organization-bound credential routes without inserting license branches into OSS handlers or changing existing resource meaning.

Collection fields, filters, order, and payload limits are defined by the owning resource document. All ordinary collections use the shared cursor shape. Lifecycle replay uses its own monotonic cursor and explicit retention-gap response.

## Agent Invocation and Control

[Agent Input](33-agent-input.md) owns the versioned `AgentInput` protocol.
[Agent Control: Input and Continuation](34-agent-control-input-and-continuation.md)
owns start, selected-head and explicit historical same-Thread continuation,
fork, retry, and atomic waiting feedback. [Agent Control: Active
Execution](35-agent-control-active-execution.md) owns the Thread inbox plus the
Turn steer, exact steer-status read, and interrupt routes. The Thread inbox
remains an internal persistence model rather than a generic public resource.
[Agent Control: Queued
Submissions](36-agent-control-queued-submissions.md) owns queued-submission
resources, queue-if-busy Thread submission, ordering, editing, state-first
completion handoff, terminal recovery drain, and atomic consumption into a
Turn.
These operations follow the common API, authorization, idempotency, and durable
mutation conventions referenced by this catalog. The owning contracts define
the exact AgentPresetVersion and Runtime-lock selection, input validation,
Thread advancement, inbox receipt, and acceptance receipt rather than
duplicating those schemas here.

Every direct new-Turn command owned by the input-and-continuation contract can
additionally create one exact Turn-scoped HookSubscription inline. The
queue-if-busy submission command cannot carry one because its commit-time
outcome may be queue admission rather than Turn acceptance. The direct command
normalizes Hook input into the same resource exposed by the Hook-subscription
routes and returns its ID in the Turn acceptance receipt. [Hook
Notifications](20a-hook-notifications.md#durable-hook-subscriptions) owns the
input, authorization, transaction, and delivery semantics.

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
    queue_version: int
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

| Command                        | Route                                                        | Required mutation contract                                                                                                                                                                                                                                      |
| ------------------------------ | ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Accept invitation              | `POST /invitations/{invitation_id}/accept`                   | Exact single-use token; atomically creates User credentials and RoleBindings                                                                                                                                                                                    |
| Resend invitation              | `POST /invitations/{invitation_id}/resend`                   | Current authorization; rotates the token under the same Invitation ID                                                                                                                                                                                           |
| Revoke invitation              | `POST /invitations/{invitation_id}/revoke`                   | Current authorization; terminal for the current invitation                                                                                                                                                                                                      |
| Rotate API key                 | `POST /api-keys/{api_key_id}/rotate`                         | Authorized owner or Service Account Admin; same key ID and immediate cutover                                                                                                                                                                                    |
| Revoke API key                 | `POST /api-keys/{api_key_id}/revoke`                         | Idempotently sets permanent revocation without deleting metadata                                                                                                                                                                                                |
| Publish Agent Preset           | `POST /agent-presets/{agent_preset_id}/publish`              | Idempotency key; creates and activates one immutable Version from the current config                                                                                                                                                                            |
| Roll back Agent Preset         | `POST /agent-presets/{agent_preset_id}/rollback`             | Idempotency key and exact source Version; creates and activates a new immutable Version                                                                                                                                                                         |
| Duplicate Agent Preset         | `POST /agent-presets/{agent_preset_id}/duplicate`            | Idempotency key; creates an independent enabled custom Preset and Version 1                                                                                                                                                                                     |
| Change AgentPreset lifecycle   | `POST /agent-presets/{agent_preset_id}/{action}`             | `enable`, `disable`, `archive`, or `unarchive` with expected resource version                                                                                                                                                                                   |
| Upload Plugin Version          | `POST /plugins`, `POST /plugins/{plugin_id}/versions`        | Idempotency key; first upload creates Plugin and Version, later uploads add an immutable Version                                                                                                                                                                |
| Activate Plugin Version        | `POST /plugin-versions/{plugin_version_id}/activate`         | Runner only: idempotency key; succeeds only after every serviceable Worker can run the candidate Runtime lock; on-demand returns `plugin_runtime_mode_unsupported`                                                                                              |
| Deactivate Plugin              | `POST /plugins/{plugin_id}/deactivate`                       | Runner only: idempotency key and active transitive-reference check; on-demand returns `plugin_runtime_mode_unsupported`                                                                                                                                         |
| Change Plugin lifecycle        | `POST /plugins/{plugin_id}/{action}`                         | `archive` or `unarchive`; archive requires an inactive uploaded Plugin                                                                                                                                                                                          |
| Test candidate ModelConfig     | `POST /workspaces/{workspace_id}/models/test`                | Synchronous candidate test using Secret references; creates no health resource                                                                                                                                                                                  |
| Test Environment revision      | `POST /environment-revisions/{environment_revision_id}/test` | Synchronously connects the exact revision with current credentials; creates no resource, lease, or retained health state                                                                                                                                        |
| Copy ModelConfig               | `POST /workspaces/{workspace_id}/models/{model_id}/copy`     | Idempotency key; creates a new ModelConfig and copies no Secret value                                                                                                                                                                                           |
| Submit Thread input            | `POST /threads/{thread_id}/submissions`                      | Idempotency key and `AgentInput`; accepts an immediate default continuation or null-head root-like Turn when eligible, otherwise appends a queued submission without bypassing existing entries                                                                 |
| Continue existing Thread       | `POST /threads/{thread_id}/turns`                            | Empty queue, expected Thread version, and idempotency key; uses the completed head or accepts a null-head root-like Turn after failed/cancelled current work; a waiting head conflicts                                                                          |
| Continue from completed Turn   | `POST /turns/{turn_id}/continue`                             | Source can be any retained readable completed Turn in its Thread; expected Thread version and idempotency key; atomically selects the source as head and creates its successor                                                                                  |
| Reorder queued submissions     | `POST /threads/{thread_id}/queued-submissions/reorder`       | Expected queue version and the exact ordered set of currently queued IDs; changes no Turn or Thread advancement version                                                                                                                                         |
| Consume queued submission      | `POST /threads/{thread_id}/queued-submissions/consume`       | Expected Thread and queue versions plus idempotency key; atomically marks one entry consumed and accepts a continuation from the completed head or a root-like Turn when the failed/cancelled current Turn has a null head                                      |
| Steer running Turn             | `POST /turns/{turn_id}/steer`                                | Idempotency key, current authorization, and canonical `AgentInput`; appends one target-Turn-ordered pending inbox entry without creating another Turn                                                                                                           |
| Interrupt active Turn          | `POST /turns/{turn_id}/interrupt`                            | Idempotency key and current authorization; seals the active Turn as cancelled, supersedes its pending steer entries, and wakes the owning Worker best-effort                                                                                                    |
| Retry failed or cancelled Turn | `POST /turns/{turn_id}/retry`                                | Target must be the Thread's current failed or cancelled Turn; expected Thread version and idempotency key; copies its accepted input kind, state-parent edge, Preset Version, Runtime lock, Skill selection, and Environment configuration without reopening it |
| Fork completed Turn            | `POST /turns/{turn_id}/fork`                                 | Idempotency key; creates an independent Thread and first Turn from exact frozen source state                                                                                                                                                                    |
| Finalize waiting feedback      | `POST /turns/{turn_id}/feedback`                             | Expected Thread version, exact sealed-state digest, idempotency key, and an explicit subset of approve, reject, complete, or respond entries; omitted actions normalize to reject or no-response                                                                |

A command returns the mutated resource or a durable receipt. `202` means accepted, not completed. Unknown outcome after possible dispatch is reconciled by repeating the same idempotency key or reading the returned resource; clients never generate a new key merely because acknowledgement was lost.

Successful runner-profile Plugin runtime commands return a thin receipt containing `operation_id`,
`status` in `running`, `succeeded`, or `failed`, resulting resource references,
and bounded failure evidence. `GET /api/v1/operations/{operation_id}` reads a
known receipt. Foundation exposes no operation collection, update, deletion, or
independent business lifecycle; this route is only the query boundary for an
asynchronous command result.

The route catalog is stable across Plugin Runtime profiles so SDKs expose one
surface. In `on_demand`, Upload, List, Get, Archive, and exact Preset bindings
remain available, while Activate and Deactivate fail before dispatch with
`409 plugin_runtime_mode_unsupported` and create no operation receipt.

OAuth redirects terminate at `GET /api/v1/connector-callbacks/{provider_key}` and Connector event delivery terminates at `POST /api/v1/connector-events/{trigger_id}`. These are bounded external ingress protocols, not management resources. The callback requires the exact expiring setup state; the event route requires Provider verification and a stable Provider event identity. Path identifiers grant no authority. Success means setup committed, or the event occurrence was accepted or already known; it never waits for Agent execution.

## Read Models

Public resources expose stable product fields and safe references, not ORM objects or provider-private state. Read models follow these boundaries:

- Session, Thread, Turn, and Item use the shared interaction meanings;
- Thread exposes its stored advancement and queue versions, Session membership, origin, current Turn, continuation head, and timestamps; current-Turn status supplies the latest execution/result projection and determines whether the Thread is active;
- QueuedSubmission exposes authorized submitted input, queue order while
  queued, and its accepted Turn correlation after consumption; it exposes no
  execution lease, attempt, failure state, or object-store locator;
- an exact steer receipt read exposes only authorized safe steer identity,
  target, status, consumption correlation, and timestamps; it exposes neither
  the submitted payload nor an object-store locator;
- ModelConfig exposes only safe provider, endpoint, credential-reference, capability, lifecycle, and actor metadata;
- Skill exposes safe Workspace identity, version, current immutable revision,
  manifest, content digest, source kind, resolved GitHub commit when applicable,
  and actor metadata; it exposes no staged object, Secret selector, credential,
  native path, or object-store key;
- Turn exposes lifecycle, wait reason, accepted input kind, its stable AgentPreset,
  exact AgentPresetVersion and Runtime-lock selection, effective Skill names,
  safe model observation, interaction lineage, `retry_of_turn_id`, trigger
  correlation, cancellation intent, and timestamps;
- TurnAttempt exposes generation, worker-safe status, safe model observation, lease timing, Harness correlation, bounded Agent tool dispatch evidence, and bounded failure evidence, but no credential or process-private value;
- Environment exposes safe metadata and its current immutable revision; an authorized EnvironmentRevision detail exposes its protected non-secret connection configuration, connector lock, credential requirements, and permission ceiling without Secret values or provider state;
- LifecycleEvent reads preserve event identity and type, schema version, owning-resource sequence, subject, actor when applicable, TurnAttempt attribution, resource version, bounded payload, and commit time;
- HookSubscription reads preserve version, active or paused status, exact Hook names, bounded resource filters, callback URL, managed signing-Secret reference, signature profile, and timestamps without URL credentials or signing Secret values;
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

Workspace lifecycle replay uses its own opaque cursor over authorized durable
`lifecycle_events`. It supports only owning lifecycle/resource filters and
reports an explicit retained-floor gap. It does not include detailed Turn text,
reasoning, tool, or message deltas.

`GET /api/v1/turns/{turn_id}/stream` opens the detailed Turn SSE and resumes
with `Last-Event-ID`. `WS /api/v1/notifications` opens the distinct Native
best-effort notification channel and begins with no subscriptions. Their
framing, topic registry, cursor, gap, heartbeat, and reconciliation behavior are
owned by [Native Streaming and
Notifications](29-native-streaming-and-notifications.md). There is no
`GET` or `WS /api/v1/workspaces/{workspace_id}/stream` route and no detailed
Turn WebSocket.

## Concurrency and Idempotency

Routes identify which mutable resources require `expected_version`, which
intentionally non-versioned representations require a strong `ETag` and
`If-Match`, and which retryable creates or commands require `Idempotency-Key`.
Thread advancement compares `expected_thread_version`; queue-wide reorder and
consumption compare the independent `expected_queue_version`; an entry edit or
delete compares that entry's `expected_version`.
Queue-if-busy submission intentionally accepts either immediate Turn acceptance
or queue admission at commit-time state and therefore requires neither expected
version; its idempotency evidence preserves the selected result.
Their shared wire behavior follows
[Platform API Conventions](../api-conventions.md#mutations-and-retries), and
their evidence and atomic commit follow
[Durable Operations and Outbox](06-durable-operations-and-outbox.md). Immutable
revisions and usage records reject mutation rather than carrying artificial
versions. ModelConfig is the non-versioned exception defined by
[Model Management](25-model-management.md).

## Errors and Compatibility

The API uses the shared bounded errors and stable codes enforced by the [HTTP ingress contract](05-http-ingress-and-request-contract.md#errors-and-diagnostics). Owning domains add safe details such as `current_version`, `wait_reason`, `pending_kind`, or `replay_gap`; they never expose traceback, SQL, provider payload, Secret value, credential, attachment, private path, or raw model/tool content. A command unavailable under the deployment's durable Plugin Runtime profile returns `409 plugin_runtime_mode_unsupported`. Turn submission returns `400` with `skill_selection_invalid` when an explicit Skill selection is null, too large, duplicate, or outside the selected AgentPresetVersion catalog and creates no Turn.

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
11. Workspace lifecycle events, Turn SSE, and Native WebSocket notifications
    have separate envelopes, continuation behavior, and replay guarantees.
12. A Turn request selects a stable AgentPreset; acceptance pins one exact active AgentPresetVersion and internal Runtime lock.
13. A Plugin command receipt is a bounded queryable command result, not a general-purpose product resource.
14. Queue-only mutation creates no Turn and changes no Thread advancement
    reference; queue consumption atomically changes the queue and accepts one
    Turn.
15. Direct Continue cannot bypass queued submissions. Queue-if-busy submission
    preserves their order. A completed source can seal together with first-entry
    consumption and successor acceptance after state-first preparation;
    otherwise terminal recovery drain remains independently repeatable.
