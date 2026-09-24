# The HTTP API

## Design position

The Service exposes one HTTP namespace, `/api/v1`. The exported OpenAPI document is the exact contract for paths, parameters and request and response shapes. This chapter owns what applies across routes: authentication at the HTTP boundary, path resolution, collections, preconditions, request-key replay, errors and statuses. It also indexes every route with the chapter that owns its behavior; it does not repeat that behavior.

Resource routes are served by the `all` and `control` roles; the `worker` role serves only `/healthz` and `/readyz` ([09](09-runtime.md#roles)). The Service follows the [platform API conventions](../api-conventions.md) except where this chapter states otherwise.

## Conventions

### Authentication

Every route requires a credential except the health routes, the OpenAPI document (`/api/v1/openapi.json`), the API docs (`/api/v1/docs` and `/api/v1/docs/oauth2-redirect`) and the public account flows: authentication configuration, login, password-reset request and confirmation, email-change confirmation, invitation acceptance and the connection OAuth callback. [03](03-tenancy.md#authentication) owns the credentials:

- **Login session.** `POST /auth/login` sets the `__Host-a13n_session` cookie. A cookie request with a method other than GET, HEAD or OPTIONS must send `X-CSRF-Token`, and a present `Origin` must equal the origin of `server.public_url`; either failure is 403 `forbidden`. Login and the other public account `POST`s check `Origin` the same way.
- **API key.** `Authorization: Bearer a13n_…`. A key is confined to exactly one workspace and holds only its principal's current verbs there, and at most `read` and `run` on organization-shared resources; account operations, issuing API keys or invitations and browser authorizations need a login session (403 `forbidden`, [03](03-tenancy.md#authorization)). When an `Authorization` header is present the cookie is ignored.

A missing or dead credential is 401 `unauthenticated`. Every authenticated response, a route's own response and an error after authentication included, carries `Cache-Control: no-store` (stored content keeps its own `private, no-store`, [representations](#representations)) and any renewed login cookie.

While the distribution keeps the local authenticator, the OpenAPI document declares these credentials as the security schemes `apiKey` (bearer), `loginSession` (the cookie) and `csrf` (the `X-CSRF-Token` header). Every authenticated operation accepts `apiKey` or `loginSession`, the latter together with `csrf` on methods other than GET, HEAD and OPTIONS; the public operations declare no scheme.

### Paths and scope

- An organization path takes the organization ID. A workspace path takes the workspace ID or key; agent and skill paths take the head's ID or key, and an ID match wins. Every other path segment is an ID.
- Path resolution conceals other tenants, and every route authorizes its scope before it resolves a supplied ID, on replay and content routes too ([03](03-tenancy.md#authorization)).
- Provider resources and models live in organization collections. Each row has a `workspace_id`, NULL when the organization shares it, and lists filter by `workspace_id`; there are no workspace-mirrored provider routes, and a connector provider's app catalogue is the only provider read under a workspace ([04](04-resources.md#provider-resources)).
- A command that is not create, read, update or delete is `POST …/{id}/{verb}` with an imperative verb (`archive`, `interrupt`, `resume`, `set-default`, `test`, `redeliver`). A check that stores nothing is `POST …/{collection}/validate`.

### Representations

Request and response bodies are JSON objects without an envelope, except these routes:

| Route                                                           | Body                                                                                                    |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `POST …/uploads`                                                | `multipart/form-data` request with one `file`                                                           |
| `PUT` avatar and icon routes                                    | raw `image/png`, `image/jpeg` or `image/webp` request ([03](03-tenancy.md#images))                      |
| `GET` avatar and icon routes and `GET …/assets/{asset}/content` | the stored bytes with their recorded type                                                               |
| `GET …/skills/{skill}/revisions/{revision}/content`             | the package as `application/zip`                                                                        |
| `GET …/skills/{skill}/revisions/{revision}/files/{path}`        | one file of the package as `application/octet-stream`                                                   |
| `GET …/threads/{thread}/stream`                                 | `text/event-stream` ([07](07-facts-and-delivery.md#the-thread-stream))                                  |
| `GET /connections/callback`                                     | a 303 redirect to the authorization's return URL, or a JSON outcome ([04](04-resources.md#connections)) |

Failures on these routes still use the JSON [error envelope](#errors). Stored tenant bytes are served with `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox` and `Cache-Control: private, no-store`; asset and skill content adds `Content-Disposition: attachment` with the stored name, and images are served inline. Timestamps are RFC 3339 in UTC. IDs are kind-prefixed ([02](02-layout.md#object-ids)); clients never parse them.

### Collections

A collection answers `{"items": [...], "next_cursor": "…" | null}`; `null` means there is no next page. `limit` is 1 to 100 and defaults to 50. `cursor` is opaque and at most 2048 characters. A cursor is bound to its collection, to the scope that produced it and to every filter of the query that issued it, so it continues only that query; a malformed cursor, or one presented with another collection, scope or filter set, is 400 `invalid_cursor`. Each list's order and filters are in its owning chapter.

Some reads are bounded catalogues and return all their items with `next_cursor: null`, taking no `limit` or `cursor`: provider types, a connection's tools, a connector app's actions and a thread's mounts. Toolsets and a run's attempts return `{"items": [...]}` only and the model catalog `{"items": [...], "status": ...}`; lineage takes only `cursor`. Trace pages keep the backend's native order, can hold fewer items than the limit, and keep the first page's resolved time window on every later page; their cursor is also bound to the window parameters as given ([07](07-facts-and-delivery.md#trace-query)).

### Preconditions

Every mutable resource has a `version` that a database trigger advances on each change; an update that changes nothing, or changes only bookkeeping columns its table declares unversioned, keeps it ([04](04-resources.md#rules-every-kind-follows)). A resource's strong ETag is the quoted `"{id}:{version}"` of its view, so a client may form `If-Match` from any view, including a list row, without reading the item again; a response that represents one versioned resource also carries it in `ETag`. Rows without a version are immutable or have their own precondition: revisions, audit events, grants, members, login sessions, webhook deliveries and spans. Inbox entries and thread mounts are edited under the thread's ETag, which the mount routes also return in `ETag`.

Every conditional route declares the `If-Match` header (at most 512 characters). The OpenAPI document marks the header optional, but the server requires it: a missing header is 428 `precondition_required` (details `{header: "If-Match"}`) and a stale one is 412 `precondition_failed` (details `{current_etag}`). These state changes take no `If-Match`:

- creations other than a revision (under its head's ETag, [04](04-resources.md#revisioned-heads)) and a thread mount (under the thread's ETag), and the idempotent submissions below;
- the public account flows, which a password or a one-use token authorizes;
- a grant's role change and removal, where the grant ID is the precondition because a role change replaces the grant ([03](03-tenancy.md#grants));
- ending a login session, changing one's password and disabling one's own account, which name one session or check the current password;
- interrupt, whose outcome follows from the run's state ([05](05-runs.md#waiting-interrupt-and-fork));
- webhook redelivery, which requires a dead delivery ([07](07-facts-and-delivery.md#lifecycle-webhooks));
- provider and connection tests, validation checks and preparing the configuration assistant.

### Idempotency

These commands require `Idempotency-Key`: 1 to 512 visible ASCII characters (`^[!-~]+$`); a missing or malformed key is 400 `invalid_argument`. The evidence lives on what the command created:

| Command                                                                     | Evidence and key namespace                                                        | Replay compares                      | Reuse for another request                |
| --------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ------------------------------------ | ---------------------------------------- |
| `POST …/threads`, `POST …/threads/{thread}/inbox`, `POST …/runs/{run}/fork` | The entry; unique `(workspace_id, principal_id, request_key)` shared by all three | Request kind, target and body digest | 409 `conflict`, `idempotency_key_reused` |
| `POST …/runs/{run}/resume`                                                  | The successor run; unique `(workspace_id, resumed_by_id, request_key)`            | Digest of the run ID and request     | 409 `conflict`, `idempotency_key_reused` |
| `POST …/uploads`                                                            | The upload, whose ID derives from organization, workspace, principal and key      | The bytes, filename and content type | 409 `conflict`, `idempotency_key_reused` |

Authentication and current scope permission precede the lookup, and the lookup precedes validation of mutable state. A first call answers 201 (uploads: 200); a replay answers 200 with the created objects in their current state, not a byte-for-byte copy of the first response. Concurrent callers are arbitrated by the unique index (uploads: by the object store's create-once write): the loser rolls back everything it tentatively created and replays the winner. Pending edits never change the stored digest, and keys and withdrawn entries stay with history, so a key is never reusable. Submissions return `Submitted {thread, entry, run | null}`; resume returns the successor run.

Two internal commands replay the same way without a header: a child spawn is unique by its delegating tool call, and a child result by its sealed child run ([05](05-runs.md#child-runs)). Asset creation replays by its upload without a header ([04](04-resources.md#uploads-and-assets)). Other creations promise no replay; they rely on resource uniqueness (`already_exists`) and readback.

### Errors

Every failure, including a request no route answers, answers in one envelope; `/readyz` reports unreadiness in its own 503 body ([09](09-runtime.md#startup-readiness-and-shutdown)):

```json
{"error": {"code": "not_found", "message": "agent agent_… not found",
           "details": {"kind": "agent", "id": "agent_…"}, "request_id": "req_…"}}
```

[02](02-layout.md#error-codes) lists the codes, their statuses and details. Clients branch on `code` and `details`, never on `message`.

- Request validation fails with 400 `invalid_argument` and `details.fields`, at most 20 `{field, reason}` pairs that never echo input values. A body that cannot be parsed, such as a malformed multipart upload, is 400 `invalid_argument` with `field` `body` and `reason` `unparsable`.
- A path no route answers, or a method a path does not accept, is 404 `not_found` with details `{"kind": "route", "id": "<METHOD> <path>"}`; 405 is never sent. Status 422 means `disabled`: the target, its principal or its workspace is disabled or archived where it is used. Changing an archived agent or skill is 409 `conflict` `archived`, and new input naming a retired asset is `invalid_argument`.
- The OpenAPI document declares the envelope once, as the `ErrorEnvelope` schema and a shared `Error` response carrying `X-Request-Id`, and every `/api/v1` operation lists that response as its default, and for 400 when it takes parameters or a body.
- `rate_limited` details carry `retry_after_seconds`, which the `Retry-After` header repeats; 408 `request_timeout` and 413 `payload_too_large` close the connection; error responses carry `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.
- `request_id` repeats the response's `X-Request-Id` ([09](09-runtime.md#http-ingress-and-escaping-failures)).

### Statuses

201 answers a creation, 200 a replay or any other success with a body, and 204 a success without one. A revision publication equal to the current default also answers 201 ([04](04-resources.md#revisioned-heads)), and preparing the configuration assistant answers 200 whether it creates or updates the agent. 202 answers environment stop and delete, which only begin an operation ([06](06-environments.md#stop-start-and-delete)).

## Route index

Paths are relative to `/api/v1` unless they start at the root. `{org}` is an organization ID and `{ws}` a workspace ID or key.

### Health

| Path       | Methods | Owner                                              |
| ---------- | ------- | -------------------------------------------------- |
| `/healthz` | GET     | [09](09-runtime.md#startup-readiness-and-shutdown) |
| `/readyz`  | GET     | [09](09-runtime.md#startup-readiness-and-shutdown) |

`/api/v1/openapi.json`, `/api/v1/docs` and `/api/v1/docs/oauth2-redirect` are served by API roles and are not part of the exported document.

### Authentication and account

| Path                                 | Methods     | Owner                                           |
| ------------------------------------ | ----------- | ----------------------------------------------- |
| `/auth/configuration`                | GET         | [03](03-tenancy.md#identity-mail) (public)      |
| `/auth/login`                        | POST        | [03](03-tenancy.md#authentication) (public)     |
| `/auth/logout`                       | POST        | [03](03-tenancy.md#authentication)              |
| `/auth/session`                      | GET         | [03](03-tenancy.md#authentication)              |
| `/auth/password-reset`               | POST        | [03](03-tenancy.md#account-management) (public) |
| `/auth/password-reset/confirm`       | POST        | [03](03-tenancy.md#account-management) (public) |
| `/auth/email-change/confirm`         | POST        | [03](03-tenancy.md#account-management) (public) |
| `/invitations/{invitation}/accept`   | POST        | [03](03-tenancy.md#invitations) (public)        |
| `/users/me`                          | GET, PATCH  | [03](03-tenancy.md#account-management)          |
| `/users/me/password`                 | POST        | [03](03-tenancy.md#account-management)          |
| `/users/me/disable`                  | POST        | [03](03-tenancy.md#disabling)                   |
| `/users/me/login-sessions`           | GET         | [03](03-tenancy.md#account-management)          |
| `/users/me/login-sessions/{session}` | DELETE      | [03](03-tenancy.md#account-management)          |
| `/users/me/keys`                     | GET, POST   | [03](03-tenancy.md#api-keys)                    |
| `/users/me/keys/{key}`               | DELETE      | [03](03-tenancy.md#api-keys)                    |
| `/users/me/audit-events`             | GET         | [03](03-tenancy.md#audit)                       |
| `/users/me/avatar`                   | PUT, DELETE | [03](03-tenancy.md#images)                      |
| `/users/{user}/avatar`               | GET         | [03](03-tenancy.md#images)                      |

### Organizations, workspaces and members

| Path                                                   | Methods            | Owner                                            |
| ------------------------------------------------------ | ------------------ | ------------------------------------------------ |
| `/organizations`                                       | GET                | [03](03-tenancy.md#organizations-and-workspaces) |
| `/organizations/{org}`                                 | GET, PATCH         | [03](03-tenancy.md#organizations-and-workspaces) |
| `/organizations/{org}/icon`                            | GET, PUT, DELETE   | [03](03-tenancy.md#images)                       |
| `/organizations/{org}/workspaces`                      | GET, POST          | [03](03-tenancy.md#organizations-and-workspaces) |
| `/organizations/{org}/members`                         | GET                | [03](03-tenancy.md#grants)                       |
| `/organizations/{org}/grants`                          | GET, POST          | [03](03-tenancy.md#grants)                       |
| `/organizations/{org}/grants/{grant}`                  | PATCH, DELETE      | [03](03-tenancy.md#grants)                       |
| `/organizations/{org}/invitations`                     | GET, POST          | [03](03-tenancy.md#invitations)                  |
| `/organizations/{org}/invitations/{invitation}/resend` | POST               | [03](03-tenancy.md#invitations)                  |
| `/organizations/{org}/invitations/{invitation}/revoke` | POST               | [03](03-tenancy.md#invitations)                  |
| `/organizations/{org}/audit-events`                    | GET                | [03](03-tenancy.md#audit)                        |
| `/workspaces`                                          | GET                | [03](03-tenancy.md#organizations-and-workspaces) |
| `/workspaces/{ws}`                                     | GET, PATCH         | [03](03-tenancy.md#organizations-and-workspaces) |
| `/workspaces/{ws}/archive`                             | POST               | [03](03-tenancy.md#organizations-and-workspaces) |
| `/workspaces/{ws}/icon`                                | GET, PUT, DELETE   | [03](03-tenancy.md#images)                       |
| `/workspaces/{ws}/grants`                              | GET, POST          | [03](03-tenancy.md#grants)                       |
| `/workspaces/{ws}/grants/{grant}`                      | PATCH, DELETE      | [03](03-tenancy.md#grants)                       |
| `/workspaces/{ws}/invitations`                         | GET, POST          | [03](03-tenancy.md#invitations)                  |
| `/workspaces/{ws}/invitations/{invitation}/resend`     | POST               | [03](03-tenancy.md#invitations)                  |
| `/workspaces/{ws}/invitations/{invitation}/revoke`     | POST               | [03](03-tenancy.md#invitations)                  |
| `/workspaces/{ws}/service-accounts`                    | GET, POST          | [03](03-tenancy.md#service-accounts)             |
| `/workspaces/{ws}/service-accounts/{account}`          | GET, PATCH, DELETE | [03](03-tenancy.md#service-accounts)             |
| `/workspaces/{ws}/service-accounts/{account}/keys`     | GET, POST          | [03](03-tenancy.md#api-keys)                     |
| `/workspaces/{ws}/keys`                                | GET                | [03](03-tenancy.md#api-keys)                     |
| `/workspaces/{ws}/keys/{key}`                          | DELETE             | [03](03-tenancy.md#api-keys)                     |
| `/workspaces/{ws}/audit-events`                        | GET                | [03](03-tenancy.md#audit)                        |

### Providers and models

`{kind}-providers` stands for each of `model-providers`, `environment-providers`, `web-providers`, `connector-providers` and `memory-providers`.

| Path                                                    | Methods    | Owner                                            |
| ------------------------------------------------------- | ---------- | ------------------------------------------------ |
| `/provider-types/{kind}`                                | GET        | [08](08-providers.md#provider-type-descriptions) |
| `/organizations/{org}/{kind}-providers`                 | GET, POST  | [04](04-resources.md#provider-resources)         |
| `/organizations/{org}/{kind}-providers/{provider}`      | GET, PATCH | [04](04-resources.md#provider-resources)         |
| `/organizations/{org}/{kind}-providers/{provider}/test` | POST       | [04](04-resources.md#provider-resources)         |
| `/model-catalog`                                        | GET        | [08](08-providers.md#model-catalog)              |
| `/organizations/{org}/models`                           | GET, POST  | [04](04-resources.md#models)                     |
| `/organizations/{org}/models/{model}`                   | GET, PATCH | [04](04-resources.md#models)                     |
| `/workspaces/{ws}/media-understanding-defaults`         | GET, PUT   | [04](04-resources.md#models)                     |

### Agents, skills and toolsets

| Path                                                                | Methods          | Owner                                             |
| ------------------------------------------------------------------- | ---------------- | ------------------------------------------------- |
| `/workspaces/{ws}/agents`                                           | GET, POST        | [04](04-resources.md#agents)                      |
| `/workspaces/{ws}/agents/validate`                                  | POST             | [04](04-resources.md#operations)                  |
| `/workspaces/{ws}/agents/{agent}`                                   | GET, PATCH       | [04](04-resources.md#operations)                  |
| `/workspaces/{ws}/agents/{agent}/archive`                           | POST             | [04](04-resources.md#revisioned-heads)            |
| `/workspaces/{ws}/agents/{agent}/unarchive`                         | POST             | [04](04-resources.md#revisioned-heads)            |
| `/workspaces/{ws}/agents/{agent}/duplicate`                         | POST             | [04](04-resources.md#operations)                  |
| `/workspaces/{ws}/agents/{agent}/avatar`                            | GET, PUT, DELETE | [04](04-resources.md#operations)                  |
| `/workspaces/{ws}/agents/{agent}/revisions`                         | GET, POST        | [04](04-resources.md#revisioned-heads)            |
| `/workspaces/{ws}/agents/{agent}/revisions/{revision}`              | GET              | [04](04-resources.md#revisioned-heads)            |
| `/workspaces/{ws}/agents/{agent}/revisions/{revision}/set-default`  | POST             | [04](04-resources.md#revisioned-heads)            |
| `/workspaces/{ws}/configuration-assistant`                          | POST             | [04](04-resources.md#the-configuration-assistant) |
| `/workspaces/{ws}/toolsets`                                         | GET              | [04](04-resources.md#agents)                      |
| `/workspaces/{ws}/skills`                                           | GET, POST        | [04](04-resources.md#skills)                      |
| `/workspaces/{ws}/skills/validate`                                  | POST             | [04](04-resources.md#skills)                      |
| `/workspaces/{ws}/skills/{skill}`                                   | GET, PATCH       | [04](04-resources.md#skills)                      |
| `/workspaces/{ws}/skills/{skill}/archive`                           | POST             | [04](04-resources.md#revisioned-heads)            |
| `/workspaces/{ws}/skills/{skill}/unarchive`                         | POST             | [04](04-resources.md#revisioned-heads)            |
| `/workspaces/{ws}/skills/{skill}/revisions`                         | GET, POST        | [04](04-resources.md#skills)                      |
| `/workspaces/{ws}/skills/{skill}/revisions/{revision}`              | GET              | [04](04-resources.md#skills)                      |
| `/workspaces/{ws}/skills/{skill}/revisions/{revision}/content`      | GET              | [04](04-resources.md#skills)                      |
| `/workspaces/{ws}/skills/{skill}/revisions/{revision}/files/{path}` | GET              | [04](04-resources.md#skills)                      |
| `/workspaces/{ws}/skills/{skill}/revisions/{revision}/set-default`  | POST             | [04](04-resources.md#revisioned-heads)            |

### Connections

| Path                                                                 | Methods    | Owner                                      |
| -------------------------------------------------------------------- | ---------- | ------------------------------------------ |
| `/workspaces/{ws}/connections`                                       | GET, POST  | [04](04-resources.md#connections)          |
| `/workspaces/{ws}/connections/{connection}`                          | GET, PATCH | [04](04-resources.md#connections)          |
| `/workspaces/{ws}/connections/{connection}/authorize`                | POST       | [04](04-resources.md#connections)          |
| `/workspaces/{ws}/connections/{connection}/revoke`                   | POST       | [04](04-resources.md#connections)          |
| `/workspaces/{ws}/connections/{connection}/test`                     | POST       | [04](04-resources.md#connections)          |
| `/workspaces/{ws}/connections/{connection}/tools`                    | GET        | [04](04-resources.md#connections)          |
| `/workspaces/{ws}/connector-providers/{provider}/apps`               | GET        | [04](04-resources.md#provider-resources)   |
| `/workspaces/{ws}/connector-providers/{provider}/apps/{app}`         | GET        | [04](04-resources.md#provider-resources)   |
| `/workspaces/{ws}/connector-providers/{provider}/apps/{app}/actions` | GET        | [04](04-resources.md#provider-resources)   |
| `/connections/callback`                                              | GET        | [04](04-resources.md#connections) (public) |
| `/connections/redirect-uri`                                          | GET        | [04](04-resources.md#connections)          |
| `/mcp-servers`                                                       | GET        | [08](08-providers.md#mcp-server-catalogue) |

### Secrets, uploads and assets

| Path                                      | Methods          | Owner                                    |
| ----------------------------------------- | ---------------- | ---------------------------------------- |
| `/workspaces/{ws}/secrets`                | GET, POST        | [04](04-resources.md#secrets)            |
| `/workspaces/{ws}/secrets/{secret}`       | GET, PUT, DELETE | [04](04-resources.md#secrets)            |
| `/workspaces/{ws}/uploads`                | POST             | [04](04-resources.md#uploads-and-assets) |
| `/workspaces/{ws}/assets`                 | GET, POST        | [04](04-resources.md#uploads-and-assets) |
| `/workspaces/{ws}/assets/{asset}`         | GET, DELETE      | [04](04-resources.md#uploads-and-assets) |
| `/workspaces/{ws}/assets/{asset}/content` | GET              | [04](04-resources.md#uploads-and-assets) |

### Subscriptions

| Path                                                                            | Methods            | Owner                                             |
| ------------------------------------------------------------------------------- | ------------------ | ------------------------------------------------- |
| `/workspaces/{ws}/subscriptions`                                                | GET, POST          | [04](04-resources.md#subscriptions)               |
| `/workspaces/{ws}/subscriptions/{subscription}`                                 | GET, PATCH, DELETE | [04](04-resources.md#subscriptions)               |
| `/workspaces/{ws}/subscriptions/{subscription}/deliveries`                      | GET                | [07](07-facts-and-delivery.md#lifecycle-webhooks) |
| `/workspaces/{ws}/subscriptions/{subscription}/deliveries/{delivery}/redeliver` | POST               | [07](07-facts-and-delivery.md#lifecycle-webhooks) |

### Sessions, threads and runs

| Path                                              | Methods            | Owner                                            |
| ------------------------------------------------- | ------------------ | ------------------------------------------------ |
| `/workspaces/{ws}/sessions`                       | GET, POST          | [05](05-runs.md#reads)                           |
| `/workspaces/{ws}/sessions/{session}`             | GET, PATCH         | [05](05-runs.md#reads)                           |
| `/workspaces/{ws}/threads`                        | GET, POST          | [05](05-runs.md#submit-and-accept)               |
| `/workspaces/{ws}/threads/{thread}`               | GET, PATCH         | [05](05-runs.md#reads)                           |
| `/workspaces/{ws}/threads/{thread}/archive`       | POST               | [05](05-runs.md#waiting-interrupt-and-fork)      |
| `/workspaces/{ws}/threads/{thread}/runs`          | GET                | [05](05-runs.md#reads)                           |
| `/workspaces/{ws}/threads/{thread}/stream`        | GET                | [07](07-facts-and-delivery.md#the-thread-stream) |
| `/workspaces/{ws}/threads/{thread}/inbox`         | GET, POST          | [05](05-runs.md#submit-and-accept)               |
| `/workspaces/{ws}/threads/{thread}/inbox/order`   | PUT                | [05](05-runs.md#editing-queued-input)            |
| `/workspaces/{ws}/threads/{thread}/inbox/{entry}` | GET, PATCH, DELETE | [05](05-runs.md#editing-queued-input)            |
| `/workspaces/{ws}/runs/{run}`                     | GET, PATCH         | [05](05-runs.md#reads)                           |
| `/workspaces/{ws}/runs/{run}/interrupt`           | POST               | [05](05-runs.md#waiting-interrupt-and-fork)      |
| `/workspaces/{ws}/runs/{run}/fork`                | POST               | [05](05-runs.md#waiting-interrupt-and-fork)      |
| `/workspaces/{ws}/runs/{run}/resume`              | POST               | [05](05-runs.md#waiting-interrupt-and-fork)      |
| `/workspaces/{ws}/runs/{run}/items`               | GET                | [05](05-runs.md#reads)                           |
| `/workspaces/{ws}/runs/{run}/lineage`             | GET                | [05](05-runs.md#reads)                           |
| `/workspaces/{ws}/runs/{run}/attempts`            | GET                | [05](05-runs.md#reads)                           |

### Environments

| Path                                                    | Methods            | Owner                                                                                                   |
| ------------------------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------- |
| `/workspaces/{ws}/environment-templates`                | GET, POST          | [04](04-resources.md#environment-templates)                                                             |
| `/workspaces/{ws}/environment-templates/{template}`     | GET, PATCH         | [04](04-resources.md#environment-templates)                                                             |
| `/workspaces/{ws}/environments`                         | GET, POST          | [06](06-environments.md#mounts), [external targets](06-environments.md#external-targets)                |
| `/workspaces/{ws}/environments/{environment}`           | GET, PATCH, DELETE | [06](06-environments.md#stop-start-and-delete), [external targets](06-environments.md#external-targets) |
| `/workspaces/{ws}/environments/{environment}/stop`      | POST               | [06](06-environments.md#stop-start-and-delete)                                                          |
| `/workspaces/{ws}/threads/{thread}/environments`        | GET, POST          | [06](06-environments.md#mounts)                                                                         |
| `/workspaces/{ws}/threads/{thread}/environments/{name}` | DELETE             | [06](06-environments.md#mounts)                                                                         |

### Memories

| Path                                                         | Methods            | Owner                                                |
| ------------------------------------------------------------ | ------------------ | ---------------------------------------------------- |
| `/workspaces/{ws}/memories`                                  | GET, POST          | [11](11-memory.md#memories)                          |
| `/workspaces/{ws}/memories/{memory}`                         | GET, PATCH, DELETE | [11](11-memory.md#memories)                          |
| `/workspaces/{ws}/memories/{memory}/files`                   | GET, POST          | [11](11-memory.md#files-and-history-through-the-api) |
| `/workspaces/{ws}/memories/{memory}/files/move`              | POST               | [11](11-memory.md#files-and-history-through-the-api) |
| `/workspaces/{ws}/memories/{memory}/files/{path}`            | GET, PUT, DELETE   | [11](11-memory.md#files-and-history-through-the-api) |
| `/workspaces/{ws}/memories/{memory}/revisions`               | GET, DELETE        | [11](11-memory.md#files-and-history-through-the-api) |
| `/workspaces/{ws}/memories/{memory}/revisions/{seq}`         | GET                | [11](11-memory.md#files-and-history-through-the-api) |
| `/workspaces/{ws}/memories/{memory}/revisions/{seq}/restore` | POST               | [11](11-memory.md#files-and-history-through-the-api) |
| `/workspaces/{ws}/memories/{memory}/records`                 | GET, POST          | [11](11-memory.md#records-through-the-api)           |
| `/workspaces/{ws}/memories/{memory}/records/search`          | POST               | [11](11-memory.md#records-through-the-api)           |
| `/workspaces/{ws}/memories/{memory}/records/{record}`        | PUT, DELETE        | [11](11-memory.md#records-through-the-api)           |
| `/workspaces/{ws}/threads/{thread}/memories`                 | GET, POST          | [11](11-memory.md#mounts)                            |
| `/workspaces/{ws}/threads/{thread}/memories/{name}`          | PATCH, DELETE      | [11](11-memory.md#mounts)                            |

A file `{path}` is the file's path in the memory, with its `/` separators. A file's ETag names its current version; creating a file takes no `If-Match`, and a restore takes one only when a file exists at the revision's path. A `{record}` is the backend's record ID, at most 256 characters; records carry no ETag.

### Usage and traces

| Path                                                   | Methods | Owner                                        |
| ------------------------------------------------------ | ------- | -------------------------------------------- |
| `/workspaces/{ws}/usage`                               | GET     | [07](07-facts-and-delivery.md#usage-records) |
| `/workspaces/{ws}/runs/{run}/attempts/{attempt}/trace` | GET     | [07](07-facts-and-delivery.md#trace-query)   |
| `/workspaces/{ws}/traces`                              | GET     | [07](07-facts-and-delivery.md#trace-query)   |
| `/workspaces/{ws}/traces/{trace}`                      | GET     | [07](07-facts-and-delivery.md#trace-query)   |
| `/workspaces/{ws}/traces/{trace}/spans`                | GET     | [07](07-facts-and-delivery.md#trace-query)   |
| `/workspaces/{ws}/trace-backend`                       | GET     | [07](07-facts-and-delivery.md#trace-query)   |

## Exported contracts

`proto/a13n-service/` holds the two wire contracts generated from the code:

- `openapi.json`, the OpenAPI document the application serves at `/api/v1/openapi.json`;
- `thread-stream.schema.json`, the JSON Schema of each thread-stream frame's `data`, keyed by SSE event name (`delta`, `boundary`, `changed`, `reset`, `gap`).

`make service-contract-generate` exports both and regenerates the Console's typed client; `make service-contract-check` fails when either export or the client differs from the code. A change to a route, schema or frame updates these exports in the same change.
