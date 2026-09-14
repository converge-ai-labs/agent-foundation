# HTTP contracts

The Native API lives under `/api/v1`. Service is a durable Host, not the process-local Harness API and not Harness UI's local HTTP server. Use the [generated API reference](api-reference.md) for exact paths, parameters, bodies, and response types.

## Credentials and scope

Applications use `Authorization: Bearer ...` with a Workspace-bound API key. Browser users use the same-origin session cookie and current roles. Do not combine a bearer credential and a browser session cookie in one request.

A browser session normally has Organization scope; `X-A13N-Workspace-ID` selects a narrower Workspace boundary for applicable operations. A Workspace API key cannot broaden its authority by changing a URL, header, or explicit SDK scope. All resource reads and mutations still check current permission and credential state.

Cookie-authenticated mutations require the configured browser Origin and `X-A13N-CSRF-Token`, subject to the explicitly public authentication flows. Restore the token from login or `/api/v1/auth/csrf` before mutations. CSRF proof is not a replacement for authentication. See [Identity](identity.md).

## Resource references

Organizations, Workspaces, and Agents have immutable IDs, editable keys, and display names. Supported paths accept the ID or current key. Keys are not secrets or authorization evidence. Renaming a key preserves the object but does not create a redirect from its old address.

Use returned IDs for stored relationships and continuation commands. Other resource families have their own identifiers and selectors; do not assume every `*_id` field accepts an Agent-style key.

## Optimistic concurrency

Mutable resources expose a strong `ETag` or an explicit domain version. Send the exact current `If-Match` header or the required `expected_*` version field for the owning operation. These are different contracts; do not mechanically add both or manufacture an ETag from a body value.

After a conflict, fetch current state and reconsider the change. Do not automatically retry a stale edit against a new version. A nullable or optional field in OpenAPI does not waive conditional domain preconditions for a particular command.

## Idempotency and unknown outcomes

Commands that require `Idempotency-Key` document it in the operation parameters. Reuse the same key and canonical request only within that command's scope and replay contract. A key is not a global transaction ID, a permission grant, or a promise of unbounded retention.

If a write may have reached Service but its acknowledgement was lost, reconcile the original operation before submitting a new key. Transport cancellation or an exhausted client timeout does not prove the mutation failed. Client library shutdown stops local delivery; it does not cancel an accepted Run or undo a resource change.

Use the explicit Run interrupt or resource lifecycle command when the intent is to stop server-side work. These commands themselves have version, authorization, and idempotency requirements.

## Omitted, null, and empty values

Keep the wire distinctions intact:

- An omitted Run override normally inherits the accepted Agent selection.
- `search: null` explicitly disables inherited search.
- Supplied `connection_tools` arrays replace the corresponding category; `[]` clears it. Category-level `null` is invalid.
- Within one external-tool selection, omitted/null `tools` means all tools; `tools: []` means none.

Do not serialize every absent optional field as `null`. The [SDK guide](sdks.md) shows the language-specific representations, and [external tools](external-tools.md) owns those selection semantics.

## Responses and errors

Successful responses are the operation's documented representation or an empty response such as `204`; there is no universal success envelope to strip. TypeScript's `data()` extracts openapi-fetch's representation, not a server-side `data` wrapper, and must not be used for a `204` operation.

Failures use a safe error envelope with code, message, details, and request correlation where available. HTTP status alone is not enough to decide whether a command is retry-safe. Keep `X-Request-ID` and `Retry-After` with errors, without retaining request credentials or arbitrary sensitive bodies.

Schema validation, authorization, precondition conflicts, unavailable dependencies, and replay gaps are distinct failures. A malformed/non-JSON response may instead produce a client protocol error; network/abort errors can remain native transport errors depending on the SDK.

## Pagination

Collections return `items` and a continuation cursor under their documented schema. Limits, defaults, cursor field names, and filters are operation-specific. Identity collections commonly use `limit` 1–100 and `next_cursor`; do not infer every other collection has the same default.

A cursor is opaque and can be bound to the principal, scope, and filters. Continue with the same boundary and query rather than parsing a cursor or reusing one from another account. Search account lists default to 50 in Service; the Python Search client explicitly requests 100 by default, while Go/Rust omitted limits leave the Service default in force.

## Binary content

Asset uploads accept an octet-stream body with the required filename/media/idempotency parameters. This is not the same operation as a Skill ZIP upload receipt or Envd file transfer. Metadata does not expose internal object-store keys or turn private content into a public URL.

Use streaming bodies/downloads when needed and enforce application limits. A partially observed response is not a complete file. The TypeScript SDK accepts `Blob` or `ReadableStream<Uint8Array>` and an explicit content type; it does not provide automatic upload retry.

## Streaming boundaries

Run SSE, notification WebSocket, durable lifecycle events, AG-UI, and A2A solve different problems. Their cursors and identifiers are not interchangeable. Read [Streams, events, and gateways](streams-and-events.md) before implementing reconnect or replay.
