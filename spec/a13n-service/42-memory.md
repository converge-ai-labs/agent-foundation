# Long-Term Memory

## Design Position

Service exposes authorized long-term memory through managed Memory Provider resources. A Provider selects one deployment-installed implementation of the neutral Harness memory backend contract. Mem0 OSS is the primary built-in implementation, using the deployment's public native server API; Mem0 Platform is a separate native SDK adapter. Service owns Provider resources, authorization, trusted namespaces, Agent selection, and safe API projections. Backends own memory records and their storage. Service maintains no memory-content table, search index, replicated content, or provider job queue.

[Harness memory](../a13n-harness/09-context-and-memory.md#memory-integration) owns the typed backend contract, backend plugins, recall, and model tools. [Agent Management](28-agent-management.md) owns immutable selections and Run overrides; [IAM](33-identity-and-access-management.md) owns grants. Provider credentials are encrypted, write-only resource fields, never Agent configuration or Harness state.

## Backend and Lifetime

Deployment Provider packages register shared Harness `MemoryBackendPlugin` instances through the `memory` accessor of the existing [Service Provider registration](02-distribution-composition-and-extensions.md#deployment-provider-packages). Built-in types are `a13n.mem0-oss` and `a13n.mem0-platform`. Control and Worker use the same selected definitions, configuration schemas, and credential schemas. They do not share Python clients across processes. Registration creates no client and performs no account, database, or network I/O. This is not a Harness behavior plugin and does not make Control import Agent-selected business plugins.

Service assembles a host-owned `MemoryBackendCatalog` from these definitions; embedded hosts may inject their catalog directly. The same backend plugin owns construction in both cases: Service introduces no parallel factory contract or discovery mechanism. Installation alone never selects an external package. Missing implementations fail explicitly without backend fallback.

`memory.timeout_seconds` bounds backend opening, the operation, and write verification, and defaults to 30 seconds. Each dispatch acquires current eligibility and an encrypted credential snapshot in a short SQL session, closes that session, decrypts locally, and opens the selected backend for the operation. The host closes it on completion, failure, or cancellation. The Capability borrows an authorized facade, not an ambient singleton. No live client, endpoint, or credential enters an accepted Run graph or Harness state.

Integration composes public native operations. It requires no upstream source patch, replacement server image, custom route, or direct storage access. The development image pins unmodified upstream server/core sources for repeatable validation; existing deployments providing the same public API can be used directly. The adapter does not add capabilities missing from the upstream server. Deployment changes do not migrate memory data or select a fallback. Switching storage or embedding configuration requires operator-owned data migration and a new Provider resource.

## Memory Provider Resources

A Memory Provider has an immutable `id`, owning Organization and optional Workspace, implementation `type`, validated `configuration`, display `name`, `enabled` flag, write-only object `credential`, safe `credential_configured` status, and creation/update actor and time metadata. Organization-owned Providers are visible in their Workspaces; Workspace-owned Providers are visible only in their owning Workspace. Names are case-insensitively unique within the owning scope.

The configuration and implementation type are immutable in this version. A storage-identity change requires a new resource; rename, disable/re-enable, and credential rotation for the same target are supported in place. Credential rotation is not permission to change the remote storage identity. Disabled resources retain references and remote records. There is no Provider deletion, automatic migration, fallback, or remote cleanup on disable.

Control exposes type definitions at `/api/v1/memory-provider-types` and `/{provider_type}`, and Provider collections at `/api/v1/organizations/{organization}/memory-providers` and `/api/v1/workspaces/{workspace}/memory-providers`. Collection GET/POST and item GET/PATCH follow the standard scope, collection cursor, safe projection, ETag, and audit conventions. PATCH requires `If-Match`; names, enabled status, and credentials are its mutable fields. Item `/references` lists visible Agent Revision references, including retained non-current revisions, with bounded pagination. It is not a claim that all historical Runs have been enumerated.

`memory_provider.read` permits schema/resource/reference discovery and explicit Provider selection when authoring an Agent or changing a Run selection. `memory_provider.manage` permits creation and mutation in the owning scope. Read grants follow the other Provider read actions; builders can manage Providers. These actions do not grant access to memory contents. An invocation of an already configured Agent uses its frozen Provider selection and the existing Agent/subject memory grants, not a new requirement to administer Providers.

## Subjects and Authority

Every memory belongs to exactly one trusted scope. The provider-visible value is `a13n-` followed by SHA-256 of UTF-8 compact JSON `["a13n.memory.v2", organization_id, workspace_id, provider_id, scope, subject_id]`. IDs and scope kinds are immutable inputs. Models cannot select IDs, filters, credentials, or endpoints. Different Provider resources remain isolated even when they point to the same remote endpoint. Deliberate sharing selects the same Provider resource and still respects Organization, Workspace, and subject isolation. Provider identity is part of every namespace, management record locator, cursor binding, and any cache key.

| Scope    | Subject                                         | Provider field | Management authorization                                                                            |
| -------- | ----------------------------------------------- | -------------- | --------------------------------------------------------------------------------------------------- |
| `thread` | Stable Service Thread ID, preserved across Runs | `run_id`       | Workspace grant, or the Thread's current Run Agent grant; an empty Thread requires Workspace access |
| `agent`  | Stable Agent ID, not Revision or instance ID    | `agent_id`     | Workspace or exact Agent grant                                                                      |
| `user`   | Authenticated human User ID                     | `user_id`      | Workspace grant; service accounts cannot select this scope                                          |

Every operation authorizes the Workspace and subject before provider I/O. Agent and Thread ownership must match the selected Workspace and Organization. A supplied memory ID grants no authority: get, update, and delete verify its subject against the authorized namespace. Update and delete pre-read scope before mutation. Provider record subject identity must remain immutable; operators must not reassign record IDs between subjects behind Service.

Each Worker recall or tool call rechecks current Attempt authority, the retained execution principal, root and selected child Agent invocation grants, and current Agent eligibility. IAM grants come from the current Attempt snapshot, not an arbitrary continuous refresh of bindings. Memory read/write actions must cover the selected scope. A direct-Agent-only User does not receive implicit user-wide memory. No SQL session spans provider I/O.

## Agent Selection

`AgentConfig.memory` is absent/null by default. An object enables the following bounded behavior:

| Field              | Default  | Meaning                                                                                  |
| ------------------ | -------- | ---------------------------------------------------------------------------------------- |
| `provider_id`      | required | Stable visible Memory Provider resource ID; configuration and credentials are not copied |
| `scope`            | null     | Fixed `thread`, `agent`, or `user`; null recalls the union of available scopes           |
| `auto_recall`      | true     | One bounded automatic recall per logical Harness Run                                     |
| `toolset`          | true     | Expose search, list, and explicit-add tools                                              |
| `recall_limit`     | 5        | 1–100 results                                                                            |
| `recall_threshold` | null     | Optional similarity threshold in [0, 1]                                                  |
| `recall_timeout`   | 2        | Positive seconds, at most 300                                                            |
| `recall_required`  | false    | Fail before model work on recall failure instead of omitting context                     |

Each accepted root and child definition retains its own complete selection, including `provider_id`. Authoring and acceptance validate Provider visibility and eligibility; runtime dispatch checks current Provider eligibility and credentials without replacing the accepted selection. A Run override inherits on omission, disables on null, and replaces the complete object otherwise. Backend configuration alone enables no Agent behavior. An unavailable explicit scope fails instead of falling back. Inline children use their own Agent namespace and the stable Service Thread namespace; async children use their own Service Thread. Recall results are untrusted context, never instructions or restored access authority.

## Management API

`GET /api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memory-access` accepts the same subject query as content operations. It requires current subject read access and returns `MemoryAccess { can_write: boolean }`, using the same Workspace, direct-Agent, or current-Thread-head authorization as content writes. It performs no backend I/O and grants no durable authority: each content operation authorizes again. This projection does not require Provider administration permission and is not a connectivity check.

All content routes use `/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories`. The Provider is explicit rather than inferred from current Agent configuration, so a caller can address an old Provider after changing the Agent selection. Query parameter `scope` is required. `thread` and `agent` require `subject_id`; `user` forbids it and uses the authenticated User. Resource identifiers are immutable Service IDs, not aliases. The path memory ID remains an opaque native provider identifier.

| Method and suffix     | Input                                                     | Result                      |
| --------------------- | --------------------------------------------------------- | --------------------------- |
| GET collection        | `limit` 1–1,000 (default 1,000), optional native `cursor` | `MemoryCollection`          |
| POST collection       | `{ "text": string }`                                      | 201 `Memory`                |
| POST `/search`        | `{ "query": string, "limit": 20, "threshold": null }`     | `MemoryCollection`          |
| GET `/{memory_id}`    | Subject query                                             | `Memory`                    |
| PUT `/{memory_id}`    | `{ "text": string }`                                      | `Memory`                    |
| DELETE `/{memory_id}` | Subject query                                             | 204 after confirmed absence |

Text is 1–8,000 characters, stored verbatim with inference disabled. Search query is 1–16,000 characters, limit is 1–100, and threshold is optional in [0, 1]. `Memory` contains `id`, `memory`, and nullable finite `score`; no credentials, provider payload, namespace values, or configuration diagnostics are projected. `MemoryCollection` contains `items` and nullable `pagination`. Null `pagination` identifies a bounded result with no traversal or completeness guarantee. A pagination object contains `next_cursor`; null within that object means the final native page. This distinguishes unavailable traversal from exhausted traversal without provider or development-status fields.

OSS listing calls native `GET /memories` once with trusted subject filters and `top_k=limit`, loading at most 1,000 records by default. It returns `pagination=null` and rejects supplied cursors; no private pagination endpoint or fabricated offset exists. Results retain native ordering and expiry filtering. A short or empty list does not prove exhaustion: the native provider can cap or filter its candidate set. The 1,000-row management bound is not a storage or recall limit. Automatic recall searches the provider independently, not this loaded subset.

A client may paginate the already-loaded subset locally, but must describe counts as loaded records rather than a complete total, and must display a bounded-list hint. For example: "Showing up to 1,000 loaded memories. More may exist; search for relevant memories." Local pages are display slices, not server continuation or a complete export. Search is relevance-ranked and is not a substitute for full export either.

Platform preserves native page/page-size traversal, with each request capped at its 200-record page ceiling. The cursor envelope is bound to Provider resource identity, subject, and requested limit; a mismatch is invalid. Clients follow `pagination.next_cursor` only when a pagination object exists. Returned provider URLs are never followed. Search returns bounded results with `pagination=null` on both backends.

## Completion and Failures

Explicit add requires one completed `ADD` result and a read-back matching subject and exact text. Update verifies the same; delete verifies native not-found after deletion. Provider acceptance, queuing, or an empty response is not durable success. Writes are not idempotent and are never automatically retried.

Authorization denial or a memory outside the selected subject is concealed as not found. Read dependency failure is a bounded `memory_unavailable` error. Failure after possible mutation, including timeout or failed verification, is `memory_write_unconfirmed`; callers inspect current records before deciding to repeat. Cancellation propagates and does not prove rollback. The total backend deadline includes mutation pre-read and verification. Provider error bodies and credentials are not returned.

There is no automatic transcript extraction, model-visible update/delete, bulk deletion, history API, or provider job polling. Deleting a memory does not erase previously observed text from Thread context, exports, or traces. Retention and erasure of those surfaces belong to their owners.
