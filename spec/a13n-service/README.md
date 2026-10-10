# a13n Service

This directory is the current product and architecture contract of the a13n Service, implemented by `packages/a13n-service`. Its exported contract is `proto/a13n-service/` (`openapi.json` and `thread-stream.schema.json`), and its user documentation is `docs/a13n-service/`. If a contract conflict is found, identify the exact conflict and resolve it in the owning section before changing the affected behavior.

## The shape in one paragraph

The Service is a managed-agent runtime. A tenant configures resources (agents and their revisions, skills, provider accounts, models, environment templates, connections, memories, assets and webhook subscriptions), submits input to a thread, and the Service turns that input into runs: it accepts the input, a worker claims the run as a fenced attempt, executes it through the embedded Harness with the run's frozen configuration and mounted environments and memories, and seals the outcome as immutable facts. Callers integrate by submitting input and observing runs through the thread stream, webhooks and reads; configuration, credentials, resume and control use the same resource API. Everything the Service calls, a model, a sandbox, a tool server, a web search, a record memory backend, is a provider selected by `type`. The code is five business packages, `tenancy`, `resources`, `runs`, `providers` and `usage`, on shared mechanisms under `infra/`, assembled by one `build_app(distribution)`.

## How to read this

| If you want to know                                    | Read                                                 |
| ------------------------------------------------------ | ---------------------------------------------------- |
| what the Service is, its boundaries and principles     | [00-overview.md](00-overview.md)                     |
| where code goes, how things are named, the error codes | [02-layout.md](02-layout.md)                         |
| who can do what                                        | [03-tenancy.md](03-tenancy.md)                       |
| what a tenant configures                               | [04-resources.md](04-resources.md)                   |
| how input becomes a sealed run                         | [05-runs.md](05-runs.md)                             |
| where a run executes                                   | [06-environments.md](06-environments.md)             |
| what agents remember across conversations              | [11-memory.md](11-memory.md)                         |
| what the Service remembers and tells others            | [07-facts-and-delivery.md](07-facts-and-delivery.md) |
| what the Service calls                                 | [08-providers.md](08-providers.md)                   |
| processes, background work, assembly, extension points | [09-runtime.md](09-runtime.md)                       |
| what the Service logs and measures about itself        | [12-observability.md](12-observability.md)           |
| the HTTP surface                                       | [10-api.md](10-api.md)                               |
| what a word means                                      | [glossary.md](glossary.md)                           |

Start with [00-overview.md](00-overview.md), [02-layout.md](02-layout.md) and the [glossary](glossary.md), then read the chapter that owns the behavior you are changing. Authorization ([03](03-tenancy.md)), durable facts ([07](07-facts-and-delivery.md)), runtime rules ([09](09-runtime.md)) and observability ([12](12-observability.md)) apply to every feature.

## Rule ownership

A complete rule is defined once, in its owning section. Other chapters describe their own interface or storage contribution and link to that rule instead of copying it. When behavior changes, update the owner and every affected interface together.

| Rule                                                                                                                                                                                                                             | Owner                                              |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| Service boundaries, scope limits and design principles                                                                                                                                                                           | [00: overview](00-overview.md#boundaries)          |
| Package responsibilities, import direction and its checked contracts, service-function conventions, naming, object IDs and error codes                                                                                           | [02: layout](02-layout.md#import-direction)        |
| Principals, authentication, authorization and credential scope, execution authority, credential encryption and audit                                                                                                             | [03: tenancy](03-tenancy.md#authorization)         |
| Resource lifecycles and what a run freezes, revisioned heads, agent configuration validation, provider resources, connection authorization and operations, header names and caller headers, the upload budget                    | [04: resources](04-resources.md#two-lifecycles)    |
| Input capacity, source selection, acceptance, overrides, resume, claim, execution, sealing and child runs                                                                                                                        | [05: runs](05-runs.md)                             |
| Environment instances, mounts, external operations, idle policy and external envd targets                                                                                                                                        | [06: environments](06-environments.md)             |
| Memories, the PostgreSQL file store and its history, records and namespace purges, thread memory mounts, what a run executes with and memory cursors                                                                             | [11: memory](11-memory.md)                         |
| Immutability, usage records, objects, checkpoints and display, the thread stream, the outbox, lifecycle webhooks, trace queries, findings and analysis                                                                           | [07: facts and delivery](07-facts-and-delivery.md) |
| Provider definitions, the registry, handles, the outbound endpoint policy, tool sources, the MCP server catalogue, trace backends and installed plugins                                                                          | [08: providers](08-providers.md)                   |
| Roles and the command line, startup, readiness and shutdown, migrations, settings and operational limits, HTTP ingress and escaping failures, Redis use, sweeps, the worker and its claim wakeups, assembly and extension points | [09: runtime](09-runtime.md)                       |
| Routes, authentication at the HTTP boundary, path resolution, representations and stored-content headers, collections and cursors, preconditions and ETags, idempotency, the error envelope and statuses, and the OpenAPI export | [10: API](10-api.md)                               |
| Signal ownership, log fields and events, the metric catalog and backlog, trace export and its correlation                                                                                                                        | [12: observability](12-observability.md)           |

Tests, validation gates and end-to-end scenarios are contributor workflow, owned by [CONTRIBUTING.md](../../CONTRIBUTING.md#local-validation) and [e2e/service](../../e2e/service/README.md). The glossary gives short definitions and links; it does not repeat rules.

## Conventions in these documents

- Tables are shown as column lists. `NULL` marks a nullable column; every other column is NOT NULL. Types are omitted where obvious: `*_id` columns hold IDs, `*_at` columns hold UTC timestamps, and `labels`, `config`, `payload`, `settings`, `failure` and the like are typed JSONB. `*_ref` holds an immutable object key. `runs.checkpoint` and `runs.tail` are typed pointers to the run's committed state and display tail objects. A prefix in parentheses after a table name is its object-ID prefix. Constraints are listed where they carry behavior; the `tables.py` modules and the migration hold the exact DDL.
- Settings are named `section.field`, such as `providers.operation_seconds`. `a13n_service/settings.py` defines them, and the generated [configuration reference](../../docs/a13n-service/configuration-reference.md) lists their defaults and bounds.
- Paths omit `/api/v1`. A business path such as `POST /agents` acts in the workspace its request names ([03](03-tenancy.md#authorization)); administration paths name their organization or workspace, as [10](10-api.md) lists. Error codes are those of [02](02-layout.md#error-codes); "409 `conflict` with reason `archived`" names the `reason` detail.
- Code is Python 3.13, SQLAlchemy 2 (async), FastAPI and Pydantic 2. Snippets are conceptual signatures that state a contract; the exported schemas hold exact wire shapes.
