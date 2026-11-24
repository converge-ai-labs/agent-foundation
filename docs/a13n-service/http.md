---
title: HTTP conventions
description: Authentication headers, workspace routing, Idempotency-Key replay, ETag conditional updates, cursor pagination, and structured error formats for the Service HTTP API.
---

All Service JSON API routes sit under `/api/v1` (OpenAPI schema at `/api/v1/openapi.json`, interactive explorer at `/api/v1/docs`). Request bodies reject unknown fields, timestamps use RFC 3339, and resource IDs carry kind prefixes (`ws_`, `ap_`, `sk_`, `sess_`, `thread_`, `run_`; models use their `key`).

## Authentication

Send a workspace [API key](identity.md#api-keys) in `Authorization: Bearer $A13N_API_KEY`:

```sh
curl -i "$A13N_URL/api/v1/agents" -H "Authorization: Bearer $A13N_API_KEY"
```

Browser clients using login cookies (`/api/v1/auth/login`) must include `X-CSRF-Token` on non-`GET`/`HEAD`/`OPTIONS` requests and match `server.public_url` `Origin`. Account management, key issuance, and invitations require a login session.

### Workspace

- **API key:** Implicitly selects its bound workspace (no extra header needed).
- **Login session:** Send `X-Workspace-ID: ws_...` on workspace-scoped routes (`/agents`, `/threads`, `/runs`, etc.).

## Concurrency control

Resource mutations require optimistic locking via strong ETags (`"{id}:{version}"`):

1. `GET` the resource and copy its `ETag` response header (including quotes).
2. Send `If-Match: "<id>:<version>"` on `PATCH` / `PUT` / `DELETE` (`428 precondition_required` if omitted; `412 precondition_failed` if stale—re-read before retrying):

```sh
ETAG='"ap_example:1"'
curl -i -X PATCH "$A13N_URL/api/v1/agents/ap_example" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" -H "If-Match: $ETAG" \
  -d '{"description": "Answers billing questions"}'
```

Thread inbox edits and environment/memory mount changes use the **thread's** ETag.

## Idempotent requests

Send a unique `Idempotency-Key` (1–512 visible ASCII chars) on `POST` to `/threads`, `/threads/{id}/inbox`, `/runs/{id}/fork`, `/runs/{id}/resume`, `/schedules`, `/schedules/{id}/dispatch`, `/finding-analyses`, and `/uploads`. Keep the exact key and body to safely retry a lost response (`200` on replay; `409` `idempotency_key_reused` if reused with a different payload).

## Paging, streams, and errors

- **Cursor pagination:** Pass `limit` (`1`–`100`, default `50`) and follow `next_cursor` with identical query filters until `next_cursor` is `null`.
- **SSE streams:** Subscribe to `GET /api/v1/sessions/{session_id}/stream` ([stream workflow](stream-and-read-results.md#follow-a-session-stream)) or read bounded slices ([read large API results](reading-api.md)).
- **Errors:** Branch on `error.code` (`invalid_argument` 400, `unauthenticated` 401, `forbidden` 403, `not_found` 404, `already_exists` / `conflict` 409, `precondition_failed` 412, `payload_too_large` 413, `disabled` 422, `precondition_required` 428, `rate_limited` 429 with `Retry-After`, `internal` 500, `unavailable` 503) and retain `X-Request-Id` (`error.request_id`) for troubleshooting.
