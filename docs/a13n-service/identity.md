---
title: Identity and access
description: Manage multi-tenant organizations, workspaces, member invitations, service accounts, OIDC SSO, RBAC roles (runner, builder, admin), and API keys.
---

Service authenticates **principals** (human user accounts and automated service accounts) via browser login sessions or workspace-scoped API keys, and authorizes every request against the principal's current **grants** on the organization and workspace.

## Organizations and workspaces

- **Organization:** Top-level tenant boundary created at [bootstrap](get-started.md#register-your-administrator-account), grouping members and workspaces.
- **Workspace:** Owns agents, threads, environments, providers, connections, and API keys. Create workspaces in **Organization settings → Workspaces** (`POST /api/v1/organizations/{organization_id}/workspaces`), and archive retired ones via `POST /api/v1/workspaces/{workspace_id}/archive`. Login sessions select a workspace via `X-Workspace-ID`; API keys are already bound to one workspace.

## Roles

| Role      | Effective verbs                 | Typical assignment                                   |
| --------- | ------------------------------- | ---------------------------------------------------- |
| `viewer`  | `read`                          | Inspect agents, conversations, and results           |
| `runner`  | `read`, `run`                   | Execute agents, manage threads, and use environments |
| `builder` | `read`, `run`, `write`          | Create and edit agents, templates, and providers     |
| `admin`   | `read`, `run`, `write`, `admin` | Manage members, keys, workspace settings, and audit  |

Organization grants apply across every workspace in the organization and combine additively with workspace grants.

## Invitations, members, and grants

### Invitations

Invite new users in **Invitations** (`POST /api/v1/workspaces/{workspace_id}/invitations` or the organization equivalent with `{email, role}`, requires a login session). Service emails the link when [SMTP is configured](configuration.md#identity-and-mail) or returns a one-time `invitation_url`.

### Members and grants

Manage existing principals' access via `GET /api/v1/organizations/{organization_id}/members` and `POST` / `PATCH` / `DELETE` on `/api/v1/workspaces/{workspace_id}/grants` (or `/api/v1/organizations/{organization_id}/grants`) with `{principal_id, role}`.

## API keys

Issue personal keys in **API keys** (`POST /api/v1/users/me/keys` with `{name, workspace_id, expires_at?}`, login session required). Copy the returned `secret` once and send it as `Authorization: Bearer $A13N_API_KEY`:

```sh
curl "$A13N_URL/api/v1/workspaces/$WORKSPACE_ID" -H "Authorization: Bearer $A13N_API_KEY"
```

Revoke keys via `DELETE /api/v1/users/me/keys/{key_id}` (or `/api/v1/workspaces/{workspace_id}/keys` as workspace admin) with `If-Match`.

## Service accounts

Create dedicated per-workspace service accounts for applications in **Workspace settings → Service accounts** (`POST /api/v1/workspaces/{workspace_id}/service-accounts` with `{"name": "Support app", "role": "runner"}`), then issue keys at `POST …/service-accounts/{account_id}/keys`. Pause with `PATCH {"status": "disabled"}` or retire with `DELETE` (with `If-Match`).

## Sign in, sessions, and OIDC SSO

- **Login sessions:** Cookie-authenticated browser requests (`/api/v1/auth/login`, `/session`, `/logout`) require `X-CSRF-Token` on state-changing calls. Users manage active sessions, passwords, email, and self-disablement (`POST /api/v1/users/me/disable`) under **Personal settings**, and organization admins can issue password-reset links from **Members**.
- **OIDC SSO (`[auth.oidc]` / `A13N_AUTH__OIDC`):** Register callback `{server.public_url}/api/v1/auth/oidc/{provider_id}/callback` at your IdP (Discovery, PKCE S256, `RS256`/`ES256`, `openid profile email`) and configure `defaults` plus optional claim-based `role_rules` (`A13N_AUTH__OIDC` replaces the entire `[auth.oidc]` table):

```json
{
  "defaults": [{"workspace_id": "ws_example", "role": "runner"}],
  "providers": {
    "team": {
      "label": "Team Identity",
      "issuer": "https://identity.example.com/realms/team",
      "client_id": "a13n-console",
      "client_secret": "replace-at-deploy-time",
      "extra_scopes": ["groups"],
      "role_rules": [
        {"match": {"groups": "console-admins"}, "organization_id": "org_example", "role": "admin"}
      ]
    }
  }
}
```

- **Sign out at the IdP:** For providers that publish an `end_session_endpoint`, Console sends the browser there after ending its Service session. Register `{server.public_url}/login` as an allowed post-logout redirect URI. Service sends the client ID without an ID token hint; confirm that your IdP supports that flow.
- **Sign out from the IdP:** If your IdP supports Back-Channel Logout, register `{server.public_url}/api/v1/auth/oidc/{provider_id}/backchannel-logout` for the same OIDC client. Enable session IDs at the IdP if it offers that setting and you want per-device logout. Sessions created without a session ID can still be revoked by subject notifications. API keys and Schedules keep their own validity.
- **Bundled sign-in icons (`brand`):** Console automatically matches common issuer URLs (Google, Microsoft Entra, Authentik, Keycloak) or whole brand tokens in the provider ID/label. Override explicitly with `brand` (`google`, `microsoft`, `okta`, `auth0`, `github`, `gitlab`, `cloudflare`, `authentik`, `keycloak`, `authelia`, `zitadel`, or `generic` for the default shield).
- **Display email (`require_email_verified`):** Service stores the ID token's `email` strictly as display metadata (`oidc_email`) when `email_verified` is boolean `true` (`require_email_verified = true` by default; display email never links accounts or grants access). For providers that omit `email_verified` (Microsoft Entra ID workforce tenants) or default it to `False` (Authentik), set `require_email_verified = false` on `[auth.oidc.providers.<id>]` **only** when users cannot set arbitrary unverified addresses.
- **OIDC-only sign-in:** After promoting an OIDC account to manual organization `admin` and verifying sign-in, set `A13N_AUTH__PASSWORD_ENABLED=false` on API replicas. Keep the bootstrap password admin for emergency recovery (or disable it via `a13n-service user disable --email <bootstrap-email>`—never delete rows directly from `principals` in PostgreSQL). OIDC sessions expire after `auth.oidc.session_seconds` (default 12 hours).

## Operator account control

Operators can suspend or restore any user account from the CLI (use `--principal-id usr_...` for OIDC accounts):

```sh
a13n-service --config service.toml user disable --email person@example.com
a13n-service --config service.toml user enable --email person@example.com
```

## Audit

Inspect immutable access and security events (`actor_id`, `action`, `outcome`) under **Organization settings → Audit** (`/api/v1/organizations/{organization_id}/audit-events`), **Workspace settings → Audit** (`/api/v1/workspaces/{workspace_id}/audit-events`), or `GET /api/v1/users/me/audit-events`.
