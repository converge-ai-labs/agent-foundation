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

For an API update, read the resource first and use its returned `ETag` in `If-Match`. Try renaming an existing model provider:

1. In a terminal with `curl`, set `A13N_URL` to the Service origin and `A13N_API_KEY` to a workspace key whose principal has `write`. Set `PROVIDER_ID` to the ID of a model provider you intend to rename.

2. Read the provider and its response headers:

   ```sh
   curl --fail-with-body -sS -i "$A13N_URL/api/v1/model-providers/$PROVIDER_ID" \
     -H "Authorization: Bearer $A13N_API_KEY"
   ```

3. Set the shell variable `ETAG` to the returned ETag value, including its double quotes. Send the changed name:

   ```sh
   curl --fail-with-body -sS -i -X PATCH "$A13N_URL/api/v1/model-providers/$PROVIDER_ID" \
     -H "Authorization: Bearer $A13N_API_KEY" \
     -H "If-Match: $ETAG" -H "Content-Type: application/json" \
     -d '{"name": "Project models"}'
   ```

4. Expect `200` and the new name. Repeat the read to confirm the saved result and use the new ETag for another edit. A stale ETag returns `412`; read and review the current resource before retrying. Omitting `If-Match` returns `428`.

The rename leaves credentials and configuration unchanged. A no-op update keeps the version and records no audit event. See [Concurrency control](http.md#concurrency-control).

Use up to 32 `labels` on agents, skills and environment templates to organize resources. Filter with `?label=key:value`; up to eight selectors must all match. Agent and skill lists also accept `q` for case-insensitive name/description search and `archived=true|false`. Views include creator/editor IDs and timestamps; the [audit trail](identity.md#audit) records resource changes.

## Providers

A provider stores one configured backend account. Choose the kind for the work you need:

| Kind          | Use it for                                              |
| ------------- | ------------------------------------------------------- |
| `model`       | [Model inference](models.md)                            |
| `web`         | [Web search and scrape](tools.md#web-search-and-scrape) |
| `environment` | [Execution environments](environments.md)               |
| `connector`   | [Connector connections](tools.md#composio-connections)  |
| `memory`      | [Record memories](memory.md#record-memories)            |

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
