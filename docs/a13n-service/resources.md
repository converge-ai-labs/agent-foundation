---
title: Resource basics
description: Choose the right resource, update it with its version, and understand what a run keeps.
---

To build a project-notes agent, first connect a provider account, save a model from that provider, and select the model in an agent. Add a connection for business-system tools or an environment template for file work. These saved objects are **workspace resources**. This page explains how to identify, change and retire them.

For the creation steps, use [Models](models.md), [Tools and connections](tools.md), [Skills](skills.md), [Environments](environments.md), [Memory](memory.md) or [Files and webhooks](files-and-webhooks.md).

## Workspaces

Select the workspace before configuring resources. Each resource belongs to one workspace and stays there; its references must point to resources in that same workspace. A model, for example, selects one of the workspace's model providers. A request for a resource outside the selected workspace returns `404`.

API collections sit under `/api/v1`, such as `/api/v1/agents`. Credentials select the workspace as described in [HTTP conventions](http.md#workspace). Creating and changing resources requires `write`; webhook subscriptions require `admin`. In Console, manage provider accounts under **Workspace settings → Providers**.

## Lifecycles

Choose the operation based on what you are changing:

| Change                                                                           | What happens                                                                                                                 |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Edit a provider, model, connection, environment template, subscription or memory | The live resource changes in place. Changed versioned fields advance its `version`; later uses resolve the current resource. |
| Edit an agent or skill configuration                                             | Service publishes an immutable, numbered revision. The head identifies the resource and selects its default revision.        |
| Rename an agent or skill, or change its description or labels                    | Only the head changes; its configuration revisions remain intact.                                                            |

A run pins the revisions it accepts. Changing an agent's default affects later unpinned runs, not the run already accepted. Live resources remain live: disabling a provider or connection blocks new use. Publishing configuration identical to the current default returns that revision with `201`, without changing the head, even with `make_default: false`.

Use the resource's retirement operation when you no longer need it:

| Resource                           | Operation and result                                                                                 |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Providers, models                  | `PATCH {"enabled": false}` stops new use. Unused providers can also be deleted; see below.           |
| Connections, environment templates | `PATCH {"enabled": false}` stops new use. `POST …/revoke` removes a connection's credential.         |
| Agents, skills                     | `POST …/archive` keeps the head and revision history; `POST …/unarchive` restores availability.      |
| Assets                             | `DELETE` retires the asset while keeping its bytes readable for history.                             |
| Subscriptions, environments        | Use the kind's `DELETE` operation.                                                                   |
| Standalone memories                | `DELETE` removes mounts, files and history, or purges the record namespace. See [Memory](memory.md). |

## Common conventions

Use a **model key** in model paths and agent configuration. Keys are immutable and workspace-unique: 1–128 lowercase letters, digits, `-` and `.`, starting with a letter or digit (`^[a-z0-9][a-z0-9.-]{0,127}$`). Model views have no `id`. Other resources use kind-prefixed IDs, such as `ap_…` for an agent and `sk_…` for a skill; revisions have their own IDs.

Read the current resource and retain its ETag before an update. Send that exact value as `If-Match`; a stale value returns `412`, and a missing required value returns `428`. Follow the complete [conditional-update exercise](http.md#concurrency-control), then read the resource again to verify the change.

Use up to 32 `labels` on agents, skills and environment templates to organize resources. Filter with `?label=key:value`; up to eight selectors must all match. Agent and skill lists also accept `q` for case-insensitive name/description search and `archived=true|false`. Views include creator/editor IDs and timestamps; the [audit trail](identity.md#audit) records resource changes.

## Providers

A provider stores one configured backend account. Choose the kind for the work you need:

| Kind          | Use it for                                              |
| ------------- | ------------------------------------------------------- |
| `model`       | [Model inference](models.md)                            |
| `web`         | [Web search and scrape](tools.md#web-search-and-scrape) |
| `environment` | [Execution environments](environments.md)               |
| `connector`   | [Connector connections](tools.md#composio-connections)  |
| `memory`      | [Record memories](standalone-memory.md#record-memories) |
| `browser`     | [Managed browser accounts](browsers.md)                 |

1. Open **Workspace settings → Providers**, choose the category and add a provider of the required type.
2. Fill in its configuration and credentials. Console builds the form from `GET /api/v1/provider-types/{kind}`: the response contains configuration/credential JSON Schemas, credential requirements and setup links. Model types also describe APIs/settings; web types list operations; environment types describe recipes and lifecycle support.
3. Save the provider and use its ID when creating the dependent model, connection, template or memory. API clients create it in `/{kind}-providers` with `type`, `name`, `config` and the credential required by that type.
4. Run the provider test where supported, then test the actual workflow. `POST …/{provider_id}/test` requires `run` and returns `succeeded`, `failed` or `unsupported`. A non-billable probe does not establish that an agent task will succeed.

An `unsupported` result means the type has no probe; verify it through a run. Web types have no probe. Among environment types, only Docker is tested, by pinging the engine without creating anything. Model types use their declared connection probe; memory providers list one page of a namespace no memory uses.

When rotating credentials or moving an endpoint, keep these update rules in view:

- Credentials are encrypted and write-only. Views expose `credential_configured`, not the secret. In a `PATCH`, a supplied `credential` replaces it, `null` removes it, and omission preserves it.
- Changing `config`, including its base URL, preserves an omitted credential and omitted model-provider extra headers. An empty `extra_headers` also preserves saved values; set a named header to `null` to remove it. The new configuration must permit the retained credential and header names.
- Endpoints must pass the deployment's [outbound policy](configuration.md#outbound-requests). Plain HTTP needs an explicit origin allowance. This policy permits private IP addresses; enforce network restrictions at the deployment boundary.

Disable a provider with `PATCH {"enabled": false}` to stop new use. Maintenance of its existing environments continues. To remove an unused provider, open its edit dialog, choose **Delete** and confirm. This permanently removes local configuration and credentials, not the upstream account or native resources. References from resources, retained configurations or pending cleanup block deletion with `provider_in_use`; disable the provider instead. [Environment providers](environments.md#providers) show template and environment references before deletion.

## Egress credentials

An egress credential stores a static token for controlled Envd HTTPS requests. It belongs to one workspace and uses an `ecred_…` ID. Creating, editing, rotating and revoking it requires workspace `write`. Values are encrypted and write-only; resource queries return metadata and versions, never the token.

In Console, open **Capabilities → Credentials**. Select **Add credential** to create a token resource, then open its detail page to edit metadata, rotate its token or inspect Session application. **Configuration** shows the immutable domains and the revoke action. Token fields start empty and clear when the dialog closes; saved values cannot be read back. A successful save confirms the displayed version in Service. **Sync active Sessions** requests an asynchronous retry and leaves idle Sessions disconnected. The application view refreshes every ten seconds while open. The Environment details inspector also has a **Credentials** section. Expand it to inspect the credential references and application state for that environment.

1. Create the resource with `POST /api/v1/egress-credentials`, supplying `name`, optional `description`, `allowed_hosts` and `value`. Allowed hosts are exact ASCII DNS names, such as `api.github.com`; URLs, IP addresses and wildcards are refused. The token must contain 1–8192 UTF-8 bytes without ASCII control characters.
2. Read the resource and retain its ETag. Use `PATCH /api/v1/egress-credentials/{id}` with `If-Match` to edit its name or description. Allowed hosts are fixed after creation; create a new resource to change them.
3. Rotate with `PUT /api/v1/egress-credentials/{id}/value`, `If-Match` and `{"value": "<new token>"}`. The response returns `202` and the new `credential_version`. Metadata edits change only the ordinary `version`.
4. Revoke with `POST /api/v1/egress-credentials/{id}/revoke` and `If-Match`. This clears the stored value and permanently marks the credential revoked. Create a new resource to resume use; there is no restore or hard-delete operation.

Use `GET /api/v1/egress-credentials/{id}/sessions` to inspect each Session's application state, or `GET /api/v1/environments/{id}/credential-sessions` for one Environment. Follow `next_cursor` without changing the target. `applied` means the last confirmed version matches the recorded native Session; it does not probe whether that connection is online. `pending` and `retry` refer to a live tracked execution, `on_next_use` waits for another attach, `on_create` waits for native creation, and `unknown` means the current Session cannot be confirmed. Revocation is confirmed in a Session only when the revoked credential version is applied there.

`POST /api/v1/egress-credentials/{id}/sync` requires workspace `write` and returns `202` after requesting another comparison by existing owners. It works for both active and revoked resources, changes no resource version, and does not require `If-Match`. It never attaches an idle Session or proves that every Session has updated.

A `202` confirms that Service saved the rotation or revocation. It does not confirm that all existing Envd Sessions applied it, and does not revoke the token at its upstream provider. Credential writes are unavailable to agent platform tools and management MCP; MCP exposes only metadata queries.

To select credentials for an Agent, open **Environment → Default Session configuration** in its editor. Configure controlled HTTPS egress, allowed destinations, and a credential binding such as `GH_TOKEN` for `api.github.com`. Each injection domain must also be an allowed destination when using an allowlist. **Agent usage preview** shows the variable and a command example. Save the Agent to publish these defaults for new primary environment bindings; existing conversation bindings keep their own configuration. **Edit Session JSON** retains access to other Session settings and updates the draft before you save. Importing an Agent into another workspace requires selecting available credentials in that workspace.
