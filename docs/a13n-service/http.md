---
title: HTTP conventions
description: Authenticate a request, read and update a resource, and handle retries and paged responses.
---

Use this page to build the request handling shared by a Service client: authenticate, retain the IDs and versions you read, then update or submit work with the right headers. For a complete agent conversation, follow [Connect your application](connect-application.md); for a language client, use [SDKs and CLI](sdks.md).

API paths start with `/api/v1` on your Service URL. Requests and responses use JSON except for file transfers, images and event streams. Request bodies reject unknown fields. Your deployment publishes its exact schema at `/api/v1/openapi.json` and an interactive explorer at `/api/v1/docs`; the [HTTP reference](api-reference/index.md) documents the operations.

## Authentication

Start with a workspace [API key](identity.md#api-keys) whose principal has `read` permission. Set `A13N_URL` to your Service URL without a trailing slash and `A13N_API_KEY` to that key in your shell. List the agents:

```sh
curl -i "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

A successful response contains `items` and `next_cursor`. For the update exercise below, choose the returned `id` of an unarchived custom agent. If you have none, first [create an agent](agents-and-runs.md). Supplying an `Authorization` header selects that credential and ignores cookies. A missing, invalid or disabled principal's credential returns `401 unauthenticated`.

Browser clients use the login cookie from `POST /api/v1/auth/login`. For cookie-authenticated methods other than `GET`, `HEAD` and `OPTIONS`, send `X-CSRF-Token` from the login response or `GET /api/v1/auth/session`. A supplied `Origin` must match the origin of `server.public_url`; `localhost` and `127.0.0.1` are interchangeable for a loopback public URL.

Use a login session for account changes, login-session and personal audit reads, logout, API-key creation, invitations and personal Connection authorization. API keys receive `403 forbidden` on these operations, including `GET /auth/session`. Workspace Model Provider authorization accepts API keys with the required permissions. See [Identity and access](identity.md) for the permission model.

Authenticated responses carry `Cache-Control: no-store`, including errors after authentication. A renewed login session also returns its refreshed cookie.

### Workspace

Workspace resources have paths such as `/api/v1/agents`; the credential selects the workspace:

| Credential    | Workspace selection                                                                                                      |
| ------------- | ------------------------------------------------------------------------------------------------------------------------ |
| API key       | Uses its own workspace. No extra header is needed. Naming another workspace in `X-Workspace-ID` returns `403 forbidden`. |
| Login session | Send `X-Workspace-ID: ws_…`. Omitting it returns `400 invalid_argument` with `details.field: X-Workspace-ID`.            |

Account and administration routes take their scope from the path, including `/auth/…`, `/users/…`, `/organizations/…` and `/workspaces/{workspace_id}/…`. They do not use the workspace header. Deployment-wide reads such as `/model-catalog`, `/provider-types/{kind}`, `/mcp-servers` and `/connections/redirect-uri` also need no workspace selection. Paths in this paragraph are relative to `/api/v1`.

## Errors

Branch on `error.code` and structured `details`; use `message` for display. Retain the response's `X-Request-Id` when reporting a failure. The same ID appears in the error body:

```json
{
  "error": {
    "code": "precondition_failed",
    "message": "Resource changed",
    "details": {"current_etag": "\"ap_...:4\""},
    "request_id": "req_..."
  }
}
```

| Code                    | Status | What to check or do                                                                                             |
| ----------------------- | ------ | --------------------------------------------------------------------------------------------------------------- |
| `invalid_argument`      | 400    | Correct `details.field` or the `{field, reason}` entries in `details.fields`.                                   |
| `invalid_cursor`        | 400    | Restart the same collection query with its intended scope and filters.                                          |
| `unauthenticated`       | 401    | Supply a valid credential.                                                                                      |
| `forbidden`             | 403    | Check permissions (`details.verb`), CSRF and origin.                                                            |
| `not_found`             | 404    | Check the target ID and your access to it.                                                                      |
| `request_timeout`       | 408    | Send the body within `server.request_timeout`.                                                                  |
| `already_exists`        | 409    | Choose an unused key or read the existing resource.                                                             |
| `conflict`              | 409    | Inspect `details.reason`, such as `archived`, `builtin`, `last_organization_admin` or `idempotency_key_reused`. |
| `precondition_failed`   | 412    | Read the changed resource before deciding how to reapply your edit.                                             |
| `payload_too_large`     | 413    | Check `details.limit`; use a smaller body or a supported section/content read.                                  |
| `disabled`              | 422    | Check whether the target, principal or workspace is disabled or archived.                                       |
| `precondition_required` | 428    | Supply the resource's `If-Match`.                                                                               |
| `rate_limited`          | 429    | Wait the `Retry-After` seconds; also reported as `details.retry_after_seconds`.                                 |
| `internal`              | 500    | Report the `request_id`.                                                                                        |
| `unavailable`           | 503    | Check `details.dependency` and retry when that dependency is available.                                         |

An unknown route or unsupported method returns `not_found` with `kind: route` and the method/path as `id`. An unparsable body returns `invalid_argument` with `field: body` and `reason: unparsable`. Changing an archived agent or skill returns `409 conflict`, rather than `422 disabled`. Error details omit submitted values and secrets.

## Concurrency control

An ETag identifies the version you read. To edit an agent, your principal needs workspace `write` permission:

1. Read the agent and retain the response's `ETag`.
2. Send the edit with that exact value in `If-Match`, including its double quotes.
3. Read the agent again to confirm the saved description.

Replace `AGENT_ID` with the ID selected above:

```sh
AGENT_ID='ap_REPLACE_WITH_RETURNED_ID'
curl -i "$A13N_URL/api/v1/agents/$AGENT_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

Copy the returned ETag into `ETAG`; the value below is a placeholder:

```sh
ETAG='"ap_REPLACE_WITH_RETURNED_ID:1"'
curl -i -X PATCH "$A13N_URL/api/v1/agents/$AGENT_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" -H "If-Match: $ETAG" \
  -d '{"description": "Answers billing questions"}'
curl "$A13N_URL/api/v1/agents/$AGENT_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

If the edit returns `412`, another change has made your ETag stale. Read the resource again, decide whether your edit still applies, and use the newly read ETag. Do not replace the header blindly. Missing `If-Match` returns `428`; weak validators and `*` do not match. Every operation declaring `If-Match` requires it, even though OpenAPI marks the header optional.

Strong ETags have the form `"{id}:{version}"`, or `"{key}:{version}"` for models. A versioned view's ID/key and `version` can also supply that value. Inbox edits, withdrawals and reordering, thread updates, and environment/memory mount changes use the **thread's** ETag. Publishing an agent or skill revision uses its head's ETag. Ordinary creations and run commands such as interrupt take no ETag; check the operation for exceptions.

## Idempotent requests

Use `If-Match` to protect an edit from concurrent changes. Use `Idempotency-Key` to retry one logical submission after a lost response. The following operations require a key of 1–512 visible ASCII characters:

- `POST …/threads` and `POST …/threads/{thread_id}/inbox`
- `POST …/runs/{run_id}/fork` and `POST …/runs/{run_id}/resume`
- `POST …/schedules` and `POST …/schedules/{schedule_id}/dispatch`
- `POST …/finding-analyses`
- `POST …/uploads`

Generate one unique key before submitting. Keep the same key, target and request content for a retry. Use a new key for a new logical request. Keys belong to the caller and workspace and do not expire. Reusing a key for different content or a different target returns `409 conflict` with reason `idempotency_key_reused`.

The first submission normally returns `201`; manual Schedule dispatch returns `202`, and uploads return `200`. A replay returns `200` with the same created objects in their **current** state. Inspect that state to continue; submission acceptance alone does not mean execution finished. [Connect your application](connect-application.md#direct-http-integration) demonstrates retaining the thread and inbox entry before following the assigned run.

Creating a [Schedule](schedules.md) delegates durable execution authority. Revoking or expiring the creating API key does not stop future submissions; pause or archive the Schedule.

## Paging

Read `items`, then follow `next_cursor` until it is `null`. Ordinary collection pages accept `limit` from 1 to 100, defaulting to 50. Keep the collection, workspace and filters unchanged across pages:

```sh
curl -G "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  --data-urlencode 'archived=false' --data-urlencode 'limit=10'
```

If that response has a non-null `next_cursor`, copy it into `CURSOR`:

```sh
CURSOR='REPLACE_WITH_NEXT_CURSOR'
curl -G "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  --data-urlencode 'archived=false' --data-urlencode 'limit=10' \
  --data-urlencode "cursor=$CURSOR"
```

Treat cursors as opaque values. A cursor used with another collection, scope or filter set returns `invalid_cursor`. Bounded catalogs, toolsets, mounts and attempts can return all items without paging; their operation schemas describe the response.

## Read summaries and selected details

For large histories, find the record first, then fetch the field you need. For example:

```http
GET /api/v1/runs?status=failed&limit=10
GET /api/v1/runs/{run_id}?section=failure
GET /api/v1/runs/{run_id}/items?view=compact&kind=tool_call&state=failed
GET /api/v1/runs/{run_id}/items/{item_id}/content?ordinal={ordinal}&field=result&offset=0
```

Substitute the returned run ID, item ID and that item's actual `ordinal`. Compact item reads begin at the oldest ordinal unless you supply `before` or `after`; follow `next_before` or `next_after`, including after an empty filtered page. Detailed unfiltered reads select the latest items and full tail.

Run lists and lineage return metadata and `input_preview: {text, truncated}`, with at most 200 Unicode code points. They do not accept `view`. Read `/runs/{run_id}` for full details. Thread run pages additionally support `anchor=<run_id>` instead of `cursor`, report `total` and each item's chronological `number` (starting at one), and supply cursors toward older (`next_cursor`) and newer (`previous_cursor`) runs. Usage summaries already include each run's `run_number`.

Revisions, models, inbox entries, display items, spans and single-run detail support `view=compact`; omission keeps `view=detailed`. Compact lists default to ten records. Both compact lists and run discovery pages can stop early at the shared 10,000-character response budget. Follow their continuation values rather than assuming `limit` records will arrive.

Read the response metadata before treating a preview as complete:

- `truncated` marks a shortened preview; `omitted_sections` identifies fields available separately.
- `source_truncated` marks content shortened before storage. Further reads cannot recover the discarded part.
- `representation: fragment` supplies `text`, a content `version`, `next_offset` and `has_more`. Continue with both the returned offset and version; offsets count Unicode code points. On `409 conflict` with reason `content_changed`, restart the read. Join JSON fragments before parsing the whole value.
- Literal `query` searches return a match and its context. Their next offset continues the search, not a full extraction.

Choose the detail route for the task:

| Task                     | Read path                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Edit configuration       | Read a revision's `section=all` or a field such as `instructions`. Run `/agent` accepts section `original`, `effective` or `overrides`, with optional `part` for original/effective. Revision `/compare` takes `base_revision_id` and optional section. Obtain the complete original configuration and head ETag before editing; preserve unrelated fields. |
| Inspect tool definitions | Search `/toolsets/index`, `/connections/{connection_id}/tools/index` or `/connector-providers/{provider_id}/apps/{app}/actions/index`. Call the matching `/definitions` with up to five repeated exact `name` values. Check `missing` and `oversized`; retrieve an oversized definition by one name and versioned fragments.                                |
| Inspect a model          | Use model-catalog compact search, capability filters and paging, then select the exact provider/model for characteristics or pricing.                                                                                                                                                                                                                       |
| Inspect a trace span     | Filter trace lists by `status`, `kind` or `tool_name`. Read `/traces/{trace_id}/spans/{span_id}/content?section=input` or `section=output`, continuing fragments with version and offset.                                                                                                                                                                   |

Trace span content queries fetch that span alone; empty `items` means it is absent from the trace. A span exceeding the backend's 8 MiB response limit returns `413 payload_too_large`. Available trace data depends on backend availability and retention. Use complete configuration and definitions for writes, rather than submitting a summary, fragment or diff as the whole value.

## Limits

Request bodies must fit `server.request_bytes` and arrive within `server.request_timeout`; violations return `413` and `408`. Uploads and images have smaller limits. Credential checks, uploads and authorization callbacks can return `429` with `Retry-After`; a full thread inbox also returns `429`.

## Identifiers and time

Retain IDs exactly as returned. Prefixes identify kinds, such as `ws_`, `ap_` (agent), `sk_` (skill), `sess_`, `thread_` and `run_`. Models use their [key](resources.md#common-conventions) in paths and references. Timestamps use RFC 3339 with an offset.

## Streams

To observe a conversation, use `GET /api/v1/sessions/{session_id}/stream`. It delivers server-sent events for the session's thread states and active runs' live output. Follow the [session stream workflow](agents-and-runs.md#follow-a-session-stream) for connection, readback and execution controls.
