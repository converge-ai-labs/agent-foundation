# Resources: what a tenant configures

`resources/` holds what a tenant configures before submitting work: agents, skills, provider resources, models, environment templates, connections, uploads, assets, webhook subscriptions and memories, whose files and mounts [11](11-memory.md) owns. Each kind is one package ([02](02-layout.md#a-resource-package)). This chapter owns their lifecycles, stored shape, validation and the rules they share. The exported contract ([10](10-api.md)) owns exact request and response shapes, [03](03-tenancy.md) owns verbs and credential encryption, and [08](08-providers.md) owns provider definitions, capability flags and runtime handles.

## Two lifecycles

Every resource has one lifecycle, decided by one question: **does a run freeze it?** A run freezes only the content that execution, including recovery, re-reads to define the agent's behavior: its agent revision and the skill and subagent revisions that revision pins. Everything else is live: each use resolves the current row and checks it again, because credentials rotate, endpoints move and policies change.

| Lifecycle         | Shape                                                                    | Kinds                                                                                   | Retirement                                                                                                           |
| ----------------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Revisioned        | a head plus immutable numbered revisions; the head points at its default | agents, skills                                                                          | `archived_at` on the head, reversed by `unarchive`                                                                   |
| Live              | one mutable row with an ETag                                             | provider resources, models, environment templates, connections, subscriptions, memories | `enabled = false` for provider resources, models, templates and connections; deletion for subscriptions and memories |
| Immutable content | one row over stored bytes                                                | assets                                                                                  | `retired_at`; the content stays readable                                                                             |

A consumer that needs a live value to stay fixed copies it where it uses it: a webhook delivery copies its subscription's URL and signing secret ([07](07-facts-and-delivery.md#lifecycle-webhooks)), an environment instance keeps the recipe it was created with ([06](06-environments.md)), and a run freezes its thread's caller headers ([Caller headers](#caller-headers)).

Every mutable row has a `version` that the database advances on each change of a versioned column; state the Service maintains on its own, such as a connection's renewed tokens, is unversioned ([Connections](#connections)). Its ETag and the `If-Match` every conditional route requires are [10](10-api.md#preconditions)'s. Timestamps are for display, never for concurrency.

## Rules every kind follows

- A change is one short transaction: authorize, lock the row, check `If-Match`, act, stamp `updated_by_id`, record the audit event. Work that needs the network, such as a GitHub import, a provider test, tool discovery or an authorization step, runs outside any transaction after authorization; what it depended on is read again under the lock.
- An update that changes nothing keeps the version and records no audit event.
- A resource row of an archived workspace refuses every verb but `read` with `disabled` (details `kind: workspace`), as the workspace itself does ([03](03-tenancy.md#authorization)). A connection's `revoke` is offboarding and stays allowed ([Connections](#connections)).
- Every change records one audit event with action `<kind>.<verb>`, such as `agent.update`, `agent.revision.create`, `model_provider.update` or `connection.authorization.complete`, through the function [03](03-tenancy.md#audit) owns. An update's details name the changed `fields`, never values or credentials.
- References are validated when written, and live references again when used. A failed check is `invalid_argument` naming the field path. A referenced row the caller cannot read is `not_found` and a disabled one is `disabled`, except inside an agent configuration, where a missing, disabled, archived or unusable reference is `invalid_argument` at its field with the referenced `kind` and `id` (a model's key); a missing verb stays `forbidden`.
- Retired rows stay readable. An archived head is listed with `archived_at` and refuses new runs, revisions and pins; a disabled provider resource, model, template or connection is listed and refuses new use. Disabling or removing a credential can fail later execution; it changes no recorded fact and no staged webhook delivery.
- `labels` is a string map of at most 32 entries, keys and values at most 128 characters, on agents, skills and environment templates (and on sessions, threads and runs, [05](05-runs.md)). It is edited through the resource's own `PATCH`; lists filter with up to eight `?label=key:value` selectors, all of which must match.
- Lists are cursor-paged in a stable order ([10](10-api.md#collections)).

## Keys

Only models have a **key**: it matches `^[a-z0-9][a-z0-9.-]{0,127}$`, is unique in its workspace and never changes. A create derives the key from the model's provider type and upstream model name ([models](#models)) unless it passes one, and must pass one when the derived key is taken (`already_exists`). The API addresses a model by key alone: its view carries no ID, and paths, ETags, errors and agent configurations name the key. A model is never deleted, so a key never comes to name another model. Model rows keep an internal ID for foreign keys, such as a usage record's model. Every other resource, skills included, is addressed by its ID and has no key.

## Revisioned heads

Agents and skills share the head and revision shape and one set of helpers in `resources/revisions.py`.

```
<heads>
  id  organization_id  workspace_id  name  description  default_revision_id NULL  labels
  archived_at NULL  version  created_by_id  updated_by_id  created_at  updated_at
  FOREIGN KEY (id, default_revision_id) -> <revisions> (<head>_id, id), deferred

<revisions>
  id  organization_id  workspace_id  <head>_id  number  config  digest  note NULL
  created_by_id  created_at
  UNIQUE (<head>_id, number)
  CHECK (number > 0)
```

- **Creation.** Creating a head creates revision 1 in the same transaction and makes it the default, so a committed head always has a default. The default must be a revision of the same head.
- **Revisions.** A revision is immutable by trigger. `number` increments per head under the head's row lock. `config` is the complete validated configuration, and `digest` is the SHA-256 of its canonical JSON. The head's `version` orders every head mutation, publication included, through its ETag ([data conventions](../data-conventions.md#resource-revisions-and-concurrency)).
- **Publication.** `POST …/revisions` takes the configuration, an optional `note` and `make_default` (default true) and requires `If-Match` on the head. A new revision changes the head's ETag whether or not it becomes the default, and is audited as `<kind>.revision.create` on the head with its `revision_id`. A configuration whose digest equals the head's current default creates nothing, whatever `make_default` says: the request answers 201 with that revision, and the head's version, ETag and audit trail stay unchanged. With `make_default: false`, any other digest creates a revision that is not the default.
- **Default.** `set-default` (with `If-Match`) repoints the head at one of its revisions, audited as `<kind>.revision.set_default`; repointing at the current default changes nothing.
- **Reference.** The API addresses a head by its ID; runs and revisions record revision IDs.
- **Archive.** An archived head changes only by `unarchive`: metadata `PATCH`, new revisions and default changes are 409 `conflict` with reason `archived`. A new run naming an archived agent is `disabled`, and a configuration cannot newly pin an archived skill or subagent; a run's override keeps the pins its revision holds without that check.
- **Lists.** Head lists are ordered by ID and filter by `label`, `q` (a case-insensitive substring of name or description, with `%` and `_` taken literally) and `archived` (true: only archived; false: only open; omitted: all). Revision lists are newest first.

## Agents

```
agents            head columns + source  image NULL
                  CHECK (source IN ('custom', 'builtin'))
                  UNIQUE (workspace_id) WHERE source = 'builtin'
agent_revisions   revision columns; config: AgentConfig
```

`AgentConfig` is the agent definition as the Service accepts it:

- `model`: a model key, with `model_settings`, native settings for the model's calling API, and `model_characteristics`, the context characteristics the agent assumes.
- `instructions`.
- `toolsets`: the built-in toolsets `files`, `shell`, `web`, `memory`, `assets` and `configuration`, stored normalized against the catalogue `GET /toolsets` serves. Enabled web search and scrape name a web provider resource.
- `skills`: `{skill_id, revision_id}` selections.
- `connection_tools`: `{connection_id, tools, defer_loading, permission, permissions}` selections ([Connections](#connections)).
- `client_tools`, whose results a client supplies through resume, and `user_questions`, which offers `ask_user_question`.
- `subagent_mode` (`inline` or `async`) and named `subagents`: `{agent_id, revision_id, description, context, usage_limits, environment}`. `environment.mode` is `none`, `shared` or `dedicated`, and `dedicated` names a `template_id`.
- `reviewer`: a model key and its settings for the `review` tool permission.
- `media_understanding`: image, video and audio model keys.
- `plugins`: `{instance_name, plugin_key, config}` selections of installed Harness plugins ([08](08-providers.md#installed-harness-plugins)).
- `output_spec` and `retries`.
- `default_environment_template_id`: referenced, not pinned; it is read when an environment is created from it, never during execution.
- `memory_mounts`: default memory mounts `{name, memory_id, access, recall}`, at most 32 with unique names and memories, which a thread takes at its first acceptance ([11](11-memory.md#mounts)). They are referenced, not pinned, and a run's override cannot change them.

Tool permissions live on each selection and compile into one Harness tool-permission table.

### Validation

Revision creation, `set-default`, duplication and `POST /agents/validate` apply one validation. It pins every skill and subagent edge whose `revision_id` is omitted to that head's current default, and it reports the first failure as `invalid_argument` with the field path relative to the configuration:

01. Rules the configuration obeys on its own: enabled web search and scrape name `provider_id`; client tool names do not collide with built-in tool names, and their parameter schemas are valid, self-contained JSON Schemas; the `review` permission requires a `reviewer`; an async agent's edges set only `usage_limits.request_limit`.
02. The output schema compiles, and each plugin's installed factory accepts its configuration.
03. Skill and subagent edges name open heads and revisions of those heads, and the pinned skill revisions declare distinct `SKILL.md` names, since the model sees each skill by its name.
04. The model exists, is enabled and is usable by the author. `model_settings` must match the settings schema of the model's calling API (`providers/model_settings.py`, reached through the registry: the schema `GET /provider-types/model` publishes, [08](08-providers.md#provider-type-descriptions)); the reviewer's `model_settings` is checked against the reviewer model's API as `reviewer.model_settings`.
05. Each media model is usable and declares the matching `{kind}_understanding` capability.
06. Each connection selection passes the connection's own check, and the agent declares no more tool permissions than one agent may have.
07. Each web provider is enabled and usable, and its type serves the selected operation (and domain restriction for scrape). Operation support is checked here, not at run time.
08. The default template and every dedicated edge's template are usable.
09. Each default memory mount names a memory of the workspace, at `memory_mounts.{index}.memory_id`. Only authoring checks it; the acceptance that mounts a default checks its memory again.
10. The inline subagent graph does not lead back to the agent itself, nests at most 16 deep and reaches at most 256 revisions.

An author's references need `read`. A run's override (`options.overrides`, [05](05-runs.md#submit-and-accept)) passes the same validation on the configuration it produces, with verb `run` under the run's authority, and is frozen with the same pins. Credentials are resolved only at execution, where live references are checked again. Pinning direct edges keeps changed defaults from altering recovered execution without copying a second graph.

### Operations

- `POST /agents` `{name, description, labels, config}` creates the head and revision 1.
- `POST /agents/validate` `{config, agent_id?}` needs `write`, applies revision validation and writes nothing: 204, or the same `invalid_argument`. `agent_id` names the agent the configuration would become a revision of, so the graph check sees cycles through it.
- `GET /agents` filters by `label`, `q`, `archived`, `source`, and by `skill_id` and `skill_revision_id`, which keep the agents with a revision that pins them.
- `PATCH /agents/{agent}` changes `name`, `description` and `labels`.
- `POST /agents/{agent}/archive` and `/unarchive` take `If-Match`.
- `POST /agents/{agent}/duplicate` `{name, description, labels, revision_id?}` creates a custom head whose revision 1 copies one revision of an unarchived source, validated again. The avatar is not copied.
- Revisions: `POST`, list, get, and `POST …/revisions/{rev}/set-default`, which validates the configuration again.
- `PUT`, `GET` and `DELETE /agents/{agent}/avatar` manage the head's image. Changing it needs `write` on an unarchived custom agent and `If-Match`; reading it needs `read`. The image follows the [image rules](03-tenancy.md#images).

Export and import are Console features: the Console serializes one revision's configuration and imports by creating an agent, whose configuration is [validated](#validation) like any other: every resource it names, pinned revisions included, must belong to the workspace.

### Agent Composer

**Agent Composer** is an ordinary agent with `source = 'builtin'`, the workspace's only one; `GET /agents?source=builtin` finds it. `POST /agent-composer` (needs `write`) creates or refreshes it on demand and returns it:

- Its model is the one it already uses while that model stays usable, else the first model whose upstream name, after its last `/`, matches `composer.models` in preference order, else the workspace's first usable model by key. Without a usable model the call is 409 `model_required`.
- A refresh synchronizes the deployment-owned name and description and appends a revision only when the configuration's digest changed. A preparation that changes neither metadata nor configuration leaves the head version unchanged and emits no update audit event.
- A builtin head refuses metadata and avatar changes, revisions, default changes and archiving (409 `builtin`). It is visible and can be duplicated into a custom agent.

Its tools are the built-in `configuration` toolset, which any agent may enable: `find_resources`, `read_resource` (by a model's key or any other resource's ID; an agent with its default revision's configuration, or with `revision_id`'s) and `describe_agent_config` read resources the run's principal may read, and `create_agent` and `create_agent_revision` call the same service functions as the API under the run's authority. The write tools default to the `ask` permission, which also keeps a repeated `create_agent`, which makes another agent, from going unseen. How a refusal reaches the model is [05](05-runs.md#execute)'s Service tools rule. There are no drafts and no separate session kind: the conversation is the editing session, and the revision is its result.

## Skills

```
skills            head columns
skill_revisions   revision columns + package_ref; config: SkillManifest
```

A skill revision is a validated package. Its source is `{kind: upload, upload_id}` or `{kind: github, repository, ref?, path, commit?}`:

- **Upload.** The package is a staged upload of the caller's workspace ([Uploads and assets](#uploads-and-assets)).
- **GitHub.** The Service reads a directory of a public repository at `ref` and records the resolved commit. A given `commit` that differs from the resolved one is 409 `commit_mismatch`. The content is staged as the actor's upload, keyed by repository and package digest, so a repeated import reuses it. Every GitHub read (creation, validation or a new revision) spends the caller's [upload budget](#uploads-and-assets) first, and a new revision checks `If-Match` and the head before anything is fetched. Each GitHub request, the archive download included, passes the endpoint policy and is bounded by `control.import_timeout` and each response by `objects.max_bytes`. A repository, ref or path GitHub does not find, or a path without files, is `invalid_argument` on `source`; GitHub's rate limit is `rate_limited`, and any other GitHub failure is `unavailable` with dependency `github`.

The package contract: a zip archive with `SKILL.md` at its root or in its only top-level directory; at most 1000 files, 8 MiB per file, 32 MiB expanded, 256 KiB for `SKILL.md` and 1024 bytes per path; no links and no path escaping the root; `__MACOSX/` entries are ignored. The manifest records the name, description, root, files, size, package digest and size, and the source with its resolved commit.

The model sees a skill by the `name` its pinned revision's `SKILL.md` declares; the head's `name` is display text. Two skills of a workspace may declare the same name, and a new revision may declare another; only the skill revisions one agent pins must declare distinct names ([validation](#validation)).

- `POST /skills` `{name?, description?, labels, source}` creates the head and revision 1. Name and description default to the manifest's.
- `POST /skills/validate` `{source}` needs `write`, checks a package exactly as creation does and returns the manifest, staging nothing.
- `GET /skills` filters by `label`, `q`, `archived` and `source` (`upload` or `github`, the default revision's source). A skill's representation includes `default_revision {id, number, source}`.
- `PATCH` changes `name`, `description` and `labels`.
- Revisions: `POST` with a source and `If-Match`, list, get, `set-default`, `GET …/revisions/{rev}/content` (the zip) and `GET …/revisions/{rev}/files/{path}` (one file of the package). Both reads serve the bytes as an attachment ([10](10-api.md#representations)).

## Provider resources

A provider resource is a tenant-configured backend account: a model provider, an environment provider, a connector provider, a web provider or a memory provider. [08](08-providers.md) owns the definitions its `type` selects.

```
model_providers        (mprov_)
environment_providers  (eprov_)
connector_providers    (cprov_)
web_providers          (wprov_)
memory_providers       (memprov_)
  id  organization_id  workspace_id  type  name  config  credential NULL  extra_headers
  enabled  version  created_by_id NULL  updated_by_id NULL  created_at  updated_at
  UNIQUE (workspace_id, id)
```

- **Workspace.** A provider belongs to one workspace. Creating or changing one needs `write` there, and using one needs `run` ([03](03-tenancy.md#authorization)). A provider the caller cannot read is `not_found`.
- **Configuration and credential.** `config` and `credential` are validated against the type's schemas; a refusal carries at most five locations with the schema's own messages, never the submitted value. The credential is write-only: encrypted for its row ([03](03-tenancy.md#credential-encryption)), never returned (views show `credential_configured`), replaced whole when present and removed by `null`. A credential is bound to the configuration it was entered with: a `PATCH` that changes `config` while a credential is stored must also replace or remove `credential`, or it is `invalid_argument` on `credential`.
- **Extra headers.** A model provider may carry up to 32 extra request headers. Values are write-only secrets, each encrypted for its row and header name; views return only `header_names`. `PATCH` edits them per name: a value sets one, `null` removes one, and names left out keep theirs. Names the Harness definition reserves for transport, authentication or protocol are refused, and other kinds refuse the field (`invalid_argument` on `extra_headers`). A `config` change must also replace or remove every stored header; replacing only the credential keeps them. The audit event names the field, never a value.
- **Enabled.** `enabled` is set on create and changed by `PATCH`. A disabled provider refuses new use (`disabled`); maintenance of environments that already use it continues ([06](06-environments.md)).
- **Same-workspace references.** Models, environment templates, environments, connections and memories reference their provider by `(workspace_id, provider_id)`, so the database refuses a provider of another workspace.
- **Test.** `POST /{kind}-providers/{id}/test` needs `run`. It makes one non-billable probe of the current configuration outside any transaction, bounded by `providers.operation_seconds` and `providers.response_bytes`, and changes nothing. It returns `{provider_id, provider_version, status, message}` with status `succeeded`, `failed` or `unsupported`; the message is fixed text or the provider's classified code, never an upstream body. A model type is probed when its definition has a connection probe; a connector provider runs its own account test; a memory provider lists one page of a namespace no memory owns ([11](11-memory.md#memory-providers)); web types are `unsupported`. Of the environment types only Docker is probed, and its test only reads. A remote engine the account names passes the endpoint policy first ([08](08-providers.md#outbound-endpoint-policy)): a denied one fails with `provider_endpoint_denied` and a host that does not resolve with `provider_unavailable`, without being dialed; the engine then answers one ping. Nothing is pulled, created or started, and a failure reports only the provider's error code. Billable verification goes through a run and its admission checks, so a test never becomes an unmetered execution path.

Environment providers serve [environment templates](#environment-templates) and the managed instances created from them ([06](06-environments.md)); an external envd target is no provider resource. Memory providers serve [record memories](11-memory.md#memory-providers), each owning one namespace of the provider's backend. A connector provider, such as Composio, is configured once per workspace with the platform's API key as its credential; each [connection](#connections) through it binds one external account. `GET /connector-providers/{id}/apps` (`query`, `refresh`, cursor), `…/apps/{app}` and `…/apps/{app}/actions` read its app catalogue with the provider's credential and need `run`; app and action listings are cached for `providers.discovery_ttl` per provider version, and `refresh=true` reads the apps again.

A null creator or updater represents system initialization ([09](09-runtime.md#workspace-provisioning)); authenticated API mutations always record their principal.

## Models

```
models   (mdl_)
  id  organization_id  workspace_id  provider_id  key  name  description  config  pricing NULL
  catalog_ref NULL  enabled  version  created_by_id  updated_by_id  created_at  updated_at
  UNIQUE (workspace_id, key)
```

A model is one upstream model served by one model provider of its workspace, addressed by its [key](#keys) (`/models/{key}`). The key defaults to `{provider type}-{config.model_name}`, lowercased, with every run of characters a key cannot hold replaced by `-` and cut to 128 characters, so `openai` serving `anthropic/Claude Opus 5.1` defaults to `openai-anthropic-claude-opus-5.1`. A create may choose another, such as a shorter alias, and must when the default is taken (`already_exists`).

- `config` holds `model_name`, `model_api`, `characteristics` (context window, modalities and understanding capabilities) and `settings`, a native request-defaults object checked against the calling API's settings schema and Provider header authority. `settings` defaults to `{}`. Existing flat `max_tokens`, `temperature`, `top_p`, `extra_body` and `extra_headers` remain accepted and returned; `settings` replaces a same-named flat default whole, including explicit empty objects. `model_api` must be one of the provider type's calling APIs (`invalid_argument` on `config.model_api`).
- `pricing`, when present, prices the model's own calls, whatever `pricing.provider` and `pricing.model` name: they only record where the prices came from, such as the catalog channel and model ID they were copied from.
- Creation takes `config` and optional `pricing`; clients seed both from the [model catalog](08-providers.md#model-catalog) and submit them whole.
- `catalog_ref` `{provider, model}` optionally records the catalog model a model started from: a channel ID (`^[a-z0-9][a-z0-9_-]{0,127}$`) and a model ID of 1 to 256 characters. It is provenance for clients, checked for shape only and never resolved, so it may name a model the catalog no longer lists. `PATCH` replaces it when present and removes it with `null`.
- `description` is optional (default empty), and a model may be created disabled.
- A model spends its provider's credential: creating one or changing its `config` needs `write` on the provider, and a disabled provider refuses new models (`disabled`).
- A model the caller cannot read is `not_found`. Using a model at execution needs it and its provider enabled and usable under the run's authority.

Execution selects every model of an agent graph by its key, and every call keeps that selection: the agents' own requests and their compaction, the tool reviewer and media understanding alike. Each model call is therefore admitted as, attributed to and priced by the model that selected it, even when another model of the graph names the same upstream model or the provider answers under another model name; its usage record carries the model and a price snapshot ([07](07-facts-and-delivery.md#usage-records)) and keeps the reported names only as information. A call that names no model of the graph is refused, failing the run with `model_call_unknown`.

### Request overrides

Effective settings layer Service defaults, Model defaults, then Agent/reviewer settings. For `openai.responses`, the Service default is `openai_store: false`, including existing Models with no authored settings and every primary, compaction, reviewer, child and media-understanding call. It does not apply to other calling APIs merely because they share a settings type. Explicit `true`, `false` or `null` remain authoritative; `null` delegates storage behavior to the upstream. No reasoning effort, summary, service tier or output budget is forced. The baseline is evaluated at execution and does not rewrite saved Models or Agent revisions.

`extra_body` and `extra_headers` are JSON objects, empty by default. Agent and reviewer settings layer over their selected Model's defaults. An omitted object inherits the Model value; an explicit object replaces it whole; `{}` clears it; `null` is invalid. Run `model_settings` still replaces the Agent's entire settings object before Model defaults are applied ([05](05-runs.md#submit-and-accept)). There is no recursive merge or deletion syntax. Revision settings and Run options remain frozen; Model and Provider defaults remain live.

Raw bodies are accepted only for `openai.responses` and `openai.chat_completions`. They override ordinary inference parameters at the native SDK's final shallow body merge, including future parameter values. They cannot replace upstream selection, input, tools, structured output, transport, account state or execution lifecycle. Responses protects the whole `text` and `prompt_cache_options` containers; native verbosity and cache settings remain available. The API's published settings schema lists the protected names. Unknown raw inference fields are intentionally accepted; the Service does not infer the meaning of arbitrary gateway extensions or promise that an upstream accepts them. Effective settings remain bounded to 64 KiB and 16 levels.

Request headers are readable configuration, not secret storage. They follow the Harness's header shape bounds and case-insensitive uniqueness. Authentication and HTTP protocol names, the Provider's custom authentication header, every stored Provider header name, and its configured session-affinity header are reserved. Collisions fail, including when the submitted value is empty; values are never included in override-validation errors. Secrets belong only in the Provider's encrypted credential or extra headers. Clearing Model header defaults does not remove Provider headers or automatic affinity.

Model writes, Agent/reviewer validation and Run overrides use the same API and Provider-aware checks. Each execution attempt rechecks effective settings against the current Model and Provider before opening model clients, including every inline child and media model. An incompatible live change can therefore fail an old revision or resumed Run before any model request.

### Request affinity

A Provider's optional `config.session_affinity_header` is bound at model resolution to the current Harness Thread using the shared [UUID v5 derivation](../a13n-harness/16-input-model-and-output.md#automatic-request-affinity). It is disabled when absent or null. Primary and compaction requests, reviewers, inline and asynchronous children, and media understanding use the calling Thread, not the enclosing Service Session or transient Run. Continuing the same Thread keeps the value; a new child or fork has its own. The value is not persisted and never mutates a shared client. The header name is an operator-selected gateway convention, not a guarantee of upstream routing. Automatic OpenAI prompt-cache keys remain independent.

**Media-understanding defaults.** `GET` and `PUT /media-understanding-defaults` hold the workspace's image, video and audio model keys in `workspaces.settings.media`. `PUT` replaces all three, needs workspace `admin` and `If-Match` on the workspace version, and refuses a model that does not declare `{kind}_understanding`; it records `workspace.media.replace`. At execution an agent's own selection wins and must be usable; an unusable workspace default is skipped with a warning, leaving that media kind without understanding.

## Environment templates

```
environment_templates   (envtpl_)
  id  organization_id  workspace_id  name  description NULL  provider_id  config  enabled  labels
  version  created_by_id NULL  updated_by_id NULL  created_at  updated_at
```

A template is the live configuration managed environments are created from ([06](06-environments.md)). `config` is `{recipe, stop_after_seconds, delete_after_seconds}`:

- `recipe` is validated by the provider type's environment schema and stored normalized; errors carry each location and the schema's own message, never the submitted value. The provider must be enabled. Creating a template, or changing its provider or normalized recipe, needs `write` on the provider, whose account the recipe runs on; changing only the idle policy needs `read`. The operator further restricts Docker recipes ([08](08-providers.md#registry)).
- `stop_after_seconds` (default 1800, 60 to 2592000, null for never) and `delete_after_seconds` (default null, 60 to 31536000) are the idle policy; [06](06-environments.md#idle-policy) owns how it applies.

A template is created enabled. `PATCH` (with `If-Match`) may change `provider_id`, `config`, `name`, `description`, `labels` and `enabled`; a disabled template refuses new environments. How template changes reach instances is [06](06-environments.md#templates-and-instances)'s.

Automatically provisioned templates have null creator/updater until a user changes them ([09](09-runtime.md#workspace-provisioning)).

## Connections

```
connections   (conn_)
  id  organization_id  workspace_id  type  name  config  auth  credential NULL  tokens NULL  expires_at NULL
  client_secret NULL  connector_provider_id NULL  status  failure NULL
  authorization NULL  oauth_state_hash NULL  authorization_expires_at NULL
  operation_id NULL  operation_kind NULL  operation_deadline NULL  last_test NULL
  enabled  version  created_by_id  updated_by_id  created_at  updated_at
  CHECK (auth IN ('none', 'bearer', 'headers', 'oauth', 'account'))
  CHECK (status IN ('pending', 'ready', 'reauthorization_required'))
  CHECK (operation_kind IN ('setup', 'complete', 'refresh', 'revoke'))
  CHECK ((connector_provider_id IS NULL) = (auth <> 'account'))
  CHECK ((type = 'mcp') = (connector_provider_id IS NULL))
  CHECK (status <> 'ready' OR auth = 'none' OR credential IS NOT NULL)
  CHECK (client_secret IS NULL OR auth = 'oauth')
  CHECK ((tokens IS NOT NULL) = (auth = 'oauth' AND credential IS NOT NULL))
  CHECK operation_id, operation_kind and operation_deadline are all set or all NULL
  CHECK authorization, oauth_state_hash and authorization_expires_at are all set or all NULL
  UNIQUE (operation_id), UNIQUE (oauth_state_hash) where set
  tokens, expires_at, the operation columns, failure, the authorization columns and last_test are unversioned
```

A connection is a source of tools with **one credential**:

- `type = 'mcp'` is a Remote MCP server: `config` is `{url, tools?, headers, oauth?}`. `url` passes the endpoint policy ([08](08-providers.md#outbound-endpoint-policy)). `tools` narrows what the connection exposes; without it the connection exposes everything the server lists.
- Any other `type` is a connector app served by `connector_provider_id`, whose provider must have that type: `config` is `{app, actions, setup}`, where `actions` (1 to 128) are the exposed actions and `setup` is validated by the provider type. The connection binds exactly one external account; two accounts of one app are two connections.

There is no per-principal credential: anyone with `run` in the workspace can let an agent use a connection's account, so a private account belongs in a personal workspace. Reading a connection needs `read`, and changing one needs `write`. There is no delete: `PATCH {enabled: false}` and `…/revoke` retire a connection.

A connection's version, and so its ETag, changes only with what a person configures or authorizes: the configuration, the entered credential, `enabled`, and a completed, revoked or lost authorization. Renewed tokens, the outstanding operation, failures, a pending browser flow and test outcomes leave it unchanged.

**Authentication.** `auth` says how the credential is obtained, and `status` is `ready` when the connection can be used, `pending` before its credential exists, and `reauthorization_required` when an operation left no usable credential:

| `auth`    | Credential                                                                                                                        |
| --------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `none`    | none; the connection is `ready` at once                                                                                           |
| `bearer`  | an entered token, presented as `Authorization: Bearer`                                                                            |
| `headers` | entered values for exactly the header names in `config.headers`, bounded like [caller headers](#caller-headers)                   |
| `oauth`   | the OAuth client an MCP server's grant was made to, in `credential`, and the grant's tokens, in `tokens`; obtained by `authorize` |
| `account` | the connector provider's account reference, obtained by `authorize`; the provider keeps and refreshes the external tokens         |

The credential, tokens, client secret and pending flow are encrypted for their row and column ([03](03-tenancy.md#credential-encryption)) and never returned. Entered credentials are replaced whole when present and removed by `null`; views show `credential_configured`. A connection's credential is bound to its identity: changing the server URL, the header names, the OAuth settings, the app or its setup, or `auth` drops the credential and any pending authorization, so another endpoint never inherits a credential. Changing only the name, the tool selection or `enabled` keeps it. A credential dropped this way is only cleared, never revoked remotely.

**Header names.** The credential header names in `config.headers`, the MCP server catalogue's `header_names` ([08](08-providers.md#mcp-server-catalogue)) and [caller headers](#caller-headers) follow one rule: a lowercase HTTP token of at most 128 characters that is none of `authorization` (which only a bearer credential sets), `connection`, `content-length`, `content-type`, `accept`, `accept-encoding`, `cookie`, `set-cookie`, `host`, `keep-alive`, `te`, `trailer`, `transfer-encoding` and `upgrade`, and does not start with `proxy-`, `mcp-` or `sec-`.

**OAuth clients.** `config.oauth` is `{client_id?, token_endpoint_auth_method, grant_type, scopes}`:

- Without `client_id`, each authorization registers a public client dynamically. With one, the client is registered in advance, and `GET /connections/redirect-uri` returns the Service's callback, `{server.public_url}/api/v1/connections/callback`, for registering it.
- `token_endpoint_auth_method` is `none`, `client_secret_basic` or `client_secret_post`. A secret method needs `client_id`, and its `client_secret` is write-only (`client_secret_configured`). The secret belongs to the server and client ID: it is dropped when either changes, when the method becomes `none` or when `auth` leaves `oauth`. A new secret drops the credential obtained with the old one; the secret survives revoke and a lost refresh. Authorizing without it is 409 `client_secret_missing`.
- `grant_type = client_credentials` needs a secret method. It is a machine credential that serves every run of the workspace, the same exposure as a bearer token. `authorize` obtains the token without a browser as the connection's single operation and returns `{redirect_url: null, expires_at: null}`. Renewal requests a new token; a failed renewal needs `authorize` again.

**Authorization.** `POST …/authorize` `{return_url?}` needs `write` on the connection and `If-Match`, because the resulting credential serves every run of the workspace. `return_url` must be a page on the origin of `server.public_url` or exactly one of `providers.return_urls` (`invalid_argument` otherwise).

- For `oauth` with the authorization-code grant, the Service discovers the authorization server, registers a client when none is configured, and stores the hash of a random one-use `state` with the encrypted pending flow (PKCE verifier, client, initiator, return URL and browser binding) and its expiry, `authorization_expires_at`, after `providers.flow_seconds`. It returns `{redirect_url, expires_at}`. An authorization-server failure is 503 `unavailable` with dependency `oauth` and a safe `reason` code, `authorization_server_denied` when the endpoint policy refuses the server.
- For `account`, starting the provider's hosted setup is the connection's single operation. The pending flow stores the account reference and provider-resolved `expected_metadata` snapshot the setup created ([Harness Provider contract](../a13n-harness/22-provider-subsystem.md#connector-setup-identity)) and expires at the sooner of `providers.flow_seconds` and the provider's own expiry. A setup failure is 503 `unavailable` with dependency `connector:{type}` and a safe `reason`.
- An MCP connection whose `auth` is not `oauth` is 409 `no_browser_authorization`.
- A browser flow (the authorization-code grant or a connector account setup) requires a login session (`forbidden` for an API key, [03](03-tenancy.md#authorization)), so an API-key client never hands a third party an authorization URL. The flow is bound to that browser: the response sets the cookie `__Secure-a13n_flow_{connection_id}` (`HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/api/v1/connections/callback`, `Max-Age` of `providers.flow_seconds`; over plain HTTP `a13n_flow_{connection_id}` without `Secure`, [03](03-tenancy.md#authentication)), and the flow keeps only the hash of its random value. An API key may start only a flow without a browser, the client-credentials grant.
- A new flow replaces any pending one, and the view's `authorization_pending` holds until `authorization_expires_at`. Starting an OAuth browser flow replaces only the pending flow and a completion of it in progress; an outstanding refresh or revocation continues, and the current credential stays usable until a completed flow replaces it. Completing a flow while a refresh is outstanding ends that refresh as lost: `failure` becomes `{reason: outcome_unknown, code: superseded}` and the completed flow's credential replaces the old one; a possibly rotated refresh token is never presented again.

The public callback `GET /api/v1/connections/callback` is authenticated by `state` and the flow's cookie. Its attempts are rate-limited per client address ([03](03-tenancy.md#authentication)). An unknown, used or replaced `state` is `invalid_argument` on `state`. The callback completes the flow once as the connection's single operation, for the principal who started it, who must still hold `write`. The OAuth path checks the issuer and exchanges the code; the account path inspects only the account the setup created, by its stored reference, and ignores account identifiers the browser sends. Before granting account authority, the Service requires ready status and an exact match for every captured metadata predicate; a missing, null or different value fails with `account_metadata_mismatch`. These failures clear the pending flow without replacing an existing credential. A duplicate callback joins the completion in progress. An expired flow (`authorization_expired`), a callback without the flow's cookie (`browser_mismatch`), an initiator who lost `write`, and a refused or failed flow record `failure` and clear the pending flow. The callback answers `{connection_id, status, error}` as JSON, or redirects (303) to the return URL with those values as query parameters. `error` is a code of at most 64 ASCII letters, digits and underscores; any other browser- or provider-supplied value becomes `failed`. Responses delete the flow cookie and carry `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

A completed authorization that replaces a credential revokes the replaced OAuth grant or connector account remotely as a best effort, bounded by `providers.operation_seconds`; a failure there changes nothing. A replaced grant issued to the same client (the same issuer and `client_id`) is not revoked, since revoking it can end the new grant too.

`POST …/revoke` (`write`, `If-Match`) clears the credential, any pending flow and any outstanding operation at once and sets `pending`. For an `oauth` or `account` connection that holds a credential and whose remote side offers revocation, a disabled connection and one in an archived workspace included (revoking is offboarding), it then asks the remote side to end the credential as the connection's single operation; a connector connection whose provider is disabled or not usable is only cleared (`skipped`). It answers the connection with `remote_revocation`: `revoked`, `failed` (details in `failure`) or `skipped` (nothing to end remotely). A connection with `auth = 'none'` is 409 `no_credential`. Starting an authorization is audited as `connection.authorize`, its completion as `connection.authorization.complete` for the initiator, and revoke as `connection.revoke` with `{remote}`.

**Single operation.** A connection has at most one outstanding remote operation (`setup`, `complete`, `refresh` or `revoke`), claimed under the row lock before its request is sent: the claim records a new `connop_` `operation_id`, `operation_kind` and a deadline of twice `providers.operation_seconds`. Only the claimant sends the request. Sending it and publishing its result are shielded from the caller's cancellation and bounded by `providers.operation_seconds`, so only a timeout or a crash leaves an outcome unknown, and only a claim that is still current publishes. A renewal that finds an operation outstanding waits for it outside the database, bounded by its deadline, and then reads its outcome; a duplicate callback joins the completion. A connector account setup, a client-credentials authorization, a completing callback, revoke and a change of identity end the outstanding operation, and an OAuth browser flow ends only a completion in progress; an ended operation's late result changes nothing and its caller gets 409 `superseded`. `failure` records the last failed operation: `{operation_id, operation_kind, reason, code}`, where `reason` is `rejected` or `outcome_unknown` and `code` is a safe classified code.

An operation that may have been sent but has no result is never repeated. An operation past its deadline fails as `outcome_unknown` with code `deadline_exceeded`, audited as `connection.operation.expire` without an actor: before a renewal reads the grant, and in the `recover_connection_operations` sweep ([09](09-runtime.md#sweeps)). A failed setup or completion clears the pending flow. A failed refresh clears the credential and sets `reauthorization_required` only when its outcome is unknown, the server refused the grant (`invalid_grant`) or the client is a client-credentials client; any other refusal keeps the credential and records `failure`, and a later use renews again. A token request that never left the Service, refused by the endpoint policy (`token_endpoint_denied`) or failing while waiting for or making a connection (`token_endpoint_unreachable`), has a known outcome and keeps the credential; the token client's connection and pool timeouts are a fifth of `providers.operation_seconds`, so that failure arrives before the operation's bound. Occasional reauthorization after a crash is preferred to presenting a possibly revoked refresh token ([RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html#name-refresh-token-protection)).

**Use.** An MCP connection presents its credential on every request. An OAuth access token within 60 seconds of expiry is renewed on use as the connection's `refresh` operation, with the refresh token or, for a client-credentials client, by asking again; concurrent users wait for its result, and a cancelled caller never loses the renewed credential. A renewal the server fails is 503 `unavailable` with dependency `oauth` and a safe `reason`; a use without a grant is 409 `reauthorization_required`. At execution a run resolves each selected connection under its authority: the connection must be enabled and `ready`, and a connector's provider enabled and usable. Before every tool call the connection must still be enabled and `ready`, and the call must pass the run's dispatch check ([09](09-runtime.md#extension-points)); otherwise the call is never sent.

**Tools and discovery.** `GET …/tools` and `POST …/test` need `run`, because discovery presents the credential.

- Discovery reads up to the Harness bound of 2048 tools. A connection exposes at most 128 tools to a model, and an agent selects at most 128 from one connection, because every definition costs model context. An MCP connection without `config.tools` whose server lists more than 128 makes a run fail with `connection_tools_exceeded`.
- `GET …/tools` returns cached tools when present (Redis, `providers.discovery_ttl`, keyed by connection version) and otherwise discovers them; a failure is `unavailable` with dependency `connection:{type}`. Renewed OAuth tokens keep the cached tools, since they leave the version unchanged. Before an account is bound, a connector connection lists the app's catalogue, and its test fails.
- `test` always discovers with the current configuration and credential and returns `{connection_id, connection_version, status, message, tested_at, tools}`; a failure is `status: failed` with a safe message, not an HTTP error. It records `last_test {connection_version, status, message, tested_at}` without changing the version, and an outcome for an older version never replaces a newer one. Tests are not audited.
- Tools report `name`, `description`, `input_schema`, `output_schema` and `annotations` as the server or app declares them. Annotations are hints shown to people, never permissions.

**Selections.** An agent's `connection_tools` entry names an enabled connection of its workspace. `tools` must be a subset of what the connection exposes; `permission` applies to every tool without its own entry in `permissions`, whose keys must be selected tools. `defer_loading` lets the model find an MCP connection's tools through tool search; it applies only to MCP connections, and a connector selection with it is `invalid_argument`. At execution, selected tools the connection no longer exposes are dropped.

The Remote MCP servers suggested for new connections are a catalogue that only prefills a connection ([08](08-providers.md#mcp-server-catalogue)).

### Caller headers

`threads.mcp_headers` maps connection IDs to header name/value pairs. It is how a caller tells its MCP server which conversation a tool call belongs to, so it belongs to the thread, not to individual messages. It is set when the thread is created and edited with `PATCH /threads/{thread}` under the thread's `If-Match`; acceptance freezes the current map into the run, so an edit affects later runs only. The run view never shows the frozen map.

The map is validated when written:

- It names at most 32 connections, and every key is an enabled `mcp` connection of the thread's workspace.
- Names are stored lower-case, unique ignoring case, and follow the [header-name rule](#connections); `authorization` is refused.
- Each connection has at most 32 headers, values of at most 4096 printable ASCII characters, and at most 8192 bytes in total; the whole map holds at most 16384 bytes.
- A name colliding with the connection's own credential header names is refused, and the collision is checked again when a run resolves the connection.

There is no per-connection allow list and no check against an agent revision, because different messages of one thread may select different agents. A child thread copies its parent run's frozen map; a fork copies its origin thread's map. Context is not a tool grant: a call still needs a connection its agent selects and passes the ordinary checks, and only the entry for that exact connection is sent, through the Harness `ContextualMCP` header factory, resolved once per logical run. Values are plain context, stored and returned with the thread, never credentials. Headers take no part in steer compatibility, since every run of a thread carries the same context.

## Uploads and assets

`POST /uploads` stages bytes: a multipart `file` with a required `Idempotency-Key`, needing `write`. Its size is at most the smaller of `objects.upload_bytes` and `objects.max_bytes`.

Stored bytes are never reclaimed, so each principal has one **upload budget** of `objects.upload_limit` requests per `objects.upload_window_seconds`, spent by every upload, every stored image ([03](03-tenancy.md#images)) and every [GitHub read](#skills); an exhausted budget is `rate_limited`.

```
uploads   (upl_)
  id  organization_id  workspace_id  created_by_id  request_key
  filename  content_type  size  digest  created_at
  UNIQUE (workspace_id, created_by_id, request_key)
  CHECK (size >= 0)
```

- A new request key gets a new random upload ID. Its bytes land under `orgs/{org}/uploads/{upload}` ([07](07-facts-and-delivery.md#objects)) and the row is written after them, so an upload ID always names finished bytes. An upload resolves only through its row and only in its own workspace, so a handle never names an arbitrary object key. Rows are immutable and staged uploads are not reclaimed.
- The row is the request key's evidence. The same key with the same bytes, filename and content type returns the same upload; anything else is 409 `idempotency_key_reused` ([10](10-api.md#idempotency)). Concurrent requests with one key can each store bytes; the unique constraint admits one row, and the others answer from it as a replay.

```
assets   (ast_)
  id  organization_id  workspace_id  name  content_type  size  digest  content_ref
  source NULL  retired_at NULL  version  created_by_id  created_at  updated_at
  UNIQUE (workspace_id, content_ref)
  CHECK (size >= 0)
```

An asset is immutable content with metadata. `POST /assets {upload_id, name}` returns 201 with the new asset, or 200 when the same upload and name already made one; the same upload with another name is 409 `upload_in_use`. The built-in `publish_asset` tool publishes a file of the run's environment the same way under the run's authority, with `source = {run_id, run_attempt_id, tool_call_id}`. Inbox entries and run outputs reference assets by ID; new input naming a retired asset, or one of another workspace, is `invalid_argument` (field `content.{index}.asset_id` of the part naming it, reason `not_usable`, with the asset's `kind` and `id`). How a message's asset reaches the model is [05](05-runs.md#how-files-reach-the-model). `DELETE` retires an asset (`write`, `If-Match`, idempotent); `GET …/content` still serves a retired asset, as an attachment ([10](10-api.md#representations)). No asset content is physically deleted.

## Subscriptions

```
subscriptions   (sub_)
  id  organization_id  workspace_id  name  url  signing_secret  kinds  filter  enabled
  version  created_by_id  updated_by_id  created_at  updated_at
  CHECK (kinds is a non-empty array)
```

A subscription is a webhook. `kinds` lists the lifecycle kinds it wants, any of `run.accepted`, `run.running`, `run.waiting`, `run.completed`, `run.failed`, `run.cancelled`, `run_attempt.leased`, `run_attempt.running`, `run_attempt.succeeded`, `run_attempt.yielded`, `run_attempt.failed` and `run_attempt.cancelled`; `filter` optionally narrows by `agent_id`, `session_id` and `thread_id`.

- Every operation needs workspace `admin`. A workspace holds at most `control.subscriptions` subscriptions; more are 409 `subscription_limit`.
- `url` passes the endpoint policy when written.
- The signing secret (16 to 256 characters) is generated as `whsec_` and random text when absent, encrypted, and returned only in the create response. `PATCH` may replace it, which never returns it.
- `PATCH` and `DELETE` require `If-Match`; `DELETE` deletes the row. Delivery staging, signing, retries, the delivery list and redelivery are [07](07-facts-and-delivery.md#lifecycle-webhooks)'s. Each delivery copies the URL and secret when staged, so a change affects later deliveries only.
