---
title: Resource basics
description: 'What workspace resources have in common: lifecycles, revisions, identifiers, and providers.'
---

Resources are what a workspace configures for its work: providers and models, agents and skills, connections, environment templates, memories, subscriptions and assets. This page covers what they have in common. The following pages cover each kind: [Models](models.md), [Tools and connections](tools.md), [Skills](skills.md), [Environments](environments.md), [Memory](memory.md) and [Files and webhooks](files-and-webhooks.md).

## Workspaces

Every resource belongs to exactly one workspace and never moves. Its collection lives under `/api/v1`, such as `/api/v1/model-providers` or `/api/v1/agents`, in the workspace the request's credential selects (see [HTTP conventions](http.md#workspace)). A resource refers only to resources of its own workspace, such as a model to one of its workspace's providers; anything outside the workspace answers `404`, as if it did not exist. Creating and changing resources needs `write` in the workspace; webhook subscriptions need `admin`. In Console, providers are under **Workspace settings → Providers**.

## Lifecycles

Resources follow one of two lifecycles.

**Live** resources (providers, models, connections, environment templates, subscriptions, memories) change in place. Each change increments the row's `version`, and runs use the current state when they need the resource: a disabled provider or connection stops new use at once.

**Revisioned** resources (agents and skills) have a head and immutable, numbered revisions. Each configuration change adds a revision, and a change to name, description or labels changes only the head. The head's `default_revision_id` selects the revision that new runs without a pin use, and runs pin the exact revision they started with. Heads are archived rather than deleted. Publishing content identical to the current default returns that revision (`201`) without changing the head, even with `make_default: false`.

Revisioned configuration and retired assets remain available for run history. Other resources have their own removal rules:

| Kind                               | Stop using it                                                                                        |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Providers, models                  | `PATCH` with `{"enabled": false}`.                                                                   |
| Connections, environment templates | `PATCH {"enabled": false}`. A connection's credential is removed with `POST …/revoke`.               |
| Agents, skills                     | `POST …/archive`; `POST …/unarchive` reverses it.                                                    |
| Assets                             | `DELETE` retires the asset; its content stays readable for history.                                  |
| Subscriptions, environments        | `DELETE`.                                                                                            |
| Standalone memories                | `DELETE` removes mounts, files and history, or purges the record namespace. See [Memory](memory.md). |

## Common conventions

- **Keys and IDs.** Only models are identified by a `key`: 1–128 lowercase letters, digits, `-` and `.`, starting with a letter or digit (`^[a-z0-9][a-z0-9.-]{0,127}$`). A key is unique in its workspace and never changes; paths, agent configurations and ETags (`"{key}:{version}"`) name a model by it, and its view has no `id`. Every other resource, including agents, skills, memories, environment templates and revisions, is identified by a kind-prefixed ID, such as `ap_…` for agents and `sk_…` for skills.
- **Concurrency.** Reads return an `ETag`; changes require it in `If-Match`. See [HTTP conventions](http.md#concurrency-control).
- **Labels.** Agents, skills and environment templates carry up to 32 `labels`. Filter them with `?label=key:value` (up to eight selectors, all must match). Agent and skill lists also support `q` (case-insensitive substring of the name or description) and `archived=true|false`.
- **Authors.** Views carry `created_by_id`, `updated_by_id`, `created_at` and `updated_at`.
- **Audit.** Changes to resources are recorded in the [audit trail](identity.md#audit). An update that changes nothing keeps the version and records nothing.

## Providers

A provider is one configured account of an external service. There are five kinds:

| Kind          | Guide                                                  |
| ------------- | ------------------------------------------------------ |
| `model`       | [Models](models.md)                                    |
| `web`         | The [web toolset](tools.md#web-search-and-scrape)      |
| `environment` | [Environments](environments.md)                        |
| `connector`   | [Connector connections](tools.md#composio-connections) |
| `memory`      | [Record memories](memory.md#record-memories)           |

`GET /api/v1/provider-types/{kind}` describes each installed type: its `configuration_schema` and `credential_schema` (JSON Schema), when a credential is required, and a `setup_url` for obtaining one. Model types add their model APIs and per-API settings schemas; web types list the operations they serve; environment types describe their environment schema and whether they support managed instances, stop and destroy. Console builds its provider forms from this endpoint.

Create a provider in its kind's collection, such as `/api/v1/model-providers`, with its type, name, configuration and credential:

```sh
curl -X POST "$A13N_URL/api/v1/model-providers" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"type": "openai", "name": "OpenAI", "config": {}, "credential": {"api_key": "sk-..."}}'
```

- **Credentials are write-only.** They are encrypted with the deployment's key ring and never returned; views show only `credential_configured`. In a `PATCH`, a `credential` value replaces it, `null` removes it, and leaving it out keeps it.
- **Configuration changes keep saved secrets.** Changing `config`, including the base URL, keeps an omitted credential and every omitted extra header. An empty `extra_headers` object also keeps saved values; set a header to `null` to remove it. The updated configuration must still permit the saved credential and header names.
- **Endpoints obey the deployment's outbound policy.** Base URLs and other endpoints must pass the [endpoint policy](configuration.md#outbound-requests); plain-HTTP endpoints need the operator's explicit origin allowance. Private IP addresses are not blocked by this policy; apply network restrictions at the deployment boundary.
- **Disable** with `PATCH {"enabled": false}`. A disabled provider refuses new use with `disabled`; existing environments of a disabled environment provider are still maintained.
- **Test** with `POST …/{provider_id}/test` (needs `run`). It sends one inexpensive probe with the stored configuration and returns `succeeded`, `failed` (with a message) or `unsupported`. Web providers, the `openai_chatgpt`, `google_vertex`, `aws_bedrock`, `vercel` and `typesafe` model types, and every environment type except `docker` have no test and return `unsupported`. The `docker` test only pings the Docker Engine and creates nothing. A memory provider test lists one page of a namespace no memory uses.

Unused [Environment connections](environments.md#providers) can be deleted after checking their references. Deletion removes the saved account, not its native resources. Disable other provider kinds to stop new use.
