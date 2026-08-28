# Foundation Service

## Design Position

This directory defines `foundation-service`, the optional durable Host that embeds `agent-harness`. It is a modular service with independently selectable control and worker process roles, not another Agent loop and not a collection of independently versioned microservices.

Foundation owns managed Secrets, resource authorization, serializable Agent authoring resources, immutable revisions and dependency locks, trusted Harness plugin artifacts, durable Threads, Turns, and TurnAttempts, scheduling, pending actions, Environment management, lifecycle events, raw usage records, and the public management API.

It does not redefine the code-first Harness `AgentDefinition`, Pydantic Agent loop, Harness result and state semantics, Agent Stream Protocol conversion, Environment provider lifecycle types, EIP, or provider-native state. Platform-owned data and APIs follow [Platform Data Conventions](../data-conventions.md) and [Platform API Conventions](../api-conventions.md).

## Accepted Identity Model

The shared [Platform Interaction Model](../interaction-model.md) owns the public concepts `Session`, `Thread`, `Turn`, and `Item`. Foundation uses Turn and TurnAttempt directly as its durable scheduling identities:

```text
Session -> Thread -> Turn -> TurnAttempt -> Harness Run -> ModelAttempt
```

Every Foundation-managed Agent invocation accepts a Turn belonging to exactly one Session and Thread. Interactive requests, schedules, webhooks, service requests, and asynchronous children use the same Turn scheduler and recovery contract.

Each hosted Thread is an independent versioned relational resource. It owns
Session membership, origin, the current Turn (the most recently accepted Turn),
the selected continuation head, and optimistic concurrency for accepted
advancement; Turn rows remain the durable work and state DAG. Whether the
current Turn is active derives from its status.

A non-terminal Turn can span several process-local Harness Runs when worker recovery creates another TurnAttempt. A waiting Turn is sealed; authenticated feedback accepts a new Turn whose `parent_turn_id` names that waiting Turn. The new Turn receives fresh state and, when scheduled, its own TurnAttempt, bindings, and Harness Run. One `TurnAttempt` starts at most one Harness Run; internal Harness `ModelAttempt` values are not durable worker generations. Neither `turn_attempt_id` nor `run_id` replaces `turn_id` or `thread_id`.

## Specification Catalog

| Document                                                                                              | Owning contract                                                                                                                    |
| ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| [00 Overview](00-overview.md)                                                                         | Service shape, end-to-end flow, subsystem boundaries, dependency direction, and completion boundaries                              |
| [01 Runtime Configuration and Deployment](01-runtime-configuration-and-deployment.md)                 | Configuration precedence, deployment profiles, control and worker roles, startup, readiness, supervision, drain, and shutdown      |
| [02 Distribution Composition and Extensions](02-distribution-composition-and-extensions.md)           | OSS, EE, and Cloud composition, dependency direction, contribution conflicts, configuration, and final schema assembly             |
| [03 Storage](03-storage.md)                                                                           | Relational, Redis-compatible, object, and mounted-filesystem capabilities and local/network semantics                              |
| [04 Relational Schema](04-relational-schema.md)                                                       | Final distribution metadata, migration authority, compatibility, application, and failure semantics                                |
| [05 HTTP Ingress and Request Contract](05-http-ingress-and-request-contract.md)                       | Role surfaces, request context, proxy and browser trust, authentication boundaries, errors, streaming, and drain                   |
| [06 Durable Operations and Outbox](06-durable-operations-and-outbox.md)                               | Versioned mutation, idempotency evidence, atomic durable commits, outbox publication, retries, and unknown outcomes                |
| [10 Identity and Access Management](10-identity-and-access-management.md)                             | Organization and Workspace tenancy, User and Service Account identity, credentials, RoleBindings, authorization, and audit         |
| [11 Secret Management](11-secret-management.md)                                                       | Managed Secret identity, ownership, metadata-only API, encrypted persistence, mutation, deletion, and disclosure controls          |
| [12 Agent Revisions and Reconstruction](12-agent-revisions-and-reconstruction.md)                     | Agent Presets, immutable revisions, model integrations, dependency locks, and trusted process-local reconstruction                 |
| [13 Interactions, Turns, and Attempts](13-interactions-turns-and-attempts.md)                         | Interaction-to-runtime mapping, dispatch boundary, Harness Run binding, cancellation, and unknown outcomes                         |
| [14 Durable Turn State](14-turn-persistence.md)                                                       | Turn identity, lifecycle, lineage, deterministic state object, conditional checkpoints, sealing, recovery budget, and retention    |
| [15 Durable Turn Attempt Persistence](15-turn-attempt-persistence.md)                                 | TurnAttempt allocation, relational shape, leases, fences, dispatch evidence, loss, recovery, and attempt outcomes                  |
| [16 Scheduling, Workers, and Recovery](16-scheduling-workers-and-recovery.md)                         | Eligibility, Redis coordination, claims, leases, stale-worker rejection, dispatch uncertainty, replacement, retry, and shutdown    |
| [17 Lifecycle and Stream Persistence](17-lifecycle-and-stream-persistence.md)                         | Lifecycle-event persistence, stable Turn Redis Stream, bounded live replay, Items, and immutable replay snapshots                  |
| [18 Deferred Actions and Children](18-deferred-actions-and-children.md)                               | Approval, client tools, user input, sealed waiting Turns, asynchronous child Turns, result delivery, and cancellation              |
| [19 Environment Management](19-environment-management.md)                                             | Host use of provider specifications, resource state, operations, attachments, reconciliation, and envd boundary                    |
| [20 Events, Usage, and Delivery](20-events-usage-and-delivery.md)                                     | Harness observation, AG-UI and Item projection, lifecycle events, delivery, raw usage, large content, and telemetry                |
| [21 Management API](21-management-api.md)                                                             | Public resource routes, existing-Thread and root Turn submission, commands, read models, replay, and compatibility                 |
| [22 Agent Interaction Retrieval](22-agent-interaction-retrieval.md)                                   | Agent-facing authorized retrieval of retained Turn lineage and interaction projections                                             |
| [23 Connectors, Connections, and Triggers](23-connectors-connections-and-triggers.md)                 | Trusted Provider discovery, Connector revisions, account Connections, managed tools, and Trigger occurrence acceptance             |
| [24 Durable Thread Persistence](24-thread-persistence.md)                                             | Thread relational identity, Session membership, origin, version, current Turn, continuation head, creation, advancement, and reads |
| [25 Harness Plugin Artifacts and Runtime Loading](25-harness-plugin-artifacts-and-runtime-loading.md) | Internal trusted wheel publication, one-plugin packaging, exact artifact locks, and process-local on-demand loading                |

Read `00`, `01`, and `02` before changing process startup, roles, or distribution contents. Read `03`, `04`, and `06` before introducing a durable capability. Read `05`, `10`, and `21` before changing public ingress. Read `25` with `12` and `16` before changing managed Harness plugin artifacts, process-local loading, or Worker compatibility. Read `24` before `13` through `17` when changing Thread or Turn acceptance, persistence, recovery, or reads. Read `18` before changing waiting feedback or asynchronous children. Read `23` before changing Connector Providers, Connections, managed Connector tools, or Trigger ingress. Read the shared interaction model before changing Session, Thread, Turn, or Item semantics. Read the Environment Provider and Agent Stream Protocol catalogs before adding provider or event adapters.

## Implementation Orientation

The current package establishes these service-wide roots:

| Path                                                             | Architectural role                                                                     |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `packages/foundation-service/a13n_service/settings.py`           | Maps process environment into typed provider and migration configuration               |
| `packages/foundation-service/a13n_service/app.py`                | Owns FastAPI lifespan, constructs one storage resource set, and exposes readiness      |
| `packages/foundation-service/a13n_service/storage/`              | Generic backend configuration, construction, lifecycle, and capability semantics       |
| `packages/foundation-service/a13n_service/database/metadata.py`  | Explicit common registry selected by the OSS distribution descriptor                   |
| `packages/foundation-service/a13n_service/database/migration.py` | Programmatic Alembic runner and bounded migration coordination                         |
| `packages/foundation-service/a13n_service/database/migrations/`  | OSS distribution revision location assembled into its final graph                      |
| `packages/foundation-service/a13n_service/cli.py`                | Stable `foundation-service serve` and `foundation-service db ...` executable interface |

These roots are boundaries, not a requirement that every capability become a subpackage. Small capabilities remain focused modules; a capability gains a subdirectory only when it owns several cohesive implementations or contracts. Runnable configuration and migration usage live in the [Foundation Service package guide](../../packages/foundation-service/README.md).

## Authority Rules

- The control role accepts resources and commands, commits immutable selections, and owns durable lifecycle authority.
- The worker role claims fenced `TurnAttempt` leases and invokes Harness in-process. It does not expose another product API or run migrations.
- PostgreSQL is authoritative for accepted lifecycle state and fencing. Real Redis is required for distributed data flow and coordination; each owning domain defines its Redis retention and replay semantics, and Redis delivery alone never proves a relational transition.
- The artifact's distribution descriptor explicitly composes the complete configuration, routers, role components, authorization contributions, metadata, and migration graph; installed packages never change the service implicitly.
- Foundation records contain only Foundation-owned serializable data. They contain no Python class, plugin instance, native Model, Toolset, Capability, callable, client, credential, provider attachment, or live controller.
- The worker verifies exact locks and uses trusted installed adapters to reconstruct a process-local Harness `AgentDefinition` and fresh `RunBindings`.
- A managed Harness plugin wheel becomes usable only through an exact AgentRevision lock. A Worker loads that revision on demand, records loaded provenance only in process memory, and never substitutes or reloads another revision in the same interpreter.
- Connector Provider package presence grants no trust. Agent revisions freeze tool contracts and exact Provider dependency locks; every TurnAttempt resolves current Connection authority and credentials.
- Trigger ingress deduplicates one source occurrence into one root Turn under the common Session and Thread contract. It does not bypass Agent, IAM, scheduling, or Turn authority.
- Foundation consumes the canonical Environment Provider types and `HarnessAguiObserver`; it does not create parallel provider or Harness-event models.
- A stale TurnAttempt cannot mutate Thread current/head selection, Turn lifecycle or state, pending work, retained Items, child delivery, or terminal outcome. A late immutable `UsageRecord` can still be ingested under its original TurnAttempt when record identity and content validate, but it cannot mutate lifecycle.
- Before any external effect, the worker durably advances from `pre_dispatch` to `effects_possible`. Recovery never treats missing acknowledgement as proof that no effect occurred.
- Harness completion, durable Turn sealing, Item projection, event delivery, external delivery, usage ingestion, and any external settlement are separate facts.

## Specification Conventions

- Python-like schemas are conceptual unless explicitly declared as API or storage formats.
- An `AgentRevision` is immutable; changing materialized Agent content, its exact integration revision, or a dependency lock creates another revision.
- `Ref` values identify entities or revisions and grant no authority.
- Process-local objects are reconstructed and never become durable payloads.
- Domain schemas, repositories, queue messages, and events live in their owning domain rather than the generic storage substrate.
