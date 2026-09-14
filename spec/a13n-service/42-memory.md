# Long-Term Memory

## Design Position

Service exposes authorized long-term memory backed by Mem0. OSS is the primary supported backend, using its native server and PGVector storage. Platform is a separate native SDK adapter. Service owns authorization, trusted namespaces, Agent selection, and safe API projections; Mem0 owns records, embeddings, and native history. Service maintains no memory table, search index, replicated content, or provider job queue.

[Harness memory](../a13n-harness/09-context-and-memory.md#mem0-long-term-memory) owns recall and model tools. [Agent Management](28-agent-management.md) owns immutable selections and Run overrides; [IAM](33-identity-and-access-management.md) owns grants. Process configuration owns deployment secrets, not Agent resources.

## Backend and Lifetime

`memory.provider` selects `none` (default), `oss`, or `platform`. Enabled backends require `api_key`; OSS also requires `base_url`. `timeout_seconds` bounds one management operation's backend I/O, including verification, and defaults to 30 seconds. Control and Worker each open one process-lifetime transport and close it on shutdown; Connectivity opens none. The library Capability borrows its backend. No Run reconstructs an SDK client or reads ambient Mem0 credentials.

The supported OSS image pins the native server source and Mem0 core and adds PGVector keyset pagination. It is not a Platform protocol gateway. Other deployments must provide the same native pagination contract. Deployment changes select a backend, not an automatic data migration or fallback. Switching backend or embedding model requires operator-owned data migration.

## Subjects and Authority

Every memory belongs to exactly one trusted scope. The provider-visible value is `a13n-` followed by SHA-256 of UTF-8 compact JSON `["a13n.memory.v1", organization_id, workspace_id, scope, subject_id]`. IDs and scope kinds are immutable inputs. Models cannot select IDs, filters, credentials, or endpoints.

| Scope    | Subject                                         | Provider field | Management authorization                                                                            |
| -------- | ----------------------------------------------- | -------------- | --------------------------------------------------------------------------------------------------- |
| `thread` | Stable Service Thread ID, preserved across Runs | `run_id`       | Workspace grant, or the Thread's current Run Agent grant; an empty Thread requires Workspace access |
| `agent`  | Stable Agent ID, not Revision or instance ID    | `agent_id`     | Workspace or exact Agent grant                                                                      |
| `user`   | Authenticated human User ID                     | `user_id`      | Workspace grant; service accounts cannot select this scope                                          |

Every operation authorizes the Workspace and subject before provider I/O. Agent and Thread ownership must match the selected Workspace and Organization. A supplied memory ID grants no authority: get, update, and delete verify its subject against the authorized namespace. Update and delete pre-read scope before mutation. Provider record subject identity must remain immutable; operators must not reassign record IDs between subjects behind Service.

Each Worker recall or tool call rechecks current Attempt authority, the retained execution principal, root and selected child Agent invocation grants, and current Agent eligibility. IAM grants come from the current Attempt snapshot, not an arbitrary continuous refresh of bindings. Memory read/write actions must cover the selected scope. A direct-Agent-only User does not receive implicit user-wide memory. No SQL session spans provider I/O.

## Agent Selection

`AgentConfig.memory` is absent/null by default. An object enables the following bounded behavior:

| Field              | Default | Meaning                                                                        |
| ------------------ | ------- | ------------------------------------------------------------------------------ |
| `scope`            | null    | Fixed `thread`, `agent`, or `user`; null recalls the union of available scopes |
| `auto_recall`      | true    | One bounded automatic recall per logical Harness Run                           |
| `toolset`          | true    | Expose search, list, and explicit-add tools                                    |
| `recall_limit`     | 5       | 1–100 results                                                                  |
| `recall_threshold` | null    | Optional similarity threshold in [0, 1]                                        |
| `recall_timeout`   | 2       | Positive seconds, at most 300                                                  |
| `recall_required`  | false   | Fail before model work on recall failure instead of omitting context           |

Each accepted root and child definition retains its own selection. A Run override inherits on omission, disables on null, and replaces the complete object otherwise. Backend configuration alone enables no Agent behavior. An unavailable explicit scope fails instead of falling back. Inline children use their own Agent namespace and the stable Service Thread namespace; async children use their own Service Thread. Recall results are untrusted context, never instructions or restored access authority.

## Management API

All routes use `/api/v1/workspaces/{workspace}/memories`. Query parameter `scope` is required. `thread` and `agent` require `subject_id`; `user` forbids it and uses the authenticated User. Resource identifiers are immutable Service IDs, not aliases. The path memory ID remains an opaque native provider identifier.

| Method and suffix     | Input                                                 | Result                      |
| --------------------- | ----------------------------------------------------- | --------------------------- |
| GET collection        | `limit` 1–100 (default 50), optional `cursor`         | `MemoryCollection`          |
| POST collection       | `{ "text": string }`                                  | 201 `Memory`                |
| POST `/search`        | `{ "query": string, "limit": 20, "threshold": null }` | `MemoryCollection`          |
| GET `/{memory_id}`    | Subject query                                         | `Memory`                    |
| PUT `/{memory_id}`    | `{ "text": string }`                                  | `Memory`                    |
| DELETE `/{memory_id}` | Subject query                                         | 204 after confirmed absence |

Text is 1–8,000 characters, stored verbatim with inference disabled. Search query is 1–16,000 characters, limit is 1–100, and threshold is optional in [0, 1]. `Memory` contains `id`, `memory`, and nullable finite `score`; no credentials, provider payload, namespace values, or configuration diagnostics are projected. `MemoryCollection` contains `items` and nullable `next_cursor`.

The cursor envelope is bound to subject, backend kind, and page size. A mismatched cursor is invalid. OSS pages traverse native UUID IDs ascending using an exclusive keyset cursor and at most `limit + 1` storage rows. Expired rows are hidden after selection; an empty page can still have a continuation. Clients must stop only when `next_cursor` is null. Pages are not snapshots: concurrent inserts before the cursor become visible after restarting traversal. There is no 1,000-row cutoff or offset over a truncated response. Platform uses its native page/page-size continuation and never follows returned URLs.

## Completion and Failures

Explicit add requires one completed `ADD` result and a read-back matching subject and exact text. Update verifies the same; delete verifies native not-found after deletion. Provider acceptance, queuing, or an empty response is not durable success. Writes are not idempotent and are never automatically retried.

Authorization denial or a memory outside the selected subject is concealed as not found. Read dependency failure is a bounded `memory_unavailable` error. Failure after possible mutation, including timeout or failed verification, is `memory_write_unconfirmed`; callers inspect current records before deciding to repeat. Cancellation propagates and does not prove rollback. The total backend deadline includes mutation pre-read and verification. Provider error bodies and credentials are not returned.

There is no automatic transcript extraction, model-visible update/delete, bulk deletion, history API, or provider job polling. Deleting a memory does not erase previously observed text from Thread context, exports, or traces. Retention and erasure of those surfaces belong to their owners.
