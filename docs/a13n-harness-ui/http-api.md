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

| Method and route                                               | Purpose                                                       |
| -------------------------------------------------------------- | ------------------------------------------------------------- |
| `GET /api/status`                                              | Listener/API/App status                                       |
| `GET /api/host/files`                                          | Bounded native directory page                                 |
| `GET /api/host/files/metadata`                                 | Native entry metadata, without following the final symlink    |
| `GET /api/host/files/text`                                     | Complete editable UTF-8 or explicit binary/large presentation |
| `PUT /api/host/files/text`                                     | Create or save text with an observed revision                 |
| `GET /api/host/files/content`                                  | Bounded attachment-only native download                       |
| `PUT /api/host/files/content`                                  | Bounded raw upload with create/replace preconditions          |
| `POST /api/host/files/directories`                             | Create one native directory                                   |
| `POST /api/host/files/move`                                    | Rename/move one observed entry to an absent destination       |
| `POST /api/host/files/delete`                                  | Deliberate nonrecursive or bounded recursive deletion         |
| `POST /api/threads/{thread_id}/host-file-captures`             | Capture reviewed file bytes or lines as Thread input          |
| `GET /api/setup`                                               | Current setup view                                            |
| `POST /api/setup/preview`                                      | Preview a setup selection                                     |
| `POST /api/setup/apply`                                        | Apply a setup selection                                       |
| `POST /api/environments/preflight`                             | Preflight native/sandbox profile for a project path           |
| `GET /api/auth/keys`                                           | Safe model-provider key metadata                              |
| `PUT /api/auth/keys`                                           | Store provider credentials                                    |
| `DELETE /api/auth/keys/{reference}`                            | Remove a provider key reference                               |
| `POST /api/auth/logins`                                        | Start provider login                                          |
| `GET /api/auth/logins/{session_id}`                            | Read provider login progress                                  |
| `DELETE /api/auth/logins/{session_id}`                         | Cancel/remove the selected login session                      |
| `GET /api/configuration/sources`                               | Accepted source metadata                                      |
| `GET /api/configuration/sources/{relative_path}`               | Accepted source content where available                       |
| `PUT /api/configuration/sources/{relative_path}`               | Validate and publish source replacement                       |
| `DELETE /api/configuration/sources/{relative_path}`            | Validate and remove a non-root source                         |
| `POST /api/configuration/validate`                             | Validate source replacement without publication               |
| `POST /api/threads/preview`                                    | Resolve new Thread selections without creating one            |
| `PATCH /api/threads/{thread_id}/configuration`                 | Versioned exact configuration change                          |
| `GET /api/threads/{thread_id}/project-defaults`                | Preview the selected Project's configured defaults            |
| `POST /api/threads/{thread_id}/project-defaults`               | Apply reviewed defaults with version/digest checks            |
| `GET /api/projects`                                            | Available Projects and creation defaults                      |
| `GET /api/selectors`                                           | Configuration selection options                               |
| `GET /api/threads`                                             | Query/page Threads                                            |
| `GET /api/threads/activity`                                    | Navigation activity and pending summaries                     |
| `GET /api/threads/{thread_id}/tasks`                           | Selected Working State task projection                        |
| `GET /api/threads/{thread_id}/children`                        | Parent-scoped child listing or exact query                    |
| `GET /api/threads/{thread_id}/children/wait`                   | Bounded child wait or poll                                    |
| `GET /api/threads/{thread_id}/children/{execution_id}/review`  | Bounded child inspection                                      |
| `POST /api/threads/{thread_id}/children/{execution_id}/steer`  | Enqueue child steering text                                   |
| `POST /api/threads/{thread_id}/children/{execution_id}/cancel` | Request child cancellation                                    |
| `POST /api/threads`                                            | Create using optional defaults/title                          |
| `GET /api/threads/{thread_id}`                                 | Detail, continuation, available actions                       |
| `GET /api/threads/{thread_id}/transcript`                      | Bounded retained transcript                                   |
| `PATCH /api/threads/{thread_id}/metadata`                      | Versioned title/archive change                                |
| `POST /api/threads/{thread_id}/attachments`                    | Stage raw bytes with a filename                               |
| `GET /api/threads/{thread_id}/attachments/{attachment_id}`     | Download a scoped attachment                                  |
| `POST /api/threads/{thread_id}/submit`                         | Submit ordinary prompt and attachment IDs                     |
| `GET /api/threads/{thread_id}/decisions`                       | Exact pending-decision projection                             |
| `POST /api/threads/{thread_id}/decisions`                      | Respond to the complete pending set                           |
| `GET /api/operations/{receipt_id}`                             | Query exact process-local operation                           |
| `POST /api/operations/{receipt_id}/steer`                      | Add steering text                                             |
| `POST /api/operations/{receipt_id}/cancel`                     | Request cancellation                                          |
| `GET /api/threads/{thread_id}/events`                          | Focused SSE snapshot/events                                   |
| `GET /api/events`                                              | Summary SSE invalidations                                     |

`GET /api/openapi.json`, `/healthz`, `/readyz`, and static navigation/assets are additional non-schema-listed boundaries. Serving an application shell at a recognized browser route does not implement that screen. `features.host_files` is true only when the App was opened with native sharing enabled. Shared drafts, Host Git, and Host terminal flags remain false; the Files API does not imply those modules or browser panels exist.

## Native Host Files

Start with `a13n-harness-ui webui`. Computer sharing is enabled by default and exposes the server OS account's file authority to admitted instance clients, independently of Agent Environment selection. Use `--no-share-computer` to disable it. In Docker it means the container and its mounts, not the browser machine. Authentication bypass does not override the sharing selection. Disabled sharing returns `403 host_files_disabled` from native operations, including direct App calls before filesystem access. Project roots from `/api/projects` are navigation starts, not a jail; Files also works outside Git or any Project.

Read `GET /api/host/files?path=<absolute-path>` for a directory, or `/api/host/files/metadata?path=...` for entry metadata. Directory pages default to 200 entries (maximum 500), reject scans over 10000 entries, and return `next_offset`. For subsequent pages, pass both `offset` and the previous `directory.revision`; a conflict requires starting a fresh listing. Metadata describes the final symlink itself; text reads and browsing return the resolved target and its revision.

Read `/api/host/files/text?path=...` before editing. `presentation: text` supplies complete NUL-free UTF-8 within 512 KiB; `binary` and `too_large` supply no editable text. Save with `PUT /api/host/files/text` and a JSON body containing `path`, `text`, and the observed `expected_revision`. Omitting the revision is **create only**, not last-write-wins. A stale save returns `409 host_files_conflict` without discarding the client's buffer. Saving a symlink requires explicitly selecting its resolved target. Atomic replacement of a hard-linked file changes only the selected directory entry; other aliases keep their original bytes. Raw upload uses `PUT /api/host/files/content?path=...&expected_revision=...` and octet-stream bytes under a 10 MiB limit; omit the revision only for a new file. Downloads use the corresponding GET, optionally pinning `expected_revision`, and always have attachment disposition and octet-stream content type. Larger files require another native workflow.

Directory creation accepts `{"path":"/absolute/new-directory"}` with an existing parent. Move accepts `path`, `destination`, and the source's `expected_revision`, atomically refuses an existing destination (including concurrent creation), rejects cross-device moves, and does not implement implicit copy/delete. A platform/filesystem without no-replace move support returns `host_files_unsupported` rather than risking overwrite. Delete accepts `path`, `expected_revision`, and optional `recursive: true`; otherwise directories must be empty. Recursive preflight bounds the operation to 10000 entries and 128 directory levels. Symlinks are removed as entries, not followed. A later `host_files_partial_failure` reports completed removals; refresh rather than retrying the original tree deletion blindly.

Native revisions are opaque OS metadata observations, not content hashes or historical versions. File reads check for changes while capturing bytes; saves recheck before atomic replacement. External processes can still race a final precondition check and native mutation. File operations are not an OS-wide transaction. Permission failures return `403 host_files_permission_denied`; missing paths return 404 and oversized operations return 413. Started filesystem work is not abandoned by a disconnected request, and a lost response can mean the mutation completed. No mutation is automatically replayed.

### Select reviewed file content for input

`POST /api/threads/{thread_id}/host-file-captures` accepts `path`, the reviewed target's `expected_revision`, and optionally both `start_line` and `end_line` (inclusive, one-based). The reply contains an ordinary `attachment` with its `source` (Host location, requested/resolved paths, revision, and range), and `prompt_text` when the captured text fits 64 KiB. The source file is read **at selection time**, not at Send.

Pass that `attachment.attachment_id` in the ordinary `/submit` body's `attachment_ids`. Small-text captures add their attributed content inline to model input; binary and larger-text captures remain retained attachments rather than pretending to be inline text. Existing attachment count, total-input limits, Thread scope, and scratch/retention rules apply. Normal uploaded text attachments without captured source provenance retain their existing behavior.

Steering endpoints still accept only `prompt`. Deliberately selected `prompt_text` can be composed into a text-only steering message; attachment IDs are rejected. When `prompt_text` is null, retain that selection in the caller's draft for ordinary submission instead of silently dropping it. There is no new multimodal steering or draft-sync protocol in Files.

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
