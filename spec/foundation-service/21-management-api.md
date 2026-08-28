# Foundation Management API

## Design Position

Foundation exposes one resource-oriented `/api/v1` management contract for IAM, Agent authoring, interaction, durable Turns, deferred work, Environment resources, events, and raw usage. The API follows [Platform API Conventions](../api-conventions.md), [Platform Data Conventions](../data-conventions.md), the [HTTP ingress contract](05-http-ingress-and-request-contract.md), the shared [durable operation contract](06-durable-operations-and-outbox.md), and the [Identity and Access Management contract](10-identity-and-access-management.md); this document owns Foundation resource routes, command boundaries, read models, and cross-resource mutation behavior.

The API is the public boundary consumed by Foundation SDKs and the remote `agent-foundation` CLI. SDKs map this contract and do not invent another lifecycle, retry policy, or HTTP client semantics. Harness Python APIs, Agent Stream Protocol, EIP, provider APIs, and external webhook payloads retain their own contracts. The deployment-authenticated [Harness plugin artifact operator API](25-harness-plugin-artifacts-and-runtime-loading.md#internal-operator-api) is deliberately outside `/api/v1` and is not added to public clients.

## Scope and Authorization

Every protected route authenticates one Principal and authorizes an explicit action against the selected Organization, Workspace, or resource. Login, invitation acceptance, bootstrap, and password reset authenticate their exact credentials before creating a Principal session. Identifiers, parent paths, cursors, Item references, TurnAttempt IDs, content delivery URLs, and idempotency keys grant no authority.

Workspace collections return only resources visible under current policy. A concealed resource can return `404`. Mutation authorization is re-evaluated at acceptance even when a caller can read the current resource. Turn workers use internal application capabilities rather than calling public HTTP routes to mutate lifecycle state.

## Resource Route Catalog

The following paths are relative to `/api/v1` and are the owning collection and command surfaces. Child reads can also return canonical links to their top-level resource representation; aliases do not create another identity.

| Resource                  | Core routes                                                                                                                                 | Notes                                                                                     |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Authentication            | `/auth/login`, `/auth/logout`, `/auth/password-reset`, `/auth/password-reset/complete`                                                      | Local password and browser-session boundary; no public signup                             |
| Current User and sessions | `/users/me`, `/users/me/auth-sessions`                                                                                                      | Self profile and browser-session lifecycle                                                |
| Organizations             | `/organizations`, `/organizations/{organization_id}`                                                                                        | OSS reads its singleton and updates safe settings; no create, delete, transfer, or switch |
| Organization RoleBindings | `/organizations/{organization_id}/role-bindings`                                                                                            | Canonical Organization grants; `/members` is an authorized User projection                |
| Organization invitations  | `/organizations/{organization_id}/invitations`                                                                                              | Email invitation with one or more validated grants                                        |
| Workspaces                | `/organizations/{organization_id}/workspaces`, `/workspaces/{workspace_id}`                                                                 | Organization-owned collaboration and resource-isolation boundary                          |
| Workspace RoleBindings    | `/workspaces/{workspace_id}/role-bindings`                                                                                                  | Canonical Workspace grants; `/members` is an authorized User projection                   |
| Workspace invitations     | `/workspaces/{workspace_id}/invitations`                                                                                                    | Workspace Admin invitation with automatic Organization Member grant                       |
| Service Accounts          | `/workspaces/{workspace_id}/service-accounts`, `/service-accounts/{service_account_id}`                                                     | Workspace-owned non-human Principal lifecycle                                             |
| Personal API keys         | `/workspaces/{workspace_id}/personal-api-keys`, `/api-keys/{api_key_id}`                                                                    | Current User's Workspace-bound keys; safe Admin metadata projection                       |
| Service Account API keys  | `/service-accounts/{service_account_id}/api-keys`, `/api-keys/{api_key_id}`                                                                 | Admin-managed Workspace-bound keys                                                        |
| Presets and Agents        | `/workspaces/{workspace_id}/presets`, `/workspaces/{workspace_id}/agents`                                                                   | Mutable authoring heads and immutable selected revisions                                  |
| Agent revisions           | `/agents/{agent_id}/revisions`                                                                                                              | Immutable create/read collection; no in-place revision mutation                           |
| Model integrations        | `/workspaces/{workspace_id}/model-integrations`                                                                                             | Mutable heads with immutable integration revisions                                        |
| Connector Providers       | `/connector-providers`, `/connector-providers/{provider_key}`                                                                               | Read-only catalog of deployment-trusted Provider metadata; not an installation API        |
| Connectors                | `/workspaces/{workspace_id}/connectors`, `/connectors/{connector_id}`                                                                       | Stable Workspace resources; create atomically includes revision `1`                       |
| Connector revisions       | `/connectors/{connector_id}/revisions`, `/connector-revisions/{connector_revision_id}`                                                      | Immutable create/read configuration revisions                                             |
| Connections               | `/workspaces/{workspace_id}/connections`, `/connections/{connection_id}`                                                                    | Safe account and lifecycle projection; credentials and Provider state remain private      |
| Triggers                  | `/workspaces/{workspace_id}/triggers`, `/triggers/{trigger_id}`                                                                             | Mutable schedule or Connector-event source targeting one exact Agent revision             |
| Sessions                  | `/workspaces/{workspace_id}/sessions`                                                                                                       | Hosted interaction tree and product/presentation scope                                    |
| Threads                   | `/sessions/{session_id}/threads`, `/threads/{thread_id}`                                                                                    | Independently versioned advancing histories within one Session                            |
| Turns                     | `/workspaces/{workspace_id}/turns`, `/threads/{thread_id}/turns`, `/turns/{turn_id}`, `/turns/{turn_id}/lineage`                            | Root or continued Host-accepted advancement and exact ancestor lineage                    |
| Items                     | `/turns/{turn_id}/items`                                                                                                                    | Ordered user-visible semantic records                                                     |
| TurnAttempts              | `/turns/{turn_id}/attempts`, `/turn-attempts/{turn_attempt_id}`                                                                             | Read-only operational history; workers mutate internally                                  |
| Pending actions           | `/turns/{turn_id}/pending-actions`                                                                                                          | Read projections and authorized response commands for one waiting Turn                    |
| Environment resources     | `/workspaces/{workspace_id}/environments`                                                                                                   | Desired provider spec, lifecycle, and safe state metadata                                 |
| Secrets                   | Routes owned by [Secret Management](11-secret-management.md)                                                                                | Workspace and User ownership with metadata-only reads and write-only values               |
| Security audit            | `/organizations/{organization_id}/security-audit-events`, `/workspaces/{workspace_id}/security-audit-events`, `/users/me/security-activity` | IAM-owned bounded security projections                                                    |
| Lifecycle events          | `/workspaces/{workspace_id}/events` and resource-scoped event collections                                                                   | Durable replay, not ordinary pagination                                                   |
| Delivery stream           | `GET /workspaces/{workspace_id}/stream`, `WS /workspaces/{workspace_id}/stream`                                                             | SSE or WebSocket over the same retained and live delivery-envelope contract               |
| Usage records             | `/workspaces/{workspace_id}/usage-records`                                                                                                  | Immutable raw records with durable attribution                                            |

The selected [distribution](02-distribution-composition-and-extensions.md) registers exactly the routes for its supported capabilities. An EE or Cloud capability can add Organization lifecycle, external identity, Group, custom-role, or Organization-bound credential routes without inserting license branches into OSS handlers or changing existing resource meaning.

Collection fields, filters, order, and payload limits are defined by the owning resource document. All ordinary collections use the shared cursor shape. Lifecycle replay uses its own monotonic cursor and explicit retention-gap response.

## Interactive Submission

```http
POST /api/v1/threads/{thread_id}/turns
Idempotency-Key: opaque-caller-key
```

The request carries `expected_thread_version`, bounded input, selected Agent authoring reference when permitted, and optional policy-supported metadata. Foundation reads the independent Thread row, requires the current Turn not to be `accepted` or `running`, selects its exact completed `head_turn_id` as the parent, and never infers a parent from Turn timestamps. Acceptance atomically sets `current_turn_id` to the new accepted Turn, preserves the head, increments Thread version, and creates the Turn, first user Item, lifecycle events, idempotency evidence, and outbox intents.

The `202` response is an acceptance receipt containing the Session, Thread, and Turn references plus the resource versions committed by that acceptance. It does not wait for a Worker or Harness result. Later Turn lifecycle transitions can advance Thread version. Repeating the same key and canonical request returns the original receipt; different content conflicts.

## Root Turn Submission

```http
POST /api/v1/workspaces/{workspace_id}/turns
Idempotency-Key: opaque-caller-key
```

Root submission accepts an Agent invocation that does not continue an existing Thread. It selects an immutable Agent revision, bounded input, and declared trigger metadata, then selects or creates one Session and its root Thread under current policy. Acceptance initializes that Thread's root state and atomically commits the version `1` Thread row, one root Turn, lifecycle events, idempotency evidence, and outbox intents. Foundation exposes no standalone empty-Thread create operation.

The `202` response returns the exact Session, Thread, and Turn references. Schedules, webhooks, service requests, and Host-managed asynchronous children use the same Turn acceptance application contract even when their owning ingress is not this public route. Foundation never creates work outside Session, Thread, and Turn identity merely because the invocation is non-interactive.

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

## Thread Fork

Foundation exposes an explicit in-Session Thread fork from one selected
completed Turn:

```http
POST /api/v1/turns/{turn_id}/fork
Idempotency-Key: opaque-caller-key
```

The request carries bounded new input, an optional policy-permitted compatible
Agent revision selection, and fork metadata. Foundation authorizes the source
Turn and Session, verifies the source's frozen state, applies
`HarnessState.fork()`, and atomically creates a child-role Thread with
`origin_kind="fork"` plus its first accepted Turn. The first Turn's
`parent_turn_id` names the source Turn. The response is the same Session,
Thread, and Turn acceptance receipt used by root and continuation submission.

Fork idempotency is scoped to the source Turn, principal, and canonical request.
Repeating the same key returns the original Thread and Turn. A Session fork that
creates a new Session and root Thread remains a distinct Session-domain
operation and is never implied by this route.

## Commands

Commands are subordinate to the resource whose state they mutate:

| Command                          | Route                                                | Required mutation contract                                                                                                                                              |
| -------------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Accept invitation                | `POST /invitations/{invitation_id}/accept`           | Exact single-use token; atomically creates User credentials and RoleBindings                                                                                            |
| Resend invitation                | `POST /invitations/{invitation_id}/resend`           | Current authorization; rotates the token under the same Invitation ID                                                                                                   |
| Revoke invitation                | `POST /invitations/{invitation_id}/revoke`           | Current authorization; terminal for the current invitation                                                                                                              |
| Rotate API key                   | `POST /api-keys/{api_key_id}/rotate`                 | Authorized owner or Service Account Admin; same key ID and immediate cutover                                                                                            |
| Revoke API key                   | `POST /api-keys/{api_key_id}/revoke`                 | Idempotently sets permanent revocation without deleting metadata                                                                                                        |
| Cancel active Turn               | `POST /turns/{turn_id}/cancel`                       | Idempotency key and current authorization; seals the active Turn                                                                                                        |
| Retry failed or cancelled Turn   | `POST /turns/{turn_id}/retry`                        | Target must be the Thread's current failed or cancelled Turn; expected Thread version and idempotency key; advances the same Thread without reopening the sealed source |
| Fork completed Turn              | `POST /turns/{turn_id}/fork`                         | Idempotency key; creates an independent Thread and first Turn from exact frozen source state                                                                            |
| Approve pending action           | `POST /pending-actions/{pending_action_id}/approve`  | Expected pending and Thread versions plus idempotency key                                                                                                               |
| Reject pending action            | `POST /pending-actions/{pending_action_id}/reject`   | Expected pending and Thread versions plus idempotency key                                                                                                               |
| Submit client-tool result        | `POST /pending-actions/{pending_action_id}/complete` | Expected Thread version, exact native result envelope, and idempotency key                                                                                              |
| Supply structured user input     | `POST /pending-actions/{pending_action_id}/respond`  | Expected Thread version, schema-valid bounded response, and idempotency key                                                                                             |
| Resume/pause/destroy Environment | `POST /environments/{environment_id}/{action}`       | Expected Environment version and idempotency key                                                                                                                        |
| Reconcile Environment operation  | `POST /environments/{environment_id}/reconcile`      | Targets the exact unresolved operation identity                                                                                                                         |

A command returns the mutated resource or a durable receipt. `202` means accepted, not completed. Unknown outcome after possible dispatch is reconciled by repeating the same idempotency key or reading the returned resource; clients never generate a new key merely because acknowledgement was lost.

OAuth redirects terminate at `GET /api/v1/connector-callbacks/{provider_key}` and Connector event delivery terminates at `POST /api/v1/connector-events/{trigger_id}`. These are bounded external ingress protocols, not management resources. The callback requires the exact expiring setup state; the event route requires Provider verification and a stable Provider event identity. Path identifiers grant no authority. Success means setup committed, or the event occurrence was accepted or already known; it never waits for Agent execution.

## Read Models

Public resources expose stable product fields and safe references, not ORM objects or provider-private state. Read models follow these boundaries:

- Session, Thread, Turn, and Item use the shared interaction meanings;
- Thread exposes its stored version, Session membership, origin, current Turn, continuation head, and timestamps; current-Turn status supplies the latest execution/result projection and determines whether the Thread is active;
- Turn exposes lifecycle, wait reason, selected revisions, interaction lineage, trigger and retry correlation, cancellation intent, and timestamps;
- TurnAttempt exposes generation, worker-safe status, dispatch phase, lease timing, Harness correlation, and bounded failure evidence, but no credential or process-private value;
- Environment exposes desired spec revision, desired phase, safe lifecycle status, current operation, and effective observations, but never provider resource-state ciphertext or attachment material;
- LifecycleEvent reads preserve event type, schema version, owning-resource sequence, subject, actor when applicable, TurnAttempt attribution, resource version, bounded payload, and commit time;
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

Routes identify which mutable resources require `expected_version` and which retryable creates or commands require `Idempotency-Key`. Their shared wire behavior follows [Platform API Conventions](../api-conventions.md#mutations-and-retries), and their evidence and atomic commit follow [Durable Operations and Outbox](06-durable-operations-and-outbox.md). Immutable revisions and usage records reject mutation rather than carrying artificial versions.

## Errors and Compatibility

The API uses the shared bounded errors and stable codes enforced by the [HTTP ingress contract](05-http-ingress-and-request-contract.md#errors-and-diagnostics). Owning domains add safe details such as `current_version`, `wait_reason`, `pending_kind`, `dispatch_phase`, or `replay_gap`; they never expose traceback, SQL, provider payload, Secret value, credential, attachment, private path, or raw model/tool content.

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
10. Connector Provider catalog routes never install or import caller-selected code, and public Turn routes never forge Trigger or Connection selections.
