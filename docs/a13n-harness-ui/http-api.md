# Harness UI HTTP API

The optional browser server exposes a local App API even though its bundled browser only implements authentication/status. These endpoints do not imply that browser chat, configuration editors, Host Files, Git, or terminal panels are implemented.

This is **not Service Native `/api/v1`**. It has a shared instance access key, process-local operation receipts, and best-effort live subscriptions. Use the [browser-server guide](webui.md) to start/configure the listener and [Python embedding guide](embedding.md) for App ownership.

## Authenticate and discover the contract

Set `HUI_URL` to the listener origin and `HUI_API_KEY` to its configured access key:

```bash
curl --fail-with-body "$HUI_URL/api/status" \
  -H "Authorization: Bearer $HUI_API_KEY"

curl --fail-with-body "$HUI_URL/api/openapi.json" \
  -H "Authorization: Bearer $HUI_API_KEY"
```

The status contract has `api_version: "1"`, package/build information, App status, access mode, and feature flags. The live OpenAPI JSON describes exact request/response models and constraints. Swagger and ReDoc pages are disabled. The [checked schema](../assets/reference/harness-ui-openapi.json) is generated from the source version, not proof of another running version.

Authentication and Host/Origin validation apply at the listener boundary. Use a header-capable HTTP/fetch client. Do not put access keys in API query strings or logs, or confuse model-provider credentials managed under `/api/auth/*` with the listener key. The deliberate dangerous-bypass mode is not a production authentication mechanism.

## Create a Thread and submit input

First inspect `/api/selectors` and `/api/setup` to confirm usable accepted configuration. Configure an Agent, Model credentials, and Environment profile before running; the following uses their defaults and can invoke model/tool side effects.

```bash
curl --fail-with-body "$HUI_URL/api/threads" \
  -H "Authorization: Bearer $HUI_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"title":"API conversation"}'
```

Save the returned `thread_id` as `THREAD_ID`:

```bash
curl --fail-with-body "$HUI_URL/api/threads/$THREAD_ID/submit" \
  -H "Authorization: Bearer $HUI_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"prompt":"Explain this project without changing files."}'
```

The returned `RootRunReceipt` has `receipt_id`, `thread_id`, and `submitted_at`. Read `/api/operations/{receipt_id}` until terminal status; there is no root-operation HTTP `wait` endpoint. Preparing/running is not completion. Completed/suspended/failed/cancelled describes the operation; inspect any `outcome.execution`, `outcome.continuation`, and `outcome.environment` separately.

Only one active root operation is allowed per Thread. A second submit is rejected, not queued. There is no Service-style durable acceptance/idempotency contract here. After losing an acknowledgement, read current Thread/root activity before deciding what to do; do not blindly submit the input again. After process restart, old receipts can be unavailable while the saved continuation remains readable.

## Current route map

These are all schema-listed operations; the grouped table preserves method distinctions. Exact field schemas live in OpenAPI.

| Method and route                                               | Purpose                                             |
| -------------------------------------------------------------- | --------------------------------------------------- |
| `GET /api/status`                                              | Listener/API/App status                             |
| `GET /api/setup`                                               | Current setup view                                  |
| `POST /api/setup/preview`                                      | Preview a setup selection                           |
| `POST /api/setup/apply`                                        | Apply a setup selection                             |
| `POST /api/environments/preflight`                             | Preflight native/sandbox profile for a project path |
| `GET /api/auth/keys`                                           | Safe model-provider key metadata                    |
| `PUT /api/auth/keys`                                           | Store provider credentials                          |
| `DELETE /api/auth/keys/{reference}`                            | Remove a provider key reference                     |
| `POST /api/auth/logins`                                        | Start provider login                                |
| `GET /api/auth/logins/{session_id}`                            | Read provider login progress                        |
| `DELETE /api/auth/logins/{session_id}`                         | Cancel/remove the selected login session            |
| `GET /api/configuration/sources`                               | Accepted source metadata                            |
| `GET /api/configuration/sources/{relative_path}`               | Accepted source content where available             |
| `PUT /api/configuration/sources/{relative_path}`               | Validate and publish source replacement             |
| `DELETE /api/configuration/sources/{relative_path}`            | Validate and remove a non-root source               |
| `POST /api/configuration/validate`                             | Validate source replacement without publication     |
| `POST /api/threads/preview`                                    | Resolve new Thread selections without creating one  |
| `PATCH /api/threads/{thread_id}/configuration`                 | Versioned exact configuration change                |
| `GET /api/threads/{thread_id}/project-defaults`                | Preview the selected Project's configured defaults  |
| `POST /api/threads/{thread_id}/project-defaults`               | Apply reviewed defaults with version/digest checks  |
| `GET /api/projects`                                            | Available Projects and creation defaults            |
| `GET /api/selectors`                                           | Configuration selection options                     |
| `GET /api/threads`                                             | Query/page Threads                                  |
| `GET /api/threads/activity`                                    | Navigation activity and pending summaries           |
| `GET /api/threads/{thread_id}/tasks`                           | Selected Working State task projection              |
| `GET /api/threads/{thread_id}/children`                        | Parent-scoped child listing or exact query          |
| `GET /api/threads/{thread_id}/children/wait`                   | Bounded child wait or poll                          |
| `GET /api/threads/{thread_id}/children/{execution_id}/review`  | Bounded child inspection                            |
| `POST /api/threads/{thread_id}/children/{execution_id}/steer`  | Enqueue child steering text                         |
| `POST /api/threads/{thread_id}/children/{execution_id}/cancel` | Request child cancellation                          |
| `POST /api/threads`                                            | Create using optional defaults/title                |
| `GET /api/threads/{thread_id}`                                 | Detail, continuation, available actions             |
| `GET /api/threads/{thread_id}/transcript`                      | Bounded retained transcript                         |
| `PATCH /api/threads/{thread_id}/metadata`                      | Versioned title/archive change                      |
| `POST /api/threads/{thread_id}/attachments`                    | Stage raw bytes with a filename                     |
| `GET /api/threads/{thread_id}/attachments/{attachment_id}`     | Download a scoped attachment                        |
| `POST /api/threads/{thread_id}/submit`                         | Submit ordinary prompt and attachment IDs           |
| `GET /api/threads/{thread_id}/decisions`                       | Exact pending-decision projection                   |
| `POST /api/threads/{thread_id}/decisions`                      | Respond to the complete pending set                 |
| `GET /api/operations/{receipt_id}`                             | Query exact process-local operation                 |
| `POST /api/operations/{receipt_id}/steer`                      | Add steering text                                   |
| `POST /api/operations/{receipt_id}/cancel`                     | Request cancellation                                |
| `GET /api/threads/{thread_id}/events`                          | Focused SSE snapshot/events                         |
| `GET /api/events`                                              | Summary SSE invalidations                           |

`GET /api/openapi.json`, `/healthz`, `/readyz`, and static navigation/assets are additional non-schema-listed boundaries. Serving an application shell at a recognized browser route does not implement that screen. Shared drafts, Host Files, Host Git, and Host terminal flags are currently false.

## Configure Projects and Threads

Configuration source queries describe the **accepted generation**, which may differ from invalid or newly edited files on disk. `GET /api/configuration/sources` lists relative paths, resource IDs, digests, and editability; the path-specific GET includes source text when available. MCP source text is withheld (`content: null`, `content_available: false`) because it can contain literal credentials. Do not save that null as a replacement. Account credential stores are not configuration sources.

Create or replace an approved source with `PUT /api/configuration/sources/{relative_path}` and `{"content":"..."}`. `POST /api/configuration/validate?path=projects/work.yaml` accepts the same body for validation only; it publishes nothing. Both validate the resulting complete candidate, so replacement can repair a malformed source and deletion can remove an invalid unused source. Other invalid files still block publication. Root deletion, traversal, symlink sources, and arbitrary Host paths are not allowed.

Source saves use last-write-wins, **not** an expected source digest. Validation does not reserve the file. The publication response distinguishes the source digest written from the subsequent generation digest; another editor may write later. A failed/disconnected response is not proof that no file was written. Inspect current status and accepted sources before retrying.

Preview creation without allocating a Thread:

```bash
curl --fail-with-body "$HUI_URL/api/threads/preview" \
  -H "Authorization: Bearer $HUI_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"project_id":null}'
```

This request body is `NewThreadDefaults` directly, whereas Thread creation nests it under `defaults`. The preview resolves the exact Agent, Environment, Plugin, Run Extension, and MCP selections from current configuration; creation resolves again rather than reserving that preview. Null Project suppresses the global Project default. Project defaults are shown by `/api/projects`; existing Thread selections are shown in Thread detail.

`PATCH /api/threads/{thread_id}/configuration` accepts `expected_version` and `patch`. Supported patch fields are `project_id`, `agent_id`, `environment_profile_id`, `harness_plugin_ids`, `environment_run_extension_ids`, and `mcp_server_ids`. Omission preserves the saved value, `project_id: null` clears the Project, and an empty list selects none. Changing Agent does not implicitly replace other saved axes. These root-Thread commands do not modify child Threads or an already captured Run.

To apply the selected Project's configured defaults:

1. GET `/api/threads/{thread_id}/project-defaults` and inspect `current`, `replacement`, and `patch`.
2. POST to the same path with the returned `expected_version` and `defaults_digest`.
3. On 409, refresh the preview rather than automatically accepting newer values.

Only Project-specified axes are applied, not all lower-priority creation defaults. An empty combination has no apply action. This operation uses Thread concurrency checks; it does not change source-file last-write-wins semantics.

## Metadata, decisions, and attachments

Metadata PATCH accepts `expected_version` plus `patch`; use the Thread's current `metadata_version`. Omitting a title preserves it; null clears it. A supplied `archived` must be boolean, and archiving requires no active root operation. Configuration version and continuation ID are different preconditions.

Decision responses carry `expected_continuation_id` and every selected request exactly once. Question, approval, and external-result kinds must match their pending contract. Do not replace a suspended continuation with an ordinary prompt or submit only the answers convenient to the current UI.

Attachment upload uses raw bytes with a `name` query parameter, not multipart form data. The contract advertises `application/octet-stream`; preserve the returned attachment ID and use it only within its Thread. Limits are 10 MiB each, eight per input, and 20 MiB combined. Downloads return bytes with attachment disposition, not JSON. Root and child steering use a separate text-only `SteerRequest` (`prompt`); attachment fields are rejected rather than accepted and ignored.

## Navigate and inspect child work

- `GET /api/threads/activity` returns navigation summaries, pending counts, current activity, and retained terminal outcomes. It accepts `project_id`, `query`, `include_archived`, `cursor`, and `limit`.
- `GET /api/threads/{thread_id}/tasks` returns the selected task projection. Tasks and decisions accept `expected_continuation_id`; a mismatch returns a conflict rather than mixing snapshots.
- `GET /api/threads/{thread_id}/children` lists children under that exact parent. Supply `execution_id` for a specific child, or `cursor` and `limit` for a page.
- `GET /api/threads/{thread_id}/children/wait` uses the same scope and selectors plus `timeout_seconds` (0–60), waiting only through the existing child operator.
- `GET /api/threads/{thread_id}/children/{execution_id}/review` returns bounded child inspection.
- POST to the child's `/steer` with `{"prompt":"..."}`, or `/cancel`, controls only that execution under the specified parent. A control acknowledgment is not terminal completion.

Use the existing root operation GET for polling and exact-receipt steering/cancellation. These operations introduce no alternate execution coordinator or persistent work queue.

## Consume SSE and recover gaps

```bash
curl --no-buffer --fail-with-body "$HUI_URL/api/threads/$THREAD_ID/events" \
  -H "Authorization: Bearer $HUI_API_KEY"
```

SSE frames carry JSON in `data`. A focused stream without `after` begins with `kind: "snapshot"`. If `snapshot.root_stream` is present, the snapshot cursor is null and `kind: "root_stream"` batches follow, each containing at most 16 indexed events from that exact Run's existing Stream Protocol observer. Apply these once to the Run's provisional display, then store the `resume_cursor` from `kind: "ready"`. If interrupted before ready, discard the incomplete bootstrap and open a fresh watch. Without root replay the initial snapshot already carries a cursor. Following `kind: "event"` frames carry later live events and their cursors.

The root prefix includes only events published at the snapshot cutover, not later observations. Observer indexes are not the live hub's global sequence. Fetch saved transcript independently, bound to the selected continuation, and replace provisional output when that continuation advances. `recent_events` is only incomplete diagnostic context: do not append it again beside history or root replay. Child inspection uses the existing compact closed-activity projection; reconnect does not expose unfinished child activity.

Store cursors only after applying their frames; reconnect using the opaque `after` query value, URL-encoded. A valid cursor assumes the client retained its display. A newly loaded page needs a fresh bootstrap instead. This is not the Service Run stream's `Last-Event-ID` contract.

The summary stream begins with `kind: "open"` and emits `kind: "invalidation"`; refetch affected summaries instead of interpreting invalidation as a full resource. Focus and summary cursors are distinct and bound to scope/epoch. Sparse sequences are valid; do not demand contiguous global numbering.

A `kind: "reset"` frame requires refetch and a fresh subscription. The live buffer is bounded and process-local. `watch_thread` provides subscribe-before-query cutover, not transactional durable replay. Disconnect stops observation, not execution. Browser `EventSource` cannot attach arbitrary authorization headers; use an appropriate authenticated fetch/SSE reader rather than moving the key to the URL.

## Errors and versions

App errors use `{"error":{"code":"...","message":"..."}}`. Typical mappings are 400 for invalid App requests, 409 for conflicts/stale versions/preflight requirements, 413 for oversized bodies, 404 for unavailable resources/receipts, and 503 for App not ready/stopping. Query validation can return FastAPI's 422 validation response. Listener authentication/Origin/Host rejection uses 401/403/400 respectively.

Handle code and current state, not text matching. A timeout or disconnected client does not establish whether a mutation took effect. Check `/api/status` and schema compatibility before assuming source documentation matches a deployed listener.
