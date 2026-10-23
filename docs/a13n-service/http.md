---
title: HTTP conventions
description: Authentication, errors, concurrency, idempotency, paging, and streaming rules shared by every endpoint.
---

Every API operation is under `/api/v1` on the Service's public URL. Requests and responses are JSON unless an operation says otherwise (uploads, file content, images and the session stream). A running Service publishes its OpenAPI document at `/api/v1/openapi.json` and an interactive page at `/api/v1/docs`; the [HTTP reference](api-reference/index.md) is generated from the same document.

Request bodies are strict: unknown fields are refused. For language integrations or shell scripts, see [SDKs and CLI](sdks.md); detailed usage is maintained in each client's own Markdown documentation.

## Authentication

Applications authenticate with an [API key](identity.md#api-keys) as a bearer token:

```sh
curl "$A13N_URL/api/v1/agents" -H "Authorization: Bearer $A13N_API_KEY"
```

When an `Authorization` header is present, cookies are ignored. An API key acts only in its own workspace, and never changes the account of the user or service account it belongs to.

Browsers use the login session cookie set by `POST /api/v1/auth/login`. For cookie-authenticated requests other than `GET`, `HEAD` and `OPTIONS`:

- send the login session's CSRF token in `X-CSRF-Token`; login returns it, and `GET /api/v1/auth/session` returns it again for an existing login session;
- an `Origin` header, if sent, must equal the origin of `server.public_url`; for a loopback public URL, `localhost` and `127.0.0.1` are interchangeable.

Account operations (changing your profile, password or email, disabling your account, listing login sessions and your own audit trail), `GET /api/v1/auth/session` and `POST /api/v1/auth/logout` require a login session, as do creating an API key, sending or resending an invitation, and starting a browser authorization (an OAuth authorization code or a connector account setup). An API key gets `403 forbidden` from all of them, one status everywhere: it never mints something that can outlive it, and never hands a third party a link to complete on your behalf. A request without valid credentials receives `401 unauthenticated`; a disabled principal's credentials are treated as invalid.

Every authenticated response, including the route's own answer and any error after authentication, carries `Cache-Control: no-store` and, when the login session was renewed, its refreshed cookie.

### Workspace

Everything a workspace holds, such as agents, threads, runs, providers, models, skills and memories, is addressed without the workspace in the path, as in `/api/v1/agents`. Each such request acts in one workspace, which the credential selects:

- An API key acts in its own workspace and needs no header. An `X-Workspace-ID` naming another workspace is refused with `403 forbidden`.
- A login session names the workspace by ID in `X-Workspace-ID: ws_…`. Without the header the request fails with `400 invalid_argument` and `details.field` `X-Workspace-ID`.

Administration and deployment-wide reads take no header. Administration names its scope in the path: `/api/v1/auth/…`, `/api/v1/users/…`, `/api/v1/organizations/…`, `/api/v1/workspaces`, and `/api/v1/workspaces/{workspace_id}` with its `icon`, `archive`, `audit-events`, `grants`, `invitations`, `service-accounts` and `keys`. The deployment-wide reads are `/api/v1/model-catalog`, `/api/v1/provider-types/{kind}`, `/api/v1/mcp-servers` and `/api/v1/connections/redirect-uri`.

## Errors

Every error has one shape, and every response carries an `X-Request-Id` header:

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

| Code                    | Status | Meaning                                                                                                                                                              |
| ----------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `invalid_argument`      | 400    | The request is malformed or refers to something unusable. `details.field` names the offending field; request validation lists `details.fields` as `{field, reason}`. |
| `invalid_cursor`        | 400    | The paging cursor is malformed, or was issued for a different collection, scope or set of filters.                                                                   |
| `unauthenticated`       | 401    | No valid credential.                                                                                                                                                 |
| `forbidden`             | 403    | The credential lacks a verb (`details.verb`), or a CSRF or origin check failed.                                                                                      |
| `not_found`             | 404    | The target does not exist or you cannot read it (`details.kind`, `details.id`).                                                                                      |
| `request_timeout`       | 408    | The request body did not arrive within `server.request_timeout`.                                                                                                     |
| `already_exists`        | 409    | A key or other unique value is taken.                                                                                                                                |
| `conflict`              | 409    | The target's state refuses the operation; `details.reason` says why, such as `archived`, `builtin`, `last_organization_admin` or `idempotency_key_reused`.           |
| `precondition_failed`   | 412    | `If-Match` is stale; `details.current_etag` has the current value.                                                                                                   |
| `payload_too_large`     | 413    | A body or file exceeds its limit (`details.limit`).                                                                                                                  |
| `disabled`              | 422    | The target, its principal or the workspace is disabled or archived where the request uses it. Changing an archived agent or skill is `409 conflict` instead.         |
| `precondition_required` | 428    | The operation requires `If-Match`.                                                                                                                                   |
| `rate_limited`          | 429    | Too many requests; retry after `Retry-After` seconds (`details.retry_after_seconds`).                                                                                |
| `internal`              | 500    | An unexpected error; report the `request_id`.                                                                                                                        |
| `unavailable`           | 503    | A dependency (`details.dependency`: `database`, `redis`, `objects`, `mail`, a provider type, ...) cannot serve the request now. Retry later.                         |

A path no route answers, or a method a path does not accept, is `not_found` with `{"kind": "route", "id": "<METHOD> <path>"}`; a body that cannot be parsed as JSON is `invalid_argument` with `{"field": "body", "reason": "unparsable"}`.

Messages are for people; branch on `code` and `details.reason`. Error details never contain submitted values or secrets.

## Concurrency control

Single-resource responses carry a strong `ETag`, such as `"ap_…:4"` (`"{key}:{version}"` for models), and views carry the same `version`. Operations that change an existing resource require the ETag you last read in `If-Match`:

```sh
curl -X PATCH "$A13N_URL/api/v1/agents/$AGENT" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H 'If-Match: "ap_...:4"' -d '{"description": "Answers billing questions"}'
```

Without `If-Match` the request fails with `428 precondition_required`; with a stale value, with `412 precondition_failed` and the current ETag. Read the resource again, reapply your change and retry. The comparison is exact, so weak validators and `*` never match. The OpenAPI document marks `If-Match` as an optional header, but every operation that declares it requires it.

Inbox operations (edit, withdraw and reorder queued messages), environment and memory mount changes, and thread updates take the **thread's** ETag. Creating an agent or skill revision takes its head's ETag. Other creations and run commands such as interrupt take none.

## Idempotent requests

These operations require an `Idempotency-Key` header of 1–512 visible ASCII characters:

- `POST …/threads` (start a thread with its first message) and `POST …/threads/{thread_id}/inbox` (submit a message)
- `POST …/runs/{run_id}/fork` and `POST …/runs/{run_id}/resume`
- `POST …/uploads`

Generate a unique key per logical request and reuse it when retrying after a lost response. A first submission returns `201`; uploads return `200`. A retry with the same key and request returns `200` with the same created objects in their current state, not a copy of the first response. Reusing the key with a different body or target is `409 conflict` with reason `idempotency_key_reused`. Keys are scoped to the caller and workspace and do not expire.

## Paging

Collections return `{"items": [...], "next_cursor": "..."}`. Pass `limit` (1–100, default 50) and, for the next page, `cursor=<next_cursor>`; `next_cursor` is `null` on the last page. A cursor is opaque and bound to its collection, its scope and every filter of the query that produced it; reusing it with a different filter is `invalid_cursor`. Bounded catalogs, toolsets, mounts and attempts may return all items without pagination; check the operation's schema.

## Read summaries and selected details

Run lists and lineage return metadata and `input_preview: {text, truncated}`, containing at most 200 Unicode code points of input text. They do not accept `view`; read `/runs/{run_id}` for full input, output, options and other details. Thread run pages accept `anchor=<run_id>` to start at a specific Run, mutually exclusive with `cursor`, and include `total`, `first_number`, `next_cursor` toward older Runs and `previous_cursor` toward newer Runs. `first_number` is the first returned Run's chronological number, starting at one; it is not the number of loaded records. `limit` is a maximum: the shared 10,000-character response budget can yield fewer records, so always follow the returned cursors. Usage summaries include each Run’s `run_number` without additional Run-list requests.

Use `view=compact` to find relevant revisions, models, inbox entries, display items or spans, and to summarize a selected Run. Combine the view with the operation's filters. These calls default to `view=detailed`. Compact lists default to ten records and may return fewer to keep responses small. Continue with the returned cursor or ordinal even when a filtered page is empty.

A preview contains `text` and `truncated`. `omitted_sections` lists fields you can read separately. `source_truncated` means the stored content was already shortened; fetching more cannot recover discarded data. An omitted field or a shortened preview does not prove that the source lacks the information.

For example, find failed runs and read one failure:

```http
GET /api/v1/runs?status=failed&limit=10
GET /api/v1/runs/{run_id}?section=failure
GET /api/v1/runs/{run_id}/items?view=compact&kind=tool_call&state=failed
GET /api/v1/runs/{run_id}/items/{item_id}/content?ordinal=7&field=result&offset=0
```

Use the item's actual `ordinal` with its ID. Compact item reads start at the oldest ordinal unless you provide `before` or `after`; follow `next_before` or `next_after`. Detailed unfiltered item reads retain the existing latest-items and full-tail behavior.

Section and content reads return `representation=fragment`, a content `version`, `text`, `next_offset` and `has_more`. Offsets count Unicode code points. Continue with the returned offset **and version**. Changed content returns `409 conflict` with reason `content_changed`; restart the read. JSON fragments must be joined before parsing the whole value. Literal `query` search finds the next match with surrounding context; its next offset continues searching, rather than extracting every character.

To inspect configuration, read a revision with `section=all` or a configuration field such as `instructions`. A run's `/agent` endpoint uses `section=original|effective|overrides`, with an optional `part` for original/effective. Revision `/compare` takes `base_revision_id` and an optional section. Before editing, obtain the complete original configuration and the agent head's ETag. Preserve unrelated fields. Never submit a summary, fragment or diff as the full configuration.

Search tool catalogs through `/toolsets/index`, `/connections/{connection_id}/tools/index` or `/connector-providers/{provider_id}/apps/{app}/actions/index`. Read the matching `/definitions` endpoint with up to five repeated exact `name` values. Check `missing` and `oversized`. An oversized definition is read through that same endpoint with one name and versioned fragments; it is never silently shortened. The model catalog accepts compact search, capability filters and paging; select the exact provider/model for characteristics or pricing sections.

Trace lists support `status`, `kind` and `tool_name`. To read one span's input or output, use `/traces/{trace_id}/spans/{span_id}/content` with `section=input|output`. The Service queries the backend for that span alone; empty `items` means the trace has no such span. Continue its text fragments with `version` and `next_offset`. A trace response over the backend's 8 MiB limit is `413 payload_too_large`, not an outage; for this route it means the span alone exceeds the limit. Trace data remains subject to the deployment's retention and backend availability.

## Limits

- Request bodies are bounded by `server.request_bytes` (`413`) and must arrive within `server.request_timeout` (`408`). Uploads and images have smaller limits.
- Password login and other credential checks, uploads and the authorization callback are rate limited (`429` with `Retry-After`). A thread whose inbox is full also answers `429`.

## Identifiers and time

IDs are opaque strings with a kind prefix, such as `ws_`, `ap_` (agent), `sk_` (skill), `sess_`, `thread_`, `run_`. Models have no exposed ID: paths and references name them by their [key](resources.md#common-conventions). Timestamps are RFC 3339 with an offset.

## Streams

`GET …/sessions/{session_id}/stream` is a server-sent event stream of a session's thread states and the live output of its active runs. See [the session stream](agents-and-runs.md#follow-a-session-stream).
