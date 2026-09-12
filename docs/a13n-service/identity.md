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

Open [Console](console.md) at its `/login` page to sign in. `/api/v1/auth/login` is a JSON endpoint, not a browser page. Applications may POST `{ "email": "...", "password": "..." }` to that URL. Successful login and invitation acceptance set an `HttpOnly`, `Secure`, `SameSite=Lax` cookie and return safe User/session metadata plus `csrf_token`. Session expiry defaults to seven days and is configured by `A13N_SERVICE_IAM_SESSION_DAYS`.

Browser mutations require an `Origin` matching the configured public origin. Authenticated mutations also require `X-A13N-CSRF-Token`; retrieve it from the login response or `GET /api/v1/auth/csrf`. Send cookies with same-origin requests. Never send a bearer credential together with a session cookie.

A browser session defaults to Organization scope. `X-A13N-Workspace-ID` selects a narrower Workspace scope when working with Workspace-owned configuration. Workspace routes check their target against that selection. Use Organization scope for Organization administration.

- `GET /api/v1/users/me` reads the current User.
- `GET /api/v1/users/me/auth-sessions` lists sessions.
- `DELETE /api/v1/users/me/auth-sessions/{id}` revokes one session.
- `POST /api/v1/auth/logout` revokes the current session and clears its cookie.
- `POST /api/v1/users/me/password` accepts `current_password` and `password`; it preserves the current session and revokes the others.

## Invite members

An Organization Admin can POST to `/api/v1/organizations/{organization}/invitations` with an email and grants:

```json
{
  "email": "teammate@example.com",
  "grants": [
    { "resource_type": "workspace", "resource_id": "ws_REPLACE", "role_key": "runner" }
  ]
}
```

A Workspace Admin can POST `{ "email": "...", "role": "runner" }` to `/api/v1/workspaces/{workspace}/invitations`. Acceptance also establishes Organization membership. Existing users must supply their existing password; invitations never overwrite that password or demote existing roles.

Delivery returns `sent`, `failed`, or `manual`. Manual delivery includes `invitation_url` once. SMTP failure retains the invitation; fix delivery and resend. Configure `A13N_SERVICE_IAM_SMTP_HOST`, `SMTP_PORT`, `SMTP_TLS` (`starttls` or `tls`), `SMTP_SENDER`, and optional paired `SMTP_USERNAME`/`SMTP_PASSWORD`, each with the `A13N_SERVICE_IAM_` prefix. Invitation expiry defaults to seven days and is configured by `A13N_SERVICE_IAM_INVITATION_DAYS`.

Resend and revoke use `POST /api/v1/invitations/{id}/resend` or `/revoke` with `{ "expected_version": 1 }`. Read the current version from the invitation collection. Resend invalidates the previous token. Acceptance rechecks the inviter's current authority and every target grant.

Manage existing membership through the Organization or Workspace `role-bindings` collections. Read `/api/v1/role-bindings/{id}` for its ETag; PATCH its `role`, or DELETE it, with `If-Match`. Removing the final active Organization Admin is rejected.

## Browser OAuth callbacks

Browser authorization uses the same Connection Authorization API for Console users and application Service Accounts. The initiating principal supplies an exact registered HTTPS return URL, opaque application state, and a SHA-256 completion challenge. The Service browser bridge binds the provider round trip to that tab; the application receives a short-lived receipt and completes it with the original principal and verifier. An application can keep its own user identities and Connection mapping without creating Console users.

Composio returns through `/connection-authorizations/browser`; MCP providers return to `/api/v1/oauth/mcp/callback/{issuer_key}` before entering the same bridge. Console receives the application handoff at `/connections/callback`, removes query material immediately, and completes with the signed-in User and CSRF token. Keep authorization in the same tab. Service omits query strings from access logs; configure ingress access logs to do the same. See [external tool authorization](external-tools.md#application-owned-users) for the API flow and return URL configuration.

## Create application keys

POST `{ "name": "my-application" }` to `/api/v1/workspaces/{workspace}/personal-api-keys` from an authenticated user session. The response contains safe `key` metadata and a `bearer` value shown once. Store the bearer securely and send it as `Authorization: Bearer <value>` on application requests without a browser cookie.

The key is restricted to its `boundary_type: "workspace"` and `boundary_id`. It inherits its owner's current permissions. The default expiry is 90 days; supply an absolute `expires_at` timestamp or explicit `null` for no expiry. Read metadata at `/api/v1/api-keys/{id}` and revoke with `POST /api/v1/api-keys/{id}/revoke`.

There is no key rotation endpoint. Create a replacement, move callers, and revoke the old key. Revocation is permanent; removing access permanently revokes affected personal keys.

Workspace Admins can create a Service Account at `/api/v1/workspaces/{workspace}/service-accounts` with `name` and a `role` of `viewer`, `runner`, or `builder`. Create its keys at `/api/v1/service-accounts/{id}/api-keys`. Use these credentials for unattended application or ingress integration. A Service Account cannot administer identity or obtain a browser session.

Service Account updates require `expected_version`, `name`, `description`, `status`, and `role`. Disablement blocks existing keys; re-enablement restores otherwise valid keys. Deletion requires `expected_version` and permanently revokes all keys.

Collection endpoints accept `limit` (1-100) and `cursor`, and return `items` and `next_cursor`. Cursors are bound to the requesting principal and collection scope.

Embedded distributions that supply `Components.request_authenticator` own authentication and initialization; the built-in local identity runtime is selected only when that override is absent.

## Resource keys and browser links

Organizations, Workspaces, and Agents have an immutable `id`, a display `name`, and an editable `key` for readable addresses. For example, an Agent can appear at `/workspace/research/agents/code-reviewer` in Console. Display names can repeat. A generated key uses the readable ASCII parts of the name; a collision adds four random hexadecimal characters. You can choose an explicit key when creating a resource or edit it later in its settings.

Keys use lowercase letters, numbers, and single hyphens, up to 64 characters. Renaming the display label preserves the key. Changing the key preserves the resource and its history but invalidates its previous address; there are no redirects or aliases.

Native API paths accept either IDs or current keys: `/api/v1/workspaces/research/agents/code-reviewer` addresses the same Agent as the equivalent path with its Workspace and Agent IDs. The credential still determines the allowed Organization or Workspace. API Key SDK callers can use `await client.workspaceHttp()` to bind that Workspace automatically, then call `/agents/{agent}` without supplying the Workspace again.
