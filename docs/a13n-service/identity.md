---
title: Identity, roles, and access
description: Manage organizations, workspaces, RBAC roles, invitations, service accounts, API keys, and audit logs in Service.
---

Service isolates resources by **Organization** and **Workspace**, and enforces role-based access control (RBAC) for both human users and automated service accounts.

## Organizations and workspaces

- **Organization:** Top-level tenant boundary owning members, billing/quotas, and workspaces.
- **Workspace:** Scopes agents, threads, environments, providers, connections, and API keys.

## Roles

| Role      | Permissions                                                              |
| --------- | ------------------------------------------------------------------------ |
| `viewer`  | `read` — inspect agents, runs, and resources                             |
| `runner`  | `read` + `run` — execute agents, manage threads, and use environments    |
| `builder` | `read` + `run` + `write` — create/edit agents, templates, and providers  |
| `admin`   | Full workspace/organization administration, members, and security policy |

## Members and grants

Manage user roles under **Workspace settings → Members** or **Organization settings → Members**. Removing a grant immediately revokes access on subsequent requests.

### Invitations

Admins invite new users under **Organization settings → Invitations**. Invited users sign in and automatically receive their assigned organization and workspace grants.

## Service accounts

Create dedicated **Service accounts** for backend applications and CI pipelines instead of using personal user credentials. Assign the minimum workspace role needed (for example, `runner` for an app that only executes published agents).

## API keys

Create workspace-scoped API keys under **API keys** (personal or service-account). Send them in `Authorization: Bearer $A13N_API_KEY`. Keep API keys on your server—never embed them in browser code.

## Operator account control

Operators can disable or re-enable compromised accounts from the CLI:

```sh
a13n-service user disable --email user@example.com
a13n-service user enable --email user@example.com
```

## Audit

Service records resource mutations, membership changes, and security events in the workspace and organization audit trail (**Settings → Audit** or `/api/v1/audit-events`).
