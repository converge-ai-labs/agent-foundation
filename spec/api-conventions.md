# Platform API Conventions

## Design Position

This document defines the shared HTTP and SDK contract for Foundation-owned, resource-oriented JSON APIs. It keeps common representations, collection reads, errors, mutation safety, and compatibility consistent while each subsystem continues to own its resources, fields, authorization policy, and lifecycle.

These conventions do not replace an upstream or project-owned protocol. The Harness Python API, Environment Interaction Protocol (EIP), Agent Stream Protocol observation contract, provider APIs, and external webhook schemas retain their own wire contracts.

## Boundaries

| Concern                                  | Owner                                            | Relationship                               |
| ---------------------------------------- | ------------------------------------------------ | ------------------------------------------ |
| Shared JSON, HTTP, and SDK conventions   | This document                                    | Default for Foundation-owned resource APIs |
| Object identity and domain versions      | [Platform Data Conventions](data-conventions.md) | Reused without redefining their formats    |
| Resources, fields, filters, and ordering | Owning subsystem API                             | Applies these conventions to one domain    |
| Authentication and authorization         | Host or product policy                           | Re-evaluated for every operation           |
| Durable event ordering and replay        | Owning event protocol                            | Not ordinary collection pagination         |
| Process-local and external protocols     | Their defining owner                             | Preserve their native contract             |

## HTTP Resource Model

Foundation-owned product HTTP APIs use the `/api` namespace. The current public compatibility line places its versioned resource routes below `/api/v1`. Operational endpoints such as liveness and readiness are outside `/api` and are not public resource APIs.

Path segments use lowercase kebab-case, with plural names for resource collections. `GET` reads a resource or collection, `POST` creates a resource or invokes an explicit command, `PATCH` applies a partial mutation, and `DELETE` removes a resource only when its owning contract defines deletion. `PUT` is used only for a genuine complete replacement. A command uses a subordinate action path such as `POST /api/v1/runs/{run_id}/interrupt`; arbitrary verb-shaped RPC endpoints are not used when a resource or command expresses the operation directly.

A successful single-resource, mutation, or command response returns that resource or receipt directly. There is no universal `data` envelope. Ordinary status meanings are:

| Status | Meaning                                                              |
| -----: | -------------------------------------------------------------------- |
|  `200` | Successful read, mutation, or synchronously completed command        |
|  `201` | Resource created                                                     |
|  `202` | Durably accepted but not complete; a resource or receipt is returned |
|  `204` | Successful operation with no response representation                 |

An asynchronous acceptance never holds an HTTP request open until execution finishes and never reports `200` as if acceptance and completion were the same fact.

## JSON Representations

Resource, mutation, command, and error representations are UTF-8 JSON unless the owning domain defines the binary content-transfer exception below. A domain can define an explicit bounded content-transfer endpoint with one exact binary media type when encoding the bytes in JSON would defeat streaming or size safety. A binary upload accepts that media type as its request body and returns JSON metadata or a receipt; a binary download returns that media type directly. Failures still use the shared JSON error representation. Neither form accepts an ambiguous mixed representation or turns storage keys or signed URLs into resource authority. Object fields and query parameters use `snake_case`; enum values use stable lowercase `snake_case`. First-party SDKs use the idiomatic casing of their language while preserving the same concise domain meaning.

Presence has one consistent meaning:

- an omitted request field is not supplied and, in a partial mutation, is not changed;
- `null` is an explicit absence or clear operation only when the field permits it;
- an empty string, collection, or object is a supplied value and is never interchangeable with `null`.

Requests reject unknown fields. Clients ignore unknown response fields and preserve an unknown enum value as unknown rather than failing decoding or mapping it to a known value.

Absolute timestamps use an `*_at` name and an RFC 3339 UTC string serialized with `Z`. Public durations, byte sizes, and other scalar quantities are integers whose names state the unit, such as `timeout_ms` and `size_bytes`. Bare numeric values with an implicit unit and floating-point durations are not public API fields.

Every request and response is bounded. The owning API defines tighter limits for strings, collections, metadata, nesting, and payloads where the shared collection bound is insufficient.

## Collection Reads

Ordinary resource collections use one cursor-based shape:

```http
GET /api/v1/agents?limit=50&cursor=opaque-value
```

```json
{
  "items": [],
  "next_cursor": "opaque-value"
}
```

`limit` is an integer from `1` through `100` and defaults to `50`. `next_cursor` is either an opaque string or `null`; `null` means that the observed traversal has no next page. The page does not also carry `has_more`, and ordinary collections do not expose parallel `page`, `page_size`, or `offset` pagination. They do not compute `total` by default.

Each endpoint defines one deterministic default order and uses a unique stable tie-breaker. It exposes only explicit filters and sort choices rather than a platform query language. A cursor is bound to the authenticated scope and the query that created it. A changed scope, filter, or ordering, or an invalid or expired cursor, returns a typed error instead of an empty page.

[Trace query](a13n-service/07-facts-and-delivery.md#trace-query) is a scoped exception for provider-backed telemetry: descending start time uses the selected backend's stable opaque identity continuation for ties rather than a cross-provider public ID comparator. Service preserves this native order across filtered pages; it does not impose a different global order by sorting individual pages.

A bounded transient catalog command may return its complete `items` array without pagination when its owning API explicitly defines that contract and its response limits, as in the [model catalog](a13n-service/08-providers.md#model-catalog). This does not change ordinary resource collection pagination.

The cursor is a continuation value, not an object ID or bearer authority. Clients do not parse or construct it, and the server reauthorizes every page. Ordinary collection pagination does not imply a database snapshot; an API that requires snapshot isolation or durable replay defines that stronger contract separately.

## Errors

Every error response uses one bounded shape:

```json
{
  "error": {
    "code": "invalid_argument",
    "message": "The request is invalid.",
    "details": {},
    "request_id": "opaque-request-id"
  }
}
```

`code` is the stable lowercase `snake_case` machine contract. `message` is a bounded human explanation and is not used for branching. `details` is always a JSON object containing only safe, code-specific fields. `request_id` correlates the response with service diagnostics but grants no authority.

HTTP status expresses the broad failure class:

| Status | Failure class                                          |
| -----: | ------------------------------------------------------ |
|  `400` | Malformed or invalid request                           |
|  `401` | Missing or invalid authentication                      |
|  `403` | Authenticated but not authorized                       |
|  `404` | Resource absent or intentionally concealed             |
|  `409` | State, version, or idempotency conflict                |
|  `412` | Required representation precondition is stale          |
|  `428` | Required mutation precondition is absent               |
|  `429` | Request rate or admitted-capacity limit                |
|  `500` | Unexpected service failure                             |
|  `503` | Service or required dependency temporarily unavailable |

An owning security policy can conceal a forbidden resource as `404`. When the server knows a safe retry delay for `429` or `503`, it returns `Retry-After`. Errors never expose credentials, traceback text, SQL, private paths, raw provider failures, or arbitrary object representations.

SDKs expose one common API error base carrying HTTP status, `code`, `message`, `details`, and `request_id`. More specific exception classes may be added only when applications can act on a stable semantic distinction.

## Mutations and Retries

A versioned state-machine mutation that can lose a concurrent update accepts `expected_version` and compares it with the model's `version`. When one request boundary necessarily exposes multiple independent version axes, secondary preconditions are qualified just enough to distinguish them. A mismatch returns `409`. A mutable representation without an addressable history requires a strong `ETag` plus `If-Match`; an absent precondition returns `428` and a stale tag returns `412`. The tag changes whenever that complete representation changes and is not an addressable version, revision, or history selector. A revisioned resource head orders Revision publication, default selection and metadata changes with its one `ETag` ([data conventions](data-conventions.md#resource-revisions-and-concurrency)). One mutation axis never mixes the two contracts. Resources that cannot lose updates do not require an artificial concurrency token.

A create or command that callers may safely retry accepts an `Idempotency-Key` header containing 1–512 visible ASCII bytes. Scope includes the authenticated Principal, applicable Workspace or Organization boundary, operation, target, and key digest. The same scoped key selects the accepted business result; a request whose content differs from the one the key accepted is refused with `409` rather than replayed, so a reused key never silently returns another request's result. Service reauthorizes replay access and constructs the response from retained records using current logic, so mutable fields and versions may have advanced. Replay precedes new-mutation version and semantic-input checks. Keys do not expire after 24 hours; deleting the owning data may end deduplication. No response snapshot or replay-specific retention hold is required. External protocol identities retain their separately owned semantics.

Idempotent replay is resolved before evaluating `expected_version` or `If-Match`, so replay of a committed mutation does not conflict with the state it already changed. A timeout or lost response after possible dispatch has unknown outcome unless the same idempotency key or authoritative receipt reconciles it. A caller never changes the key merely because acknowledgement was lost.

SDKs automatically retry only bounded reads and mutations whose owning contract and idempotency evidence make replay safe. They honor `Retry-After` when present and never label an unknown mutation outcome as failure or success.

## Compatibility

`v1` evolves additively. Clients tolerate new response fields and preserve unknown response enum values. Servers reject unknown request fields so a typo or unsupported intent does not silently change behavior.

Removing or repurposing a field, changing its presence or null semantics, changing an existing enum meaning, adding a required request field, changing a mutation's side-effect boundary, or weakening authorization requires an incompatible API version. A resource's domain `version` is independent from the `v1` HTTP compatibility identity; responses do not repeat a generic API schema version on every object.

Cursor encoding, storage layout, framework models, and SDK transport machinery are implementation details. An issued cursor remains valid only for its documented lifetime and query contract. External and project-owned protocols continue to evolve under their own compatibility rules.

## Invariants

1. Foundation-owned product HTTP APIs use the `/api` namespace, and current public resource routes share one `/api/v1` contract.
2. Single-resource responses are direct objects; paginated collection responses use only `items` and `next_cursor` for pagination.
3. JSON wire fields use `snake_case`, presence is explicit, timestamps are UTC, and scalar units appear in field names; an owning binary transfer route declares one exact bounded media type.
4. Every collection read is bounded, deterministically ordered, and reauthorized; cursors are opaque and non-authoritative.
5. Clients branch on stable error codes, never message text, and errors disclose no implementation-private or secret data.
6. Each concurrent mutation axis uses either its owning counter and expected counter or a strong `ETag` and `If-Match`; it never adds a second generic revision counter or mixes both preconditions on one axis.
7. A mutation is retried only with idempotency or other authoritative replay evidence; post-dispatch uncertainty remains explicit.
8. `v1` changes are additive, and unknown response additions do not prevent an older client from decoding the response.

## Resource References

The owning API defines which path segments take a readable key and how they resolve; for the Service, [paths and scope](a13n-service/10-api.md#paths-and-scope) and [authorization](a13n-service/03-tenancy.md#authorization) own that contract. Resolution never widens a credential's boundary, and an out-of-scope reference returns a concealed not-found result. A key change updates the canonical address immediately without retaining old routes or aliases.
