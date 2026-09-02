# Foundation Management API

## Design Position

Foundation exposes one resource-oriented `/api/v1` management contract for IAM, Agent authoring, managed Skills and Assets, model configuration, interaction, durable Runs, deferred work, Environment configuration, events, and raw usage. The API follows [Platform API Conventions](../api-conventions.md), [Platform Data Conventions](../data-conventions.md), the [HTTP ingress contract](05-http-ingress-and-request-contract.md), the shared [durable operation contract](06-durable-operations-and-outbox.md), and the [Identity and Access Management contract](33-identity-and-access-management.md); this document owns the Foundation resource catalog, common command boundaries, read models, and cross-resource mutation behavior. Agent invocation, continuation, queued submission, deferred feedback, and active control routes are owned by their dedicated control contracts.

Workspace Skill route bodies, ZIP staging, GitHub selectors, revision receipts, authorization, and error codes are owned in detail by [Foundation Skill Management](31-skill-management.md#public-management-api); this catalog does not redefine them.

Asset binary upload, immutable identity, content read, deletion, Agent publication, authorization, and error semantics are owned in detail by [Asset Management](32-asset-management.md#native-management-api); this catalog does not introduce revisions, replacement, or a Run-link resource.

The API is the Native boundary consumed by Foundation SDKs and the remote `agent-foundation` CLI. The [Protocol Gateway](15-protocol-gateway.md) also exposes Hosted AG-UI and A2A as separate standard protocol surfaces; they call the same application authority but are not `/api/v1` aliases. SDKs map this contract and do not invent another lifecycle, retry policy, or HTTP client semantics. Harness Python APIs, Agent Stream Protocol, EIP, provider APIs, and external webhook payloads retain their own contracts. [Managed Harness Plugins and Runtime](36-managed-harness-plugins-and-runtime.md) owns the public Plugin resources and commands plus their artifact validation, on-demand import, and Runner materialization; this document only catalogs their routes. The deployment-authenticated Environment Provider package operator API remains outside `/api/v1` and is not added to public clients. [Foundation Hook Notifications](26-hook-notifications.md) owns durable Hook subscriptions and external Webhook delivery. Native Run SSE, lifecycle event reads, and notification WebSocket behavior remain owned by [Native Streaming and Notifications](21-native-streaming-and-notifications.md).

## Scope and Authorization

Every protected route authenticates one Principal and authorizes an explicit action from the IAM [stable action registry](33-identity-and-access-management.md#stable-action-registry) against the selected Organization, Workspace, or resource. Exact self-service operations use registered actions plus subject equality. Login, invitation acceptance, bootstrap, and password-reset token use authenticate their exact pre-Principal credentials. Identifiers, parent paths, cursors, Item references, RunAttempt IDs, content delivery URLs, and idempotency keys grant no authority.

Workspace collections return only resources visible under current policy. A concealed resource can return `404`. Mutation authorization is re-evaluated at acceptance even when a caller can read the current resource. Run workers use internal application capabilities rather than calling public HTTP routes to mutate lifecycle state.

The route families select primary actions as follows. `GET` includes exact reads, collections, metadata, content, and subordinate read projections unless a row states otherwise. A mutation that references another protected resource also evaluates the additional action required by that resource; the client never supplies an action string. Login, bootstrap, invitation acceptance, and password-reset token completion are pre-Principal credential flows rather than protected resource actions.

| Route family or operation                                                                               | Primary registered action                                                                                                                                              |
| ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Current User profile, credential, and browser-session read or mutation                                  | `user_profile.read`, `user_profile.update`, `user_credentials.manage`, `auth_session.read`, `auth_session.revoke` plus exact User equality                             |
| Organization read and update                                                                            | `organization.read`, `organization.update`                                                                                                                             |
| Workspace read, create, update, and delete                                                              | `workspace.read`, `workspace.create`, `workspace.update`, `workspace.delete`                                                                                           |
| RoleBinding read and mutation                                                                           | `role_binding.read`, `role_binding.manage`                                                                                                                             |
| Invitation read, create, resend, and revoke                                                             | `invitation.read`, `invitation.manage`                                                                                                                                 |
| Service Account read and mutation                                                                       | `service_account.read`, `service_account.manage`                                                                                                                       |
| API key metadata, create, rotate, and revoke                                                            | `api_key.read`, `api_key.manage` plus exact owner or Admin-operation predicates                                                                                        |
| Security audit projections                                                                              | `security_audit.read`                                                                                                                                                  |
| Agent and Revision read, create, update, default selection, lifecycle, duplicate, and invocation        | `agent.read`, `agent.create`, `agent.update`, `agent.revision.create`, `agent.current_revision.set`, `agent.lifecycle`, `agent.duplicate`, `agent.invoke`              |
| Plugin read, upload or lifecycle, and runner-profile activation or deactivation                         | `plugin.read`, `plugin.manage`, `plugin.runtime.manage`                                                                                                                |
| Skill read, create, revision publication, update, delete, and Agent binding                             | `skill.read`, `skill.create`, `skill.revision.publish`, `skill.update`, `skill.delete`, `skill.bind`                                                                   |
| Asset metadata or content read, upload or Agent publication, Run input use, and delete                  | `asset.read`, `asset.create`, `asset.use`, `asset.delete`                                                                                                              |
| Model provider or Model read and Model mutation or test                                                 | `models.read`, `models.manage`                                                                                                                                         |
| Ingress read and lifecycle mutation                                                                     | `ingress.read`, `ingress.manage` plus referenced Service Account and credential predicates                                                                             |
| Route read and mutation                                                                                 | `route.read`, `route.manage` plus referenced Agent and capability predicates                                                                                           |
| Connector read and lifecycle mutation                                                                   | `connector.read`, `connector.manage`                                                                                                                                   |
| Connection read and lifecycle mutation                                                                  | `connection.read`, `connection.manage` plus exact personal-owner or Workspace-shared predicates                                                                        |
| MCPConnection read, credential setup, OAuth, and lifecycle mutation                                     | `mcp_connection.read`, `mcp_connection.manage` plus exact personal-owner or Workspace-shared predicates                                                                |
| Session, Thread, Run, Item, RunAttempt, pending-action, steer-receipt, lineage, and Run Stream read     | `session.read`, `thread.read`, or `run.read` according to the selected owning resource                                                                                 |
| Continue, Continue From, Fork, Retry, Feedback, Steer, and Interrupt                                    | `run.continue`, `run.continue`, `run.fork`, `run.retry`, `run.feedback`, `run.steer`, `run.interrupt`                                                                  |
| Queue read, create, update, delete, reorder, and consume                                                | `queued_submission.read`, `queued_submission.create`, `queued_submission.update`, `queued_submission.delete`, `queued_submission.reorder`, `queued_submission.consume` |
| Environment provider read or Workspace selection, Environment read or mutation, test, and Run selection | `environment_provider.read`, `environment_provider.select`, `environment.read`, `environment.manage`, `environment.test`, `environment.use`                            |
| Workspace-owned Secret metadata read, value mutation, and Agent binding                                 | `secrets.read`, `secrets.manage`, `secrets.bind`; no product action reads plaintext                                                                                    |
| Lifecycle event replay and Native notification subscription                                             | `lifecycle_event.read`, `notification.subscribe` plus current resource-read authority                                                                                  |
| Hook subscription read, create, update, delete, and redrive                                             | `hook_subscription.read`, `hook_subscription.create`, `hook_subscription.update`, `hook_subscription.delete`, `hook_subscription.redrive`                              |
| UsageRecord and trace queries                                                                           | `usage.read`, `trace.read`                                                                                                                                             |

Run acceptance routes can require more than their command action: Start requires `agent.invoke`; Continue and Continue From require `run.continue` plus `agent.invoke`; Fork requires `run.fork` plus invocation authority; waiting Continue requires both `run.continue` and `run.feedback`; and queue admission requires `queued_submission.create` plus invocation authority. Retry, Feedback, queued consumption, native Ingress acceptance, and internal asynchronous acceptance separately reauthorize the persisted Run, queue, Ingress execution, or origin authority Principal defined by their owning contracts. An administrator or internal process authorized to cause the transition does not thereby become that accepted Run's Principal.

## Resource Route Catalog

The following paths are relative to `/api/v1` and are the owning collection and command surfaces. Child reads can also return canonical links to their top-level resource representation; aliases do not create another identity.

| Resource                  | Core routes                                                                                                                                 | Notes                                                                                                                                           |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Authentication            | `/auth/login`, `/auth/logout`, `/auth/password-reset`, `/auth/password-reset/complete`                                                      | Local password and browser-session boundary; no public signup                                                                                   |
| Current User and sessions | `/users/me`, `/users/me/auth-sessions`                                                                                                      | Self profile and browser-session lifecycle                                                                                                      |
| Organizations             | `/organizations`, `/organizations/{organization_id}`                                                                                        | OSS reads its singleton and updates safe settings; no create, delete, transfer, or switch                                                       |
| Organization RoleBindings | `/organizations/{organization_id}/role-bindings`                                                                                            | Canonical Organization grants; `/members` is an authorized User projection                                                                      |
| Organization invitations  | `/organizations/{organization_id}/invitations`                                                                                              | Email invitation with one or more validated grants                                                                                              |
| Workspaces                | `/organizations/{organization_id}/workspaces`, `/workspaces/{workspace_id}`                                                                 | Organization-owned collaboration and resource-isolation boundary                                                                                |
| Workspace RoleBindings    | `/workspaces/{workspace_id}/role-bindings`                                                                                                  | Canonical Workspace grants; `/members` is an authorized User projection                                                                         |
| Workspace invitations     | `/workspaces/{workspace_id}/invitations`                                                                                                    | Workspace Admin invitation with automatic Organization Member grant                                                                             |
| Service Accounts          | `/workspaces/{workspace_id}/service-accounts`, `/service-accounts/{service_account_id}`                                                     | Workspace-owned non-human Principal lifecycle                                                                                                   |
| Personal API keys         | `/workspaces/{workspace_id}/personal-api-keys`, `/api-keys/{api_key_id}`                                                                    | Current User's Workspace-bound keys; safe Admin metadata projection                                                                             |
| Service Account API keys  | `/service-accounts/{service_account_id}/api-keys`, `/api-keys/{api_key_id}`                                                                 | Admin-managed Workspace-bound keys                                                                                                              |
| Agent Agents              | `/workspaces/{workspace_id}/agents`, `/agents/{agent_id}`                                                                                   | Mutable authoring configuration and default-Revision pointer                                                                                    |
| Agent Agent Revisions     | `/agents/{agent_id}/revisions`, `/agent-agent-revisions/{agent_revision_id}`                                                                | Immutable creation, paginated collection, and exact Revision read; no in-place mutation                                                         |
| Plugins                   | `/plugins`, `/plugins/{plugin_id}`                                                                                                          | Deployment-level stable identity; active-Version pointer is runner-profile-only                                                                 |
| Plugin Versions           | `/plugins/{plugin_id}/versions`, `/plugin-versions/{plugin_version_id}`                                                                     | Immutable artifact metadata; no in-place mutation                                                                                               |
| Skill uploads             | `/workspaces/{workspace_id}/skill-uploads`, `/skill-uploads/{upload_id}`                                                                    | Expiring bounded ZIP staging receipts; not Agent or package authority                                                                           |
| Skills                    | `/workspaces/{workspace_id}/skills`, `/skills/{skill_id}`                                                                                   | Stable Workspace resources with versioned display/head selection                                                                                |
| Skill revisions           | `/skills/{skill_id}/revisions`, `/skill-revisions/{skill_revision_id}`, `/skill-revisions/{skill_revision_id}/content`                      | Immutable normalized packages; binary content download is separately authorized                                                                 |
| Assets                    | `/workspaces/{workspace_id}/assets`, `/assets/{asset_id}`, `/assets/{asset_id}/content`                                                     | Binary create plus immutable metadata/content reads and terminal delete; no update or revision                                                  |
| Model Providers           | `/model-providers`                                                                                                                          | Read-only trusted Provider registry; not an installation API                                                                                    |
| Models                    | `/workspaces/{workspace_id}/models`, `/workspaces/{workspace_id}/models/{model_id}`                                                         | Mutable current Model resources with integer optimistic concurrency                                                                             |
| Ingresses                 | `/workspaces/{workspace_id}/ingresses`, `/ingresses/{ingress_id}`                                                                           | Inbound-capable provider identities, execution Service Account, allowed Agents, default Agent, typed provider configuration, and safe lifecycle |
| Routes                    | `/ingresses/{ingress_id}/routes`, `/routes/{route_id}`                                                                                      | Provider-specific event matching and policy, Agent override, safe input mapping, input batching, and per-Agent capability overlays              |
| Connectors                | `/workspaces/{workspace_id}/connectors`, `/connectors/{connector_id}`                                                                       | Pluggable external Connector service endpoint, typed configuration, access credential, and lifecycle                                            |
| Connections               | `/workspaces/{workspace_id}/connections`, `/connections/{connection_id}`                                                                    | Safe Connector-held account reference and lifecycle projection; third-party credentials remain private                                          |
| MCP Connections           | `/workspaces/{workspace_id}/mcp-connections`, `/mcp-connections/{mcp_connection_id}`                                                        | Streamable HTTP endpoint, personal or Workspace scope, safe authorization status, and lifecycle                                                 |
| MCP credentials           | `/mcp-connections/{mcp_connection_id}/credentials`, `/mcp-connections/{mcp_connection_id}/authorize`, `/oauth/mcp/callback`                 | Replaces write-only bearer or bounded static-header credentials, or starts and completes the standard MCP OAuth client flow                     |
| Schedules                 | `/workspaces/{workspace_id}/schedules`, `/schedules/{schedule_id}`                                                                          | Mutable schedule source targeting one stable Agent; native provider events use independent Ingress and routing                                  |
| Sessions                  | `/workspaces/{workspace_id}/sessions`                                                                                                       | Hosted interaction tree and product/presentation scope                                                                                          |
| Threads                   | `/sessions/{session_id}/threads`, `/threads/{thread_id}`                                                                                    | Independently versioned advancing histories within one Session                                                                                  |
| Thread queue              | `/threads/{thread_id}/queued-submissions`, `/queued-submissions/{queued_submission_id}`                                                     | Editable ordered queued Run intents; a queued entry is not a Run                                                                                |
| Runs                      | `/workspaces/{workspace_id}/runs`, `/threads/{thread_id}/runs`, `/runs/{run_id}`, `/runs/{run_id}/lineage`                                  | Root acceptance, queue-if-busy existing-Thread submission, and exact ancestor lineage                                                           |
| Steer receipts            | `/runs/{run_id}/steers/{steer_id}`                                                                                                          | Exact authorized acceptance, waiting/active binding, FIFO position, and consumption status for one submitted steer                              |
| Items                     | `/runs/{run_id}/items`                                                                                                                      | Ordered user-visible semantic records                                                                                                           |
| RunAttempts               | `/runs/{run_id}/attempts`, `/run-attempts/{run_attempt_id}`                                                                                 | Read-only operational history; workers mutate internally                                                                                        |
| Pending actions           | `/runs/{run_id}/pending-actions`                                                                                                            | Read-only projections of one waiting Run's frozen pending set                                                                                   |
| Environment Providers     | `/environment-providers`, `/workspaces/{workspace_id}/environment-providers/{provider_key}`                                                 | Read-only trusted catalog plus exact Workspace provider selection                                                                               |
| Environments              | `/workspaces/{workspace_id}/environments`, `/environments/{environment_id}`                                                                 | Stable named desired configuration and current immutable revision                                                                               |
| Environment revisions     | `/environments/{environment_id}/revisions`, `/environment-revisions/{environment_revision_id}`                                              | Immutable Provider configuration, credential references, permissions, and exact Provider lock                                                   |
| Secrets                   | Routes owned by [Secret Management](27-secret-management.md)                                                                                | Workspace and User ownership with metadata-only reads and write-only values                                                                     |
| Security audit            | `/organizations/{organization_id}/security-audit-events`, `/workspaces/{workspace_id}/security-audit-events`, `/users/me/security-activity` | IAM-owned bounded security projections                                                                                                          |
| Lifecycle events          | `/workspaces/{workspace_id}/events`, `/runs/{run_id}/events`, `/run-attempts/{run_attempt_id}/events`                                       | Workspace cursor replay plus resource-sequence gap recovery                                                                                     |
| Run stream                | `GET /runs/{run_id}/stream`                                                                                                                 | Detailed Run SSE with bounded replay and live cutover                                                                                           |
| Native notifications      | `WS /notifications`                                                                                                                         | Explicit Thread or Workspace subscriptions; best-effort wake-ups without replay                                                                 |
| Hook subscriptions        | `/workspaces/{workspace_id}/hook-subscriptions`, `/hook-subscriptions/{hook_subscription_id}`                                               | Long-lived creation plus management of every durable subscription, including Run-inline resources                                               |
| Usage records             | `/workspaces/{workspace_id}/usage-records`                                                                                                  | Immutable raw records with durable attribution                                                                                                  |
| Trace queries             | `/workspaces/{workspace_id}/traces`, `/workspaces/{workspace_id}/traces/{trace_id}`                                                         | Authorized provider-backed list and detail views; not durable Foundation resources                                                              |

The selected [distribution](02-distribution-composition-and-extensions.md) registers exactly the routes for its supported capabilities. An EE or Cloud capability can add Organization lifecycle, external identity, Group, custom-role, or Organization-bound credential routes without inserting license branches into OSS handlers or changing existing resource meaning.

Collection fields, filters, order, and payload limits are defined by the owning resource document. All ordinary collections use the shared cursor shape. Lifecycle replay uses its own monotonic cursor and explicit retention-gap response.

## Agent Invocation and Control

[Agent Input](17-agent-input.md) owns the versioned `AgentInput` protocol. [Agent Control: Input and Continuation](18-agent-control-input-and-continuation.md) owns start, the existing-Thread Run submission request and immediate-acceptance branches, explicit historical same-Thread continuation, fork, retry, atomic waiting feedback, and explicit waiting Continue with defaults. [Agent Control: Active Execution](19-agent-control-active-execution.md) owns the Thread inbox FIFO plus the Run steer, exact steer-status read, and interrupt routes. The Thread inbox remains an internal persistence model rather than a generic public resource. [Agent Control: Queued Submissions](20-agent-control-queued-submissions.md) owns the existing-Thread route's queue branch, queued-submission resources, ordering, editing, state-first completion handoff, terminal recovery drain, and atomic consumption into a Run. These operations follow the common API, authorization, idempotency, and durable mutation conventions referenced by this catalog. The owning contracts define the exact AgentRevision and Runtime-lock selection, input validation, Thread advancement, inbox receipt, and acceptance receipt rather than duplicating those schemas here.

Every input-bearing Run command can additionally carry one exact Run-scoped HookSubscription input. Immediate acceptance normalizes it into the same resource exposed by the Hook-subscription routes and returns its ID in the Run acceptance receipt. When an existing-Thread Run submission queues, its Hook input remains editable unaccepted intent and creates no subscription until the queued submission is consumed into a Run. [Hook Notifications](26-hook-notifications.md#durable-hook-subscriptions) owns the input, authorization, transaction, and delivery semantics.

## Thread Reads

Foundation exposes the stored Thread resource directly:

```http
GET /api/v1/threads/{thread_id}
GET /api/v1/sessions/{session_id}/threads?limit=...&cursor=...
```

The exact read and each collection item have this conceptual wire shape:

```python
class Thread:
    thread_id: str
    version: int
    queue_version: int
    session_id: str
    role: Literal["root", "child"]
    origin_kind: Literal["new", "fork", "child"]
    origin_thread_id: str | None
    origin_run_id: str | None
    head_run_id: str | None
    current_run_id: str
    created_at: datetime
    updated_at: datetime
```

The response is an authorized projection of the [durable Thread row](11-thread-persistence.md), not a grouping synthesized from Run activity. The Session collection defaults to deterministic `updated_at desc, thread_id desc` order and supports explicit creation order. An optional bounded current-Run summary comes from `current_run_id`; it never changes Thread version or head meaning. Foundation exposes no general Thread PATCH or independent hard-delete route. Origin references are present only when the caller can currently read their source; otherwise both source identifiers are omitted without weakening authorization for the current Thread.

## Commands

Commands are subordinate to the resource whose state they mutate:

| Command                       | Route                                                        | Required mutation contract                                                                                                                                                                                                                           |
| ----------------------------- | ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Accept invitation             | `POST /invitations/{invitation_id}/accept`                   | Exact single-use token; atomically creates User credentials and RoleBindings                                                                                                                                                                         |
| Resend invitation             | `POST /invitations/{invitation_id}/resend`                   | Current authorization; rotates the token under the same Invitation ID                                                                                                                                                                                |
| Revoke invitation             | `POST /invitations/{invitation_id}/revoke`                   | Current authorization; terminal for the current invitation                                                                                                                                                                                           |
| Rotate API key                | `POST /api-keys/{api_key_id}/rotate`                         | Authorized owner or Service Account Admin; same key ID and immediate cutover                                                                                                                                                                         |
| Revoke API key                | `POST /api-keys/{api_key_id}/revoke`                         | Idempotently sets permanent revocation without deleting metadata                                                                                                                                                                                     |
| Create Agent Revision         | `POST /agents/{agent_id}/revisions`                          | Idempotency key; creates one immutable Revision from the current config without changing the default pointer                                                                                                                                         |
| Set default Agent Revision    | `POST /agents/{agent_id}/set-default-revision`               | Idempotency key and exact existing Revision; changes only the Agent's default selector                                                                                                                                                               |
| Duplicate Agent Agent         | `POST /agents/{agent_id}/duplicate`                          | Idempotency key; creates an independent enabled custom Agent and Revision 1                                                                                                                                                                          |
| Change Agent lifecycle        | `POST /agents/{agent_id}/{action}`                           | `enable`, `disable`, `archive`, or `unarchive` with expected resource version                                                                                                                                                                        |
| Upload Plugin Version         | `POST /plugins`, `POST /plugins/{plugin_id}/versions`        | Idempotency key; first upload creates Plugin and Version, later uploads add an immutable Version                                                                                                                                                     |
| Activate Plugin Version       | `POST /plugin-versions/{plugin_version_id}/activate`         | Runner only: idempotency key; succeeds only after every serviceable Worker can run the candidate Runtime lock; on-demand returns `plugin_runtime_mode_unsupported`                                                                                   |
| Deactivate Plugin             | `POST /plugins/{plugin_id}/deactivate`                       | Runner only: idempotency key; removes the key from future resolution without rewriting frozen Revisions; on-demand returns `plugin_runtime_mode_unsupported`                                                                                         |
| Change Plugin lifecycle       | `POST /plugins/{plugin_id}/{action}`                         | `archive` or `unarchive`; archive requires an inactive uploaded Plugin                                                                                                                                                                               |
| Test candidate Model          | `POST /workspaces/{workspace_id}/models/test`                | Synchronous candidate test using Secret references; creates no health resource                                                                                                                                                                       |
| Replace MCP credentials       | `POST /mcp-connections/{mcp_connection_id}/credentials`      | Exact current version and write-only bearer or complete static-header value set; atomically replaces the MCPConnection-owned credential bundle without changing its immutable endpoint, auth mode, owner, or static-header names                     |
| Test Environment revision     | `POST /environment-revisions/{environment_revision_id}/test` | Validates the exact revision and current credential sources and constructs a fresh adapter without external I/O; creates no target or retained state                                                                                                 |
| Copy Model                    | `POST /workspaces/{workspace_id}/models/{model_id}/copy`     | Idempotency key; creates a new Model and copies no Secret value                                                                                                                                                                                      |
| Submit existing-Thread Run    | `POST /threads/{thread_id}/runs`                             | Expected Thread version and idempotency key; ordinary input accepts from an eligible completed/null head or queues, while explicit `waiting_resolution.mode="defaults"` accepts one waiting successor without consuming the queue                    |
| Continue from completed Run   | `POST /runs/{run_id}/continue`                               | Source can be any retained readable completed Run in its Thread; expected Thread version and idempotency key; atomically selects the source as head and creates its successor                                                                        |
| Reorder queued submissions    | `POST /threads/{thread_id}/queued-submissions/reorder`       | Expected queue version and the exact ordered set of currently queued IDs; changes no Run or Thread advancement version                                                                                                                               |
| Consume queued submission     | `POST /threads/{thread_id}/queued-submissions/consume`       | Expected Thread and queue versions plus idempotency key; atomically marks one entry consumed and accepts a continuation from the completed head or a root-like Run when the failed/cancelled current Run has a null head                             |
| Steer running or waiting Run  | `POST /runs/{run_id}/steer`                                  | Idempotency key, current authorization, and canonical `AgentInput`; appends one Thread-FIFO entry bound to the current running Run or sourced to the current/head waiting Run                                                                        |
| Interrupt active Run          | `POST /runs/{run_id}/interrupt`                              | Idempotency key and current authorization; seals the active Run as cancelled, suppresses async results originating from it, supersedes other pending inbox delivery bound to it, and wakes the owning Worker best-effort                             |
| Retry failed or cancelled Run | `POST /runs/{run_id}/retry`                                  | Target must be the Thread's current failed or cancelled Run; expected Thread version and idempotency key; copies accepted input, state-parent edge, Agent Revision, `EffectiveAgentConfig`, protected payload, and Runtime lock without reopening it |
| Fork completed Run            | `POST /runs/{run_id}/fork`                                   | Idempotency key; creates an independent Thread and first Run from exact frozen source state                                                                                                                                                          |
| Finalize waiting feedback     | `POST /runs/{run_id}/feedback`                               | Expected Thread version, exact sealed-state digest, idempotency key, and an explicit subset of approve, reject, complete, or respond entries; omitted actions normalize to reject or no-response                                                     |

A command returns the mutated resource or a durable receipt. `202` means accepted, not completed. For commands that declare an idempotency key, an unknown outcome after possible dispatch is reconciled by repeating the same key or reading the returned resource; clients never generate a new key merely because acknowledgement was lost.

Successful runner-profile Plugin runtime commands return a thin receipt containing `operation_id`, `status` in `running`, `succeeded`, or `failed`, resulting resource references, and bounded failure evidence. `GET /api/v1/operations/{operation_id}` reads a known receipt. Foundation exposes no operation collection, update, deletion, or independent business lifecycle; this route is only the query boundary for an asynchronous command result.

The route catalog is stable across Plugin Runtime profiles so SDKs expose one surface. In `on_demand`, Upload, List, Get, Archive, and exact Agent bindings remain available, while Activate and Deactivate fail before dispatch with `409 plugin_runtime_mode_unsupported` and create no operation receipt.

Connector account-setup completion, MCP OAuth callbacks, and native provider event delivery are bounded external protocol routes, not management resources. Connector setup carries only exact expiring Connector correlation and never returns a third-party account credential to Foundation. MCP OAuth callbacks consume exact single-use setup state. Native ingress requires provider authentication and one stable external event identity. A path identifier grants no authority. Success means the safe resource projection committed or the event was durably admitted or already known; it never waits for Agent execution. Their ownership boundaries are defined by [External Connectivity](40-connectivity/README.md).

## Read Models

Public resources expose stable product fields and safe references, not ORM objects or provider-private state. Read models follow these boundaries:

- Session, Thread, Run, and Item use the shared interaction meanings;
- Thread exposes its stored advancement and queue versions, Session membership, origin, current Run, continuation head, and timestamps; current-Run status supplies the latest execution/result projection and determines whether the Thread is active;
- QueuedSubmission exposes the immutable authority Principal, authorized submitted Run intent, queue order while queued, and its accepted Run correlation after consumption; it exposes no caller credential, role snapshot, execution lease, attempt, failure state, or object-store locator;
- an exact steer receipt read exposes only authorized safe steer identity, immutable accepted-against Run, current active/waiting binding, delivery sequence, status, consumption correlation, and timestamps; it exposes neither the submitted payload nor an object-store locator;
- Model exposes only safe provider, endpoint, credential-reference, capability, lifecycle, and actor metadata;
- Skill exposes safe Workspace identity, version, current immutable revision, manifest, content digest, source kind, resolved GitHub commit when applicable, and actor metadata; it exposes no staged object, Secret selector, credential, native path, or object-store key;
- Asset exposes one immutable Workspace publication's safe filename, media type, byte size, content digest, creation source, and creation time; content uses its separately authorized binary route, and neither read exposes an object key or Environment path;
- Run exposes lifecycle, wait reason, its immutable authority Principal reference, accepted input kind, stable Agent, exact AgentRevision and Runtime-lock selection, non-secret effective configuration plus digest, safe model observation, interaction lineage, `retry_of_run_id`, bounded source correlation, cancellation intent, and timestamps; it never exposes the encrypted sensitive payload or plaintext;
- RunAttempt exposes generation, worker-safe status, immutable Worker build identity, safe model observation, lease timing, Harness correlation, planned yield reason or bounded failure evidence, but no generic tool invocation history, credential, or process-private value;
- Environment exposes safe metadata and its current immutable revision; an authorized EnvironmentRevision detail exposes its protected non-secret Provider configuration, Provider package lock, credential requirements, and `read_only`, `read_write`, or `full` access without Secret values or current Environment state;
- LifecycleEvent reads preserve event identity and type, schema version, owning-resource sequence, subject, actor when applicable, RunAttempt attribution, resource version, bounded payload, and commit time;
- HookSubscription reads preserve version, active or paused status, exact Hook names, bounded resource filters, callback URL, managed signing-Secret reference, signature profile, and timestamps without URL credentials or signing Secret values;
- UsageRecord reads preserve immutable identity and attribution.
- Trace reads expose normalized provider telemetry only after validating exact Foundation RunAttempt correlation and current resource visibility; they do not become lifecycle, retained interaction, audit, or usage authority.

An Item read never substitutes for lifecycle event replay, and an event read never expands private Item or object-backed content without separate authorization.

## Run Lineage Read

Foundation exposes the exact ancestor path of one caller-selected Run:

```http
GET /api/v1/runs/{run_id}/lineage
```

The route selects an explicit head and follows the [`parent_run_id` persistence contract](12-run-persistence.md#git-like-run-dag). It does not infer the latest Run or accept a Thread selector in place of the head. Its direct response has this conceptual shape:

```python
class RunLineageEntry:
    run_id: str
    session_id: str
    thread_id: str
    parent_run_id: str | None
    lineage_kind: RunLineageKind
    status: RunStatus
    depth_from_head: int
    created_at: datetime


class RunLineage:
    head_run_id: str
    items: tuple[RunLineageEntry, ...]
```

`items` is ordered from root to head. The head has `depth_from_head=0`; each ancestor's depth is its number of parent edges from the head. The path can cross Session and Thread boundaries through retained parent edges and never includes siblings or descendants.

The service authorizes the head and every ancestor under current tenant, principal, visibility, archival, and retention policy. An absent or concealed head returns `404 run_not_found`. A missing or unauthorized ancestor, cycle, or path deeper than 1,000 Runs returns `409 run_lineage_invalid` with safe reason `missing_parent`, `cycle`, or `max_depth`; the route never returns a complete-looking prefix. The complete bounded path is one response and is not cursor-paginated.

## Pagination, Filtering, and Replay

Ordinary collections use `limit` and opaque `cursor` exactly as defined by Platform API Conventions. Each resource defines deterministic default ordering and explicit filters. Cursors are bound to principal scope, filter, order, and retention.

The Trace collection additionally follows the provider-backed range, search, cursor, and authorization rules in [Trace Query](39-trace-query.md). Its cursor is ordinary query continuation, not telemetry or lifecycle authority.

Workspace lifecycle replay uses its own opaque cursor over authorized durable `lifecycle_events`. It supports only owning lifecycle/resource filters and reports an explicit retained-floor gap. It does not include detailed Run text, reasoning, tool, or message deltas.

`GET /api/v1/runs/{run_id}/stream` opens the detailed Run SSE and resumes with `Last-Event-ID`. `WS /api/v1/notifications` opens the distinct Native best-effort notification channel and begins with no subscriptions. Their framing, topic registry, cursor, gap, heartbeat, and reconciliation behavior are owned by [Native Streaming and Notifications](21-native-streaming-and-notifications.md). There is no `GET` or `WS /api/v1/workspaces/{workspace_id}/stream` route and no detailed Run WebSocket.

## Concurrency and Idempotency

Routes identify which versioned mutations require `expected_version`, which boundaries with multiple independent versions require a qualified precondition, which mutable representations require a strong `ETag` and `If-Match`, and which retryable creates or commands require `Idempotency-Key`. Existing-Thread Run submission and every accepted Thread advancement compare `expected_thread_version`; queue-wide reorder and consumption also compare the independent `expected_queue_version`; an entry edit or delete compares that entry's `expected_version`. The queue-if-busy route selects immediate Run acceptance or queue admission only after verifying the Thread version, and its idempotency evidence preserves that selected result. Their shared wire behavior follows [Platform API Conventions](../api-conventions.md#mutations-and-retries), and their evidence and atomic commit follow [Durable Operations and Outbox](06-durable-operations-and-outbox.md). Immutable revisions, Assets, and usage records reject content mutation rather than carrying artificial versions; Asset upload uses idempotency and Asset deletion is its only terminal transition. Model revision publication uses the canonical resource `version`; Model metadata uses `ETag` and `If-Match` under [Model Management](30-model-management.md).

## Errors and Compatibility

The API uses the shared bounded errors and stable codes enforced by the [HTTP ingress contract](05-http-ingress-and-request-contract.md#errors-and-diagnostics). Owning domains add safe details such as `current_version`, `wait_reason`, `pending_kind`, or `replay_gap`; they never expose traceback, SQL, provider payload, Secret value, credential, attachment, private path, or raw model/tool content. A command unavailable under the deployment's durable Plugin Runtime profile returns `409 plugin_runtime_mode_unsupported`. Run submission returns `400 skill_selection_invalid` when an effective Skill list is invalid, inaccessible, too large, or has duplicate final names and creates no Run.

`/api/v1` evolves additively. Removing or repurposing a field, changing a command side-effect boundary, weakening authorization, changing idempotency scope, or changing resource identity requires an incompatible API version. First-party SDK releases can add idiomatic convenience methods but preserve the same resources, receipts, errors, and retry boundaries.

## Invariants

01. Every protected Foundation operation authenticates a Principal and authorizes an explicit resource action; credential-establishment routes validate their exact one-time or login credential first.
02. Existing-Thread and root Run submission are distinct acceptance routes over the same Run resource and allocation contract.
03. Thread reads come from the independent durable Thread resource, and accepted advancement compares and updates its exact version.
04. Public API acceptance never waits for Harness completion.
05. RunAttempt mutation is internal; public RunAttempt routes are bounded operational reads.
06. Commands use resource-scoped paths and explicitly declare the idempotency evidence or version checks required by their mutation contract.
07. Replay cursors, identifiers, receipts, and signed URLs grant no authority by possession.
08. API read models contain no process-local object, current Environment state, external provider private state, adapter, entered facade, credential, or Secret value.
09. SDKs and the CLI consume this API rather than defining parallel lifecycle or retry semantics.
10. Connectivity and Model Provider routes never install or import caller-selected code, and public Run routes never forge native provider identity, routing, safe external-resource, tool, or model selections.
11. Workspace lifecycle events, Run SSE, and Native WebSocket notifications have separate envelopes, continuation behavior, and replay guarantees.
12. A Run request selects a stable Agent and may select an exact retained Revision plus typed override; acceptance pins one exact AgentRevision, complete `EffectiveAgentConfig`, and internal Runtime lock.
13. A Plugin command receipt is a bounded queryable command result, not a general-purpose product resource.
14. Queue-only mutation creates no Run and changes no Thread advancement reference; queue consumption atomically changes the queue and accepts one Run.
15. Ordinary existing-Thread Run submission queues behind earlier submissions and while the current Run is `accepted`, `running`, or `waiting`; it never bypasses queue order. Explicit waiting Continue resolves the selected waiting head without consuming that queue. A completed source can seal together with first-entry consumption and successor acceptance only after its eligible inbox FIFO is empty; otherwise terminal recovery drain remains independently repeatable.
16. Every distinct Asset upload creates one immutable Asset identity; the API exposes no overwrite, rename, revision, or Run-to-Asset link surface.
17. Trace query views normalize authorized backend telemetry and never become Foundation resources or durable authority.
