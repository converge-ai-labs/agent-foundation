# Layout and naming

This chapter owns the package tree of `packages/a13n-service`, the import direction and the contracts that check it, the conventions every service function follows, naming, object-ID prefixes and the error codes. [DEVELOPMENT.md](../../DEVELOPMENT.md#database-sessions-and-transactions) owns the repository-wide engineering rules these build on: async I/O, short sessions and transactions, SQL operation design, migrations and logging.

## Package tree

```
a13n_service/
  app.py              build_app(distribution, role=..., settings=...): the runtime, routers and background tasks of one role
  cli.py              `a13n-service run | migrate | bootstrap | user disable | user enable`
  settings.py         Settings: one section per concern, plus the sections a distribution declares
  distribution.py     Distribution: what a build contributes; OSS, the built-in distribution

  infra/              shared mechanisms without business rules
    db.py  ids.py  errors.py  http.py  ingress.py  cursors.py  labels.py  crypto.py
    audit.py  outbox.py  sweeps.py  redis.py  outbound.py  images.py  telemetry.py
    objects/          interface.py  local.py  s3.py

  tenancy/            who is asking and what they may do
    tables.py         organizations, workspaces, principals, passwords, api_keys, tokens, grants, invitations
    access.py  authenticate.py  authorize.py  credentials.py  bootstrap.py  expiry.py  mail.py
    organizations.py  workspaces.py  users.py  principals.py  service_accounts.py  api_keys.py
    grants.py  invitations.py  audit.py
    requests.py  schemas.py  routes.py  organization_routes.py  member_routes.py

  resources/          what a tenant configures
    revisions.py      rules every revisioned kind shares
    rows.py           rules every kind's rows share: scoped lookup, partial change, audit and key collisions
    requests.py       the runtime dependency resource routes use
    agents/           tables  schemas  service  routes  validation  definition  toolsets  assistant
    skills/           tables  schemas  service  routes  package  content  github  pins
    providers/        tables (all four provider tables)  schemas  service  routes  scope  probe
    models/           tables  schemas  service  routes  catalog  models_dev  media  runtime
    environment_templates/   tables  schemas  service  routes
    connector_providers/     schemas  routes  catalog
    web_providers/    runtime
    connections/      tables  schemas  service  routes  access  authorization  oauth  account  credentials
                      operations  discovery  headers  runtime
    secrets/          tables  schemas  service  routes
    uploads/          schemas  service  routes
    assets/           tables  schemas  service  routes
    subscriptions/    tables  schemas  service  routes  delivery

  runs/               how input becomes sealed runs
    tables.py         sessions, threads, inbox_entries, runs, run_attempts, usage_records
    sessions.py  threads.py  archive.py  inbox.py  entries.py  inputs.py  attachments.py  placement.py
    submit.py  accept.py  admission.py  resume.py  claim.py  worker.py  attempts.py  execute.py  seal.py
    agent.py  host.py  calls.py  boundaries.py  checkpoints.py  display.py  deferred.py  children.py  subagents.py
    configuration.py  assets.py  skills.py  secrets.py  web.py
    stream.py  webhooks.py  usage.py  traces.py  runs.py  runtime.py
    schemas.py  routes.py  trace_routes.py
    environments/     tables  schemas  service  routes  lifecycle  maintenance  mounts  execution  adapters
                      external

  providers/          what the Service calls
    registry.py       provider definitions by (kind, type)
    endpoints.py      the environment endpoints Service processes dial, checked by the endpoint policy
    envd.py           external envd targets: endpoint and token rules, device identity, the adapter
    model_settings.py the settings schema of each calling API
    environments/     the offered environment types; docker.py  e2b.py  local.py
    tools/            the tool-source contract; mcp.py  oauth.py  connectors.py  discovery.py
                      mcp_catalog.py  mcp_servers.json
    traces/           the trace backend contract; langfuse.py  logfire.py

  migrations/         env.py  runner.py  script.py.mako  versions/
```

Four business packages answer four questions. `tenancy`: who is asking and what may they do ([03](03-tenancy.md)). `resources`: what has the tenant configured ([04](04-resources.md)). `runs`: how does one input become one sealed run ([05](05-runs.md), [06](06-environments.md), [07](07-facts-and-delivery.md)). `providers`: what does the Service call ([08](08-providers.md)). Shared mechanisms live under `infra/`; configuration and assembly stay at the root ([09](09-runtime.md)).

Packages under `resources/` own tenant-configured records: identity, scope, configuration, encrypted credentials, enabled state and their API. `providers/` owns backend adapters and contracts that receive plain values. For example, `resources/providers/` stores a web provider account, and the Harness definition registered in `providers/registry.py` builds the backend that serves it.

The tree fixes responsibilities and boundaries, not a file inventory. A module becomes a package, or a package gains a module, when a cohesive responsibility needs it; empty layers are not created ahead of need.

## Import direction

```
app.py, cli.py, distribution.py, migrations/  ->  everything   (no infra, business or provider module imports them)
runs  ->  resources  ->  tenancy  ->  infra
runs, resources  ->  providers.registry, providers.tools, providers.traces
runs             ->  providers.envd
tenancy, resources, runs  ->  settings.py
settings.py      ->  providers.tools.mcp_catalog, providers.traces
providers  ->  infra                                          (and the Harness)
```

`packages/a13n-service/.importlinter` states these contracts, and `make service-boundaries` checks them; `make typecheck`, `make verify` and the Service CI workflow run it.

| Contract                | Rule                                                                                                                       |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `layers`                | `runs` above `resources` above `tenancy` above `infra`; a lower layer never imports a higher one                           |
| `providers`             | `providers` never imports `tenancy`, `resources` or `runs`: providers are adapters over infrastructure                     |
| `assembly`              | `infra`, `tenancy`, `resources`, `runs` and `providers` never import `app`, `distribution` or `cli`                        |
| `infrastructure`        | `infra` never imports `settings`; callers pass configuration values                                                        |
| `environment-providers` | `tenancy`, `resources` and `runs` never import `providers.environments`; they reach environment types through the registry |
| `acyclic-resources`     | the packages under `resources/` depend on each other without cycles                                                        |

Generic mechanisms belong in `infra`: the outbox table and its claim, settle and retry rules are there, while delivery handlers and scan predicates stay with their business owners and are wired in `distribution.py`.

The resource packages depend on each other in one direction:

```
agents     ->  connections, environment_templates, models, providers, secrets, skills
connections  ->  connector_providers, providers
models, environment_templates, connector_providers, web_providers  ->  providers
skills, assets  ->  uploads
```

A package uses another's service functions, schemas and focused queries, and may import its row class where a query needs it. Cross-package references are IDs and detached values, never live ORM rows held across a session.

## Infrastructure

| Module         | Owns                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `db.py`        | `Storage` (the engine and session factory), `short_session` and `transaction` (which runs `after_commit` callbacks), `lock` (`FOR UPDATE`), `advisory_lock` (transaction-scoped), `now` (database `clock_timestamp()`), `Base`, `Stamped` (`version`, `created_at`, `updated_at`, advanced by the `stamp_resource` trigger except for a table's unversioned columns) and the declarative trigger rules `immutable` and `identity_guarded` |
| `ids.py`       | `new_object_id(kind)` and the `ObjectId` acceptance type ([Object IDs](#object-ids))                                                                                                                                                                                                                                                                                                                                                      |
| `errors.py`    | `ServiceError(code, message, details)`, the code list, the factories `not_found`, `conflict`, `disabled`, `invalid` and `rate_limited`, and `at_field`, which reports a failed reference as the field that names it                                                                                                                                                                                                                       |
| `http.py`      | request IDs, the error envelope, its status mapping and its OpenAPI description, strong ETags and `require_match`, the `If-Match`, page-limit and `Idempotency-Key` parameter types, and the headers stored content is served with ([10](10-api.md))                                                                                                                                                                                      |
| `ingress.py`   | request-body size and arrival-time bounds before parsing                                                                                                                                                                                                                                                                                                                                                                                  |
| `cursors.py`   | opaque, bounded pagination positions bound to one collection and the query that issued them ([10](10-api.md#collections))                                                                                                                                                                                                                                                                                                                 |
| `labels.py`    | the `labels` type and its `key:value` list filter                                                                                                                                                                                                                                                                                                                                                                                         |
| `crypto.py`    | the key ring and the authenticated envelope bound to its organization, table, column and row ([03](03-tenancy.md#credential-encryption))                                                                                                                                                                                                                                                                                                  |
| `audit.py`     | the `audit_events` table and `record` ([03](03-tenancy.md#audit))                                                                                                                                                                                                                                                                                                                                                                         |
| `outbox.py`    | the `outbox` table: `enqueue`, `claim` with a random fencing token, `settle` (including deferral), backoff and dead-lettering ([07](07-facts-and-delivery.md#outbox))                                                                                                                                                                                                                                                                     |
| `sweeps.py`    | `Sweep` and the bounded loop that runs a role's sweeps ([09](09-runtime.md#sweeps))                                                                                                                                                                                                                                                                                                                                                       |
| `redis.py`     | best-effort rate limits, the claim wakeup marker and capped stream append and reads ([09](09-runtime.md#redis))                                                                                                                                                                                                                                                                                                                           |
| `outbound.py`  | host-owned HTTP clients under the endpoint policy and response bounds ([08](08-providers.md#outbound-endpoint-policy))                                                                                                                                                                                                                                                                                                                    |
| `images.py`    | owner images: signature-checked PNG, JPEG and WebP stored create-only at their digest and served inert ([03](03-tenancy.md#images))                                                                                                                                                                                                                                                                                                       |
| `telemetry.py` | the OTLP trace export pipeline and attempt correlation attributes ([09](09-runtime.md#observability))                                                                                                                                                                                                                                                                                                                                     |
| `objects/`     | the object-store contract (`ObjectRef`, create-only writes, verified reads, prefix listing and deletion) with local and S3 implementations ([07](07-facts-and-delivery.md#objects))                                                                                                                                                                                                                                                       |

## A resource package

A resource package starts from four modules and grows cohesive ones beside them, such as `connections/authorization.py` or `skills/package.py`:

| Module       | Contains                                                                                   | Never contains             |
| ------------ | ------------------------------------------------------------------------------------------ | -------------------------- |
| `tables.py`  | the row classes, their constraints, indexes and trigger rules                              | queries, business rules    |
| `schemas.py` | the Pydantic types the API accepts and returns, and the kind's frozen configuration type   | SQLAlchemy                 |
| `service.py` | the use cases, and the resolution functions other packages call                            | FastAPI, HTTP status codes |
| `routes.py`  | the FastAPI router: parse the request, call one use case, set the ETag, shape the response | SQL, business rules        |

Every service function follows the same conventions:

- A public use case takes `Storage`, the acting principal and plain values, opens its own `short_session` or `transaction`, and returns detached API values. Authorization happens inside the use case, so tools, sweeps and extensions that call it get the same checks as HTTP.
- Inside a transaction the order is authorize the path, lock the row and authorize the verb on it (a changing verb refuses an archived workspace's row), `require_match`, act, stamp `updated_by_id`, `record` the audit event. A no-op returns without stamping or auditing.
- A function that takes an `AsyncSession` belongs to its caller's transaction: it may flush, never commits and performs no external I/O. Resolution functions such as `resolve_provider` and `resolve_connection` are of this kind and return frozen values.
- External I/O happens between short sessions, never inside one. The value it produced is published in a new transaction that rechecks the rows it depended on.

## Naming rules

| Rule                                                                                                                                                                                                                                                                                                                  | Examples                                                                                                  |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Tables are plural nouns. Join tables are `<owner>_<owned>`.                                                                                                                                                                                                                                                           | `runs`, `inbox_entries`, `thread_environments`                                                            |
| Foreign keys are the singular table name plus `_id`. Self-references say the relation.                                                                                                                                                                                                                                | `thread_id`, `agent_revision_id`, `parent_run_id`, `connector_provider_id`                                |
| Timestamps are a past participle plus `_at`.                                                                                                                                                                                                                                                                          | `created_at`, `sealed_at`, `archived_at`, `retired_at`, `lease_expires_at`                                |
| A state machine is one column called `status` with lowercase word values and a CHECK.                                                                                                                                                                                                                                 | `runs.status IN ('accepted', 'running', 'waiting', 'completed', 'failed', 'cancelled')`                   |
| An on/off switch is `enabled`.                                                                                                                                                                                                                                                                                        | `connections.enabled`                                                                                     |
| Retirement follows the owning lifecycle: heads archive, providers, models, templates and connections disable, credentials revoke, threads archive, assets retire.                                                                                                                                                     | `archived_at`, `enabled`, `retired_at`                                                                    |
| A discriminator is `kind`. A provider or connection implementation selector is `type`, because that is what the Harness calls it.                                                                                                                                                                                     | `inbox_entries.kind`, `tokens.kind`, `model_providers.type`, `connections.type`                           |
| JSON columns are named for their content, never with a `_json` suffix.                                                                                                                                                                                                                                                | `config`, `payload`, `failure`, `labels`, `settings`                                                      |
| A column holding an immutable object key ends in `_ref`; a run's state and display objects are named by the typed pointers `checkpoint` and `display`.                                                                                                                                                                | `package_ref`, `content_ref`, `runs.checkpoint`                                                           |
| A content hash is `digest` (SHA-256, hex). A hashed secret is `secret_hash`.                                                                                                                                                                                                                                          | `agent_revisions.digest`, `api_keys.secret_hash`                                                          |
| Counters: `number` for revisions and attempts, `version` for mutable-row concurrency, `position` for inbox order, `seq` for checkpoints, `generation` for invalidation.                                                                                                                                               | `run_attempts.number`, `inbox_entries.position`, `incorporated_checkpoint_seq`, `environments.generation` |
| Who: `principal_id` is the identity something executes as or belongs to, `created_by_id` and `updated_by_id` are authors, `actor_id` is the audit subject.                                                                                                                                                            | `runs.principal_id`, `secrets.principal_id`                                                               |
| Row classes end in `Row`; API types are the plain noun, except the read types of runs, threads, sessions, inbox entries, attempts, environments, mounts and grants, which end in `View` because their plain nouns already name Harness or domain types those modules use; frozen configuration types end in `Config`. | `AgentRow`, `Agent`, `RunView`, `AgentConfig`, `McpConfig`                                                |
| Functions are verb phrases.                                                                                                                                                                                                                                                                                           | `create_agent`, `resolve_connection`, `accept`, `claim`, `execute`, `seal`                                |
| Modules are named for what they hold, never for a phase or a quality.                                                                                                                                                                                                                                                 | `accept.py`, `claim.py`, `mounts.py`                                                                      |
| One word, one meaning; the [glossary](glossary.md) is normative, and a new word needs an entry.                                                                                                                                                                                                                       |                                                                                                           |

## Object IDs

Object IDs follow the platform's [data conventions](../data-conventions.md#service-id-allocation): a kind prefix, an underscore and a cryptographically random lowercase hexadecimal suffix whose length the shared allocator assigns per prefix. The data conventions own the tiers and their volume budgets. `ObjectId` accepts exactly what the allocator can produce: a prefix of 2 to 8 lowercase ASCII letters or digits starting with a letter, an underscore and 20 to 32 lowercase hexadecimal characters (at most 41 in all). Consumers never infer authority, ownership or order from an ID.

| Prefix              | Kind                                                                  | Prefix                | Kind                                         |
| ------------------- | --------------------------------------------------------------------- | --------------------- | -------------------------------------------- |
| `org`               | organizations                                                         | `mdl`                 | models                                       |
| `ws`                | workspaces                                                            | `envtpl`              | environment_templates                        |
| `usr`, `sa`         | principals (user, service account)                                    | `conn`                | connections                                  |
| `key`               | api_keys                                                              | `connop`              | connection operations                        |
| `ase`, `prt`, `ect` | tokens (login session, password reset, email change)                  | `sec`                 | secrets                                      |
| `rb`                | grants                                                                | `ast`                 | assets                                       |
| `inv`               | invitations                                                           | `sub`                 | subscriptions                                |
| `audit`             | audit_events                                                          | `sess`                | sessions                                     |
| `obx`               | outbox                                                                | `thread`              | threads                                      |
| `ap`, `apr`         | agents, agent_revisions                                               | `inb`                 | inbox_entries                                |
| `sk`, `skr`         | skills, skill_revisions                                               | `run`                 | runs                                         |
| `mprov`             | model_providers                                                       | `rat`                 | run_attempts                                 |
| `eprov`             | environment_providers                                                 | `env`                 | environments                                 |
| `cprov`             | connector_providers                                                   | `envoper`, `envrenew` | environment operations, environment renewals |
| `wprov`             | web_providers                                                         | `wrk`                 | worker IDs                                   |
| `ctl`               | control sweep claim owners (outbox delivery, environment maintenance) | `req`                 | request IDs                                  |

Uploads are `upl_` plus a 64-character SHA-256 derived from the upload's scope and request key ([04](04-resources.md#uploads-and-assets)). `passwords` and `thread_environments` are join tables without IDs, and `usage_records` keep the Harness's record IDs; every other table has a 72-character `id` primary key, and every workspace-owned row has a composite `(organization_id, workspace_id)` foreign key. Display items are `itm_` plus the first 32 hex characters of a SHA-256 of their run, kind and source, so they are stable across attempts ([07](07-facts-and-delivery.md#checkpoints-and-display)). Harness tool-call IDs, provider-owned IDs, secrets, cursors and digests keep their owners' formats.

## Error codes

One exception class, `ServiceError(code, message, details)`, carries every refusal, and one table in `infra/http.py` maps codes to HTTP status. Clients branch on `code` and `details`, never on `message`; the envelope is [10](10-api.md#errors)'s.

| Code                    | Status | Details carry                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ----------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `invalid_argument`      | 400    | `field` and `reason`; a failed reference also keeps its `kind` and `id`; request validation gives `fields`, up to 20 `{field, reason}` with reason the validation error type                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `invalid_cursor`        | 400    |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `unauthenticated`       | 401    |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `forbidden`             | 403    | `verb` when a grant is missing; `field` when a referenced resource refused the caller; `id` when a principal-owned object is not the caller's                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `not_found`             | 404    | `kind`, `id`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `already_exists`        | 409    | `kind` and the colliding `key`; a duplicate grant has kind `grant` and the principal ID as `key`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `conflict`              | 409    | `kind`, `id` and `reason`, a stable word naming the state rule that refused the operation; `limit` when the rule is a capacity bound                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `precondition_failed`   | 412    | `current_etag`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `precondition_required` | 428    | `header`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `payload_too_large`     | 413    | `limit`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `request_timeout`       | 408    |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `disabled`              | 422    | `kind`, `id`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `unavailable`           | 503    | `dependency`: `database`, `redis`, `objects`, `encryption` (no active key, or an envelope that does not decrypt), `mail`, `github`, `oauth`, `connection`, `connection:{type}`, `connector:{type}`, `environment` (with `id`), `{kind}:{type}` (a provider type, `environment:{type}` included), `model_api:{api}` (settings for a calling API the deployment no longer offers, [08](08-providers.md#provider-type-descriptions)), `harness` (the Harness run ended without a result), `url` (URL input, [05](05-runs.md#assignment-and-incorporation)), `trace` (no backend configured) or `trace:{type}`; sometimes a safe `reason` |
| `rate_limited`          | 429    | `retry_after_seconds`, also sent as `Retry-After`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `internal`              | 500    |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |

A provider failure during execution is not an HTTP error: it becomes the run's recorded failure with the provider's classified code ([05](05-runs.md)).

## What is deliberately absent

- No `common`, `support`, `management`, `utils` or `helpers` modules. A helper belongs to the module of its concern or to the package that uses it.
- No generic `Resource[T]` base class or CRUD generator. Resources written out by hand in the same shape are easier to read and to diff than one generator.
- No per-feature HTTP error types, cursor codecs or lock mechanisms: every refusal is a `ServiceError` with a shared code, and every list uses `infra/cursors.py`. A protocol adapter may classify its own failures (`OAuthError`), which callers translate into a code.
- No `_json` or `_sha256` column suffixes, and no `Record`, `Manager`, `Coordinator` or `Reconciler` class names.
- No function-local imports. If one seems necessary, the layering is wrong.
