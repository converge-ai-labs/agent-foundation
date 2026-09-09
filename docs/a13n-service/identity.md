# Identity and access

The default OSS service authenticates local users with browser sessions and applications with Workspace-bound API keys. It initializes one Organization and a `default` Workspace. Each request uses current roles and credential status.

## Initialize the administrator

Set `A13N_SERVICE_IAM_INITIAL_ADMIN_EMAIL` before starting a control or all-in-one process against an empty database. Set `A13N_SERVICE_IAM_PUBLIC_ORIGIN` to the browser-facing origin, such as `https://agents.example.com`. HTTP is accepted only on loopback for local development. Production sessions require HTTPS.

With SMTP configured, the administrator receives an invitation email. Otherwise, the service prints a single-use initialization link once to its protected startup logs. Open the link and choose a password containing 15-128 printable ASCII characters without spaces. Accepting a manually delivered link does not mark the email as verified.

Restarting the service does not issue another link. To replace a lost or expired pending link, run this command using the deployment's database and IAM configuration from a protected terminal:

```sh
a13n-service iam reissue-bootstrap
```

The command invalidates the old link. It cannot reopen completed initialization. Worker and connectivity roles do not initialize identity.

## Browser sessions

Open `/api/v1/auth/login` to sign in. Applications may POST `{ "email": "...", "password": "..." }` to that URL. Successful login and invitation acceptance set an `HttpOnly`, `Secure`, `SameSite=Lax` cookie and return safe User/session metadata plus `csrf_token`. Session expiry defaults to seven days and is configured by `A13N_SERVICE_IAM_SESSION_DAYS`.

Browser mutations require an `Origin` matching the configured public origin. Authenticated mutations also require `X-A13N-CSRF-Token`; retrieve it from the login response or `GET /api/v1/auth/csrf`. Send cookies with same-origin requests. Never send a bearer credential together with a session cookie.

A browser session defaults to Organization scope. `X-A13N-Workspace-ID` selects a narrower Workspace scope when working with Workspace-owned configuration. Workspace routes check their target against that selection. Use Organization scope for Organization administration.

- `GET /api/v1/users/me` reads the current User.
- `GET /api/v1/users/me/auth-sessions` lists sessions.
- `DELETE /api/v1/users/me/auth-sessions/{id}` revokes one session.
- `POST /api/v1/auth/logout` revokes the current session and clears its cookie.
- `POST /api/v1/users/me/password` accepts `current_password` and `password`; it preserves the current session and revokes the others.

## Invite members

An Organization Admin can POST to `/api/v1/organizations/{organization_id}/invitations` with an email and grants:

```json
{
  "email": "teammate@example.com",
  "grants": [
    { "resource_type": "workspace", "resource_id": "ws_REPLACE", "role_key": "runner" }
  ]
}
```

A Workspace Admin can POST `{ "email": "...", "role": "runner" }` to `/api/v1/workspaces/{workspace_id}/invitations`. Acceptance also establishes Organization membership. Existing users must supply their existing password; invitations never overwrite that password or demote existing roles.

Delivery returns `sent`, `failed`, or `manual`. Manual delivery includes `invitation_url` once. SMTP failure retains the invitation; fix delivery and resend. Configure `A13N_SERVICE_IAM_SMTP_HOST`, `SMTP_PORT`, `SMTP_TLS` (`starttls` or `tls`), `SMTP_SENDER`, and optional paired `SMTP_USERNAME`/`SMTP_PASSWORD`, each with the `A13N_SERVICE_IAM_` prefix. Invitation expiry defaults to seven days and is configured by `A13N_SERVICE_IAM_INVITATION_DAYS`.

Resend and revoke use `POST /api/v1/invitations/{id}/resend` or `/revoke` with `{ "expected_version": 1 }`. Read the current version from the invitation collection. Resend invalidates the previous token. Acceptance rechecks the inviter's current authority and every target grant.

Manage existing membership through the Organization or Workspace `role-bindings` collections. Read `/api/v1/role-bindings/{id}` for its ETag; PATCH its `role`, or DELETE it, with `If-Match`. Removing the final active Organization Admin is rejected.

## Browser OAuth callbacks

Composio returns to the Console page `/connector-setup/callback`. The page removes the upstream session from the address bar and posts it with the exact attempt and browser proof to `/api/v1/connector-setup/complete`, using the normal Origin, session cookie, and CSRF token. Follow the [Composio setup guide](external-tools.md#connector-providers-composio-and-openconnector) to configure the verifier and shared origin.

The MCP OAuth GET callback still requires the same CSRF proof as mutations when using a local browser session. A provider redirect alone cannot complete that route. Its interactive completion flow remains tracked in [Issue #203](https://github.com/converge-ai-labs/agent-foundation/issues/203).

## Create application keys

POST `{ "name": "my-application" }` to `/api/v1/workspaces/{workspace_id}/personal-api-keys` from an authenticated user session. The response contains safe `key` metadata and a `bearer` value shown once. Store the bearer securely and send it as `Authorization: Bearer <value>` on application requests without a browser cookie.

The key is restricted to its `boundary_type: "workspace"` and `boundary_id`. It inherits its owner's current permissions. The default expiry is 90 days; supply an absolute `expires_at` timestamp or explicit `null` for no expiry. Read metadata at `/api/v1/api-keys/{id}` and revoke with `POST /api/v1/api-keys/{id}/revoke`.

There is no key rotation endpoint. Create a replacement, move callers, and revoke the old key. Revocation is permanent; removing access permanently revokes affected personal keys.

Workspace Admins can create a Service Account at `/api/v1/workspaces/{workspace_id}/service-accounts` with `name` and a `role` of `viewer`, `runner`, or `builder`. Create its keys at `/api/v1/service-accounts/{id}/api-keys`. Use these credentials for unattended application or ingress integration. A Service Account cannot administer identity or obtain a browser session.

Service Account updates require `expected_version`, `name`, `description`, `status`, and `role`. Disablement blocks existing keys; re-enablement restores otherwise valid keys. Deletion requires `expected_version` and permanently revokes all keys.

Collection endpoints accept `limit` (1-100) and `cursor`, and return `items` and `next_cursor`. Cursors are bound to the requesting principal and collection scope.

Embedded distributions that supply `Components.request_authenticator` own authentication and initialization; the built-in local identity runtime is selected only when that override is absent.
