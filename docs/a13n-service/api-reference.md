# Service HTTP reference

This reference is generated from the Service OpenAPI export. [HTTP conventions](http.md) explain authentication, preconditions, request keys, paging and errors, which apply to every operation below.

Download [the complete OpenAPI JSON](../assets/reference/service-openapi.json).

## agents

### `GET /api/v1/workspaces/{workspace_id}/agents`

List Agents.

Agents of the workspace. `q` matches the key, name or description, ignoring case; `archived` keeps only archived agents, or only open ones; the skill filters keep those with a revision pinning that skill or revision.

| Parameter           | Location | Required | Type / schema           | Constraints and default            |
| ------------------- | -------- | -------- | ----------------------- | ---------------------------------- |
| `workspace_id`      | path     | true     | string                  | —                                  |
| `label`             | query    | false    | array of string or null | —                                  |
| `q`                 | query    | false    | string or null          | minLength=1; maxLength=256         |
| `archived`          | query    | false    | boolean or null         | —                                  |
| `skill_id`          | query    | false    | string or null          | maxLength=72                       |
| `skill_revision_id` | query    | false    | string or null          | maxLength=72                       |
| `limit`             | query    | false    | integer                 | minimum=1; maximum=100; default=50 |
| `cursor`            | query    | false    | string or null          | —                                  |

Responses:

- **200** — Successful Response (`application/json: AgentPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/agents`

Create Agent.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `AgentCreate`.

Responses:

- **201** — Successful Response (`application/json: Agent`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/agents/validate`

Validate Revision.

No content when creating a revision of the configuration would accept it, else the same `invalid_argument` error with the field's path relative to `config`; nothing is stored.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `AgentValidate`.

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/agents/{agent_id}`

Get Agent.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `agent_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Agent`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/agents/{agent_id}`

Update Agent.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `agent_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `AgentUpdate`.

Responses:

- **200** — Successful Response (`application/json: Agent`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/agents/{agent_id}/archive`

Archive Agent.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `agent_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Agent`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/agents/{agent_id}/avatar`

Delete Avatar.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `agent_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Agent`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/agents/{agent_id}/avatar`

Get Avatar.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `agent_id`     | path     | true     | string        | —                       |

Responses:

- **200** — The image (`image/jpeg: string; image/png: string; image/webp: string`).
- **400** — .
- **default** — .

### `PUT /api/v1/workspaces/{workspace_id}/agents/{agent_id}/avatar`

Put Avatar.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `agent_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `image/jpeg`: `string`.
- `image/png`: `string`.
- `image/webp`: `string`.

Responses:

- **200** — Successful Response (`application/json: Agent`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/agents/{agent_id}/duplicate`

Duplicate Agent.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `agent_id`     | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `AgentDuplicate`.

Responses:

- **201** — Successful Response (`application/json: Agent`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions`

List Revisions.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `agent_id`     | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: AgentRevisionPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions`

Create Revision.

A configuration that validates to the default revision's creates nothing and returns that revision.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `agent_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `AgentRevisionCreate`.

Responses:

- **201** — Successful Response (`application/json: AgentRevision`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions/{revision_id}`

Get Revision.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `agent_id`     | path     | true     | string        | —                       |
| `revision_id`  | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: AgentRevision`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/agents/{agent_id}/revisions/{revision_id}/set-default`

Set Default.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `agent_id`     | path     | true     | string         | —                       |
| `revision_id`  | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Agent`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/agents/{agent_id}/unarchive`

Unarchive Agent.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `agent_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Agent`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/configuration-assistant`

Prepare Assistant.

The workspace's configuration assistant, created or brought up to date with the deployment's definition.

Refused with `model_required` while the workspace has no model the caller can use.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Agent`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/toolsets`

List Toolsets.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ToolsetCatalog`).
- **400** — .
- **default** — .

## assets

### `GET /api/v1/workspaces/{workspace_id}/assets`

List Assets.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: AssetPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/assets`

Create Asset.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `AssetCreate`.

Responses:

- **200** — The asset already created from this upload (`application/json: Asset`).
- **201** — Successful Response (`application/json: Asset`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/assets/{asset_id}`

Retire Asset.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `asset_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Asset`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/assets/{asset_id}`

Get Asset.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `asset_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Asset`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/assets/{asset_id}/content`

Read Asset Content.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `asset_id`     | path     | true     | string        | —                       |

Responses:

- **200** — The asset bytes, with their stored content type (`*/*: string`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/uploads`

Create Upload.

Repeating a request with the same `Idempotency-Key` and bytes returns the same upload.

| Parameter         | Location | Required | Type / schema | Constraints and default                          |
| ----------------- | -------- | -------- | ------------- | ------------------------------------------------ |
| `workspace_id`    | path     | true     | string        | —                                                |
| `Idempotency-Key` | header   | true     | string        | minLength=1; maxLength=512; `pattern="^[!-~]+$"` |

Request body: required.

- `multipart/form-data`: `UploadCreate`.

Responses:

- **200** — Successful Response (`application/json: Upload`).
- **400** — .
- **default** — .

## auth

### `GET /api/v1/auth/configuration`

Auth Configuration.

Responses:

- **200** — Successful Response (`application/json: AuthConfiguration`).
- **default** — .

### `POST /api/v1/auth/email-change/confirm`

Confirm Email Change.

Request body: required.

- `application/json`: `EmailChangeConfirm`.

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `POST /api/v1/auth/login`

Password Login.

Request body: required.

- `application/json`: `LoginInput`.

Responses:

- **200** — Successful Response (`application/json: LoginOutput`).
- **400** — .
- **default** — .

### `POST /api/v1/auth/logout`

Logout.

Responses:

- **204** — Successful Response.
- **default** — .

### `POST /api/v1/auth/password-reset`

Request Password Reset.

Request body: required.

- `application/json`: `PasswordReset`.

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `POST /api/v1/auth/password-reset/confirm`

Confirm Password Reset.

Request body: required.

- `application/json`: `PasswordResetConfirm`.

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `GET /api/v1/auth/session`

Session Profile.

Restores a browser session; the CSRF token is stable for the session's lifetime.

Responses:

- **200** — Successful Response (`application/json: SessionProfile`).
- **default** — .

### `POST /api/v1/invitations/{invitation_id}/accept`

Accept.

Public by token: creates or joins the invited account and starts a login session.

| Parameter       | Location | Required | Type / schema | Constraints and default |
| --------------- | -------- | -------- | ------------- | ----------------------- |
| `invitation_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `InvitationAccept`.

Responses:

- **200** — Successful Response (`application/json: LoginOutput`).
- **400** — .
- **default** — .

### `GET /api/v1/users/me`

Get Profile.

Responses:

- **200** — Successful Response (`application/json: Profile`).
- **default** — .

### `PATCH /api/v1/users/me`

Update Profile.

| Parameter  | Location | Required | Type / schema  | Constraints and default |
| ---------- | -------- | -------- | -------------- | ----------------------- |
| `If-Match` | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `ProfileUpdate`.

Responses:

- **200** — Successful Response (`application/json: Profile`).
- **400** — .
- **default** — .

### `GET /api/v1/users/me/audit-events`

List Account Audit Events.

The caller's own trail, account-wide events included; requires a login session.

| Parameter | Location | Required | Type / schema  | Constraints and default            |
| --------- | -------- | -------- | -------------- | ---------------------------------- |
| `limit`   | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`  | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: AuditPage`).
- **400** — .
- **default** — .

### `DELETE /api/v1/users/me/avatar`

Delete Avatar.

| Parameter  | Location | Required | Type / schema  | Constraints and default |
| ---------- | -------- | -------- | -------------- | ----------------------- |
| `If-Match` | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Profile`).
- **400** — .
- **default** — .

### `PUT /api/v1/users/me/avatar`

Put Avatar.

| Parameter  | Location | Required | Type / schema  | Constraints and default |
| ---------- | -------- | -------- | -------------- | ----------------------- |
| `If-Match` | header   | false    | string or null | maxLength=512           |

Request body: required.

- `image/jpeg`: `string`.
- `image/png`: `string`.
- `image/webp`: `string`.

Responses:

- **200** — Successful Response (`application/json: Profile`).
- **400** — .
- **default** — .

### `POST /api/v1/users/me/disable`

Disable Account.

Disable the caller's own account, proven by the current password; no route enables it again.

Request body: required.

- `application/json`: `AccountDisable`.

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `GET /api/v1/users/me/keys`

List User Keys.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | query    | false    | string or null | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ApiKeyPage`).
- **400** — .
- **default** — .

### `POST /api/v1/users/me/keys`

Create User Key.

Needs a login session: an API key never issues keys, so a leaked key cannot outlive its revocation.

Request body: required.

- `application/json`: `UserKeyCreate`.

Responses:

- **201** — Successful Response (`application/json: IssuedKey`).
- **400** — .
- **default** — .

### `DELETE /api/v1/users/me/keys/{key_id}`

Revoke User Key.

| Parameter  | Location | Required | Type / schema  | Constraints and default |
| ---------- | -------- | -------- | -------------- | ----------------------- |
| `key_id`   | path     | true     | string         | —                       |
| `If-Match` | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: ApiKey`).
- **400** — .
- **default** — .

### `GET /api/v1/users/me/login-sessions`

List Login Sessions.

| Parameter | Location | Required | Type / schema  | Constraints and default            |
| --------- | -------- | -------- | -------------- | ---------------------------------- |
| `limit`   | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`  | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: LoginSessionPage`).
- **400** — .
- **default** — .

### `DELETE /api/v1/users/me/login-sessions/{session_id}`

Revoke Login Session.

| Parameter    | Location | Required | Type / schema | Constraints and default |
| ------------ | -------- | -------- | ------------- | ----------------------- |
| `session_id` | path     | true     | string        | —                       |

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `POST /api/v1/users/me/password`

Change Password.

Request body: required.

- `application/json`: `PasswordChange`.

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `GET /api/v1/users/{user_id}/avatar`

Get Avatar.

| Parameter | Location | Required | Type / schema | Constraints and default |
| --------- | -------- | -------- | ------------- | ----------------------- |
| `user_id` | path     | true     | string        | —                       |

Responses:

- **200** — The image (`image/jpeg: string; image/png: string; image/webp: string`).
- **400** — .
- **default** — .

## connections

### `GET /api/v1/connections/callback`

Complete Authorization.

Public: the one-use state authenticates the browser that the authorization server sends back, and the flow cookie the browser that started the flow.

Attempts per client address share the login flows' bound.

| Parameter     | Location | Required | Type / schema  | Constraints and default     |
| ------------- | -------- | -------- | -------------- | --------------------------- |
| `state`       | query    | true     | string         | minLength=16; maxLength=256 |
| `code`        | query    | false    | string or null | maxLength=4096              |
| `error`       | query    | false    | string or null | maxLength=256               |
| `iss`         | query    | false    | string or null | maxLength=2048              |
| `session_uri` | query    | false    | string or null | maxLength=2048              |

Responses:

- **200** — Successful Response (`application/json: CallbackOutcome`).
- **303** — Back to the return URL the authorization named.
- **400** — .
- **default** — .

### `GET /api/v1/connections/redirect-uri`

Get Redirect Uri.

The deployment's callback, for registering an OAuth client in advance; the same for every connection.

Responses:

- **200** — Successful Response (`application/json: OAuthRedirect`).
- **default** — .

### `GET /api/v1/mcp-servers`

List Mcp Servers.

Suggested Remote MCP servers, packaged and from the deployment; readable by every signed-in principal.

| Parameter | Location | Required | Type / schema  | Constraints and default            |
| --------- | -------- | -------- | -------------- | ---------------------------------- |
| `query`   | query    | false    | string or null | maxLength=128                      |
| `limit`   | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`  | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: McpServerPage`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/connections`

List Connections.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ConnectionPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/connections`

Create Connection.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `ConnectionCreate`.

Responses:

- **201** — Successful Response (`application/json: Connection`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/connections/{connection_id}`

Get Connection.

| Parameter       | Location | Required | Type / schema | Constraints and default |
| --------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id`  | path     | true     | string        | —                       |
| `connection_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Connection`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/connections/{connection_id}`

Update Connection.

| Parameter       | Location | Required | Type / schema  | Constraints and default |
| --------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id`  | path     | true     | string         | —                       |
| `connection_id` | path     | true     | string         | —                       |
| `If-Match`      | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `ConnectionUpdate`.

Responses:

- **200** — Successful Response (`application/json: Connection`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/connections/{connection_id}/authorize`

Authorize Connection.

A browser flow needs a login session and is bound to this browser by a cookie the callback checks; an API key authorizes only a client-credentials client, without a browser.

| Parameter       | Location | Required | Type / schema  | Constraints and default |
| --------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id`  | path     | true     | string         | —                       |
| `connection_id` | path     | true     | string         | —                       |
| `If-Match`      | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `AuthorizationRequest`.

Responses:

- **200** — Successful Response (`application/json: AuthorizationResult`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/connections/{connection_id}/revoke`

Revoke Connection.

| Parameter       | Location | Required | Type / schema  | Constraints and default |
| --------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id`  | path     | true     | string         | —                       |
| `connection_id` | path     | true     | string         | —                       |
| `If-Match`      | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: RevokedConnection`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/connections/{connection_id}/test`

Test Connection.

| Parameter       | Location | Required | Type / schema | Constraints and default |
| --------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id`  | path     | true     | string        | —                       |
| `connection_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ConnectionTest`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/connections/{connection_id}/tools`

List Tools.

| Parameter       | Location | Required | Type / schema | Constraints and default |
| --------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id`  | path     | true     | string        | —                       |
| `connection_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ToolPage`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/connector-providers/{provider_id}/apps`

List Apps.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `provider_id`  | path     | true     | string         | —                                  |
| `query`        | query    | false    | string or null | maxLength=128                      |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |
| `refresh`      | query    | false    | boolean        | default=false                      |

Responses:

- **200** — Successful Response (`application/json: ConnectorAppPage`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/connector-providers/{provider_id}/apps/{app}`

Get App.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `provider_id`  | path     | true     | string        | —                       |
| `app`          | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ConnectorApp`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/connector-providers/{provider_id}/apps/{app}/actions`

List Actions.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `provider_id`  | path     | true     | string        | —                       |
| `app`          | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ConnectorActionPage`).
- **400** — .
- **default** — .

## environments

### `GET /api/v1/workspaces/{workspace_id}/environment-templates`

List Templates.

| Parameter      | Location | Required | Type / schema           | Constraints and default            |
| -------------- | -------- | -------- | ----------------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string                  | —                                  |
| `label`        | query    | false    | array of string or null | —                                  |
| `limit`        | query    | false    | integer                 | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null          | —                                  |

Responses:

- **200** — Successful Response (`application/json: TemplatePage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/environment-templates`

Create Template.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `TemplateCreate`.

Responses:

- **201** — Successful Response (`application/json: Template`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/environment-templates/{template_id}`

Get Template.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `template_id`  | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Template`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/environment-templates/{template_id}`

Update Template.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `template_id`  | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `TemplateUpdate`.

Responses:

- **200** — Successful Response (`application/json: Template`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/environments`

List Environments.

| Parameter      | Location | Required | Type / schema  | Constraints and default                                                         |
| -------------- | -------- | -------- | -------------- | ------------------------------------------------------------------------------- |
| `workspace_id` | path     | true     | string         | —                                                                               |
| `status`       | query    | false    | string or null | `pattern="^(creating\|starting\|ready\|stopping\|stopped\|deleting\|deleted)$"` |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50                                              |
| `cursor`       | query    | false    | string or null | —                                                                               |

Responses:

- **200** — Successful Response (`application/json: EnvironmentPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/environments`

Create Environment.

Reserve a managed sandbox from a template (`creating`), or register an external envd target (`ready`).

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `ManagedEnvironmentCreate or ExternalTargetCreate`.

Responses:

- **201** — Successful Response (`application/json: EnvironmentView`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/environments/{environment_id}`

Delete Environment.

| Parameter        | Location | Required | Type / schema  | Constraints and default |
| ---------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id`   | path     | true     | string         | —                       |
| `environment_id` | path     | true     | string         | —                       |
| `If-Match`       | header   | false    | string or null | maxLength=512           |

Responses:

- **202** — Successful Response (`application/json: EnvironmentView`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/environments/{environment_id}`

Get Environment.

| Parameter        | Location | Required | Type / schema | Constraints and default |
| ---------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id`   | path     | true     | string        | —                       |
| `environment_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: EnvironmentView`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/environments/{environment_id}`

Update Environment.

| Parameter        | Location | Required | Type / schema  | Constraints and default |
| ---------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id`   | path     | true     | string         | —                       |
| `environment_id` | path     | true     | string         | —                       |
| `If-Match`       | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `EnvironmentUpdate`.

Responses:

- **200** — Successful Response (`application/json: EnvironmentView`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/environments/{environment_id}/stop`

Stop Environment.

| Parameter        | Location | Required | Type / schema  | Constraints and default |
| ---------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id`   | path     | true     | string         | —                       |
| `environment_id` | path     | true     | string         | —                       |
| `If-Match`       | header   | false    | string or null | maxLength=512           |

Responses:

- **202** — Successful Response (`application/json: EnvironmentView`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/threads/{thread_id}/environments`

List Mounts.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `thread_id`    | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: MountPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/threads/{thread_id}/environments`

Add Mount.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `thread_id`    | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `MountCreate`.

Responses:

- **201** — Successful Response (`application/json: MountView`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/threads/{thread_id}/environments/{name}`

Remove Mount.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `thread_id`    | path     | true     | string         | —                       |
| `name`         | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

## memories

### `GET /api/v1/workspaces/{workspace_id}/memories`

List Memories.

| Parameter      | Location | Required | Type / schema           | Constraints and default            |
| -------------- | -------- | -------- | ----------------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string                  | —                                  |
| `label`        | query    | false    | array of string or null | —                                  |
| `limit`        | query    | false    | integer                 | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null          | —                                  |

Responses:

- **200** — Successful Response (`application/json: MemoryPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/memories`

Create Memory.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `MemoryCreate`.

Responses:

- **201** — Successful Response (`application/json: Memory`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/memories/{memory_id}`

Delete Memory.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `memory_id`    | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/memories/{memory_id}`

Get Memory.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `memory_id`    | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Memory`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/memories/{memory_id}`

Update Memory.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `memory_id`    | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `MemoryUpdate`.

Responses:

- **200** — Successful Response (`application/json: Memory`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/memories/{memory_id}/files`

List Files.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `memory_id`    | path     | true     | string         | —                                  |
| `prefix`       | query    | false    | string         | maxLength=1024; default=""         |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: MemoryFilePage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/memories/{memory_id}/files`

Create File.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `memory_id`    | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `MemoryFileCreate`.

Responses:

- **201** — Successful Response (`application/json: MemoryFile`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/memories/{memory_id}/files/move`

Move File.

Move the source file `If-Match` names; the destination must be free.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `memory_id`    | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `MemoryFileMove`.

Responses:

- **200** — Successful Response (`application/json: MemoryFile`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/memories/{memory_id}/files/{path}`

Delete File.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `memory_id`    | path     | true     | string         | —                       |
| `path`         | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/memories/{memory_id}/files/{path}`

Read File.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `memory_id`    | path     | true     | string        | —                       |
| `path`         | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: MemoryFile`).
- **400** — .
- **default** — .

### `PUT /api/v1/workspaces/{workspace_id}/memories/{memory_id}/files/{path}`

Replace File.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `memory_id`    | path     | true     | string         | —                       |
| `path`         | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `MemoryFileReplace`.

Responses:

- **200** — Successful Response (`application/json: MemoryFile`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/memories/{memory_id}/revisions`

Purge History.

Delete every retained revision of one file path.

| Parameter      | Location | Required | Type / schema | Constraints and default     |
| -------------- | -------- | -------- | ------------- | --------------------------- |
| `workspace_id` | path     | true     | string        | —                           |
| `memory_id`    | path     | true     | string        | —                           |
| `path`         | query    | true     | string        | minLength=1; maxLength=1024 |

Responses:

- **200** — Successful Response (`application/json: HistoryPurge`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/memories/{memory_id}/revisions`

List Revisions.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `memory_id`    | path     | true     | string         | —                                  |
| `path`         | query    | false    | string or null | maxLength=1024                     |
| `run_id`       | query    | false    | string or null | maxLength=72                       |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: MemoryRevisionPage`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/memories/{memory_id}/revisions/{seq}`

Get Revision.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `memory_id`    | path     | true     | string        | —                       |
| `seq`          | path     | true     | integer       | —                       |

Responses:

- **200** — Successful Response (`application/json: MemoryRevisionDetail`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/memories/{memory_id}/revisions/{seq}/restore`

Restore Revision.

Set the path back to the content the change replaced; `If-Match` names the file there, if any.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `memory_id`    | path     | true     | string         | —                       |
| `seq`          | path     | true     | integer        | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: MemoryFileState`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/threads/{thread_id}/memories`

List Mounts.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `thread_id`    | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: MemoryMountPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/threads/{thread_id}/memories`

Add Mount.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `thread_id`    | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `MemoryMount`.

Responses:

- **201** — Successful Response (`application/json: MemoryMount`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/threads/{thread_id}/memories/{name}`

Remove Mount.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `thread_id`    | path     | true     | string         | —                       |
| `name`         | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

## models

### `GET /api/v1/model-catalog`

Get Model Catalog.

The models.dev models the registered model provider types serve, for any signed-in principal.

Responses:

- **200** — Successful Response (`application/json: ModelCatalog`).
- **default** — .

### `GET /api/v1/organizations/{organization_id}/models`

List Models.

| Parameter         | Location | Required | Type / schema  | Constraints and default            |
| ----------------- | -------- | -------- | -------------- | ---------------------------------- |
| `organization_id` | path     | true     | string         | —                                  |
| `workspace_id`    | query    | false    | string or null | —                                  |
| `limit`           | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`          | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ModelPage`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/models`

Create Model.

Needs `write` on the model's scope and on its provider, whose credential the model spends.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `ModelCreate`.

Responses:

- **201** — Successful Response (`application/json: Model`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/models/{model_id}`

Get Model.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `model_id`        | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Model`).
- **400** — .
- **default** — .

### `PATCH /api/v1/organizations/{organization_id}/models/{model_id}`

Update Model.

A configuration change also needs `write` on the model's provider.

| Parameter         | Location | Required | Type / schema  | Constraints and default |
| ----------------- | -------- | -------- | -------------- | ----------------------- |
| `organization_id` | path     | true     | string         | —                       |
| `model_id`        | path     | true     | string         | —                       |
| `If-Match`        | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `ModelUpdate`.

Responses:

- **200** — Successful Response (`application/json: Model`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/media-understanding-defaults`

Get Media Defaults.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: MediaDefaults`).
- **400** — .
- **default** — .

### `PUT /api/v1/workspaces/{workspace_id}/media-understanding-defaults`

Replace Media Defaults.

Replaces all three kinds; each model must declare it understands its kind. Requires workspace admin.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `MediaUnderstandingSelection`.

Responses:

- **200** — Successful Response (`application/json: MediaDefaults`).
- **400** — .
- **default** — .

## other

### `GET /healthz`

Health.

Responses:

- **200** — Successful Response (`application/json: object`).

### `GET /readyz`

Ready.

Ready while started and the database holds a usable schema. Redis only speeds work up, so losing it is reported as degraded rather than taking the replica out of service.

Responses:

- **200** — Successful Response (`application/json: schema-defined value`).

## providers

### `GET /api/v1/organizations/{organization_id}/connector-providers`

List Providers.

| Parameter         | Location | Required | Type / schema  | Constraints and default            |
| ----------------- | -------- | -------- | -------------- | ---------------------------------- |
| `organization_id` | path     | true     | string         | —                                  |
| `workspace_id`    | query    | false    | string or null | —                                  |
| `limit`           | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`          | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ProviderPage`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/connector-providers`

Create Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `ProviderCreate`.

Responses:

- **201** — Successful Response (`application/json: Provider`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/connector-providers/{provider_id}`

Get Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `provider_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Provider`).
- **400** — .
- **default** — .

### `PATCH /api/v1/organizations/{organization_id}/connector-providers/{provider_id}`

Update Provider.

A `config` change must also replace or remove a stored credential: it never follows a new endpoint.

| Parameter         | Location | Required | Type / schema  | Constraints and default |
| ----------------- | -------- | -------- | -------------- | ----------------------- |
| `organization_id` | path     | true     | string         | —                       |
| `provider_id`     | path     | true     | string         | —                       |
| `If-Match`        | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `ProviderUpdate`.

Responses:

- **200** — Successful Response (`application/json: Provider`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/connector-providers/{provider_id}/test`

Test Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `provider_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ProviderTest`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/environment-providers`

List Providers.

| Parameter         | Location | Required | Type / schema  | Constraints and default            |
| ----------------- | -------- | -------- | -------------- | ---------------------------------- |
| `organization_id` | path     | true     | string         | —                                  |
| `workspace_id`    | query    | false    | string or null | —                                  |
| `limit`           | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`          | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ProviderPage`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/environment-providers`

Create Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `ProviderCreate`.

Responses:

- **201** — Successful Response (`application/json: Provider`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/environment-providers/{provider_id}`

Get Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `provider_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Provider`).
- **400** — .
- **default** — .

### `PATCH /api/v1/organizations/{organization_id}/environment-providers/{provider_id}`

Update Provider.

A `config` change must also replace or remove a stored credential: it never follows a new endpoint.

| Parameter         | Location | Required | Type / schema  | Constraints and default |
| ----------------- | -------- | -------- | -------------- | ----------------------- |
| `organization_id` | path     | true     | string         | —                       |
| `provider_id`     | path     | true     | string         | —                       |
| `If-Match`        | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `ProviderUpdate`.

Responses:

- **200** — Successful Response (`application/json: Provider`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/environment-providers/{provider_id}/test`

Test Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `provider_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ProviderTest`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/model-providers`

List Providers.

| Parameter         | Location | Required | Type / schema  | Constraints and default            |
| ----------------- | -------- | -------- | -------------- | ---------------------------------- |
| `organization_id` | path     | true     | string         | —                                  |
| `workspace_id`    | query    | false    | string or null | —                                  |
| `limit`           | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`          | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ProviderPage`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/model-providers`

Create Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `ProviderCreate`.

Responses:

- **201** — Successful Response (`application/json: Provider`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/model-providers/{provider_id}`

Get Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `provider_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Provider`).
- **400** — .
- **default** — .

### `PATCH /api/v1/organizations/{organization_id}/model-providers/{provider_id}`

Update Provider.

A `config` change must also replace or remove a stored credential: it never follows a new endpoint.

| Parameter         | Location | Required | Type / schema  | Constraints and default |
| ----------------- | -------- | -------- | -------------- | ----------------------- |
| `organization_id` | path     | true     | string         | —                       |
| `provider_id`     | path     | true     | string         | —                       |
| `If-Match`        | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `ProviderUpdate`.

Responses:

- **200** — Successful Response (`application/json: Provider`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/model-providers/{provider_id}/test`

Test Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `provider_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ProviderTest`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/web-providers`

List Providers.

| Parameter         | Location | Required | Type / schema  | Constraints and default            |
| ----------------- | -------- | -------- | -------------- | ---------------------------------- |
| `organization_id` | path     | true     | string         | —                                  |
| `workspace_id`    | query    | false    | string or null | —                                  |
| `limit`           | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`          | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ProviderPage`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/web-providers`

Create Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `ProviderCreate`.

Responses:

- **201** — Successful Response (`application/json: Provider`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/web-providers/{provider_id}`

Get Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `provider_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Provider`).
- **400** — .
- **default** — .

### `PATCH /api/v1/organizations/{organization_id}/web-providers/{provider_id}`

Update Provider.

A `config` change must also replace or remove a stored credential: it never follows a new endpoint.

| Parameter         | Location | Required | Type / schema  | Constraints and default |
| ----------------- | -------- | -------- | -------------- | ----------------------- |
| `organization_id` | path     | true     | string         | —                       |
| `provider_id`     | path     | true     | string         | —                       |
| `If-Match`        | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `ProviderUpdate`.

Responses:

- **200** — Successful Response (`application/json: Provider`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/web-providers/{provider_id}/test`

Test Provider.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `provider_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ProviderTest`).
- **400** — .
- **default** — .

### `GET /api/v1/provider-types/{kind}`

List Provider Types.

| Parameter | Location | Required | Type / schema                              | Constraints and default |
| --------- | -------- | -------- | ------------------------------------------ | ----------------------- |
| `kind`    | path     | true     | "model", "environment", "connector", "web" | —                       |

Responses:

- **200** — Successful Response (`application/json: ProviderTypePage`).
- **400** — .
- **default** — .

## runs

### `GET /api/v1/workspaces/{workspace_id}/runs/{run_id}`

Get Run.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `run_id`       | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: RunView`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/runs/{run_id}`

Update Run.

Labels only.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `run_id`       | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `RunLabels`.

Responses:

- **200** — Successful Response (`application/json: RunView`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/runs/{run_id}/attempts`

Run Attempts.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `run_id`       | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Attempts`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/runs/{run_id}/attempts/{attempt_id}/trace`

List Attempt Spans.

The attempt's spans, including its inline child runs.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `run_id`       | path     | true     | string         | —                                  |
| `attempt_id`   | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: SpanPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/runs/{run_id}/fork`

Fork Run.

A new thread in the run's session that continues from this run's committed history.

| Parameter         | Location | Required | Type / schema | Constraints and default                          |
| ----------------- | -------- | -------- | ------------- | ------------------------------------------------ |
| `workspace_id`    | path     | true     | string        | —                                                |
| `run_id`          | path     | true     | string        | —                                                |
| `Idempotency-Key` | header   | true     | string        | minLength=1; maxLength=512; `pattern="^[!-~]+$"` |

Request body: required.

- `application/json`: `Fork`.

Responses:

- **200** — The replayed submission in its current state (`application/json: Submitted`).
- **201** — Successful Response (`application/json: Submitted`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/runs/{run_id}/interrupt`

Interrupt Run.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `run_id`       | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: RunView`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/runs/{run_id}/items`

Run Items.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `run_id`       | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: RunItems`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/runs/{run_id}/lineage`

Run Lineage.

The run and its ancestors, nearest first, across fork origins.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `run_id`       | path     | true     | string         | —                       |
| `cursor`       | query    | false    | string or null | —                       |

Responses:

- **200** — Successful Response (`application/json: RunPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/runs/{run_id}/resume`

Resume Run.

Answer the waiting run's approvals and client tools; the successor run continues from them.

| Parameter         | Location | Required | Type / schema | Constraints and default                          |
| ----------------- | -------- | -------- | ------------- | ------------------------------------------------ |
| `workspace_id`    | path     | true     | string        | —                                                |
| `run_id`          | path     | true     | string        | —                                                |
| `Idempotency-Key` | header   | true     | string        | minLength=1; maxLength=512; `pattern="^[!-~]+$"` |

Request body: required.

- `application/json`: `ResumeRequest`.

Responses:

- **200** — The existing successor run in its current state (`application/json: RunView`).
- **201** — Successful Response (`application/json: RunView`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/sessions`

List Sessions.

| Parameter        | Location | Required | Type / schema      | Constraints and default            |
| ---------------- | -------- | -------- | ------------------ | ---------------------------------- |
| `workspace_id`   | path     | true     | string             | —                                  |
| `q`              | query    | false    | string or null     | maxLength=72                       |
| `agent_id`       | query    | false    | string or null     | maxLength=72                       |
| `status`         | query    | false    | array of RunStatus | maxItems=6; default=[]             |
| `trigger`        | query    | false    | array of Trigger   | maxItems=5; default=[]             |
| `updated_after`  | query    | false    | string or null     | format="date-time"                 |
| `updated_before` | query    | false    | string or null     | format="date-time"                 |
| `label`          | query    | false    | array of string    | maxItems=8; default=[]             |
| `limit`          | query    | false    | integer            | minimum=1; maximum=100; default=50 |
| `cursor`         | query    | false    | string or null     | —                                  |

Responses:

- **200** — Successful Response (`application/json: SessionPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/sessions`

Create Session.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `SessionCreate`.

Responses:

- **201** — Successful Response (`application/json: SessionView`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/sessions/{session_id}`

Get Session.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `session_id`   | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: SessionView`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/sessions/{session_id}`

Update Session.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `session_id`   | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `SessionUpdate`.

Responses:

- **200** — Successful Response (`application/json: SessionView`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/threads`

List Threads.

| Parameter      | Location | Required | Type / schema           | Constraints and default            |
| -------------- | -------- | -------- | ----------------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string                  | —                                  |
| `session_id`   | query    | false    | string or null          | —                                  |
| `label`        | query    | false    | array of string or null | —                                  |
| `limit`        | query    | false    | integer                 | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null          | —                                  |

Responses:

- **200** — Successful Response (`application/json: ThreadPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/threads`

Create Thread.

Create a thread (and its session unless one is named) with its first message.

| Parameter         | Location | Required | Type / schema | Constraints and default                          |
| ----------------- | -------- | -------- | ------------- | ------------------------------------------------ |
| `workspace_id`    | path     | true     | string        | —                                                |
| `Idempotency-Key` | header   | true     | string        | minLength=1; maxLength=512; `pattern="^[!-~]+$"` |

Request body: required.

- `application/json`: `NewThread`.

Responses:

- **200** — The replayed submission in its current state (`application/json: Submitted`).
- **201** — Successful Response (`application/json: Submitted`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/threads/{thread_id}`

Get Thread.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `thread_id`    | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ThreadView`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/threads/{thread_id}`

Update Thread.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `thread_id`    | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `ThreadUpdate`.

Responses:

- **200** — Successful Response (`application/json: ThreadView`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/threads/{thread_id}/archive`

Archive Thread.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `thread_id`    | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: ThreadView`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox`

List Inbox.

In inbox order.

| Parameter      | Location | Required | Type / schema                | Constraints and default            |
| -------------- | -------- | -------- | ---------------------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string                       | —                                  |
| `thread_id`    | path     | true     | string                       | —                                  |
| `status`       | query    | false    | array of EntryStatus or null | —                                  |
| `limit`        | query    | false    | integer                      | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null               | —                                  |

Responses:

- **200** — Successful Response (`application/json: EntryPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox`

Submit Message.

Append a message; it starts a run at once when the thread can accept it, or steers the active run.

| Parameter         | Location | Required | Type / schema | Constraints and default                          |
| ----------------- | -------- | -------- | ------------- | ------------------------------------------------ |
| `workspace_id`    | path     | true     | string        | —                                                |
| `thread_id`       | path     | true     | string        | —                                                |
| `Idempotency-Key` | header   | true     | string        | minLength=1; maxLength=512; `pattern="^[!-~]+$"` |

Request body: required.

- `application/json`: `Message`.

Responses:

- **200** — The replayed submission in its current state (`application/json: Submitted`).
- **201** — Successful Response (`application/json: Submitted`).
- **400** — .
- **default** — .

### `PUT /api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox/order`

Reorder Inbox.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `thread_id`    | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `InboxOrder`.

Responses:

- **200** — Successful Response (`application/json: ThreadView`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox/{entry_id}`

Withdraw Entry.

Withdraw a pending entry; its tombstone keeps the request key.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `thread_id`    | path     | true     | string         | —                       |
| `entry_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Submitted`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox/{entry_id}`

Get Entry.

One entry and its disposition; edits name the thread's ETag.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `thread_id`    | path     | true     | string        | —                       |
| `entry_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: EntryView`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox/{entry_id}`

Edit Entry.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `thread_id`    | path     | true     | string         | —                       |
| `entry_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `EntryUpdate`.

Responses:

- **200** — Successful Response (`application/json: Submitted`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/threads/{thread_id}/runs`

List Thread Runs.

Newest first.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `thread_id`    | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: RunPage`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/threads/{thread_id}/stream`

Thread Stream.

Live output of the thread's runs over SSE: `delta` and `boundary` frames with `changed`, `reset`, `gap`.

| Parameter       | Location | Required | Type / schema  | Constraints and default           |
| --------------- | -------- | -------- | -------------- | --------------------------------- |
| `workspace_id`  | path     | true     | string         | —                                 |
| `thread_id`     | path     | true     | string         | —                                 |
| `Last-Event-ID` | header   | false    | string or null | `pattern="^\\d{1,20}-\\d{1,20}$"` |

Responses:

- **200** — Successful Response (`text/event-stream: schema-defined value`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/trace-backend`

Get Trace Backend.

The backend trace queries read, and how far back they find a trace.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: TraceBackend`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/traces`

List Traces.

Trace root spans, one per attempt. A cursor keeps the window of the first page.

| Parameter        | Location | Required | Type / schema           | Constraints and default                                        |
| ---------------- | -------- | -------- | ----------------------- | -------------------------------------------------------------- |
| `workspace_id`   | path     | true     | string                  | —                                                              |
| `session_id`     | query    | false    | string or null          | maxLength=41; `pattern="^[a-z][a-z0-9]{1,7}_[0-9a-f]{20,32}$"` |
| `thread_id`      | query    | false    | string or null          | maxLength=41; `pattern="^[a-z][a-z0-9]{1,7}_[0-9a-f]{20,32}$"` |
| `run_id`         | query    | false    | string or null          | maxLength=41; `pattern="^[a-z][a-z0-9]{1,7}_[0-9a-f]{20,32}$"` |
| `attribute`      | query    | false    | array of string or null | —                                                              |
| `started_after`  | query    | false    | string or null          | format="date-time"                                             |
| `started_before` | query    | false    | string or null          | format="date-time"                                             |
| `limit`          | query    | false    | integer                 | minimum=1; maximum=100; default=50                             |
| `cursor`         | query    | false    | string or null          | —                                                              |

Responses:

- **200** — Successful Response (`application/json: SpanPage`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/traces/{trace_id}`

Get Trace.

The trace's root span.

| Parameter      | Location | Required | Type / schema | Constraints and default    |
| -------------- | -------- | -------- | ------------- | -------------------------- |
| `workspace_id` | path     | true     | string        | —                          |
| `trace_id`     | path     | true     | string        | `pattern="^[0-9a-f]{32}$"` |

Responses:

- **200** — Successful Response (`application/json: Span`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/traces/{trace_id}/spans`

List Trace Spans.

The trace's spans.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `trace_id`     | path     | true     | string         | `pattern="^[0-9a-f]{32}$"`         |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: SpanPage`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/usage`

Summarize Usage.

| Parameter         | Location | Required | Type / schema  | Constraints and default                                        |
| ----------------- | -------- | -------- | -------------- | -------------------------------------------------------------- |
| `workspace_id`    | path     | true     | string         | —                                                              |
| `run_id`          | query    | false    | string or null | maxLength=41; `pattern="^[a-z][a-z0-9]{1,7}_[0-9a-f]{20,32}$"` |
| `thread_id`       | query    | false    | string or null | maxLength=41; `pattern="^[a-z][a-z0-9]{1,7}_[0-9a-f]{20,32}$"` |
| `session_id`      | query    | false    | string or null | maxLength=41; `pattern="^[a-z][a-z0-9]{1,7}_[0-9a-f]{20,32}$"` |
| `ingested_after`  | query    | false    | string or null | format="date-time"                                             |
| `ingested_before` | query    | false    | string or null | format="date-time"                                             |

Responses:

- **200** — Successful Response (`application/json: UsageSummary`).
- **400** — .
- **default** — .

## secrets

### `GET /api/v1/workspaces/{workspace_id}/secrets`

List Secrets.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: SecretPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/secrets`

Create Secret.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `SecretCreate`.

Responses:

- **201** — Successful Response (`application/json: Secret`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/secrets/{secret_id}`

Delete Secret.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `secret_id`    | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/secrets/{secret_id}`

Get Secret.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `secret_id`    | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Secret`).
- **400** — .
- **default** — .

### `PUT /api/v1/workspaces/{workspace_id}/secrets/{secret_id}`

Replace Secret.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `secret_id`    | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `SecretUpdate`.

Responses:

- **200** — Successful Response (`application/json: Secret`).
- **400** — .
- **default** — .

## skills

### `GET /api/v1/workspaces/{workspace_id}/skills`

List Skills.

Skills of the workspace. `q` matches the key, name or description, ignoring case; `source` the kind of source the default revision was read from; `archived` keeps only archived skills, or only open ones.

| Parameter      | Location | Required | Type / schema              | Constraints and default            |
| -------------- | -------- | -------- | -------------------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string                     | —                                  |
| `label`        | query    | false    | array of string or null    | —                                  |
| `q`            | query    | false    | string or null             | minLength=1; maxLength=256         |
| `source`       | query    | false    | "upload", "github" or null | —                                  |
| `archived`     | query    | false    | boolean or null            | —                                  |
| `limit`        | query    | false    | integer                    | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null             | —                                  |

Responses:

- **200** — Successful Response (`application/json: SkillPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/skills`

Create Skill.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `SkillCreate`.

Responses:

- **201** — Successful Response (`application/json: Skill`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/skills/validate`

Validate Package.

The manifest the package would give a new skill or revision, checked as creation checks it; nothing is stored.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `SkillValidate`.

Responses:

- **200** — Successful Response (`application/json: SkillManifest`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/skills/{skill_id}`

Get Skill.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `skill_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Skill`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/skills/{skill_id}`

Update Skill.

Name, description and labels; an archived skill changes only by unarchiving.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `skill_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `SkillUpdate`.

Responses:

- **200** — Successful Response (`application/json: Skill`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/skills/{skill_id}/archive`

Archive Skill.

Archived skills keep their revisions readable and pinned; they refuse new revisions and new pins.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `skill_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Skill`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/skills/{skill_id}/revisions`

List Revisions.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `skill_id`     | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: SkillRevisionPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/skills/{skill_id}/revisions`

Create Revision.

A package whose manifest equals the default revision's creates nothing and returns that revision.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `skill_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `SkillRevisionCreate`.

Responses:

- **201** — Successful Response (`application/json: SkillRevision`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/skills/{skill_id}/revisions/{revision_id}`

Get Revision.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `skill_id`     | path     | true     | string        | —                       |
| `revision_id`  | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: SkillRevision`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/skills/{skill_id}/revisions/{revision_id}/content`

Read Archive.

The revision's package as a zip archive.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `skill_id`     | path     | true     | string        | —                       |
| `revision_id`  | path     | true     | string        | —                       |

Responses:

- **200** — The revision's package as a zip archive (`application/zip: string`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/skills/{skill_id}/revisions/{revision_id}/files/{path}`

Read File.

One package file, by the path the revision's manifest lists.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `skill_id`     | path     | true     | string        | —                       |
| `revision_id`  | path     | true     | string        | —                       |
| `path`         | path     | true     | string        | —                       |

Responses:

- **200** — One file of the revision's package (`application/octet-stream: string`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/skills/{skill_id}/revisions/{revision_id}/set-default`

Set Default Revision.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `skill_id`     | path     | true     | string         | —                       |
| `revision_id`  | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Skill`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/skills/{skill_id}/unarchive`

Unarchive Skill.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `skill_id`     | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Skill`).
- **400** — .
- **default** — .

## subscriptions

### `GET /api/v1/workspaces/{workspace_id}/subscriptions`

List Subscriptions.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: SubscriptionPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/subscriptions`

Create Subscription.

The response is the only time the signing secret is returned.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `SubscriptionCreate`.

Responses:

- **201** — Successful Response (`application/json: CreatedSubscription`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/subscriptions/{subscription_id}`

Delete Subscription.

| Parameter         | Location | Required | Type / schema  | Constraints and default |
| ----------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id`    | path     | true     | string         | —                       |
| `subscription_id` | path     | true     | string         | —                       |
| `If-Match`        | header   | false    | string or null | maxLength=512           |

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/subscriptions/{subscription_id}`

Get Subscription.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id`    | path     | true     | string        | —                       |
| `subscription_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Subscription`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/subscriptions/{subscription_id}`

Update Subscription.

| Parameter         | Location | Required | Type / schema  | Constraints and default |
| ----------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id`    | path     | true     | string         | —                       |
| `subscription_id` | path     | true     | string         | —                       |
| `If-Match`        | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `SubscriptionUpdate`.

Responses:

- **200** — Successful Response (`application/json: Subscription`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/subscriptions/{subscription_id}/deliveries`

List Deliveries.

| Parameter         | Location | Required | Type / schema  | Constraints and default            |
| ----------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id`    | path     | true     | string         | —                                  |
| `subscription_id` | path     | true     | string         | —                                  |
| `limit`           | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`          | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: DeliveryPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/subscriptions/{subscription_id}/deliveries/{delivery_id}/redeliver`

Redeliver.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id`    | path     | true     | string        | —                       |
| `subscription_id` | path     | true     | string        | —                       |
| `delivery_id`     | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: WebhookDelivery`).
- **400** — .
- **default** — .

## tenancy

### `GET /api/v1/organizations`

List Organizations.

| Parameter | Location | Required | Type / schema  | Constraints and default            |
| --------- | -------- | -------- | -------------- | ---------------------------------- |
| `limit`   | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`  | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: OrganizationPage`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}`

Get Organization.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Organization`).
- **400** — .
- **default** — .

### `PATCH /api/v1/organizations/{organization_id}`

Update Organization.

| Parameter         | Location | Required | Type / schema  | Constraints and default |
| ----------------- | -------- | -------- | -------------- | ----------------------- |
| `organization_id` | path     | true     | string         | —                       |
| `If-Match`        | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `OrganizationUpdate`.

Responses:

- **200** — Successful Response (`application/json: Organization`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/audit-events`

List Organization Audit Events.

| Parameter         | Location | Required | Type / schema  | Constraints and default            |
| ----------------- | -------- | -------- | -------------- | ---------------------------------- |
| `organization_id` | path     | true     | string         | —                                  |
| `limit`           | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`          | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: AuditPage`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/grants`

List Organization Grants.

| Parameter         | Location | Required | Type / schema  | Constraints and default            |
| ----------------- | -------- | -------- | -------------- | ---------------------------------- |
| `organization_id` | path     | true     | string         | —                                  |
| `limit`           | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`          | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: GrantPage`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/grants`

Create Organization Grant.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `GrantCreate`.

Responses:

- **201** — Successful Response (`application/json: GrantView`).
- **400** — .
- **default** — .

### `DELETE /api/v1/organizations/{organization_id}/grants/{grant_id}`

Delete Organization Grant.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `grant_id`        | path     | true     | string        | —                       |

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `PATCH /api/v1/organizations/{organization_id}/grants/{grant_id}`

Change Organization Grant.

The grant is replaced: the result carries its new ID.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |
| `grant_id`        | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `GrantUpdate`.

Responses:

- **200** — Successful Response (`application/json: GrantView`).
- **400** — .
- **default** — .

### `DELETE /api/v1/organizations/{organization_id}/icon`

Delete Organization Icon.

| Parameter         | Location | Required | Type / schema  | Constraints and default |
| ----------------- | -------- | -------- | -------------- | ----------------------- |
| `organization_id` | path     | true     | string         | —                       |
| `If-Match`        | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Organization`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/icon`

Get Organization Icon.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |

Responses:

- **200** — The image (`image/jpeg: string; image/png: string; image/webp: string`).
- **400** — .
- **default** — .

### `PUT /api/v1/organizations/{organization_id}/icon`

Put Organization Icon.

| Parameter         | Location | Required | Type / schema  | Constraints and default |
| ----------------- | -------- | -------- | -------------- | ----------------------- |
| `organization_id` | path     | true     | string         | —                       |
| `If-Match`        | header   | false    | string or null | maxLength=512           |

Request body: required.

- `image/jpeg`: `string`.
- `image/png`: `string`.
- `image/webp`: `string`.

Responses:

- **200** — Successful Response (`application/json: Organization`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/invitations`

List Organization Invitations.

| Parameter         | Location | Required | Type / schema  | Constraints and default            |
| ----------------- | -------- | -------- | -------------- | ---------------------------------- |
| `organization_id` | path     | true     | string         | —                                  |
| `limit`           | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`          | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: InvitationPage`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/invitations`

Create Organization Invitation.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `InvitationCreate`.

Responses:

- **201** — Successful Response (`application/json: InvitationReceipt`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/invitations/{invitation_id}/resend`

Resend Organization Invitation.

| Parameter         | Location | Required | Type / schema  | Constraints and default |
| ----------------- | -------- | -------- | -------------- | ----------------------- |
| `organization_id` | path     | true     | string         | —                       |
| `invitation_id`   | path     | true     | string         | —                       |
| `If-Match`        | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: InvitationReceipt`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/invitations/{invitation_id}/revoke`

Revoke Organization Invitation.

| Parameter         | Location | Required | Type / schema  | Constraints and default |
| ----------------- | -------- | -------- | -------------- | ----------------------- |
| `organization_id` | path     | true     | string         | —                       |
| `invitation_id`   | path     | true     | string         | —                       |
| `If-Match`        | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Invitation`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/members`

List Members.

| Parameter         | Location | Required | Type / schema                     | Constraints and default            |
| ----------------- | -------- | -------- | --------------------------------- | ---------------------------------- |
| `organization_id` | path     | true     | string                            | —                                  |
| `kind`            | query    | false    | "user", "service_account" or null | —                                  |
| `limit`           | query    | false    | integer                           | minimum=1; maximum=100; default=50 |
| `cursor`          | query    | false    | string or null                    | —                                  |

Responses:

- **200** — Successful Response (`application/json: MemberPage`).
- **400** — .
- **default** — .

### `GET /api/v1/organizations/{organization_id}/workspaces`

List Organization Workspaces.

| Parameter         | Location | Required | Type / schema  | Constraints and default            |
| ----------------- | -------- | -------- | -------------- | ---------------------------------- |
| `organization_id` | path     | true     | string         | —                                  |
| `limit`           | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`          | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: WorkspacePage`).
- **400** — .
- **default** — .

### `POST /api/v1/organizations/{organization_id}/workspaces`

Create Workspace.

| Parameter         | Location | Required | Type / schema | Constraints and default |
| ----------------- | -------- | -------- | ------------- | ----------------------- |
| `organization_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `WorkspaceCreate`.

Responses:

- **201** — Successful Response (`application/json: Workspace`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces`

List Workspaces.

| Parameter | Location | Required | Type / schema  | Constraints and default            |
| --------- | -------- | -------- | -------------- | ---------------------------------- |
| `limit`   | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`  | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: WorkspacePage`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}`

Get Workspace.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: Workspace`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}`

Update Workspace.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `WorkspaceUpdate`.

Responses:

- **200** — Successful Response (`application/json: Workspace`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/archive`

Archive Workspace.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Workspace`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/audit-events`

List Workspace Audit Events.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: AuditPage`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/grants`

List Workspace Grants.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: GrantPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/grants`

Create Workspace Grant.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `GrantCreate`.

Responses:

- **201** — Successful Response (`application/json: GrantView`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/grants/{grant_id}`

Delete Workspace Grant.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `grant_id`     | path     | true     | string        | —                       |

Responses:

- **204** — Successful Response.
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/grants/{grant_id}`

Change Workspace Grant.

The grant is replaced: the result carries its new ID.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `grant_id`     | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `GrantUpdate`.

Responses:

- **200** — Successful Response (`application/json: GrantView`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/icon`

Delete Workspace Icon.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Workspace`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/icon`

Get Workspace Icon.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Responses:

- **200** — The image (`image/jpeg: string; image/png: string; image/webp: string`).
- **400** — .
- **default** — .

### `PUT /api/v1/workspaces/{workspace_id}/icon`

Put Workspace Icon.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `image/jpeg`: `string`.
- `image/png`: `string`.
- `image/webp`: `string`.

Responses:

- **200** — Successful Response (`application/json: Workspace`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/invitations`

List Workspace Invitations.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: InvitationPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/invitations`

Create Workspace Invitation.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `InvitationCreate`.

Responses:

- **201** — Successful Response (`application/json: InvitationReceipt`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/resend`

Resend Workspace Invitation.

| Parameter       | Location | Required | Type / schema  | Constraints and default |
| --------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id`  | path     | true     | string         | —                       |
| `invitation_id` | path     | true     | string         | —                       |
| `If-Match`      | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: InvitationReceipt`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/revoke`

Revoke Workspace Invitation.

| Parameter       | Location | Required | Type / schema  | Constraints and default |
| --------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id`  | path     | true     | string         | —                       |
| `invitation_id` | path     | true     | string         | —                       |
| `If-Match`      | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: Invitation`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/keys`

List Workspace Keys.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `principal_id` | query    | false    | string or null | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ApiKeyPage`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/keys/{key_id}`

Revoke Workspace Key.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `key_id`       | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: ApiKey`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/service-accounts`

List Service Accounts.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ServiceAccountPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/service-accounts`

Create Service Account.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `ServiceAccountCreate`.

Responses:

- **201** — Successful Response (`application/json: ServiceAccount`).
- **400** — .
- **default** — .

### `DELETE /api/v1/workspaces/{workspace_id}/service-accounts/{account_id}`

Delete Service Account.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `account_id`   | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Responses:

- **200** — Successful Response (`application/json: ServiceAccount`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/service-accounts/{account_id}`

Get Service Account.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `account_id`   | path     | true     | string        | —                       |

Responses:

- **200** — Successful Response (`application/json: ServiceAccount`).
- **400** — .
- **default** — .

### `PATCH /api/v1/workspaces/{workspace_id}/service-accounts/{account_id}`

Update Service Account.

| Parameter      | Location | Required | Type / schema  | Constraints and default |
| -------------- | -------- | -------- | -------------- | ----------------------- |
| `workspace_id` | path     | true     | string         | —                       |
| `account_id`   | path     | true     | string         | —                       |
| `If-Match`     | header   | false    | string or null | maxLength=512           |

Request body: required.

- `application/json`: `ServiceAccountUpdate`.

Responses:

- **200** — Successful Response (`application/json: ServiceAccount`).
- **400** — .
- **default** — .

### `GET /api/v1/workspaces/{workspace_id}/service-accounts/{account_id}/keys`

List Service Account Keys.

| Parameter      | Location | Required | Type / schema  | Constraints and default            |
| -------------- | -------- | -------- | -------------- | ---------------------------------- |
| `workspace_id` | path     | true     | string         | —                                  |
| `account_id`   | path     | true     | string         | —                                  |
| `limit`        | query    | false    | integer        | minimum=1; maximum=100; default=50 |
| `cursor`       | query    | false    | string or null | —                                  |

Responses:

- **200** — Successful Response (`application/json: ApiKeyPage`).
- **400** — .
- **default** — .

### `POST /api/v1/workspaces/{workspace_id}/service-accounts/{account_id}/keys`

Create Service Account Key.

Needs a login session: an API key never issues keys, so a leaked key cannot outlive its revocation.

| Parameter      | Location | Required | Type / schema | Constraints and default |
| -------------- | -------- | -------- | ------------- | ----------------------- |
| `workspace_id` | path     | true     | string        | —                       |
| `account_id`   | path     | true     | string        | —                       |

Request body: required.

- `application/json`: `KeyCreate`.

Responses:

- **201** — Successful Response (`application/json: IssuedKey`).
- **400** — .
- **default** — .
